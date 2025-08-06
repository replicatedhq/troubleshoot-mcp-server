"""
Bundle Manager for Kubernetes support bundles.

This module implements the Bundle Manager component, which is responsible for
handling the lifecycle of Kubernetes support bundles, including downloading,
extraction, initialization, and cleanup.
"""

import asyncio
import json
import logging
import os
import random
import re
import shutil
import signal
import socket
import tarfile
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple, Union
from urllib.parse import urlparse

import aiohttp
import psutil
import httpx  # Added for Replicated API calls
from pydantic import BaseModel, Field, field_validator

# Set up logging
logger = logging.getLogger(__name__)

# Constants for resource limits - can be overridden by environment variables
DEFAULT_DOWNLOAD_SIZE = 1024 * 1024 * 1024  # 1 GB
DEFAULT_DOWNLOAD_TIMEOUT = 300  # 5 minutes
DEFAULT_INITIALIZATION_TIMEOUT = 120  # 2 minutes

# Feature flags - can be enabled/disabled via environment variables
DEFAULT_CLEANUP_ORPHANED = True  # Clean up orphaned sbctl processes
DEFAULT_ALLOW_ALTERNATIVE_KUBECONFIG = True  # Allow finding kubeconfig in alternative locations

# Override with environment variables if provided
MAX_DOWNLOAD_SIZE = int(os.environ.get("MAX_DOWNLOAD_SIZE", DEFAULT_DOWNLOAD_SIZE))
MAX_DOWNLOAD_TIMEOUT = int(os.environ.get("MAX_DOWNLOAD_TIMEOUT", DEFAULT_DOWNLOAD_TIMEOUT))
MAX_INITIALIZATION_TIMEOUT = int(
    os.environ.get("MAX_INITIALIZATION_TIMEOUT", DEFAULT_INITIALIZATION_TIMEOUT)
)

# Feature flags from environment variables
CLEANUP_ORPHANED = os.environ.get("SBCTL_CLEANUP_ORPHANED", "true").lower() in (
    "true",
    "1",
    "yes",
)
ALLOW_ALTERNATIVE_KUBECONFIG = os.environ.get(
    "SBCTL_ALLOW_ALTERNATIVE_KUBECONFIG", "true"
).lower() in ("true", "1", "yes")

# Retry configuration for 403 errors
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 8.0


# Helper function for safe file copying
def safe_copy_file(src: Union[Path, None], dst: Union[Path, None]) -> None:
    """
    Safely copy a file, handling Path | None types.

    Args:
        src: Source path (may be None)
        dst: Destination path (may be None)

    Raises:
        ValueError: If src or dst is None
    """
    if src is None:
        raise ValueError("Source path cannot be None")
    if dst is None:
        raise ValueError("Destination path cannot be None")

    # Both paths are valid, perform the copy
    shutil.copy2(src, dst)


logger.debug(f"Using MAX_DOWNLOAD_SIZE: {MAX_DOWNLOAD_SIZE / 1024 / 1024:.1f} MB")
logger.debug(f"Using MAX_DOWNLOAD_TIMEOUT: {MAX_DOWNLOAD_TIMEOUT} seconds")
logger.debug(f"Using MAX_INITIALIZATION_TIMEOUT: {MAX_INITIALIZATION_TIMEOUT} seconds")
logger.debug(f"Feature flags - Cleanup orphaned processes: {CLEANUP_ORPHANED}")
logger.debug(f"Feature flags - Allow alternative kubeconfig: {ALLOW_ALTERNATIVE_KUBECONFIG}")

# Constants for Replicated Vendor Portal integration
REPLICATED_VENDOR_URL_PATTERN = re.compile(
    r"https://vendor\.replicated\.com/troubleshoot/analyze/([^/]+)"
)
# Ensure there is NO space between 'v' and '3'
REPLICATED_API_ENDPOINT = "https://api.replicated.com/vendor/v3/supportbundle/{slug}"

# GitHub URL patterns for attachment downloads
GITHUB_ATTACHMENT_URL_PATTERN = re.compile(r"https://github\.com/user-attachments/files/\d+/.+")
GITHUB_RELEASE_URL_PATTERN = re.compile(r"https://github\.com/[^/]+/[^/]+/releases/download/.+")
GITHUB_RAW_URL_PATTERN = re.compile(r"https://raw\.githubusercontent\.com/.+")


class BundleMetadata(BaseModel):
    """
    Metadata for an initialized support bundle.
    """

    id: str = Field(description="The unique identifier for the bundle")
    source: str = Field(description="The source of the bundle (URL or local path)")
    path: Path = Field(description="The path to the extracted bundle")
    kubeconfig_path: Path = Field(description="The path to the kubeconfig file")
    initialized: bool = Field(description="Whether the bundle has been initialized with sbctl")
    host_only_bundle: bool = Field(
        False,
        description="Whether this bundle contains only host resources (no cluster resources)",
    )


class InitializeBundleArgs(BaseModel):
    """
    Arguments for initializing a support bundle.
    """

    source: str = Field(description="The source of the bundle (URL or local path)")
    force: bool = Field(
        False,
        description="Whether to force re-initialization if a bundle is already active",
    )
    verbosity: Optional[str] = Field(
        None,
        description="Verbosity level for response formatting (minimal|standard|verbose|debug)",
    )

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        """
        Validate the bundle source.

        Args:
            v: The source string to validate

        Returns:
            The validated source string

        Raises:
            ValueError: If the source is invalid
        """
        # Check if it's a URL
        try:
            result = urlparse(v)
            if all([result.scheme, result.netloc]):
                return v
        except Exception:
            pass

        # Check if it's a local file - for validation, we only check if it's absolute path
        # If it's a relative path, it will be checked in the initialize_bundle method
        path = Path(v)
        if path.is_absolute():
            if not path.exists():
                raise ValueError(f"Bundle source not found: {v}")

            if not path.is_file():
                raise ValueError(f"Bundle source must be a file: {v}")

        return v


class BundleManagerError(Exception):
    """Base exception for bundle manager errors."""

    pass


class BundleDownloadError(BundleManagerError):
    """Exception raised when a bundle could not be downloaded."""

    pass


class BundleInitializationError(BundleManagerError):
    """Exception raised when a bundle could not be initialized."""

    pass


class BundleNotFoundError(BundleManagerError):
    """Exception raised when a requested bundle is not found."""

    pass


class ListAvailableBundlesArgs(BaseModel):
    """
    Arguments for listing available support bundles.
    """

    include_invalid: bool = Field(
        False,
        description="Whether to include invalid or inaccessible bundles in the results",
    )
    verbosity: Optional[str] = Field(
        None,
        description="Verbosity level for response formatting (minimal|standard|verbose|debug)",
    )


class BundleFileInfo(BaseModel):
    """
    Information about an available support bundle file.
    """

    path: str = Field(description="The full path to the bundle file")
    relative_path: str = Field(description="The relative path without bundle directory prefix")
    name: str = Field(description="The name of the bundle file")
    size_bytes: int = Field(description="The size of the bundle file in bytes")
    modified_time: float = Field(
        description="The modification time of the bundle file (seconds since epoch)"
    )
    valid: bool = Field(description="Whether the bundle appears to be a valid support bundle")
    validation_message: Optional[str] = Field(
        None, description="Message explaining why the bundle is invalid, if applicable"
    )


