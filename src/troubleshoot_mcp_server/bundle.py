"""
Bundle Manager for Kubernetes support bundles.

This module implements the Bundle Manager component, which is responsible for
handling the lifecycle of Kubernetes support bundles, including downloading,
extraction, initialization, and cleanup.
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
import datetime
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
import time
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple, Union
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


@dataclass
class BundleState:
    """
    State tracking for a single bundle in concurrent SSE mode.

    This replaces the global active_bundle pattern with per-bundle state management.
    Each bundle has its own lifecycle state, process handle, and synchronization primitives.

    Attributes:
        bundle_id: Unique identifier for this bundle
        metadata: Bundle metadata (None until initialization completes)
        process: sbctl subprocess handle (None if not running)
        status: Current lifecycle state
        lock: Per-bundle lock for serializing operations
        epoch: Generation counter to prevent late events from old processes
        cancel_requested: Flag indicating termination was requested
        current_event: Per-epoch event for the current initialization attempt
        stopped_event: Event signaled when process stops
        start_time: Timestamp when this state was created
        last_error: Most recent error message (if status is "failed")
    """

    bundle_id: str
    metadata: Optional[BundleMetadata]
    process: Optional[asyncio.subprocess.Process]
    status: Literal["initializing", "running", "stopping", "stopped", "failed"]
    lock: asyncio.Lock
    epoch: int = 0
    cancel_requested: bool = False
    current_event: asyncio.Event = field(default_factory=asyncio.Event)
    stopped_event: asyncio.Event = field(default_factory=asyncio.Event)
    start_time: float = field(default_factory=time.time)
    last_error: Optional[str] = None


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

        # NEW: Concurrent bundle state management (replaces active_bundle pattern)
        self._registry_lock = asyncio.Lock()  # Atomic state creation
        self.bundle_states: Dict[str, BundleState] = {}  # Per-bundle state tracking
        self.source_to_bundle_id: Dict[str, str] = {}  # Maps source URL/path -> bundle_id for deduplication

        # LEGACY: Track multiple concurrent bundles (will be replaced by bundle_states)
        self.bundles: dict[str, BundleMetadata] = {}
        self.sbctl_processes: dict[str, asyncio.subprocess.Process] = {}
        self.active_bundle_id: Optional[str] = None

        # Session-based bundle tracking (MCP session -> bundle mapping)
        self.session_bundles: dict[str, str] = {}  # session_id -> bundle_id

        self._host_only_bundle: bool = False
        self._termination_requested: bool = False

        # Stderr monitoring and crash recovery infrastructure
        self._stderr_buffer: deque = deque(maxlen=100)  # Rolling buffer for last 100 lines
        self._stderr_monitor_task: Optional[asyncio.Task] = None
        self._last_timeout_command: Optional[str] = None
        self._crash_recovery_info: Optional[dict] = None

    async def _get_or_create_state(self, bundle_id: str) -> BundleState:
        """
        Atomically get or create BundleState for the given bundle_id.

        This method uses the registry lock to ensure only one BundleState
        is created per bundle_id, preventing race conditions when multiple
        tasks try to initialize the same bundle simultaneously.

        Args:
            bundle_id: The bundle identifier

        Returns:
            BundleState for the given bundle_id (either existing or newly created)
        """
        async with self._registry_lock:
            if bundle_id not in self.bundle_states:
                logger.debug(f"Creating new BundleState for bundle: {bundle_id}")
                self.bundle_states[bundle_id] = BundleState(
                    bundle_id=bundle_id,
                    metadata=None,
                    process=None,
                    status="stopped",  # Start as stopped, not initializing
                    lock=asyncio.Lock(),
                    epoch=0,
                    cancel_requested=False,
                    current_event=asyncio.Event(),
                    stopped_event=asyncio.Event(),
                    start_time=time.time(),
                    last_error=None,
                )
            return self.bundle_states[bundle_id]

    async def _wait_for_latest(self, state: BundleState) -> BundleMetadata:
        """
        Wait for the latest initialization to complete and return its metadata.

        Used when this attempt was superseded by a newer epoch. Waits for
        the current epoch's event to signal, then returns the metadata.

        Args:
            state: The bundle state to wait on

        Returns:
            The latest metadata after waiting

        Raises:
            BundleManagerError: If the latest initialization failed
        """
        # Capture current event
        async with state.lock:
            current_event = state.current_event
            current_epoch = state.epoch

        # Wait for current epoch to complete
        logger.debug(f"[Bundle {state.bundle_id}] Waiting for epoch {current_epoch} to complete...")
        await current_event.wait()

        # Return the result
        async with state.lock:
            if state.status == "failed":
                raise BundleManagerError(
                    f"Bundle initialization failed: {state.last_error or 'Unknown error'}"
                )
            if state.metadata:
                return state.metadata
            raise BundleManagerError("Bundle initialization completed but metadata is missing")

    async def _load_bundle_from_disk_if_needed(self, bundle_id: str) -> Optional[BundleMetadata]:
        """Lazy-load bundle from disk if not in memory and restart sbctl if needed.

        Args:
            bundle_id: Bundle ID to load

        Returns:
            BundleMetadata if found, None if not on disk
        """
        # Check memory first
        if bundle_id in self.bundles:
            # Check if sbctl process is still running for this bundle
            if (
                bundle_id not in self.sbctl_processes
                or self.sbctl_processes[bundle_id].returncode is not None
            ):
                # sbctl died - need to restart it
                logger.info(f"Bundle {bundle_id} loaded but sbctl not running - restarting")
                await self._restart_sbctl_for_bundle(bundle_id)
            return self.bundles[bundle_id]

        # Check disk
        try:
            bundle_path = self.bundle_dir / bundle_id
            if not bundle_path.exists() or not bundle_path.is_dir():
                return None

            kubeconfig_path = bundle_path / "kubeconfig"
            bundle_tarball = bundle_path / "bundle.tar.gz"

            # Validate it's a real bundle
            if kubeconfig_path.exists() or bundle_tarball.exists():
                metadata = BundleMetadata(
                    id=bundle_id,
                    source=f"disk:{bundle_id}",
                    path=bundle_path,
                    kubeconfig_path=kubeconfig_path,
                    initialized=True,
                    host_only_bundle=False,
                )
                # Cache in memory (DON'T set active_bundle_id - causes concurrent conflicts)
                self.bundles[bundle_id] = metadata
                logger.info(f"Lazy-loaded bundle from disk: {bundle_id}")

                # Restart sbctl for this bundle (pass bundle_id explicitly)
                if bundle_tarball.exists():
                    await self._restart_sbctl_for_bundle(bundle_id)

                return metadata

            return None
        except Exception as e:
            logger.warning(f"Error loading bundle {bundle_id} from disk: {e}")
            return None

    async def _restart_sbctl_for_bundle(self, bundle_id: str) -> None:
        """Restart sbctl process for a bundle loaded from disk.

        Args:
            bundle_id: Bundle ID to restart sbctl for
        """
        bundle = self.bundles.get(bundle_id)
        if not bundle:
            logger.warning(f"Cannot restart sbctl: bundle {bundle_id} not in bundles dict")
            return

        # Check if sbctl already running for this bundle
        if bundle_id in self.sbctl_processes:
            process = self.sbctl_processes[bundle_id]
            if process.returncode is None:  # Still running
                logger.info(f"sbctl already running for bundle {bundle_id}")
                return

        bundle_tarball = bundle.path / "bundle.tar.gz"
        if not bundle_tarball.exists():
            logger.warning(f"Cannot restart sbctl: bundle tarball not found for {bundle_id}")
            return

        try:
            # Temporarily set as active so property setter works (avoid concurrent conflicts with lock)
            old_active = self.active_bundle_id
            self.active_bundle_id = bundle_id

            # Start sbctl (will be stored in sbctl_processes[bundle_id] via property setter)
            await self._start_sbctl_process(bundle_tarball, bundle.path)
            logger.info(f"Restarted sbctl for bundle {bundle_id}")

            # Restore previous active
            self.active_bundle_id = old_active
        except Exception as e:
            logger.error(f"Failed to restart sbctl for bundle {bundle_id}: {e}")
            self.active_bundle_id = old_active

    # Backward compatibility properties
    @property
    def active_bundle(self) -> Optional[BundleMetadata]:
        """Get active bundle (legacy compatibility)."""
        return self.bundles.get(self.active_bundle_id) if self.active_bundle_id else None

    @active_bundle.setter
    def active_bundle(self, metadata: Optional[BundleMetadata]) -> None:
        """Set active bundle (legacy compatibility)."""
        if metadata:
            self.bundles[metadata.id] = metadata
            self.active_bundle_id = metadata.id
            logger.info(
                f"SET active_bundle: bundle_id={metadata.id}, total_bundles={len(self.bundles)}"
            )
        else:
            self.active_bundle_id = None
            logger.info("CLEAR active_bundle")

    @property
    def sbctl_process(self) -> Optional[asyncio.subprocess.Process]:
        """Get active bundle's sbctl process (legacy compatibility)."""
        return self.sbctl_processes.get(self.active_bundle_id) if self.active_bundle_id else None

    @sbctl_process.setter
    def sbctl_process(self, process: Optional[asyncio.subprocess.Process]) -> None:
        """Set active bundle's sbctl process (legacy compatibility)."""
        if process and self.active_bundle_id:
            self.sbctl_processes[self.active_bundle_id] = process
        elif not process and self.active_bundle_id:
            self.sbctl_processes.pop(self.active_bundle_id, None)

    # Session management methods for MCP session-based bundle tracking
    def set_bundle_for_session(self, session_id: str, bundle_id: str) -> None:
        """
        Associate a bundle with an MCP session.

        Args:
            session_id: MCP session identifier
            bundle_id: Bundle identifier to associate with this session
        """
        logger.info(f"Session {session_id[:8]}... -> bundle {bundle_id}")
        self.session_bundles[session_id] = bundle_id

    def get_bundle_for_session(self, session_id: str) -> Optional[str]:
        """
        Get the bundle ID associated with an MCP session.

        Returns the bundle if files are initialized and ready for use,
        regardless of API status. Individual tools (kubectl, etc.) will
        check API availability as needed.

        Falls back to checking if session_id exists as bundle_id on disk,
        enabling stateless operation across BundleManager instances.

        Args:
            session_id: MCP session identifier

        Returns:
            Bundle ID if found and files are initialized, None otherwise
        """
        logger.info(f"[DEBUG] get_bundle_for_session called for session {session_id[:16]}...")
        logger.info(f"[DEBUG] session_bundles mapping: {list(self.session_bundles.keys())[:5]}")

        # Check in-memory mapping first
        bundle_id = self.session_bundles.get(session_id)
        logger.info(f"[DEBUG] Looked up session {session_id[:16]}..., got bundle_id: {bundle_id}")

        if bundle_id:
            # Verify bundle is in valid state (if using bundle_states)
            if bundle_id in self.bundle_states:
                state = self.bundle_states[bundle_id]
                logger.info(f"[DEBUG] Found state for bundle {bundle_id}, status={state.status}")
                logger.info(f"[DEBUG] state.metadata={state.metadata}, initialized={state.metadata.initialized if state.metadata else None}")

                # Check if files are initialized and ready
                # API status doesn't matter - tools will check that themselves
                files_ready = bool(state.metadata and state.metadata.initialized)

                if files_ready:
                    logger.info(
                        f"Bundle {bundle_id} for session {session_id[:16]}... files initialized "
                        f"(status={state.status}), returning"
                    )
                    return bundle_id

                logger.info(
                    f"Bundle {bundle_id} for session {session_id[:16]}... not initialized "
                    f"(status={state.status}, metadata={state.metadata})"
                )
                return None
            logger.info(f"[DEBUG] bundle_id {bundle_id} not in bundle_states, using legacy path")
            return bundle_id  # Legacy path: bundle_id found but no state tracking yet

        logger.info(f"[DEBUG] No in-memory mapping found, checking disk fallback")
        # Fallback: check if session_id itself is a bundle directory on disk
        # This allows stateless operation - if bundle was initialized with
        # session_id as bundle_id, we can find it without in-memory state
        bundle_path = self.bundle_dir / session_id
        logger.info(f"[DEBUG] Checking disk path: {bundle_path}")
        if bundle_path.exists() and bundle_path.is_dir():
            logger.info(f"Found bundle on disk for session {session_id[:16]}... (stateless lookup)")
            # Cache it for this instance
            self.session_bundles[session_id] = session_id
            return session_id

        logger.info(f"[DEBUG] No bundle found for session {session_id[:16]}..., returning None")
        return None

    async def cleanup_session(self, session_id: str) -> None:
        """
        Cleanup resources for an MCP session and its associated bundle.

        Args:
            session_id: MCP session identifier
        """
        bundle_id = self.session_bundles.pop(session_id, None)
        if bundle_id:
            logger.info(f"Cleaning up session {session_id[:8]}... (bundle: {bundle_id})")
            try:
                await self.cleanup_bundle(bundle_id)
            except Exception as e:
                logger.error(f"Error cleaning up bundle {bundle_id} for session {session_id}: {e}")
        else:
            logger.debug(f"No bundle associated with session {session_id[:8]}...")

    async def initialize_bundle(
        self, source: str, force: bool = False, token: Optional[str] = None, bundle_id: Optional[str] = None
    ) -> BundleMetadata:
        """
        Initialize a support bundle from a source with concurrent-safe bundle management.

        This method uses per-bundle state tracking and coordination to support
        concurrent bundle initialization in SSE mode while maintaining stdio compatibility.

        Args:
            source: The source of the bundle (URL or local path)
            force: Whether to force re-initialization if bundle is already running
            token: Optional SBCTL token for authenticated downloads (overrides SBCTL_TOKEN env var)
            bundle_id: Optional explicit bundle ID (if None, generates from source)

        Returns:
            Metadata for the initialized bundle

        Raises:
            BundleManagerError: If the bundle could not be initialized
        """
        # 1. Resolve bundle_id early (before any state operations)
        if not bundle_id:
            # Check if we already have a bundle for this source (prevents duplicates when force=False)
            cached_id = self.source_to_bundle_id.get(source)
            if cached_id and cached_id in self.bundle_states:
                bundle_id = cached_id
                logger.debug(f"Using existing bundle_id {bundle_id} for source: {source}")
            else:
                bundle_id = self._generate_bundle_id(source)
                self.source_to_bundle_id[source] = bundle_id
                logger.debug(f"Generated new bundle_id {bundle_id} for source: {source}")

        logger.info(f"[Bundle {bundle_id}] Initializing from source: {source}")

        # 2. Get or create bundle state atomically
        state = await self._get_or_create_state(bundle_id)

        # 4. Coordination loop: handle concurrent initialization attempts
        my_epoch: Optional[int] = None
        my_event: Optional[asyncio.Event] = None

        while True:
            async with state.lock:
                logger.debug(
                    f"[Bundle {bundle_id}][Epoch {state.epoch}] Status: {state.status}, Force: {force}"
                )

                # Fast path: bundle already running
                if state.status == "running" and not force:
                    logger.info(f"[Bundle {bundle_id}] Already running, returning existing metadata")
                    return state.metadata

                # Wait path: another initialization in progress
                if state.status == "initializing" and not force:
                    waiter = state.current_event
                    logger.info(
                        f"[Bundle {bundle_id}][Epoch {state.epoch}] Another initialization in progress, waiting"
                    )
                    # Will await outside lock
                else:
                    # Start new initialization attempt
                    state.epoch += 1
                    my_epoch = state.epoch
                    my_event = asyncio.Event()
                    state.current_event = my_event
                    state.status = "initializing"
                    state.last_error = None
                    state.metadata = None
                    logger.info(
                        f"[Bundle {bundle_id}][Epoch {my_epoch}] Starting new initialization attempt"
                    )
                    break  # Exit loop to perform initialization

            # Wait for ongoing initialization, then re-check state
            logger.debug(f"[Bundle {bundle_id}] Waiting for epoch to complete...")
            await waiter.wait()
            logger.debug(f"[Bundle {bundle_id}] Wait completed, re-checking state")

        # 5. Perform initialization WITHOUT holding lock (long operations)
        try:
            # Create bundle directory FIRST (before download)
            # This ensures each session downloads to its own unique directory
            bundle_output_dir = self.bundle_dir / bundle_id
            bundle_output_dir.mkdir(parents=True, exist_ok=True)

            # Download or locate bundle (LONG OPERATION)
            logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Downloading/locating bundle...")
            if source.startswith(("http://", "https://")):
                # Download directly into session directory to avoid collisions
                bundle_path = await self._download_bundle(source, token=token, output_dir=bundle_output_dir)
                bundle_path_for_init = bundle_path
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
                            available_bundles = await self.list_available_bundles(include_invalid=True)
                            for bundle in available_bundles:
                                if bundle.relative_path == source or bundle.name == source:
                                    logger.info(f"Found matching bundle by relative path: {bundle.path}")
                                    bundle_path = Path(bundle.path)
                                    break
                        except Exception as e:
                            logger.warning(f"Error searching for bundle by relative path: {e}")

                # If we still can't find it, raise an error
                if not bundle_path.exists():
                    raise BundleNotFoundError(
                        f"Bundle not found: {source} (tried both as absolute path and in bundle directory {self.bundle_dir})"
                    )

                bundle_path_for_init = bundle_path

            # Check epoch: superseded by newer attempt?
            if state.epoch != my_epoch:
                logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Superseded during download, exiting gracefully")
                my_event.set()
                async with state.lock:
                    return state.metadata if state.metadata else await self._wait_for_latest(state)

            # Check epoch before sbctl initialization
            if state.epoch != my_epoch:
                logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Superseded before sbctl init, exiting")
                my_event.set()
                async with state.lock:
                    return state.metadata if state.metadata else await self._wait_for_latest(state)

            # Initialize with sbctl (LONG OPERATION)
            logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Initializing with sbctl...")
            kubeconfig_path = await self._initialize_with_sbctl(
                bundle_path_for_init, bundle_output_dir, bundle_id
            )

            # Check epoch after sbctl initialization
            if state.epoch != my_epoch:
                logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Superseded after sbctl init, exiting")
                my_event.set()
                async with state.lock:
                    return state.metadata if state.metadata else await self._wait_for_latest(state)

            # Handle host-only bundles
            if self._host_only_bundle:
                kubeconfig_path = bundle_output_dir / "kubeconfig"

            # Extract and validate bundle (LONG OPERATION)
            logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Extracting and validating...")
            try:
                logger.info(f"Listing files in bundle directory: {bundle_output_dir}")
                file_count = 0
                dir_count = 0
                for root, dirs, files in os.walk(bundle_output_dir):
                    dir_count += len(dirs)
                    file_count += len(files)

                logger.info(f"Bundle directory contains {file_count} files and {dir_count} directories")

                top_entries = list(bundle_output_dir.glob("*"))
                logger.info(f"Top-level entries in bundle directory: {[e.name for e in top_entries]}")

                # Extract if needed
                extract_dir = bundle_output_dir / "extracted"
                if not extract_dir.exists():
                    logger.info(f"Creating extract directory: {extract_dir}")
                    extract_dir.mkdir(exist_ok=True)

                    # Use bundle_path_for_init which points to the actual tarball
                    if bundle_path_for_init.exists() and str(bundle_path_for_init).endswith((".tar.gz", ".tgz")):
                        import tarfile

                        logger.info(f"Extracting bundle from {bundle_path_for_init} to: {extract_dir}")
                        with tarfile.open(bundle_path_for_init, "r:gz") as tar:
                            members = tar.getmembers()
                            logger.info(f"Support bundle contains {len(members)} entries")

                            # Sanitize member paths
                            from pathlib import PurePath

                            safe_members = []
                            for member in members:
                                if member.name.startswith(("/", "../")):
                                    member.name = PurePath(member.name).name
                                safe_members.append(member)

                            tar.extractall(path=extract_dir, members=safe_members, filter="data")

                        # Verify extraction
                        file_count = 0
                        dir_count = 0
                        for root, dirs, files in os.walk(extract_dir):
                            dir_count += len(dirs)
                            file_count += len(files)

                        extracted_files = list(extract_dir.glob("*"))
                        logger.info(f"Extracted {len(extracted_files)} top-level entries to {extract_dir}")
                        logger.info(f"Extracted bundle contains {file_count} files and {dir_count} directories")
            except Exception as list_err:
                logger.warning(f"Error while listing/extracting bundle files: {list_err}")

            # Check epoch before finalizing
            if state.epoch != my_epoch:
                logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Superseded during extraction, exiting")
                my_event.set()
                async with state.lock:
                    return state.metadata if state.metadata else await self._wait_for_latest(state)

            # 6. Create metadata
            metadata = BundleMetadata(
                id=bundle_id,
                source=source,
                path=bundle_output_dir,
                kubeconfig_path=kubeconfig_path,
                initialized=True,
                host_only_bundle=self._host_only_bundle,
            )

            # 7. Finalize state under lock
            async with state.lock:
                if state.epoch == my_epoch:
                    state.metadata = metadata
                    state.process = self.sbctl_processes.get(bundle_id)
                    state.status = "running"
                    state.last_error = None
                    logger.info(f"[Bundle {bundle_id}][Epoch {my_epoch}] Initialization complete, status=running")
                else:
                    logger.info(
                        f"[Bundle {bundle_id}][Epoch {my_epoch}] Superseded during finalization, newer epoch owns state"
                    )

            # 8. Signal completion (always, even if superseded)
            my_event.set()

            # 9. Start stderr monitoring
            self._start_stderr_monitoring()

            logger.info(f"Bundle initialized successfully: {bundle_id}")
            return metadata

        except (BundleDownloadError, BundleInitializationError) as e:
            # Handle expected errors
            logger.error(f"[Bundle {bundle_id}][Epoch {my_epoch}] Failed to initialize: {str(e)}")
            if my_event:
                async with state.lock:
                    if state.epoch == my_epoch:
                        state.status = "failed"
                        state.last_error = str(e)
                my_event.set()
            raise
        except Exception as e:
            # Handle unexpected errors
            logger.exception(f"[Bundle {bundle_id}][Epoch {my_epoch}] Unexpected error: {str(e)}")
            if my_event:
                async with state.lock:
                    if state.epoch == my_epoch:
                        state.status = "failed"
                        state.last_error = str(e)
                my_event.set()
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

    async def _get_replicated_signed_url(
        self, original_url: str, token: Optional[str] = None
    ) -> str:
        """
        Get the temporary signed download URL from the Replicated Vendor Portal API.

        Args:
            original_url: The original Replicated Vendor Portal URL.
            token: Optional SBCTL token for authentication (overrides env vars)

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

        # Get token - use parameter first, then SBCTL_TOKEN, then REPLICATED env var
        auth_token = token or os.environ.get("SBCTL_TOKEN") or os.environ.get("REPLICATED")
        if not auth_token:
            raise BundleDownloadError(
                "Cannot download from Replicated Vendor Portal: "
                "SBCTL_TOKEN or REPLICATED environment variable not set."
            )

        api_url = REPLICATED_API_ENDPOINT.format(slug=slug)
        headers = {"Authorization": auth_token, "Content-Type": "application/json"}

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

    async def _download_github_attachment(self, url: str, output_dir: Optional[Path] = None) -> Path:
        """
        Download bundle from GitHub with proper authentication.

        Args:
            url: GitHub URL to download from
            output_dir: Optional directory to download to (defaults to self.bundle_dir).
                       If provided, downloads to output_dir/.bundle.tar.gz.part then atomically renames to bundle.tar.gz

        Returns:
            The path to the downloaded bundle

        Raises:
            BundleDownloadError: If the bundle could not be downloaded
        """
        # Only GITHUB_TOKEN is valid for GitHub URLs (SBCTL_TOKEN is for Replicated only)
        github_token = os.environ.get("GITHUB_TOKEN")

        if not github_token:
            raise BundleDownloadError(
                "Cannot download from GitHub: No authentication token found. "
                "Set GITHUB_TOKEN environment variable. "
                "Note: SBCTL_TOKEN is only for Replicated URLs, not GitHub."
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
        # Determine download path based on whether output_dir is provided
        if output_dir:
            # Option A (hotfix): Download directly to session directory with atomic rename pattern
            download_path = output_dir / ".bundle.tar.gz.part"
            final_path = output_dir / "bundle.tar.gz"
        else:
            # Legacy path: Generate safe filename
            filename = re.sub(r"[^\w\-.]", "_", filename)
            if not filename:
                filename = f"github_bundle_{self._generate_bundle_id(url)}.tar.gz"
            download_path = self.bundle_dir / filename
            final_path = download_path  # No rename needed for legacy path

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
                            f"Successfully downloaded GitHub bundle ({total_downloaded} bytes)"
                        )

                        # Atomic rename if using output_dir (Option A pattern)
                        if output_dir:
                            logger.info(f"Atomically renaming {download_path} to {final_path}")
                            os.replace(str(download_path), str(final_path))
                            logger.info(f"GitHub bundle downloaded to: {final_path}")
                            return final_path
                        else:
                            logger.info(f"GitHub bundle downloaded to: {download_path}")
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

    async def _download_bundle(self, url: str, token: Optional[str] = None, output_dir: Optional[Path] = None) -> Path:
        """
        Download a support bundle from a URL, handling Replicated Vendor Portal and GitHub URLs.

        Args:
            url: The URL to download the bundle from (can be original or signed)
            token: Optional SBCTL token for authenticated downloads (overrides SBCTL_TOKEN env var)
            output_dir: Optional directory to download to (defaults to self.bundle_dir).
                       If provided, downloads to output_dir/.bundle.tar.gz.part then atomically renames to bundle.tar.gz

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
            return await self._download_github_attachment(url, output_dir=output_dir)

        # Check if it's a Replicated Vendor Portal URL
        if REPLICATED_VENDOR_URL_PATTERN.match(url):
            try:
                actual_download_url = await self._get_replicated_signed_url(url, token=token)
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

        # Determine download path based on whether output_dir is provided
        if output_dir:
            # Option A (hotfix): Download directly to session directory with atomic rename pattern
            # Use temp file to ensure atomic write: .bundle.tar.gz.part -> bundle.tar.gz
            download_path = output_dir / ".bundle.tar.gz.part"
            final_path = output_dir / "bundle.tar.gz"
        else:
            # Legacy path: Generate filename based on URL type (backward compatibility)
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
            final_path = download_path  # No rename needed for legacy path

        try:
            # Headers for the actual download
            download_headers = {}
            # Add auth token ONLY for non-Replicated URLs (signed URLs have auth embedded)
            if actual_download_url == original_url:  # Check if we are using the original URL
                # Use passed token parameter or fall back to environment variable
                auth_token = token or os.environ.get("SBCTL_TOKEN")
                if auth_token:
                    download_headers["Authorization"] = f"Bearer {auth_token}"
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

            # Atomic rename if using output_dir (Option A pattern)
            if output_dir:
                logger.info(f"Atomically renaming {download_path} to {final_path}")
                os.replace(str(download_path), str(final_path))
                logger.info(f"Bundle downloaded to: {final_path}")
                return final_path
            else:
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

    async def _initialize_with_sbctl(
        self, bundle_path: Path, output_dir: Path, bundle_id: str
    ) -> Path:
        """
        Initialize a support bundle with sbctl.

        Args:
            bundle_path: The path to the bundle file
            output_dir: The directory where the bundle will be extracted
            bundle_id: The bundle ID (used for cleanup on error, prevents race conditions)

        Returns:
            The path to the kubeconfig file

        Raises:
            BundleInitializationError: If the bundle could not be initialized
        """
        logger.info(f"Initializing bundle with sbctl: {bundle_path}")

        # sbctl creates kubeconfig in a temp directory and announces the path via stdout
        # The code below detects the announcement and copies it here
        kubeconfig_path = output_dir / "kubeconfig"

        try:
            # DON'T kill existing sbctl - concurrent bundles each need their own process
            # Only kill if reinitializing the SAME bundle (handled by caller with force=True)
            # await self._terminate_sbctl_process()  # REMOVED for concurrent support

            # Start sbctl in serve mode with the bundle in the output directory
            await self._start_sbctl_process(bundle_path, output_dir, bundle_id)

            # Capture process locally to avoid race condition with concurrent bundle operations
            # IMPORTANT: Don't use self.sbctl_process property - it can change when active_bundle_id changes
            sbctl_process = self.sbctl_processes.get(bundle_id)
            if not sbctl_process:
                raise BundleInitializationError("Failed to start sbctl process")

            # First, wait a brief moment to see if sbctl exits quickly with "No cluster resources"
            try:
                # Wait for either process completion or a short timeout
                await asyncio.wait_for(sbctl_process.wait(), timeout=5.0)

                # Process completed quickly - check output
                stdout_data = b""
                stderr_data = b""

                if sbctl_process.stdout:
                    try:
                        stdout_data = await asyncio.wait_for(
                            sbctl_process.stdout.read(), timeout=1.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        pass

                if sbctl_process.stderr:
                    try:
                        stderr_data = await asyncio.wait_for(
                            sbctl_process.stderr.read(), timeout=1.0
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

                # Check if this was an intentional termination (SIGTERM = exit code -15)
                if sbctl_process.returncode == -15:
                    # Check cancel_requested from bundle state
                    if bundle_id in self.bundle_states:
                        if self.bundle_states[bundle_id].cancel_requested:
                            logger.debug(
                                f"[Bundle {bundle_id}] sbctl process terminated intentionally (cancel_requested=True)"
                            )
                            return kubeconfig_path
                    # Fallback to legacy global flag
                    if self._termination_requested:
                        logger.debug("sbctl process was intentionally terminated during initialization (quick-exit)")
                        return kubeconfig_path

                # If we get here, sbctl exited quickly but not due to "no cluster resources"
                # Check the exit code to determine if it's an error
                if sbctl_process.returncode != 0:
                    error_msg = f"sbctl process exited with code {sbctl_process.returncode}"
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
                    if sbctl_process and sbctl_process.stdout:
                        # Try to read the first few lines to catch the kubeconfig announcement
                        stdout_lines = []
                        for attempt in range(10):  # Try up to 10 lines or 5 seconds
                            try:
                                line = await asyncio.wait_for(
                                    sbctl_process.stdout.readline(), timeout=0.5
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

            # Wait for initialization to complete (pass bundle_id and process explicitly)
            await self._wait_for_initialization(kubeconfig_path, bundle_id, sbctl_process)

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
            if sbctl_process and sbctl_process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(
                        sbctl_process.stderr.read(4096), timeout=1.0
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

            # Terminate the process for THIS bundle (use local bundle_id parameter to prevent race condition)
            # IMPORTANT: Don't use self.active_bundle_id here - it can be changed by concurrent workflows!
            await self._terminate_sbctl_process(bundle_id)

            raise BundleInitializationError(
                f"Failed to initialize bundle with sbctl: {error_message}"
            )

    async def _wait_for_initialization(
        self,
        kubeconfig_path: Path,
        bundle_id: str,
        sbctl_process: asyncio.subprocess.Process,
        timeout: float = MAX_INITIALIZATION_TIMEOUT,
    ) -> None:
        """
        Wait for sbctl initialization to complete.

        Args:
            kubeconfig_path: The path to the kubeconfig file
            bundle_id: The bundle ID (for cancel_requested checks)
            sbctl_process: The sbctl process to monitor (explicit param to avoid race conditions)
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
        if sbctl_process and sbctl_process.stdout and sbctl_process.stderr:
            stdout_data = b""
            stderr_data = b""

            try:
                # Try to read without blocking the entire process
                # We need to handle the coroutines properly for type checking
                if sbctl_process.stdout is not None:
                    try:
                        # We expect bytes returned from the process stdout
                        stdout_data = await asyncio.wait_for(
                            sbctl_process.stdout.read(1024), timeout=1.0
                        )
                    except (asyncio.TimeoutError, Exception):
                        stdout_data = b""
                else:
                    stdout_data = b""

                if sbctl_process.stderr is not None:
                    try:
                        # We expect bytes returned from the process stderr
                        stderr_data = await asyncio.wait_for(
                            sbctl_process.stderr.read(1024), timeout=1.0
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
            if sbctl_process and sbctl_process.returncode is not None:
                # Process exited - check if this is expected
                if sbctl_process.returncode != 0:
                    # Process failed - read any error output and fail immediately
                    process_output = ""
                    try:
                        if sbctl_process.stdout:
                            stdout_data = await sbctl_process.stdout.read()
                            process_output += stdout_data.decode("utf-8", errors="replace")
                        if sbctl_process.stderr:
                            stderr_data = await sbctl_process.stderr.read()
                            process_output += stderr_data.decode("utf-8", errors="replace")
                    except Exception:
                        pass

                    error_msg = f"sbctl process exited with code {sbctl_process.returncode} before initialization completed"
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
                # IMPORTANT: Pass bundle_id for concurrent mode support
                api_check_attempts += 1
                if await self.check_api_server_available(bundle_id=bundle_id):
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
            if sbctl_process and sbctl_process.returncode is not None:
                # Process exited before kubeconfig was created
                if sbctl_process.returncode == 0:
                    # Process exited successfully - check if this is the "no cluster resources" case
                    try:
                        # Read any remaining stdout/stderr to check for the "no cluster resources" message
                        process_output = ""
                        if sbctl_process.stdout:
                            try:
                                stdout_data = await asyncio.wait_for(
                                    sbctl_process.stdout.read(), timeout=1.0
                                )
                                process_output += stdout_data.decode("utf-8", errors="replace")
                            except (asyncio.TimeoutError, Exception):
                                pass
                        if sbctl_process.stderr:
                            try:
                                stderr_data = await asyncio.wait_for(
                                    sbctl_process.stderr.read(), timeout=1.0
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
                cancel_requested = False
                if bundle_id in self.bundle_states:
                    cancel_requested = self.bundle_states[bundle_id].cancel_requested
                elif self._termination_requested:  # Fallback to legacy global flag
                    cancel_requested = True

                if sbctl_process.returncode == -15 and cancel_requested:
                    logger.debug(
                        f"[Bundle {bundle_id}] sbctl process was intentionally terminated during initialization"
                    )
                    return  # Exit gracefully without raising an error

                error_message = f"sbctl process exited with code {sbctl_process.returncode} before initialization completed"
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

    async def _monitor_sbctl_stderr(self) -> None:
        """
        Monitor sbctl process stderr output and maintain a rolling buffer.

        This task runs continuously while the sbctl process is active,
        capturing stderr output for crash diagnostics.
        """
        if not self.sbctl_process or not self.sbctl_process.stderr:
            return

        try:
            while self.sbctl_process.returncode is None:
                try:
                    # Read line from stderr with timeout
                    line = await asyncio.wait_for(self.sbctl_process.stderr.readline(), timeout=1.0)

                    if not line:
                        break

                    # Decode and store in rolling buffer
                    decoded_line = line.decode("utf-8", errors="replace").strip()
                    if decoded_line:
                        timestamp = datetime.datetime.now().isoformat()
                        self._stderr_buffer.append(f"[{timestamp}] {decoded_line}")
                        logger.debug(f"sbctl stderr: {decoded_line}")

                except asyncio.TimeoutError:
                    # Continue monitoring - timeouts are expected
                    continue
                except Exception as e:
                    logger.debug(f"Error monitoring sbctl stderr: {e}")
                    break

        except Exception as e:
            logger.debug(f"Stderr monitoring task stopped: {e}")

    async def _restart_sbctl_process(self) -> bool:
        """
        Restart the sbctl process after a crash.

        Returns:
            True if restart was successful, False otherwise
        """
        if not self.active_bundle:
            logger.error("Cannot restart sbctl: no active bundle")
            return False

        try:
            # IMPORTANT: Capture bundle info at start to prevent race condition
            # Don't read self.active_bundle_id later - it can change with concurrent workflows!
            bundle_to_restart = self.active_bundle
            bundle_id_to_restart = bundle_to_restart.id

            # Capture crash information
            exit_code = None
            if self.sbctl_process:
                exit_code = self.sbctl_process.returncode

            stderr_lines = list(self._stderr_buffer)[-20:]  # Last 20 lines

            # Store crash recovery info
            self._crash_recovery_info = {
                "timestamp": datetime.datetime.now().isoformat(),
                "exit_code": exit_code,
                "last_timeout_command": self._last_timeout_command,
                "stderr_lines": stderr_lines,
            }

            logger.warning(f"Restarting sbctl after crash (exit code: {exit_code})")

            # Clean up current process (use captured bundle_id to prevent race condition)
            await self._terminate_sbctl_process(bundle_id_to_restart)

            # Clear stderr buffer for fresh start
            self._stderr_buffer.clear()

            # Delete stale kubeconfig before restart
            # This ensures sbctl creates a fresh kubeconfig with the new port
            if bundle_to_restart.kubeconfig_path.exists():
                try:
                    logger.warning(
                        f"Deleting stale kubeconfig before sbctl restart: {bundle_to_restart.kubeconfig_path}"
                    )
                    bundle_to_restart.kubeconfig_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete stale kubeconfig (continuing anyway): {e}")
            else:
                logger.warning(
                    f"Skipping kubeconfig deletion (exists={bundle_to_restart.kubeconfig_path.exists()})"
                )

            # Restart sbctl with the same bundle (use captured bundle to prevent race condition)
            bundle_path = bundle_to_restart.path
            await self._start_sbctl_process(bundle_path)

            # Start stderr monitoring after successful restart
            self._start_stderr_monitoring()

            logger.info("sbctl process restarted successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to restart sbctl process: {e}")
            return False

    def record_timeout_command(self, command: str) -> None:
        """Record a command that timed out (potential crash trigger)."""
        self._last_timeout_command = command

    def get_crash_recovery_info(self) -> Optional[dict]:
        """Get crash recovery information if available."""
        recovery_info = self._crash_recovery_info
        self._crash_recovery_info = None  # Clear after retrieval
        return recovery_info

    async def _start_sbctl_process(
        self, bundle_path: Path, working_dir: Optional[Path] = None, bundle_id: Optional[str] = None
    ) -> None:
        """
        Start the sbctl process with the given bundle.

        Args:
            bundle_path: Path to the bundle to serve
            working_dir: Directory to run sbctl in (defaults to bundle path parent)
            bundle_id: Bundle ID to associate with this process (required for concurrent support)
        """
        # Determine output directory
        if working_dir:
            output_dir = working_dir
        else:
            output_dir = bundle_path.parent

        # sbctl chooses its own port (no --port flag available)
        # For concurrent support, sbctl will bind to different random ports automatically
        cmd = [
            "sbctl",
            "serve",
            "--support-bundle-location",
            str(bundle_path),
        ]

        logger.debug(f"Starting sbctl process for bundle {bundle_id}: {' '.join(cmd)}")

        # Start the process in its own process group for clean termination
        # This ensures child processes are also terminated when we stop sbctl
        import sys

        process_kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": str(output_dir),  # Set working directory for subprocess only (not process-wide)
        }

        # POSIX: use start_new_session=True
        # Windows: would use CREATE_NEW_PROCESS_GROUP (not implemented here)
        if sys.platform != "win32":
            process_kwargs["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(*cmd, **process_kwargs)

        # Store process in the per-bundle dict for concurrent support
        if bundle_id:
            self.sbctl_processes[bundle_id] = process
            logger.debug(f"Stored sbctl process for bundle {bundle_id} (PID: {process.pid})")

        # Also update legacy property for backward compatibility
        self.sbctl_process = process
        self._termination_requested = False

        # Don't start stderr monitoring immediately - it will be started after initialization
        if self._stderr_monitor_task:
            self._stderr_monitor_task.cancel()
            self._stderr_monitor_task = None

    def _find_available_port(self, start_port: int = 8080, max_attempts: int = 100) -> int:
        """Find an available port for sbctl to bind to (concurrent support).

        Args:
            start_port: Port to start searching from
            max_attempts: Maximum number of ports to try

        Returns:
            An available port number

        Raises:
            RuntimeError: If no available port found
        """
        for offset in range(max_attempts):
            port = start_port + offset
            try:
                # Try to bind to the port to check availability
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("localhost", port))
                    return port
            except OSError:
                # Port in use, try next
                continue
        raise RuntimeError(
            f"No available ports found in range {start_port}-{start_port + max_attempts}"
        )

    def _start_stderr_monitoring(self) -> None:
        """Start stderr monitoring after initialization is complete."""
        if self.sbctl_process and not self._stderr_monitor_task:
            self._stderr_monitor_task = asyncio.create_task(self._monitor_sbctl_stderr())

    async def _terminate_sbctl_process(self, bundle_id: Optional[str] = None) -> None:
        """
        Terminate sbctl process for a specific bundle.

        Args:
            bundle_id: Specific bundle ID to terminate. If None, terminates active bundle's process
                      (legacy behavior - prefer explicit bundle_id).

        Note:
            This method should be called OUTSIDE of state.lock to avoid deadlocks.
            The caller should set state.cancel_requested=True BEFORE calling this.
        """
        # Determine which bundle to terminate
        target_bundle_id = bundle_id or self.active_bundle_id
        if not target_bundle_id:
            logger.debug("No bundle to terminate")
            return

        # Mark cancel_requested in bundle state (if state exists)
        if target_bundle_id in self.bundle_states:
            state = self.bundle_states[target_bundle_id]
            async with state.lock:
                state.cancel_requested = True
                logger.debug(f"[Bundle {target_bundle_id}][Epoch {state.epoch}] cancel_requested=True")

        # Get the specific process for this bundle
        process = self.sbctl_processes.get(target_bundle_id)
        if not process:
            logger.debug(f"No sbctl process for bundle {target_bundle_id}")
            return

        # Cancel stderr monitoring task first (NOTE: still global, may need per-bundle)
        if self._stderr_monitor_task:
            self._stderr_monitor_task.cancel()
            try:
                await asyncio.wait_for(self._stderr_monitor_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._stderr_monitor_task = None

        # Legacy global flag (kept for backward compatibility with other code)
        self._termination_requested = True
        try:
            logger.debug(f"Terminating sbctl process for bundle {target_bundle_id}...")
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3.0)
                logger.debug(f"sbctl process for bundle {target_bundle_id} terminated gracefully")
            except (asyncio.TimeoutError, ProcessLookupError) as e:
                logger.warning(f"Failed to terminate sbctl process gracefully: {str(e)}")
                try:
                    logger.debug(f"Killing sbctl process for bundle {target_bundle_id}...")
                    process.kill()
                    logger.debug("sbctl process killed")
                except ProcessLookupError:
                    logger.debug("Process already gone when trying to kill")
        except Exception as e:
            logger.warning(f"Error during process termination: {str(e)}")

        # Remove from dict
        self.sbctl_processes.pop(target_bundle_id, None)

        # Check for any lingering mock_sbctl.pid file in the output directory
        # This helps us clean up in case the signal handling didn't work
        bundle_metadata = self.bundles.get(target_bundle_id)
        if bundle_metadata and bundle_metadata.path.exists():
            pid_file = bundle_metadata.path / "mock_sbctl.pid"
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

    async def _cleanup_bundle(self, bundle_id: str) -> None:
        """
        Clean up a specific bundle including its process and resources.

        This method follows proper lock discipline for concurrent execution:
        1. Acquire lock, mark as stopping, capture epoch/process
        2. Release lock, terminate process (long operation)
        3. Re-acquire lock, finalize if epoch matches

        Args:
            bundle_id: The bundle to clean up

        Note:
            This does NOT affect other bundles (unlike _cleanup_active_bundle).
            Only the specified bundle_id is cleaned up.
        """
        # Get state if it exists
        async with self._registry_lock:
            if bundle_id not in self.bundle_states:
                logger.debug(f"[Bundle {bundle_id}] No state to cleanup")
                return
            state = self.bundle_states[bundle_id]

        # Mark as stopping and capture process (under lock)
        proc_to_kill: Optional[asyncio.subprocess.Process] = None
        local_epoch: Optional[int] = None

        async with state.lock:
            logger.info(f"[Bundle {bundle_id}][Epoch {state.epoch}] Cleaning up bundle, status={state.status}")

            if state.status in ("stopped", "failed"):
                logger.debug(f"[Bundle {bundle_id}] Already stopped/failed, nothing to cleanup")
                return

            # Mark for termination
            state.cancel_requested = True
            state.status = "stopping"
            local_epoch = state.epoch
            proc_to_kill = state.process

        # Terminate process (outside lock - long operation)
        if proc_to_kill:
            logger.info(f"[Bundle {bundle_id}][Epoch {local_epoch}] Terminating sbctl process...")
            await self._terminate_sbctl_process(bundle_id)

        # Finalize state (under lock, check epoch)
        async with state.lock:
            if state.epoch == local_epoch:
                state.process = None
                state.status = "stopped"
                state.stopped_event.set()
                logger.info(f"[Bundle {bundle_id}][Epoch {local_epoch}] Cleanup complete, status=stopped")
            else:
                logger.info(
                    f"[Bundle {bundle_id}][Epoch {local_epoch}] Superseded during cleanup (current epoch: {state.epoch})"
                )

    async def _cleanup_active_bundle(self) -> None:
        """
        Clean up all bundles including processes and extracted directories.

        This method cleans up ALL bundles using the concurrent-safe bundle_states tracking.
        Legacy name kept for backward compatibility but behavior is now concurrent-safe.

        This method:
        1. Terminates sbctl processes for all bundles
        2. Removes extracted bundle directories
        3. Clears bundle state
        """
        logger.info("Cleaning up all bundles")
        for bundle_id in list(self.bundle_states.keys()):
            await self._cleanup_bundle(bundle_id)

        # Also clean up if active_bundle is set (legacy compatibility)
        if self.active_bundle:
            logger.info(f"Cleaning up active bundle: {self.active_bundle.id}")

            # 1. Stop the sbctl process for THIS bundle
            if self.active_bundle:
                await self._terminate_sbctl_process(self.active_bundle.id)

            # 2. Remove extracted bundle directories (ONLY if using temp storage)
            try:
                if self.active_bundle.path and self.active_bundle.path.exists():
                    # Don't delete bundles from persistent storage (MCP_BUNDLE_STORAGE)
                    # Only clean up temporary bundles
                    is_persistent_storage = os.getenv("MCP_BUNDLE_STORAGE") is not None
                    if is_persistent_storage:
                        logger.info(
                            f"Keeping bundle in persistent storage: {self.active_bundle.id}"
                        )
                        # Terminate sbctl but don't delete bundle files
                        self.active_bundle = None
                        return

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

    async def _health_probe_kubectl(self, kubeconfig_path: Path, timeout: float = 2.0) -> bool:
        """
        Probe kubectl API server health via quick version check.

        Phase 1: Simple health check to detect if sbctl is responding.
        Uses kubectl version command with short timeout to test API availability.

        Args:
            kubeconfig_path: Path to kubeconfig file
            timeout: Maximum time to wait for response (seconds)

        Returns:
            True if API server responds successfully, False otherwise
        """
        if not kubeconfig_path.exists():
            logger.debug(f"Kubeconfig does not exist: {kubeconfig_path}")
            return False

        try:
            # Use kubectl version with --client=false to test server connectivity
            process = await asyncio.create_subprocess_exec(
                "kubectl", "version", "--client=false",
                "--kubeconfig", str(kubeconfig_path),
                "--request-timeout", "1s",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )

            try:
                exit_code = await asyncio.wait_for(process.wait(), timeout=timeout)
                is_healthy = exit_code == 0
                logger.debug(f"Health probe for {kubeconfig_path.parent.name}: {'healthy' if is_healthy else 'unhealthy'}")
                return is_healthy
            except asyncio.TimeoutError:
                logger.debug(f"Health probe timeout for {kubeconfig_path.parent.name}")
                try:
                    process.kill()
                    await process.wait()
                except:
                    pass
                return False
        except Exception as e:
            logger.debug(f"Health probe error for {kubeconfig_path.parent.name}: {e}")
            return False

    async def cleanup_sbctl_for_bundle(self, bundle_id: str) -> bool:
        """
        Clean up sbctl process and bundle directory for a given bundle.

        Phase 1.5: Cleanup to prevent orphaned processes accumulating.
        Finds sbctl process by bundle path and terminates it gracefully.

        Args:
            bundle_id: Bundle ID to clean up

        Returns:
            True if cleanup succeeded, False otherwise
        """
        if bundle_id not in self.bundle_states:
            logger.warning(f"Cannot cleanup {bundle_id}: not found in bundle_states")
            return False

        state = self.bundle_states[bundle_id]
        if not state.metadata:
            logger.warning(f"Cannot cleanup {bundle_id}: no metadata")
            return False

        bundle_path = state.metadata.path
        logger.info(f"Cleaning up sbctl for bundle {bundle_id} at {bundle_path}")

        # Find sbctl process(es) serving this bundle
        killed_count = 0
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] == 'sbctl' and proc.info['cmdline']:
                        # Check if this sbctl is serving our bundle
                        cmdline = ' '.join(proc.info['cmdline'])
                        if str(bundle_path) in cmdline:
                            logger.info(f"Terminating sbctl process {proc.info['pid']} for {bundle_id}")
                            proc.terminate()

                            # Wait up to 5 seconds for graceful shutdown
                            try:
                                proc.wait(timeout=5)
                                logger.debug(f"sbctl process {proc.info['pid']} terminated gracefully")
                            except psutil.TimeoutExpired:
                                logger.warning(f"sbctl process {proc.info['pid']} did not terminate, killing")
                                proc.kill()
                                proc.wait(timeout=2)

                            killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            logger.error(f"Error finding/killing sbctl process for {bundle_id}: {e}")

        if killed_count > 0:
            logger.info(f"Killed {killed_count} sbctl process(es) for {bundle_id}")
        else:
            logger.debug(f"No sbctl processes found for {bundle_id}")

        # Remove from in-memory tracking
        if bundle_id in self.sbctl_processes:
            del self.sbctl_processes[bundle_id]
        if bundle_id in self.bundle_states:
            del self.bundle_states[bundle_id]

        # Optionally remove bundle directory (commented out for safety - can enable if desired)
        # try:
        #     if bundle_path.exists():
        #         shutil.rmtree(bundle_path)
        #         logger.info(f"Removed bundle directory: {bundle_path}")
        # except Exception as e:
        #     logger.warning(f"Failed to remove bundle directory {bundle_path}: {e}")

        return True

    async def check_api_server_available(self, bundle_id: Optional[str] = None) -> bool:
        """
        Check if the Kubernetes API server is available for a specific bundle.

        Phase 1: Uses kubeconfig file existence and health probe instead of process handle.
        This works across activity invocations in Temporal mode.

        Args:
            bundle_id: Bundle ID to check (required in concurrent mode)

        Returns:
            True if the API server is responding, False otherwise

        Raises:
            ValueError: If bundle_id is not provided
        """
        # Require bundle_id in concurrent mode
        if not bundle_id:
            raise ValueError(
                "bundle_id is required for API server availability check. "
                "In concurrent mode, all bundle operations must specify the bundle_id explicitly."
            )

        # Check if bundle exists
        if bundle_id not in self.bundle_states:
            logger.warning(f"Bundle {bundle_id} not found in bundle_states")
            return False

        state = self.bundle_states[bundle_id]
        if not state.metadata:
            logger.warning(f"Bundle {bundle_id} has no metadata")
            return False

        # Check for kubeconfig file in bundle directory
        kubeconfig_path = state.metadata.path / "kubeconfig"

        if not kubeconfig_path.exists():
            logger.debug(f"Kubeconfig not found for {bundle_id}: {kubeconfig_path}")
            return False

        # Health probe: test if API server is actually responding
        is_healthy = await self._health_probe_kubectl(kubeconfig_path)

        if is_healthy:
            logger.debug(f"API server healthy for {bundle_id}")
            return True
        else:
            logger.warning(f"API server not responding for {bundle_id}")
            return False

        # OLD COMPLEX IMPLEMENTATION REMOVED (270 lines)
        # - Process handle checking
        # - Auto-restart logic
        # - Port parsing from kubeconfig
        # - HTTP endpoint probing
        # All replaced with simple kubectl health probe above


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
        # Check if any bundles are initialized (concurrent mode) or active bundle (legacy mode)
        any_bundle_initialized = len(self.bundle_states) > 0 or (
            self.active_bundle is not None and self.active_bundle.initialized
        )

        diagnostics = {
            "sbctl_available": await self._check_sbctl_available(),
            "sbctl_process_running": self.sbctl_process is not None
            and self.sbctl_process.returncode is None,
            "api_server_available": await self.check_api_server_available(),
            "bundle_initialized": any_bundle_initialized,
            "system_info": await self._get_system_info(),
        }

        # Add active bundle info if available (legacy mode)
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

        The cleanup can be skipped by setting the PRESERVE_BUNDLES environment
        variable to "true". This is useful for debugging or testing scenarios
        where bundle preservation is needed.
        """
        logger.info("Performing complete cleanup during server shutdown")

        # Check if bundle preservation is enabled via environment variable
        preserve_bundles = os.environ.get("PRESERVE_BUNDLES", "false").lower() == "true"

        if preserve_bundles:
            logger.info("PRESERVE_BUNDLES is enabled, skipping bundle cleanup")
            return

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