class BundleManager:
    """
    Manages the lifecycle of Kubernetes support bundles.

    This class handles downloading, extraction, initialization, and cleanup of
    support bundles. It uses sbctl to create a Kubernetes API server emulation
    from the bundle.
    """

    def __init__(self, bundle_dir: Optional[Path] = None) -> None:
        """
        Initialize the Bundle Manager.

        Args:
            bundle_dir: The directory where bundles will be stored. If not provided,
                a temporary directory will be used.
        """
        self.bundle_dir = bundle_dir or Path(tempfile.mkdtemp(prefix="k8s-bundle-"))
        self.bundle_dir.mkdir(parents=True, exist_ok=True)
        self.active_bundle: Optional[BundleMetadata] = None
        self.sbctl_process: Optional[asyncio.subprocess.Process] = None
        self._host_only_bundle: bool = False
        self._termination_requested: bool = False

    async def initialize_bundle(self, source: str, force: bool = False) -> BundleMetadata:
        """
        Initialize a support bundle from a source.

        Args:
            source: The source of the bundle (URL or local path)
            force: Whether to force re-initialization if a bundle is already active

        Returns:
            Metadata for the initialized bundle

        Raises:
            BundleManagerError: If the bundle could not be initialized
        """
        if self.active_bundle and not force:
            logger.info(f"Using already initialized bundle: {self.active_bundle.id}")
            return self.active_bundle

        await self._cleanup_active_bundle()

        logger.info(f"Initializing bundle from source: {source}")
        try:
            # Determine if the source is a URL or local file
            if source.startswith(("http://", "https://")):
                bundle_path = await self._download_bundle(source)
            else:
                # First, check if it's a full path
                bundle_path = Path(source)

                # If the path doesn't exist, check if it's a relative path in the bundle directory
                if not bundle_path.exists() and not bundle_path.is_absolute():
                    # Try to find it in the bundle directory
                    possible_path = self.bundle_dir / source
                    logger.info(f"Path {bundle_path} not found, trying {possible_path}")
                    if possible_path.exists():
                        bundle_path = possible_path
                    else:
                        # Also check if there's a bundle with matching relative_path in available bundles
                        try:
                            available_bundles = await self.list_available_bundles(
                                include_invalid=True
                            )
                            for bundle in available_bundles:
                                if bundle.relative_path == source or bundle.name == source:
                                    logger.info(
                                        f"Found matching bundle by relative path: {bundle.path}"
                                    )
                                    bundle_path = Path(bundle.path)
                                    break
                        except Exception as e:
                            logger.warning(f"Error searching for bundle by relative path: {e}")

                # If we still can't find it, raise an error
                if not bundle_path.exists():
                    raise BundleNotFoundError(
                        f"Bundle not found: {source} (tried both as absolute path and in bundle directory {self.bundle_dir})"
                    )

            # Generate a unique ID for the bundle
            bundle_id = self._generate_bundle_id(source)

            # Create a directory for the bundle
            bundle_output_dir = self.bundle_dir / bundle_id
            bundle_output_dir.mkdir(parents=True, exist_ok=True)

            # Initialize the bundle with sbctl
            kubeconfig_path = await self._initialize_with_sbctl(bundle_path, bundle_output_dir)

            # Handle case where no kubeconfig was created (host-only bundle)
            if self._host_only_bundle:
                # Create a dummy kubeconfig path for host-only bundles (but don't create the file)
                kubeconfig_path = bundle_output_dir / "kubeconfig"

            # Debug: List all files in the bundle directory to diagnose file listing issues
            try:
                logger.info(f"Listing files in bundle directory: {bundle_output_dir}")
                # First count files recursively
                file_count = 0
                dir_count = 0
                for root, dirs, files in os.walk(bundle_output_dir):
                    dir_count += len(dirs)
                    file_count += len(files)

                logger.info(
                    f"Bundle directory contains {file_count} files and {dir_count} directories"
                )

                # List top-level entries
                top_entries = list(bundle_output_dir.glob("*"))
                logger.info(
                    f"Top-level entries in bundle directory: {[e.name for e in top_entries]}"
                )

                # Also check if extracted_dir exists or needs to be created
                extract_dir = bundle_output_dir / "extracted"
                if not extract_dir.exists():
                    logger.info(f"Creating extract directory: {extract_dir}")
                    extract_dir.mkdir(exist_ok=True)

                    # Extract the bundle if it's a tarfile - ensure support bundle extraction succeeds
                    # Support bundles often have complex structures, so we need to handle them properly
                    if str(bundle_path).endswith((".tar.gz", ".tgz")):
                        import tarfile

                        logger.info(f"Extracting bundle to: {extract_dir}")
                        with tarfile.open(bundle_path, "r:gz") as tar:
                            # First list the files to get a count
                            members = tar.getmembers()
                            logger.info(f"Support bundle contains {len(members)} entries")

                            # Extract all files
                            from pathlib import PurePath

                            safe_members = []
                            for member in members:
                                # Make path safe by removing absolute paths and parent dir traversal
                                if member.name.startswith(("/", "../")):
                                    # Remove leading slashes and parent directory traversal
                                    member.name = PurePath(member.name).name
                                safe_members.append(member)

                            # Extract with the sanitized member list
                            # Use filter='data' to only extract file data without modifying metadata
                            tar.extractall(path=extract_dir, members=safe_members, filter="data")

                        # List extracted files and verify extraction was successful
                        file_count = 0
                        dir_count = 0
                        for root, dirs, files in os.walk(extract_dir):
                            dir_count += len(dirs)
                            file_count += len(files)

                        extracted_files = list(extract_dir.glob("*"))
                        logger.info(
                            f"Extracted {len(extracted_files)} top-level entries to {extract_dir}"
                        )
                        logger.info(
                            f"Extracted bundle contains {file_count} files and {dir_count} directories"
                        )
            except Exception as list_err:
                logger.warning(f"Error while listing bundle files: {list_err}")

            # Create and store bundle metadata
            metadata = BundleMetadata(
                id=bundle_id,
                source=source,
                path=bundle_output_dir,
                kubeconfig_path=kubeconfig_path,
                initialized=True,
                host_only_bundle=self._host_only_bundle,
            )
            self.active_bundle = metadata

            logger.info(f"Bundle initialized: {bundle_id}")
            return metadata

        except (BundleDownloadError, BundleInitializationError) as e:
            logger.error(f"Failed to initialize bundle: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"Unexpected error initializing bundle: {str(e)}")
            raise BundleManagerError(f"Failed to initialize bundle: {str(e)}")

    def _calculate_retry_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter for retry attempts."""
        delay = min(RETRY_BASE_DELAY * (2**attempt), RETRY_MAX_DELAY)
        jitter = delay * (0.5 + random.random() * 0.5)
        return float(jitter)

    def _is_github_url(self, url: str) -> bool:
        """Check if the URL is a GitHub URL that requires special authentication."""
        return bool(
            GITHUB_ATTACHMENT_URL_PATTERN.match(url)
            or GITHUB_RELEASE_URL_PATTERN.match(url)
            or GITHUB_RAW_URL_PATTERN.match(url)
        )

    async def _get_replicated_signed_url(self, original_url: str) -> str:
        """
        Get the temporary signed download URL from the Replicated Vendor Portal API.

        Args:
            original_url: The original Replicated Vendor Portal URL.

        Returns:
            The signed download URL.

        Raises:
            BundleDownloadError: If the signed URL cannot be retrieved.
        """
        match = REPLICATED_VENDOR_URL_PATTERN.match(original_url)
        if not match:
            # This should not happen if called correctly, but handle defensively
            raise BundleDownloadError(f"Invalid Replicated URL format: {original_url}")

        slug = match.group(1)
        logger.info(f"Detected Replicated Vendor Portal URL with slug: {slug}")

        # Get token - SBCTL_TOKEN takes precedence over REPLICATED
        token = os.environ.get("SBCTL_TOKEN") or os.environ.get("REPLICATED")
        if not token:
            raise BundleDownloadError(
                "Cannot download from Replicated Vendor Portal: "
                "SBCTL_TOKEN or REPLICATED environment variable not set."
            )

        api_url = REPLICATED_API_ENDPOINT.format(slug=slug)
        headers = {"Authorization": token, "Content-Type": "application/json"}

        try:
            # === START RESTRUCTURE ===
            timeout = httpx.Timeout(MAX_DOWNLOAD_TIMEOUT)

            # Retry loop for 403 errors
            for attempt in range(RETRY_ATTEMPTS + 1):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    logger.debug(
                        f"Requesting signed URL from Replicated API: {api_url} (attempt {attempt + 1})"
                    )
                    response = await client.get(api_url, headers=headers)

                # Handle 403 errors with retry logic
                if response.status_code == 403 and attempt < RETRY_ATTEMPTS:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        f"Replicated API returned 403 Forbidden for slug {slug}, retrying in {delay:.2f}s (attempt {attempt + 1}/{RETRY_ATTEMPTS + 1})"
                    )
                    await asyncio.sleep(delay)
                    continue

                # Break out of retry loop for successful responses or non-retryable errors
                break

            # Process the response status and content
            if response.status_code == 401:
                logger.error(f"Replicated API returned 401 Unauthorized for slug {slug}")
                raise BundleDownloadError(
                    f"Failed to authenticate with Replicated API (status {response.status_code}). "
                    "Check SBCTL_TOKEN/REPLICATED_TOKEN."
                )
            elif response.status_code == 404:
                logger.error(f"Replicated API returned 404 Not Found for slug {slug}")
                raise BundleDownloadError(
                    f"Support bundle not found on Replicated Vendor Portal (slug: {slug}, status {response.status_code})."
                )
            elif response.status_code == 403:
                response_text = response.text[:500]
                logger.error(
                    f"Replicated API returned 403 Forbidden for slug {slug} after {RETRY_ATTEMPTS + 1} attempts: {response_text}"
                )
                raise BundleDownloadError(
                    f"Failed to get signed URL from Replicated API after retries (status {response.status_code}): {response_text}"
                )
            elif response.status_code != 200:
                response_text = response.text[:500]
                logger.error(
                    f"Replicated API returned error {response.status_code} for slug {slug}: {response_text}"
                )
                raise BundleDownloadError(
                    f"Failed to get signed URL from Replicated API (status {response.status_code}): {response_text}"
                )

            # If status is 200, parse JSON
            response_data = None  # Initialize
            try:
                response_data = response.json()
            except json.JSONDecodeError as json_e:
                logger.exception(
                    f"Error decoding JSON response from Replicated API (status 200): {json_e}"
                )
                raise BundleDownloadError(f"Invalid JSON response from Replicated API: {json_e}")

            # Add validation: Ensure response_data is a dictionary
            if not isinstance(response_data, dict):
                logger.error(
                    f"Replicated API response was not a JSON dictionary: {type(response_data)}"
                )
                raise BundleDownloadError(
                    "Invalid response format from Replicated API (expected JSON dictionary)."
                )

            # === START MODIFICATION ===
            # Access the nested 'bundle' dictionary first
            bundle_data = response_data.get("bundle")
            if not isinstance(bundle_data, dict):
                logger.error(
                    f"Missing 'bundle' dictionary in Replicated API response. Response data: {response_data}"
                )
                raise BundleDownloadError(
                    "Invalid response format from Replicated API (missing 'bundle' object)."
                )

            # Now get 'signedUri' from the nested dictionary
            signed_url = bundle_data.get("signedUri")
            # === END MODIFICATION ===

            if not signed_url:
                # Log the bundle_data specifically if signedUri is missing from it
                logger.error(
                    f"Missing 'signedUri' in Replicated API response bundle object for slug {slug}. Bundle data: {bundle_data}"
                )
                raise BundleDownloadError("Could not find 'signedUri' in Replicated API response.")

            logger.info("Successfully retrieved signed URL from Replicated API.")
            # Ensure we're returning a string type
            return str(signed_url)
            # === END RESTRUCTURE ===

        except Exception as e:
            # === START CONSOLIDATED EXCEPTION HANDLING ===
            if isinstance(e, BundleDownloadError):
                # Re-raise specific BundleDownloadErrors we've already identified
                raise e
            elif isinstance(e, httpx.Timeout):
                logger.exception(f"Timeout requesting signed URL from Replicated API: {e}")
                raise BundleDownloadError(f"Timeout requesting signed URL: {e}") from e
            elif isinstance(e, httpx.RequestError):
                # This should now correctly catch the RequestError raised by the mock
                logger.exception(f"Network error requesting signed URL from Replicated API: {e}")
                raise BundleDownloadError(f"Network error requesting signed URL: {e}") from e
            else:
                # Catch any other unexpected errors during the entire process and wrap them
                distinct_error_msg = f"UNEXPECTED EXCEPTION in _get_replicated_signed_url: {type(e).__name__}: {str(e)}"
                logger.exception(distinct_error_msg)
                raise BundleDownloadError(distinct_error_msg) from e
            # === END CONSOLIDATED EXCEPTION HANDLING ===

    async def _download_github_attachment(self, url: str) -> Path:
        """
        Download bundle from GitHub with proper authentication.

        Args:
            url: GitHub URL to download from

        Returns:
            The path to the downloaded bundle

        Raises:
            BundleDownloadError: If the bundle could not be downloaded
        """
        # Token priority: GITHUB_TOKEN > GH_TOKEN > SBCTL_TOKEN
        github_token = (
            os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or os.environ.get("SBCTL_TOKEN")
        )

        if not github_token:
            raise BundleDownloadError(
                "Cannot download from GitHub: No authentication token found. "
                "Set GITHUB_TOKEN, GH_TOKEN, or SBCTL_TOKEN environment variable."
            )

        # GitHub-specific headers
        headers = {
            "Authorization": f"token {github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "troubleshoot-mcp-server",
        }

        # Generate filename
        parsed_url = urlparse(url)
        filename = (
            os.path.basename(parsed_url.path)
            or f"github_bundle_{self._generate_bundle_id(url)}.tar.gz"
        )
        # Ensure filename is safe
        filename = re.sub(r"[^\w\-.]", "_", filename)
        if not filename:
            filename = f"github_bundle_{self._generate_bundle_id(url)}.tar.gz"

        download_path = self.bundle_dir / filename

        # Use retry logic similar to Replicated downloads for rate limits
        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                timeout = aiohttp.ClientTimeout(total=MAX_DOWNLOAD_TIMEOUT)

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 429:  # Rate limited
                            if attempt < max_retries:
                                delay = self._calculate_retry_delay(attempt)
                                logger.warning(
                                    f"GitHub rate limit hit, retrying in {delay:.1f}s (attempt {attempt + 1}/{max_retries + 1})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            else:
                                raise BundleDownloadError(
                                    "GitHub rate limit exceeded. Please try again later or check your token limits."
                                )

                        if response.status == 401:
                            raise BundleDownloadError(
                                "GitHub authentication failed. Please check your token has the correct permissions."
                            )

                        if response.status == 404:
                            raise BundleDownloadError(
                                f"GitHub resource not found: {url}. Check the URL and token permissions."
                            )

                        if response.status != 200:
                            reason = response.reason or "Unknown Error"
                            raise BundleDownloadError(
                                f"Failed to download from GitHub: HTTP {response.status} {reason}"
                            )

                        # Check content length if available
                        content_length = response.content_length
                        if content_length and content_length > MAX_DOWNLOAD_SIZE:
                            raise BundleDownloadError(
                                f"Bundle size ({content_length} bytes) exceeds maximum allowed size ({MAX_DOWNLOAD_SIZE} bytes)"
                            )

                        # Download the file
                        total_downloaded = 0
                        with open(download_path, "wb") as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                                total_downloaded += len(chunk)
                                if total_downloaded > MAX_DOWNLOAD_SIZE:
                                    raise BundleDownloadError(
                                        f"Bundle size exceeds maximum allowed size ({MAX_DOWNLOAD_SIZE} bytes)"
                                    )

                        logger.info(
                            f"Successfully downloaded GitHub bundle to {download_path} ({total_downloaded} bytes)"
                        )
                        return download_path

            except aiohttp.ClientError as e:
                if attempt < max_retries:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        f"Network error downloading from GitHub, retrying in {delay:.1f}s: {e}"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise BundleDownloadError(f"Network error downloading from GitHub: {e}")
            except BundleDownloadError:
                # Re-raise our own errors without retrying
                raise
            except Exception as e:
                logger.exception(f"Unexpected error downloading from GitHub: {e}")
                raise BundleDownloadError(f"Unexpected error downloading from GitHub: {e}")

        # This should not be reached, but add as safety
        raise BundleDownloadError("Failed to download from GitHub after all retries")

    async def _download_bundle(self, url: str) -> Path:
        """
        Download a support bundle from a URL, handling Replicated Vendor Portal and GitHub URLs.

        Args:
            url: The URL to download the bundle from (can be original or signed)

        Returns:
            The path to the downloaded bundle

        Raises:
            BundleDownloadError: If the bundle could not be downloaded
        """
        # Initialize actual_download_url with the original URL first
        actual_download_url = url
        original_url = url  # Keep track of the original URL for logging/ID generation

        # Check if it's a GitHub URL that requires special authentication
        if self._is_github_url(url):
            return await self._download_github_attachment(url)

        # Check if it's a Replicated Vendor Portal URL
        if REPLICATED_VENDOR_URL_PATTERN.match(url):
            try:
                actual_download_url = await self._get_replicated_signed_url(url)
                # Log only after successfully getting the signed URL
                logger.info(
                    f"Using signed URL for download: {actual_download_url[:80]}..."
                )  # Log truncated URL
            except BundleDownloadError as e:
                # Propagate the error from the signed URL retrieval
                # No further execution needed in this function if this fails
                raise e
            except Exception as e:
                # Catch any other unexpected errors during signed URL retrieval
                logger.exception(f"Unexpected error getting signed URL for {url}: {e}")
                # Raise specific error and exit
                raise BundleDownloadError(f"Failed to get signed URL for {url}: {str(e)}")
        # Log the download start *after* potential signed URL retrieval
        logger.info(f"Starting download from: {actual_download_url[:80]}...")

        # Use original URL to generate filename and ID for consistency
        parsed_original_url = urlparse(original_url)
        filename = ""  # Initialize filename

        # Generate filename based on URL type
        if REPLICATED_VENDOR_URL_PATTERN.match(original_url):
            match = REPLICATED_VENDOR_URL_PATTERN.match(original_url)
            slug = match.group(1) if match else "unknown_slug"
            # Sanitize slug for filename
            safe_slug = re.sub(r"[^\w\-.]", "_", slug)
            filename = f"replicated_bundle_{safe_slug}.tar.gz"
        else:
            # Use basename for non-Replicated URLs
            filename = (
                os.path.basename(parsed_original_url.path)
                or f"bundle_{self._generate_bundle_id(original_url)}.tar.gz"
            )
            # Ensure filename is safe
            filename = re.sub(r"[^\w\-.]", "_", filename)
            if not filename:  # Handle cases where sanitization results in empty string
                filename = f"bundle_{self._generate_bundle_id(original_url)}.tar.gz"

        download_path = self.bundle_dir / filename

        try:
            # Headers for the actual download
            download_headers = {}
            # Add auth token ONLY for non-Replicated URLs (signed URLs have auth embedded)
            if actual_download_url == original_url:  # Check if we are using the original URL
                token = os.environ.get("SBCTL_TOKEN")
                if token:
                    download_headers["Authorization"] = f"Bearer {token}"
                    logger.debug("Added Authorization header for direct download.")
                else:
                    logger.debug("No SBCTL_TOKEN found for direct download.")
            else:
                logger.debug("Skipping Authorization header for signed Replicated URL.")

            # Set a timeout for the download to prevent hanging
            timeout = aiohttp.ClientTimeout(total=MAX_DOWNLOAD_TIMEOUT)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                # === START MODIFICATION ===
                # Explicitly await the get call first
                response_ctx_mgr = session.get(actual_download_url, headers=download_headers)
                # Now use the awaited response object in the async with
                async with await response_ctx_mgr as response:
                    # === END MODIFICATION ===
                    if response.status != 200:
                        # Include response reason for better error messages
                        reason = response.reason or "Unknown Error"
                        raise BundleDownloadError(
                            f"Failed to download bundle from {actual_download_url[:80]}...: HTTP {response.status} {reason}"
                        )

                    # Check content length if available
                    content_length = response.content_length
                    if content_length and content_length > MAX_DOWNLOAD_SIZE:
                        raise BundleDownloadError(
                            f"Bundle size ({content_length / 1024 / 1024:.1f} MB) exceeds maximum allowed size "
                            f"({MAX_DOWNLOAD_SIZE / 1024 / 1024:.1f} MB)"
                        )

                    # Track total downloaded size
                    total_size = 0
                    with download_path.open("wb") as f:
                        # Use the 'response' variable from the inner async with
                        async for chunk in response.content.iter_chunked(8192):
                            total_size += len(chunk)

                            # Check size limit during download
                            if total_size > MAX_DOWNLOAD_SIZE:
                                # Close and remove the partial file
                                f.close()
                                if download_path.exists():
                                    download_path.unlink()
                                raise BundleDownloadError(
                                    f"Bundle download exceeded maximum allowed size "
                                    f"({MAX_DOWNLOAD_SIZE / 1024 / 1024:.1f} MB)"
                                )

                            f.write(chunk)

                    logger.info(
                        f"Downloaded {total_size / 1024 / 1024:.1f} MB from {actual_download_url[:80]}..."
                    )

            logger.info(f"Bundle downloaded to: {download_path}")
            return download_path
        except Exception as e:
            # Use original_url in error messages for clarity
            logger.exception(f"Error downloading bundle originally from {original_url}: {str(e)}")
            if download_path.exists():
                download_path.unlink(missing_ok=True)  # Use missing_ok=True for robustness
            # Re-raise BundleDownloadError if it's already that type
            if isinstance(e, BundleDownloadError):
                raise
            raise BundleDownloadError(f"Failed to download bundle from {original_url}: {str(e)}")

    async def _initialize_with_sbctl(self, bundle_path: Path, output_dir: Path) -> Path:
        """
        Initialize a support bundle with sbctl.

        Args:
            bundle_path: The path to the bundle file
            output_dir: The directory where the bundle will be extracted

        Returns:
            The path to the kubeconfig file

        Raises:
            BundleInitializationError: If the bundle could not be initialized
        """
        logger.info(f"Initializing bundle with sbctl: {bundle_path}")

        # sbctl creates the kubeconfig in the current directory with a fixed name
        # Change our working directory to the output directory and look for 'kubeconfig' there
        os.chdir(output_dir)
        kubeconfig_path = output_dir / "kubeconfig"

        try:
            # Kill any existing sbctl process
            await self._terminate_sbctl_process()

            # First, check if this bundle contains cluster resources by running sbctl briefly
            # Start sbctl in serve mode with the bundle
            # Since sbctl may create files in the current directory, we'll start it from our output directory
            os.chdir(output_dir)

            # Use the serve command to start the API server
            cmd = [
                "sbctl",
                "serve",
                "--support-bundle-location",
                str(bundle_path),
            ]

            # sbctl will write a kubeconfig file in the current working directory
            # The default name is 'kubeconfig'

            logger.debug(f"Running command: {' '.join(cmd)}")

            # Start the process
            self.sbctl_process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            self._termination_requested = False

            # First, wait a brief moment to see if sbctl exits quickly with "No cluster resources"
            try:
                # Wait for either process completion or a short timeout
                await asyncio.wait_for(self.sbctl_process.wait(), timeout=5.0)

                # Process completed quickly - check output
                stdout_data = b""
                stderr_data = b""

                if self.sbctl_process.stdout:
                    try:
                        stdout_data = await asyncio.wait_for(
                            self.sbctl_process.stdout.read(), timeout=1.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass

                if self.sbctl_process.stderr:
                    try:
                        stderr_data = await asyncio.wait_for(
                            self.sbctl_process.stderr.read(), timeout=1.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass

                # Combine output and check for "No cluster resources"
                all_output = ""
                if stdout_data:
                    all_output += stdout_data.decode("utf-8", errors="replace")
                if stderr_data:
                    all_output += stderr_data.decode("utf-8", errors="replace")

                logger.info(f"sbctl output: {all_output}")

                if "No cluster resources found in bundle" in all_output:
                    logger.info("Bundle contains no cluster resources, marking as host-only bundle")
                    self._host_only_bundle = True
                    return kubeconfig_path  # Return dummy path, file won't exist but that's OK

                # If we get here, sbctl exited quickly but not due to "no cluster resources"
                # Check the exit code to determine if it's an error
                if self.sbctl_process.returncode != 0:
                    error_msg = f"sbctl process exited with code {self.sbctl_process.returncode}"
                    if all_output.strip():
                        error_msg += f". Output: {all_output.strip()}"

                    # Log the diagnostic information for debugging
                    logger.error(f"sbctl failed to start: {error_msg}")

                    # This is a real failure, raise an exception immediately
                    raise BundleInitializationError(
                        f"sbctl failed to initialize bundle: {error_msg}"
                    )

            except asyncio.TimeoutError:
                # Process didn't exit quickly, so it's likely starting up an API server
                # Try to read stdout line by line to get kubeconfig path quickly
                try:
                    if self.sbctl_process and self.sbctl_process.stdout:
                        # Try to read the first few lines to catch the kubeconfig announcement
                        stdout_lines = []
                        for attempt in range(10):  # Try up to 10 lines or 5 seconds
                            try:
                                line = await asyncio.wait_for(
                                    self.sbctl_process.stdout.readline(), timeout=0.5
                                )
                                if not line:  # EOF
                                    break
                                line_text = line.decode("utf-8", errors="replace").strip()
                                if line_text:
                                    stdout_lines.append(line_text)
                                    logger.debug(f"sbctl stdout line: {line_text}")

                                    # Check if this line contains kubeconfig export
                                    if "export KUBECONFIG=" in line_text:
                                        import re

                                        kubeconfig_matches = re.findall(
                                            r"export KUBECONFIG=([^\s]+)", line_text
                                        )
                                        if kubeconfig_matches:
                                            announced_kubeconfig = Path(kubeconfig_matches[0])
                                            logger.info(
                                                f"sbctl announced kubeconfig at: {announced_kubeconfig}"
                                            )

                                            # Wait a brief moment for the file to be created
                                            for wait_attempt in range(10):  # Wait up to 5 seconds
                                                await asyncio.sleep(0.5)
                                                if announced_kubeconfig.exists():
                                                    logger.info(
                                                        f"Found announced kubeconfig: {announced_kubeconfig}"
                                                    )
                                                    try:
                                                        safe_copy_file(
                                                            announced_kubeconfig,
                                                            kubeconfig_path,
                                                        )
                                                        logger.info(
                                                            f"Successfully copied kubeconfig to {kubeconfig_path}"
                                                        )
                                                        return kubeconfig_path
                                                    except Exception as copy_err:
                                                        logger.warning(
                                                            f"Failed to copy announced kubeconfig: {copy_err}"
                                                        )
                                                        break
                                            break
                            except asyncio.TimeoutError:
                                # No more lines available quickly, stop trying
                                break

                        if stdout_lines:
                            logger.debug(f"sbctl initial stdout: {' | '.join(stdout_lines)}")

                except Exception as read_err:
                    logger.debug(f"Error reading initial stdout: {read_err}")

                # Continue with normal initialization
                logger.debug("sbctl process continuing, proceeding with normal initialization")

            # Wait for initialization to complete
            await self._wait_for_initialization(kubeconfig_path)

            if not kubeconfig_path.exists():
                raise BundleInitializationError(
                    f"Failed to initialize bundle: kubeconfig not created at {kubeconfig_path}"
                )

            logger.info(f"Bundle initialized with kubeconfig at: {kubeconfig_path}")
            return kubeconfig_path

        except Exception as e:
            error_message = str(e)
            stderr_output = ""

            # Try to capture any stderr output from the process for better diagnostics
            if self.sbctl_process and self.sbctl_process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(
                        self.sbctl_process.stderr.read(4096), timeout=1.0
                    )
                    if stderr_data:
                        stderr_output = stderr_data.decode("utf-8", errors="replace")
                        logger.error(f"sbctl stderr output: {stderr_output}")
                except Exception as stderr_err:
                    logger.debug(f"Could not read stderr: {stderr_err}")

            # Add stderr to the error message if available
            if stderr_output:
                error_message = f"{error_message} - Process stderr: {stderr_output}"

            logger.exception(f"Error initializing bundle with sbctl: {error_message}")

            # Terminate the process
            await self._terminate_sbctl_process()

            raise BundleInitializationError(
                f"Failed to initialize bundle with sbctl: {error_message}"
            )

    async def _wait_for_initialization(
        self, kubeconfig_path: Path, timeout: float = MAX_INITIALIZATION_TIMEOUT
    ) -> None:
        """
        Wait for sbctl initialization to complete.

        Args:
            kubeconfig_path: The path to the kubeconfig file
            timeout: The maximum time to wait for initialization

        Raises:
            BundleInitializationError: If initialization times out
        """
        start_time = asyncio.get_event_loop().time()
        error_message = ""
        kubeconfig_found = False

        # How long to wait for API server after finding kubeconfig
        # If we find kubeconfig, we'll allow up to this percentage of the timeout
        # to wait for the API server before continuing anyway
        api_server_wait_percentage = 0.3  # 30% of the timeout

        # Number of API server check attempts
        api_check_attempts = 0
        max_api_check_attempts = 5

        # Alternative kubeconfig paths the sbctl might create
        alternative_kubeconfig_paths = []

        # Attempt to read process output for diagnostic purposes
        if self.sbctl_process and self.sbctl_process.stdout and self.sbctl_process.stderr:
            stdout_data = b""
            stderr_data = b""

            try:
                # Try to read without blocking the entire process
                # We need to handle the coroutines properly for type checking
                if self.sbctl_process.stdout is not None:
                    try:
                        # We expect bytes returned from the process stdout
                        stdout_data = await asyncio.wait_for(
                            self.sbctl_process.stdout.read(1024), timeout=1.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        stdout_data = b""
                else:
                    stdout_data = b""

                if self.sbctl_process.stderr is not None:
                    try:
                        # We expect bytes returned from the process stderr
                        stderr_data = await asyncio.wait_for(
                            self.sbctl_process.stderr.read(1024), timeout=1.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        stderr_data = b""
                else:
                    stderr_data = b""

                if stdout_data:
                    stdout_text = (
                        stdout_data.decode("utf-8", errors="replace")
                        if isinstance(stdout_data, bytes)
                        else str(stdout_data)
                    )
                    logger.debug(f"sbctl stdout: {stdout_text}")

                    # Look for exported KUBECONFIG path in the output
                    if "export KUBECONFIG=" in stdout_text:
                        # Extract the kubeconfig path
                        import re

                        kubeconfig_matches = re.findall(r"export KUBECONFIG=([^\s]+)", stdout_text)
                        if kubeconfig_matches:
                            alt_kubeconfig = Path(kubeconfig_matches[0])
                            logger.info(f"Found kubeconfig path in stdout: {alt_kubeconfig}")
                            alternative_kubeconfig_paths.append(alt_kubeconfig)

                            # Since we found the kubeconfig path immediately, use it
                            if alt_kubeconfig.exists():
                                logger.info(f"Using kubeconfig from stdout: {alt_kubeconfig}")
                                try:
                                    safe_copy_file(alt_kubeconfig, kubeconfig_path)
                                    logger.info(
                                        f"Copied kubeconfig from {alt_kubeconfig} to {kubeconfig_path}"
                                    )
                                    return  # Successfully copied, exit the wait function
                                except Exception as copy_err:
                                    logger.warning(
                                        f"Failed to copy kubeconfig immediately: {copy_err}"
                                    )
                                    # Continue with normal waiting logic

                if stderr_data:
                    stderr_text = (
                        stderr_data.decode("utf-8", errors="replace")
                        if isinstance(stderr_data, bytes)
                        else str(stderr_data)
                    )
                    logger.debug(f"sbctl stderr: {stderr_text}")
                    error_message = stderr_text
            except (asyncio.TimeoutError, Exception) as e:
                logger.debug(f"Error reading process output: {str(e)}")

        # Wait for the kubeconfig file to appear (check both expected location and alternatives)
        kubeconfig_found_time = None
        found_kubeconfig_path = None

        # Check for alternative kubeconfig locations if enabled
        if ALLOW_ALTERNATIVE_KUBECONFIG:
            # Add temp dir locations that sbctl might use
            temp_kubeconfig = Path("/tmp/kubeconfig")
            if temp_kubeconfig not in alternative_kubeconfig_paths:
                alternative_kubeconfig_paths.append(temp_kubeconfig)

            # Add local-kubeconfig pattern in temp dirs
            import glob

            local_kubeconfigs = glob.glob("/var/folders/*/*/local-kubeconfig-*")
            for path in local_kubeconfigs:
                alternative_kubeconfig_paths.append(Path(path))

            # Check for kubeconfig files in standard locations
            for std_path in ["/tmp", "/etc/kubernetes", "/var/run/kubernetes"]:
                std_kubeconfig = Path(std_path) / "kubeconfig"
                if std_kubeconfig not in alternative_kubeconfig_paths:
                    alternative_kubeconfig_paths.append(std_kubeconfig)

            logger.debug(
                f"Checking for kubeconfig at alternative locations: {[str(p) for p in alternative_kubeconfig_paths]}"
            )
        else:
            logger.debug("Alternative kubeconfig locations disabled by configuration")

        while asyncio.get_event_loop().time() - start_time < timeout:
            # First check if the process is still running - if it exited with an error,
            # we should fail immediately instead of waiting for timeout
            if self.sbctl_process and self.sbctl_process.returncode is not None:
                # Process exited - check if this is expected
                if self.sbctl_process.returncode != 0:
                    # Process failed - read any error output and fail immediately
                    process_output = ""
                    try:
                        if self.sbctl_process.stdout:
                            stdout_data = await self.sbctl_process.stdout.read()
                            process_output += stdout_data.decode("utf-8", errors="replace")
                        if self.sbctl_process.stderr:
                            stderr_data = await self.sbctl_process.stderr.read()
                            process_output += stderr_data.decode("utf-8", errors="replace")
                    except Exception:
                        pass

                    error_msg = f"sbctl process exited with code {self.sbctl_process.returncode} before initialization completed"
                    if process_output.strip():
                        error_msg += f". Process output: {process_output.strip()}"

                    logger.error(f"sbctl failed during initialization: {error_msg}")
                    raise BundleInitializationError(error_msg)

            # Check the expected kubeconfig path
            if kubeconfig_path.exists() and not kubeconfig_found:
                logger.info(f"Kubeconfig found at expected location: {kubeconfig_path}")
                kubeconfig_found = True
                kubeconfig_found_time = asyncio.get_event_loop().time()
                found_kubeconfig_path = kubeconfig_path

                # Log the contents of the kubeconfig file
                try:
                    with open(kubeconfig_path, "r") as f:
                        kubeconfig_content = f.read()
                    logger.debug(f"Kubeconfig content:\n{kubeconfig_content}")
                except Exception as e:
                    logger.warning(f"Failed to read kubeconfig content: {e}")

            # Check alternative kubeconfig paths if enabled
            if not kubeconfig_found and ALLOW_ALTERNATIVE_KUBECONFIG:
                for alt_path in alternative_kubeconfig_paths:
                    if alt_path.exists():
                        logger.info(f"Kubeconfig found at alternative location: {alt_path}")
                        kubeconfig_found = True
                        kubeconfig_found_time = asyncio.get_event_loop().time()
                        found_kubeconfig_path = alt_path

                        # Log the contents
                        try:
                            with open(alt_path, "r") as f:
                                kubeconfig_content = f.read()
                            logger.debug(f"Alternative kubeconfig content:\n{kubeconfig_content}")

                            # Try to copy to expected location
                            try:
                                safe_copy_file(alt_path, kubeconfig_path)
                                logger.info(
                                    f"Copied kubeconfig from {alt_path} to {kubeconfig_path}"
                                )
                            except Exception as copy_err:
                                logger.warning(f"Failed to copy kubeconfig: {copy_err}")
                        except Exception as e:
                            logger.warning(f"Failed to read alternative kubeconfig content: {e}")

                        break

            # If we've found a kubeconfig, check API server
            if kubeconfig_found:
                # Wait an additional second for the API server to start listening
                await asyncio.sleep(1.0)

                # Check if the API server is actually responding
                api_check_attempts += 1
                if await self.check_api_server_available():
                    logger.info("API server is available and responding")

                    # If we found a kubeconfig in an alternative location,
                    # make sure it's copied to the expected location
                    if found_kubeconfig_path != kubeconfig_path:
                        try:
                            safe_copy_file(found_kubeconfig_path, kubeconfig_path)
                            logger.info(
                                f"Copied kubeconfig from {found_kubeconfig_path} to {kubeconfig_path}"
                            )
                        except Exception as copy_err:
                            logger.warning(f"Failed to copy kubeconfig: {copy_err}")

                    return
                else:
                    logger.warning(
                        f"Kubeconfig found but API server is not responding yet (attempt {api_check_attempts})"
                    )

                    # If we've been waiting too long for the API server or we've made enough attempts,
                    # continue with initialization even if the API server isn't responding
                    if api_check_attempts >= max_api_check_attempts:
                        logger.warning(
                            f"Max API check attempts ({max_api_check_attempts}) reached. Proceeding anyway."
                        )

                        # Make sure we have a kubeconfig at expected location
                        if found_kubeconfig_path != kubeconfig_path:
                            try:
                                logger.info(
                                    f"Copied kubeconfig from {found_kubeconfig_path} to {kubeconfig_path}"
                                )
                            except Exception as copy_err:
                                logger.warning(f"Failed to copy kubeconfig: {copy_err}")

                        return

                    # If we've found the kubeconfig and waited long enough, continue anyway
                    # Make sure kubeconfig_found_time is not None before subtraction
                    if kubeconfig_found_time is not None:
                        time_since_kubeconfig = (
                            asyncio.get_event_loop().time() - kubeconfig_found_time
                        )
                        if time_since_kubeconfig > (timeout * api_server_wait_percentage):
                            logger.warning(
                                f"API server not responding after {time_since_kubeconfig:.1f}s "
                                f"({api_server_wait_percentage * 100:.0f}% of timeout). Proceeding anyway."
                            )

                        # Make sure we have a kubeconfig at expected location
                        if found_kubeconfig_path != kubeconfig_path:
                            try:
                                # Use our safe_copy_file helper instead of shutil.copy2 directly
                                safe_copy_file(found_kubeconfig_path, kubeconfig_path)
                                logger.info(
                                    f"Copied kubeconfig from {found_kubeconfig_path} to {kubeconfig_path}"
                                )
                            except Exception as copy_err:
                                logger.warning(f"Failed to copy kubeconfig: {copy_err}")

                        return

            # Check if the process is still running
            if self.sbctl_process and self.sbctl_process.returncode is not None:
                # Process exited before kubeconfig was created
                if self.sbctl_process.returncode == 0:
                    # Process exited successfully - check if this is the "no cluster resources" case
                    try:
                        # Read any remaining stdout/stderr to check for the "no cluster resources" message
                        process_output = ""
                        if self.sbctl_process.stdout:
                            try:
                                stdout_data = await asyncio.wait_for(
                                    self.sbctl_process.stdout.read(), timeout=1.0
                                )
                                process_output += stdout_data.decode("utf-8", errors="replace")
                            except (asyncio.TimeoutError, Exception):
                                pass
                        if self.sbctl_process.stderr:
                            try:
                                stderr_data = await asyncio.wait_for(
                                    self.sbctl_process.stderr.read(), timeout=1.0
                                )
                                process_output += stderr_data.decode("utf-8", errors="replace")
                            except (asyncio.TimeoutError, Exception):
                                pass

                        # Also check the output we might have captured earlier in the initialization loop
                        if "stdout_data" in locals() and stdout_data:
                            stdout_str = stdout_data.decode("utf-8", errors="replace")
                            process_output += stdout_str
                        if "stderr_data" in locals() and stderr_data:
                            stderr_str = stderr_data.decode("utf-8", errors="replace")
                            process_output += stderr_str

                        if "No cluster resources found in bundle" in process_output:
                            # This is a valid case - bundle has no cluster resources
                            logger.info(
                                "Bundle contains no cluster resources, marking as host-only bundle"
                            )
                            # Set flag to indicate this is a host-only bundle
                            self._host_only_bundle = True
                            return  # Exit successfully without kubeconfig

                    except Exception as e:
                        logger.debug(f"Error checking process output: {e}")
                        # Continue with normal error handling

                # Check if this was an intentional termination (SIGTERM/-15)
                if self.sbctl_process.returncode == -15 and self._termination_requested:
                    logger.debug("sbctl process was intentionally terminated during cleanup")
                    return  # Exit gracefully without raising an error

                error_message = f"sbctl process exited with code {self.sbctl_process.returncode} before initialization completed"
                break

            # Search for any newly created kubeconfig files in common locations if enabled
            if ALLOW_ALTERNATIVE_KUBECONFIG:
                for pattern in [
                    "/tmp/kubeconfig*",
                    "/var/folders/*/*/local-kubeconfig-*",
                ]:
                    for path in glob.glob(pattern):
                        kubeconfig_file = Path(path)
                        if kubeconfig_file not in alternative_kubeconfig_paths:
                            logger.info(f"Found new kubeconfig at: {kubeconfig_file}")
                            alternative_kubeconfig_paths.append(kubeconfig_file)

            # Look for any files created in the directory to debug
            dir_contents = list(kubeconfig_path.parent.glob("*"))
            if dir_contents:
                logger.debug(
                    f"Files in {kubeconfig_path.parent}: {[file.name for file in dir_contents]}"
                )

            await asyncio.sleep(0.5)

        # If kubeconfig was found but API server wasn't responding, continue anyway
        if kubeconfig_found:
            logger.warning(
                "Timeout waiting for API server, but kubeconfig was found. Proceeding with initialization."
            )

            # Make sure we have a kubeconfig at expected location
            if found_kubeconfig_path != kubeconfig_path:
                try:
                    # Use our safe_copy_file helper instead of shutil.copy2 directly
                    safe_copy_file(found_kubeconfig_path, kubeconfig_path)
                    logger.info(
                        f"Copied kubeconfig from {found_kubeconfig_path} to {kubeconfig_path}"
                    )
                except Exception as copy_err:
                    logger.warning(f"Failed to copy kubeconfig: {copy_err}")

            return

        # If we got here, the timeout occurred without finding kubeconfig
        error_details = f" Error details: {error_message}" if error_message else ""

        # Collect additional diagnostic information
        diagnostics = await self.get_diagnostic_info()
        diagnostics_str = json.dumps(diagnostics, separators=(",", ":"))

        raise BundleInitializationError(
            f"Timeout waiting for bundle initialization after {timeout} seconds.{error_details}\n"
            f"Diagnostic information:\n{diagnostics_str}"
        )

    async def _terminate_sbctl_process(self) -> None:
        """
        Terminate the sbctl process if it's running.

        This helper method centralizes process termination logic to avoid duplication.
        """
        if self.sbctl_process:
            self._termination_requested = True
            try:
                logger.debug("Terminating sbctl process...")
                self.sbctl_process.terminate()
                try:
                    await asyncio.wait_for(self.sbctl_process.wait(), timeout=3.0)
                    logger.debug("sbctl process terminated gracefully")
                except (asyncio.TimeoutError, ProcessLookupError) as e:
                    logger.warning(f"Failed to terminate sbctl process gracefully: {str(e)}")
                    if self.sbctl_process:
                        try:
                            logger.debug("Killing sbctl process...")
                            self.sbctl_process.kill()
                            logger.debug("sbctl process killed")
                        except ProcessLookupError:
                            logger.debug("Process already gone when trying to kill")
            except Exception as e:
                logger.warning(f"Error during process termination: {str(e)}")

            # Always set to None regardless of success
            self.sbctl_process = None

            # Check for any lingering mock_sbctl.pid file in the output directory
            # This helps us clean up in case the signal handling didn't work
            if self.active_bundle and self.active_bundle.path.exists():
                pid_file = self.active_bundle.path / "mock_sbctl.pid"
                if pid_file.exists():
                    try:
                        with open(pid_file, "r") as f:
                            pid = int(f.read().strip())

                        # Try to kill the process if it exists
                        try:
                            logger.debug(f"Killing leftover process with PID {pid}")
                            os.kill(pid, signal.SIGTERM)
                            # Wait briefly for termination
                            await asyncio.sleep(0.5)
                            try:
                                # Check if process is gone
                                os.kill(pid, 0)
                                # If we get here, process still exists, try SIGKILL
                                logger.debug(f"Process {pid} still exists, sending SIGKILL")
                                os.kill(pid, signal.SIGKILL)
                            except ProcessLookupError:
                                logger.debug(f"Process {pid} terminated successfully")
                        except ProcessLookupError:
                            logger.debug(f"Process {pid} not found")
                        except PermissionError:
                            logger.warning(f"Permission error trying to kill process {pid}")

                        # Remove the PID file
                        try:
                            pid_file.unlink()
                            logger.debug(f"Removed PID file: {pid_file}")
                        except Exception as e:
                            logger.warning(f"Failed to remove PID file: {e}")
                    except Exception as e:
                        logger.warning(f"Error handling leftover PID file: {e}")

            # Cleanup any orphaned sbctl processes that might be running with the same bundle
            # This is important in container environments where processes might not be properly terminated
            if CLEANUP_ORPHANED:
                try:
                    # Find our bundle path to identify specific sbctl processes related to it
                    bundle_path = None
                    if self.active_bundle and self.active_bundle.path:
                        bundle_path = str(self.active_bundle.path)
                    elif self.active_bundle and self.active_bundle.source:
                        bundle_path = str(self.active_bundle.source)

                    if bundle_path:
                        # Use psutil to find and kill sbctl processes using our bundle
                        try:
                            # Find processes using psutil instead of ps -ef subprocess call
                            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                                try:
                                    # Check if this is an sbctl process using our bundle
                                    if (
                                        proc.info["name"]
                                        and "sbctl" in proc.info["name"]
                                        and proc.info["cmdline"]
                                        and any(bundle_path in arg for arg in proc.info["cmdline"])
                                    ):
                                        pid = proc.info["pid"]
                                        logger.debug(
                                            f"Found orphaned sbctl process with PID {pid}, attempting to terminate"
                                        )
                                        try:
                                            os.kill(pid, signal.SIGTERM)
                                            logger.debug(f"Sent SIGTERM to process {pid}")
                                            await asyncio.sleep(0.5)

                                            # Check if terminated
                                            try:
                                                os.kill(pid, 0)
                                                # Process still exists, use SIGKILL
                                                logger.debug(
                                                    f"Process {pid} still exists, sending SIGKILL"
                                                )
                                                os.kill(pid, signal.SIGKILL)
                                            except ProcessLookupError:
                                                logger.debug(
                                                    f"Process {pid} terminated successfully"
                                                )
                                        except (
                                            ProcessLookupError,
                                            PermissionError,
                                        ) as e:
                                            logger.debug(f"Error terminating process {pid}: {e}")
                                except (
                                    psutil.NoSuchProcess,
                                    psutil.AccessDenied,
                                    psutil.ZombieProcess,
                                ):
                                    # Process disappeared or access denied - skip it
                                    continue
                        except Exception as e:
                            logger.warning(f"Error cleaning up orphaned sbctl processes: {e}")

                    # As a fallback, try to clean up any sbctl processes related to serve
                    try:
                        # Use psutil to find and terminate sbctl serve processes
                        terminated_count = 0
                        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                            try:
                                # Check if this is an sbctl serve process
                                if (
                                    proc.info["name"]
                                    and "sbctl" in proc.info["name"]
                                    and proc.info["cmdline"]
                                    and any("serve" in arg for arg in proc.info["cmdline"])
                                ):
                                    try:
                                        proc.terminate()
                                        terminated_count += 1
                                        logger.debug(
                                            f"Terminated sbctl serve process with PID {proc.info['pid']}"
                                        )
                                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                                        # Process already gone or access denied - skip it
                                        continue
                            except (
                                psutil.NoSuchProcess,
                                psutil.AccessDenied,
                                psutil.ZombieProcess,
                            ):
                                # Process disappeared or access denied - skip it
                                continue

                        if terminated_count > 0:
                            logger.debug(
                                f"Successfully terminated {terminated_count} sbctl serve processes"
                            )
                        else:
                            logger.debug("No sbctl serve processes found to terminate")
                    except Exception as e:
                        logger.warning(f"Error using psutil to terminate sbctl processes: {e}")

                except Exception as e:
                    logger.warning(f"Error during extended cleanup: {e}")
            else:
                logger.debug("Skipping orphaned process cleanup (disabled by configuration)")

    async def _cleanup_active_bundle(self) -> None:
        """
        Clean up the active bundle including processes and extracted directories.

        This method:
        1. Terminates the sbctl process first (if running)
        2. Removes extracted bundle directories
        3. Resets the active bundle reference
        """
        if self.active_bundle:
            logger.info(f"Cleaning up active bundle: {self.active_bundle.id}")

            # 1. Stop the sbctl process if it's running
            await self._terminate_sbctl_process()

            # 2. Remove extracted bundle directories
            try:
                if self.active_bundle.path and self.active_bundle.path.exists():
                    # Get the bundle path before resetting active_bundle reference
                    bundle_path = self.active_bundle.path
                    logger.info(f"Removing extracted bundle directory: {bundle_path}")

                    # Log directory details
                    try:
                        dir_stats = os.stat(bundle_path)
                        logger.info(
                            f"Bundle directory stats - permissions: {oct(dir_stats.st_mode)}, "
                            f"owner: {dir_stats.st_uid}, group: {dir_stats.st_gid}"
                        )

                        # List directory contents
                        import glob

                        files = glob.glob(f"{bundle_path}/**", recursive=True)
                        logger.info(f"Found {len(files)} items in bundle directory")
                    except Exception as list_err:
                        logger.warning(f"Error getting bundle directory details: {list_err}")

                    # Create a list of paths we should not delete (containing parent directories)
                    protected_paths = [
                        self.bundle_dir,  # Main bundle directory
                        Path(self.bundle_dir).parent,  # Parent of bundle directory
                    ]
                    logger.info(f"Protected paths: {protected_paths}")

                    # Only remove if it's not a protected path and exists
                    if bundle_path.exists() and bundle_path not in protected_paths:
                        # Check if this is inside our bundle directory (additional protection)
                        if str(bundle_path).startswith(str(self.bundle_dir)):
                            try:
                                import shutil

                                logger.info(f"Starting shutil.rmtree on bundle path: {bundle_path}")
                                shutil.rmtree(bundle_path)
                                logger.info(
                                    "shutil.rmtree completed, checking if path still exists"
                                )

                                if os.path.exists(bundle_path):
                                    logger.error(
                                        f"Bundle directory still exists after rmtree: {bundle_path}"
                                    )
                                else:
                                    logger.info(
                                        f"Successfully removed bundle directory: {bundle_path}"
                                    )
                            except PermissionError as e:
                                logger.error(f"Permission error removing bundle directory: {e}")
                                logger.error(
                                    f"Error details: {str(e)}, file: {getattr(e, 'filename', 'unknown')}"
                                )
                            except OSError as e:
                                logger.error(f"OS error removing bundle directory: {e}")
                                logger.error(
                                    f"Error type: {type(e).__name__}, errno: {getattr(e, 'errno', 'N/A')}"
                                )
                        else:
                            logger.warning(
                                f"Not removing bundle directory outside bundle_dir: {bundle_path}"
                            )
                    else:
                        if bundle_path in protected_paths:
                            logger.warning(
                                f"Bundle path {bundle_path} is a protected path, not removing"
                            )
                        if not bundle_path.exists():
                            logger.warning(f"Bundle path {bundle_path} no longer exists")
                else:
                    if not self.active_bundle.path:
                        logger.warning("Active bundle path is None")
                    elif not self.active_bundle.path.exists():
                        logger.warning(
                            f"Active bundle path does not exist: {self.active_bundle.path}"
                        )
            except Exception as e:
                logger.error(f"Error cleaning up bundle directory: {e}")
                logger.error(f"Exception type: {type(e).__name__}, details: {str(e)}")
                # Continue with cleanup even if directory removal fails

            # 3. Reset the active bundle
            self.active_bundle = None

    def _generate_bundle_id(self, source: str) -> str:
        """
        Generate a unique ID for a bundle.

        Args:
            source: The source of the bundle (URL or local path)

        Returns:
            A unique ID for the bundle
        """
        # Extract just the filename component from the path/URL
        filename = os.path.basename(source.rstrip("/"))

        # If empty (e.g., from a URL without a path component), use a default
        if not filename:
            filename = "bundle"

        # Strictly sanitize by only allowing alphanumeric chars, underscore, and hyphen
        # Replace any other characters with underscore
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", filename)

        # Ensure the ID starts with a letter or underscore (not a number or hyphen)
        # This prevents issues with some file systems and tools
        if sanitized and sanitized[0].isdigit() or sanitized and sanitized[0] == "-":
            sanitized = f"b_{sanitized}"

        # If sanitization resulted in an empty string, use a default name
        if not sanitized:
            sanitized = "bundle"

        # Add randomness to ensure uniqueness
        random_suffix = os.urandom(8).hex()  # Increased from 4 to 8 bytes for more entropy

        return f"{sanitized}_{random_suffix}"

    def is_initialized(self) -> bool:
        """
        Check if a bundle is currently initialized.

        Returns:
            True if a bundle is initialized, False otherwise
        """
        return self.active_bundle is not None and self.active_bundle.initialized

    def get_active_bundle(self) -> Optional[BundleMetadata]:
        """
        Get the currently active bundle.

        Returns:
            The active bundle metadata, or None if no bundle is active
        """
        return self.active_bundle

    async def check_api_server_available(self) -> bool:
        """
        Check if the Kubernetes API server is available.

        Returns:
            True if the API server is responding, False otherwise
        """
        # First check if sbctl process is running
        if not self.sbctl_process or self.sbctl_process.returncode is not None:
            logger.warning("sbctl process is not running")
            return False

        # Check if we have a kubeconfig to extract the port from
        port = 8080  # Default port used by many K8s implementations
        server_url = None
        host = "localhost"  # Default host

        # Check if kubeconfig exists
        kubeconfig_path = None
        if self.active_bundle and self.active_bundle.kubeconfig_path.exists():
            kubeconfig_path = self.active_bundle.kubeconfig_path
        else:
            # Try to find kubeconfig in current directory (where sbctl might create it)
            current_dir_kubeconfig = Path.cwd() / "kubeconfig"
            if current_dir_kubeconfig.exists():
                logger.info(f"Found kubeconfig in current directory: {current_dir_kubeconfig}")
                kubeconfig_path = current_dir_kubeconfig

        # Try to parse kubeconfig if found
        if kubeconfig_path:
            try:
                logger.debug(f"Attempting to parse kubeconfig at: {kubeconfig_path}")
                with open(kubeconfig_path, "r") as f:
                    kubeconfig_content = f.read()

                logger.debug(f"Kubeconfig content (first 200 chars): {kubeconfig_content[:200]}...")

                # Try parsing as JSON first, then try YAML or manual parsing as fallback
                config = {}
                try:
                    config = json.loads(kubeconfig_content)
                    logger.debug("Successfully parsed kubeconfig as JSON")
                except json.JSONDecodeError:
                    # If JSON parsing fails, try YAML (since kubeconfig is often YAML)
                    try:
                        # Try to import yaml - handle gracefully if not available
                        try:
                            import yaml

                            config = yaml.safe_load(kubeconfig_content)
                            logger.debug("Successfully parsed kubeconfig as YAML")
                        except ImportError:
                            logger.warning(
                                "PyYAML not available, falling back to basic URL extraction"
                            )
                            # Simple regex-based extraction if YAML module is not available
                            import re

                            server_matches = re.findall(
                                r"server:\s*(http[^\s\n]+)", kubeconfig_content
                            )
                            if server_matches:
                                server_url = server_matches[0].strip()
                                config = {"clusters": [{"cluster": {"server": server_url}}]}
                                logger.debug(f"Extracted server URL using regex: {server_url}")
                            else:
                                logger.warning(
                                    "Could not extract server URL from kubeconfig with regex"
                                )
                    except Exception as parse_err:
                        logger.warning(
                            f"Failed to parse kubeconfig with fallback methods: {parse_err}"
                        )
                        # Continue anyway - we'll use default port

                if (
                    config
                    and isinstance(config, dict)
                    and config.get("clusters")
                    and len(config["clusters"]) > 0
                ):
                    server_url = config["clusters"][0]["cluster"].get("server", "")
                    logger.debug(f"Extracted server URL: {server_url}")

                    # Parse URL into components
                    if server_url:
                        try:
                            from urllib.parse import urlparse

                            parsed_url = urlparse(server_url)
                            if parsed_url.port:
                                port = parsed_url.port
                            if parsed_url.hostname:
                                host = parsed_url.hostname
                            logger.debug(f"Parsed URL - host: {host}, port: {port}")
                        except Exception as parse_err:
                            logger.warning(f"Error parsing server URL: {parse_err}")

                    # Try extracting port directly if URL parsing failed
                    if ":" in server_url and not parsed_url.port:
                        try:
                            port = int(server_url.split(":")[-1])
                            logger.debug(f"Extracted API server port directly: {port}")
                        except (ValueError, IndexError) as e:
                            logger.warning(f"Failed to extract port from server URL: {e}")
            except (json.JSONDecodeError, KeyError, ValueError, IndexError) as e:
                logger.warning(f"Failed to parse kubeconfig: {e}")

        # Also check the environment variable used by our mock for testing
        env_port = os.environ.get("MOCK_K8S_API_PORT")
        if env_port:
            try:
                port = int(env_port)
                logger.debug(f"Using API server port from environment: {port}")
            except ValueError:
                pass

        # Check sbctl logs for clues about server URL (real sbctl prints this on startup)
        if self.sbctl_process and self.sbctl_process.stdout:
            try:
                # Try non-blocking read from process stdout with proper transport cleanup
                from .subprocess_utils import pipe_transport_reader

                try:
                    async with pipe_transport_reader(self.sbctl_process.stdout) as stdout_reader:
                        # Set a timeout for reading
                        data = await asyncio.wait_for(stdout_reader.read(1024), timeout=0.5)
                        if data:
                            output = data.decode("utf-8", errors="replace")
                            logger.debug(f"sbctl process output: {output}")

                            # Look for server URL pattern in output
                            # Example: Server is running at http://localhost:8080
                            import re

                            url_pattern = re.compile(r"https?://[^\s]+")
                            urls = url_pattern.findall(output)
                            if urls:
                                for url in urls:
                                    logger.debug(f"Found URL in sbctl output: {url}")
                                    try:
                                        from urllib.parse import urlparse

                                        parsed_url = urlparse(url)
                                        if parsed_url.port:
                                            port = parsed_url.port
                                            logger.debug(f"Using port from sbctl output: {port}")
                                        if parsed_url.hostname:
                                            host = parsed_url.hostname
                                    except Exception:
                                        pass
                except asyncio.TimeoutError:
                    logger.debug("Timeout reading from sbctl stdout")
            except Exception as e:
                logger.debug(f"Error reading sbctl output: {e}")

        # Define a list of endpoints to check
        endpoints = [
            "/api",  # Standard K8s API endpoint
            "/healthz",  # Health check endpoint
            "/version",  # Version endpoint
            "/apis",  # APIs endpoint
            "/",  # Root endpoint
        ]

        # Try to connect to different API server endpoints
        for endpoint in endpoints:
            try:
                url = f"http://{host}:{port}{endpoint}"
                logger.debug(f"Checking API server at {url}")

                # Create a properly typed timeout object
                timeout = aiohttp.ClientTimeout(total=2.0)
                async with aiohttp.ClientSession() as session:
                    try:
                        async with session.get(url, timeout=timeout) as response:
                            logger.debug(
                                f"API server endpoint {url} returned status {response.status}"
                            )

                            # Get response body for debugging
                            try:
                                body = await asyncio.wait_for(response.text(), timeout=1.0)
                                logger.debug(
                                    f"Response from {url} (first 200 chars): {body[:200]}..."
                                )
                            except (asyncio.TimeoutError, UnicodeDecodeError):
                                logger.debug(f"Could not read response body from {url}")

                            if response.status == 200:
                                logger.info(f"API server is available at {url}")
                                return True
                    except asyncio.TimeoutError:
                        logger.warning(f"Timeout connecting to {url}")
                        continue
            except aiohttp.ClientError as e:
                logger.warning(f"Failed to connect to API server at {url}: {str(e)}")
                continue

        # Try checking with a more aggressive aiohttp retry as backup
        try:
            # Use a longer timeout for backup check
            backup_timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(timeout=backup_timeout) as session:
                for endpoint in endpoints:
                    url = f"http://{host}:{port}{endpoint}"
                    logger.debug(f"Checking API server with backup aiohttp check: {url}")

                    try:
                        async with session.get(url) as response:
                            logger.debug(
                                f"Backup aiohttp check to {url} returned status code: {response.status}"
                            )

                            if response.status == 200:
                                logger.info(
                                    f"API server is available at {url} (backup aiohttp check)"
                                )
                                return True
                    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                        logger.warning(f"Backup aiohttp check failed for {url}: {e}")
                        continue
        except Exception as e:
            logger.warning(f"Error in backup aiohttp API server check: {e}")

        logger.warning("API server is not available at any endpoint")
        return False

    def _check_port_listening_python(self, port: int) -> bool:
        """
        Python-native port checking that replaces netstat dependency.

        This function uses Python's socket module to check if a port is in use,
        eliminating the need for external netstat command which may not be
        available in container environments.

        Args:
            port: The port number to check

        Returns:
            True if port is in use (listening), False if port is free
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                # Try to bind to the port
                s.bind(("", port))
                return False  # Port is free (we could bind to it)
        except OSError:
            return True  # Port is in use (couldn't bind - something else is using it)

    async def get_diagnostic_info(self) -> dict[str, object]:
        """
        Get diagnostic information about the current bundle and sbctl.

        Returns:
            A dictionary with diagnostic information
        """
        diagnostics = {
            "sbctl_available": await self._check_sbctl_available(),
            "sbctl_process_running": self.sbctl_process is not None
            and self.sbctl_process.returncode is None,
            "api_server_available": await self.check_api_server_available(),
            "bundle_initialized": self.active_bundle is not None and self.active_bundle.initialized,
            "system_info": await self._get_system_info(),
        }

        # Add active bundle info if available
        if self.active_bundle:
            diagnostics["active_bundle"] = {
                "id": self.active_bundle.id,
                "source": self.active_bundle.source,
                "path": str(self.active_bundle.path),
                "kubeconfig_exists": self.active_bundle.kubeconfig_path.exists(),
                "kubeconfig_path": str(self.active_bundle.kubeconfig_path),
            }

        # Add sbctl process info if available
        if self.sbctl_process:
            diagnostics["sbctl_process"] = {
                "pid": self.sbctl_process.pid,
                "returncode": self.sbctl_process.returncode,
            }

        return diagnostics

    async def _check_sbctl_available(self) -> bool:
        """
        Check if sbctl is available in the current environment.

        Returns:
            True if sbctl is available, False otherwise
        """

        try:
            from .subprocess_utils import subprocess_exec_with_cleanup

            returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                "sbctl", "--help", timeout=10.0
            )

            if returncode == 0:
                logger.debug("sbctl is available")
                return True
            else:
                logger.warning("sbctl not found or not working")
                return False
        except Exception as e:
            logger.warning(f"Error checking sbctl availability: {str(e)}")
            return False

    async def _get_system_info(self) -> dict[str, object]:
        """
        Get system information.

        Returns:
            A dictionary with system information, values can be any type
        """
        info: dict[str, object] = {}

        # Get the API port from environment or default
        ports_to_check = [8080]  # Default port

        # Check for port in environment variable
        env_port = os.environ.get("MOCK_K8S_API_PORT")
        if env_port:
            try:
                ports_to_check.insert(0, int(env_port))  # Check this port first
            except ValueError:
                pass

        # If we have an active bundle with a kubeconfig, extract the port
        if self.active_bundle and self.active_bundle.kubeconfig_path.exists():
            try:
                with open(self.active_bundle.kubeconfig_path, "r") as f:
                    config = json.load(f)
                if config.get("clusters") and len(config["clusters"]) > 0:
                    server_url = config["clusters"][0]["cluster"].get("server", "")
                    if ":" in server_url:
                        port = int(server_url.split(":")[-1])
                        if port not in ports_to_check:
                            ports_to_check.insert(0, port)
            except Exception:
                pass

        # Check all possible ports
        for port in ports_to_check:
            info[f"port_{port}_checked"] = True

            # Check network connections on the port using Python sockets (replaces netstat dependency)
            try:
                # Use Python-native port checking instead of external netstat command
                port_in_use = self._check_port_listening_python(port)
                info[f"port_{port}_listening"] = port_in_use

                if port_in_use:
                    info[f"port_{port}_details"] = (
                        f"Port {port} is in use (detected via Python socket)"
                    )
                else:
                    info[f"port_{port}_details"] = (
                        f"Port {port} is free (detected via Python socket)"
                    )

                logger.debug(f"Port {port} check completed: listening={port_in_use}")

            except Exception as e:
                info["socket_port_check_exception"] = str(e)
                logger.warning(f"Error during Python socket port check for port {port}: {e}")
                # Fallback: assume port is not listening if we can't check
                info[f"port_{port}_listening"] = False
                info[f"port_{port}_details"] = f"Could not check port {port}: {e}"

            # Try aiohttp to test API server on this port
            try:
                url = f"http://localhost:{port}/api"
                timeout = aiohttp.ClientTimeout(total=3.0)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as response:
                        info[f"http_{port}_status_code"] = str(response.status)

                        # Get response body for diagnostics if available
                        try:
                            body = await asyncio.wait_for(response.text(), timeout=1.0)
                            if body:
                                info[f"http_{port}_response_body"] = body[:200]  # Limit body size
                        except (asyncio.TimeoutError, UnicodeDecodeError):
                            pass
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                info[f"http_{port}_error_text"] = str(e)
            except Exception as e:
                info[f"http_{port}_exception_text"] = str(e)

        # Add environment info
        info["env_mock_k8s_api_port"] = os.environ.get("MOCK_K8S_API_PORT", "not set")

        return info

    async def list_available_bundles(self, include_invalid: bool = False) -> List[BundleFileInfo]:
        """
        List available support bundles in the bundle storage directory.

        Args:
            include_invalid: Whether to include invalid or inaccessible bundles in the results

        Returns:
            List of bundle file information
        """
        logger.info(f"Listing available bundles in {self.bundle_dir}")

        bundles: List[BundleFileInfo] = []

        # Check if bundle directory exists
        if not self.bundle_dir.exists():
            logger.warning(f"Bundle directory {self.bundle_dir} does not exist")
            return bundles

        # Find files with bundle extensions
        bundle_files: List[Path] = []
        bundle_extensions = [".tar.gz", ".tgz"]

        for ext in bundle_extensions:
            bundle_files.extend(self.bundle_dir.glob(f"*{ext}"))

        logger.info(
            f"Found {len(bundle_files)} potential bundle files with extensions {bundle_extensions}"
        )

        # Process each file to get details and check validity
        for file_path in bundle_files:
            try:
                # Get basic file information
                stat_result = file_path.stat()

                # Check if it's a valid bundle by peeking inside
                valid = False
                validation_message = None

                try:
                    valid, validation_message = self._check_bundle_validity(file_path)
                except Exception as e:
                    logger.warning(f"Error checking bundle validity for {file_path}: {str(e)}")
                    validation_message = f"Error checking validity: {str(e)}"

                # Skip invalid bundles if requested
                if not valid and not include_invalid:
                    logger.debug(f"Skipping invalid bundle {file_path}: {validation_message}")
                    continue

                # Create the bundle info
                # Store both the full path and the relative path (without bundle_dir prefix)
                relative_path = file_path.name
                bundle_info = BundleFileInfo(
                    path=str(file_path),
                    relative_path=relative_path,
                    name=file_path.name,
                    size_bytes=stat_result.st_size,
                    modified_time=stat_result.st_mtime,
                    valid=valid,
                    validation_message=validation_message,
                )

                bundles.append(bundle_info)

            except Exception as e:
                logger.warning(f"Error processing bundle file {file_path}: {str(e)}")
                if include_invalid:
                    # If including invalid bundles, add it with the error information
                    try:
                        bundles.append(
                            BundleFileInfo(
                                path=str(file_path),
                                relative_path=file_path.name,
                                name=file_path.name,
                                size_bytes=(file_path.stat().st_size if file_path.exists() else 0),
                                modified_time=(
                                    file_path.stat().st_mtime if file_path.exists() else 0
                                ),
                                valid=False,
                                validation_message=f"Error: {str(e)}",
                            )
                        )
                    except Exception:
                        # Last resort to include something if we can't get file stats
                        bundles.append(
                            BundleFileInfo(
                                path=str(file_path),
                                relative_path=file_path.name,
                                name=file_path.name,
                                size_bytes=0,
                                modified_time=0,
                                valid=False,
                                validation_message=f"Error: {str(e)}",
                            )
                        )

        # Sort bundles by modification time (newest first)
        bundles.sort(key=lambda x: x.modified_time, reverse=True)

        return bundles

    def _check_bundle_validity(self, file_path: Path) -> Tuple[bool, Optional[str]]:
        """
        Check if a file appears to be a valid support bundle.

        Args:
            file_path: Path to the potential bundle file

        Returns:
            Tuple of (is_valid, validation_message)
        """
        if not file_path.exists():
            return False, "File not found"

        if not file_path.is_file():
            return False, "Not a file"

        # Check file extension
        if not str(file_path).lower().endswith((".tar.gz", ".tgz")):
            return False, "Not a .tar.gz or .tgz file"

        # Peek inside the tarfile to verify it's a support bundle
        try:
            with tarfile.open(file_path, "r:gz") as tar:
                # List first few entries to check structure without extracting
                members = tar.getmembers()[:20]  # Just check the first 20 entries for efficiency

                # Look for patterns that indicate a support bundle
                has_cluster_resources = False
                has_support_bundle_dir = False

                for member in members:
                    # Check for common support bundle directory structure
                    if "cluster-resources/" in member.name:
                        has_cluster_resources = True

                    # Check for a top-level support-bundle directory
                    if member.name.startswith("support-bundle-"):
                        has_support_bundle_dir = True

                if has_cluster_resources or has_support_bundle_dir:
                    return True, None

                return (
                    False,
                    "File doesn't contain expected support bundle structure (no cluster-resources or support-bundle directories)",
                )

        except tarfile.ReadError as e:
            return False, f"Not a valid tar.gz file: {str(e)}"
        except Exception as e:
            return False, f"Error checking file: {str(e)}"

    async def cleanup(self) -> None:
        """
        Clean up all resources when shutting down the server.

        This method performs a complete cleanup sequence:
        1. Terminates the active bundle and its processes
        2. Removes extracted bundle directories
        3. Removes the temporary bundle directory if created by this instance

        This should be called when shutting down the server to ensure proper resource
        management and prevent orphaned files/processes.
        """
        logger.info("Performing complete cleanup during server shutdown")

        # 1. Clean up the active bundle (processes and directories)
        await self._cleanup_active_bundle()

        # 2. Clean up any orphaned sbctl processes that might still be running
        if CLEANUP_ORPHANED:
            try:
                # Use psutil as a final safety measure to ensure no sbctl processes remain
                try:
                    logger.info("Checking for any remaining sbctl processes")
                    # Find sbctl processes using psutil instead of ps -ef subprocess call
                    sbctl_processes = []
                    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                        try:
                            # Check if this is an sbctl process
                            if (proc.info["name"] and "sbctl" in proc.info["name"]) or (
                                proc.info["cmdline"]
                                and any("sbctl" in arg for arg in proc.info["cmdline"])
                            ):
                                sbctl_processes.append(proc)
                        except (
                            psutil.NoSuchProcess,
                            psutil.AccessDenied,
                            psutil.ZombieProcess,
                        ):
                            # Process disappeared or access denied - skip it
                            continue

                    if sbctl_processes:
                        logger.warning(
                            f"Found {len(sbctl_processes)} sbctl processes still running during shutdown"
                        )
                        # Try to terminate them using psutil instead of pkill subprocess call
                        terminated_count = 0
                        for proc in sbctl_processes:
                            try:
                                proc.terminate()
                                terminated_count += 1
                                logger.debug(f"Terminated sbctl process with PID {proc.pid}")
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                # Process already gone or access denied - skip it
                                continue
                        logger.info(
                            f"Terminated {terminated_count} sbctl processes during shutdown"
                        )
                    else:
                        logger.info("No sbctl processes found during shutdown")
                except Exception as process_err:
                    logger.warning(
                        f"Error checking for orphaned processes during shutdown: {process_err}"
                    )
            except Exception as e:
                logger.warning(f"Error during extended process cleanup: {e}")

        # 3. Remove temporary directory if it was created by us
        if self.bundle_dir and str(self.bundle_dir).startswith(tempfile.gettempdir()):
            try:
                logger.info(f"Removing temporary bundle directory: {self.bundle_dir}")
                shutil.rmtree(self.bundle_dir)
                logger.info(f"Successfully removed temporary bundle directory: {self.bundle_dir}")
            except Exception as e:
                logger.error(f"Failed to remove temporary bundle directory: {str(e)}")

        logger.info("Cleanup completed")
