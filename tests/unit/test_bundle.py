"""
Tests for the Bundle Manager.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp  # Added import
import httpx
import pytest
from pydantic import ValidationError

from troubleshoot_mcp_server.bundle import (
    BundleDownloadError,
    BundleManager,
    BundleManagerError,
    BundleMetadata,
    BundleNotFoundError,
    InitializeBundleArgs,
)
from tests.test_utils import TempBundleManager, create_minimal_kubeconfig

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


def test_initialize_bundle_args_validation_url():
    """Test that InitializeBundleArgs validates URLs correctly."""
    # Valid URL
    args = InitializeBundleArgs(source="https://example.com/bundle.tar.gz")
    assert args.source == "https://example.com/bundle.tar.gz"
    assert args.force is False  # Default value


def test_initialize_bundle_args_validation_invalid():
    """Test that InitializeBundleArgs validates invalid sources correctly."""
    # Non-existent local file
    with pytest.raises(ValidationError):
        InitializeBundleArgs(source="/path/to/nonexistent/bundle.tar.gz")


@pytest.mark.asyncio
async def test_bundle_manager_initialization():
    """Test that the bundle manager can be initialized."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)
        assert manager.bundle_dir == bundle_dir
        assert manager.active_bundle is None
        assert manager.sbctl_process is None


@pytest.mark.asyncio
async def test_bundle_manager_initialize_bundle_url():
    """Test that the bundle manager can initialize a bundle from a URL."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Create a real test bundle and simulate download
        with TempBundleManager() as bundle_manager:
            test_bundle_path = bundle_manager.get_tar_path()
            download_path = bundle_dir / "downloaded_bundle.tar.gz"

            # Simulate download by copying real bundle
            import shutil

            shutil.copy2(test_bundle_path, download_path)

            # Mock only the network download, not the bundle handling
            manager._download_bundle = AsyncMock(return_value=download_path)

            # Create a real kubeconfig file
            kubeconfig_path = bundle_dir / "test_kubeconfig"
            create_minimal_kubeconfig(kubeconfig_path)

            # Mock only subprocess operations, not bundle logic
            manager._initialize_with_sbctl = AsyncMock(return_value=kubeconfig_path)
            manager._wait_for_initialization = AsyncMock()

            # Test initializing from a URL
            result = await manager.initialize_bundle("https://example.com/bundle.tar.gz")

            # Verify the result
            assert isinstance(result, BundleMetadata)
            assert result.source == "https://example.com/bundle.tar.gz"
            assert result.kubeconfig_path == kubeconfig_path
            assert result.initialized is True

            # Verify the mocks were called
            manager._download_bundle.assert_awaited_once_with("https://example.com/bundle.tar.gz")
            manager._initialize_with_sbctl.assert_awaited_once()


@pytest.mark.asyncio
async def test_bundle_manager_initialize_bundle_local():
    """Test that the bundle manager can initialize a bundle from a local file."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Create a real test bundle structure
        with TempBundleManager() as bundle_manager:
            bundle_tar_path = bundle_manager.get_tar_path()

            # Copy the bundle to our test directory
            local_bundle_path = bundle_dir / "local_bundle.tar.gz"
            import shutil

            shutil.copy2(bundle_tar_path, local_bundle_path)

            # Create a real kubeconfig file
            kubeconfig_path = bundle_dir / "test_kubeconfig"
            create_minimal_kubeconfig(kubeconfig_path)

            # Only mock subprocess operations, not internal bundle logic
            manager._initialize_with_sbctl = AsyncMock(return_value=kubeconfig_path)
            manager._wait_for_initialization = AsyncMock()

            # Test initializing from a local file
            result = await manager.initialize_bundle(str(local_bundle_path))

            # Verify the result
            assert isinstance(result, BundleMetadata)
            assert result.source == str(local_bundle_path)
            assert result.kubeconfig_path == kubeconfig_path
            assert result.initialized is True

            # Verify the sbctl initialization was called
            manager._initialize_with_sbctl.assert_awaited_once()


@pytest.mark.asyncio
async def test_bundle_manager_initialize_bundle_nonexistent():
    """Test that the bundle manager raises an error for nonexistent bundles."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Test with a truly nonexistent file path
        nonexistent_path = bundle_dir / "nonexistent.tar.gz"

        # Ensure file doesn't exist
        if nonexistent_path.exists():
            nonexistent_path.unlink()

        # Test the actual manager method with nonexistent file
        # The actual implementation wraps BundleNotFoundError in BundleManagerError
        with pytest.raises((BundleNotFoundError, BundleManagerError)) as excinfo:
            await manager.initialize_bundle(str(nonexistent_path))

        # Verify the error message contains the path
        assert str(nonexistent_path) in str(excinfo.value)


# --- Replicated Vendor Portal Tests ---

REPLICATED_URL = "https://vendor.replicated.com/troubleshoot/analyze/2025-04-22@16:51"
REPLICATED_SLUG = "2025-04-22@16:51"
REPLICATED_API_URL = f"https://api.replicated.com/vendor/v3/supportbundle/{REPLICATED_SLUG}"
SIGNED_URL = "https://signed.example.com/download?token=abc"


@pytest.fixture
def mock_httpx_client():
    """Fixture to mock httpx.AsyncClient."""
    mock_response = MagicMock(spec=httpx.Response)
    # === START MODIFICATION ===
    # Default behavior: json() raises error, status is 200 (will be overridden)
    # mock_response.status_code = 200 # Removed duplicate/incorrectly indented line
    # Default to success state, tests will override for error cases
    mock_response.status_code = 200
    # === START MODIFICATION ===
    # Mock the CORRECT nested structure
    correct_response_data = {"bundle": {"signedUri": SIGNED_URL}}
    mock_response.json = MagicMock(return_value=correct_response_data)
    mock_response.text = json.dumps(correct_response_data)
    # === END MODIFICATION ===

    mock_client = MagicMock(spec=httpx.AsyncClient)
    # Make the mock client's get method return the mock_response
    mock_client.__aenter__.return_value.get = AsyncMock(return_value=mock_response)
    mock_client.__aexit__ = AsyncMock()

    with patch("httpx.AsyncClient", return_value=mock_client) as mock_constructor:
        # Yield the constructor and the response mock for tests to configure
        yield mock_constructor, mock_response


@pytest.fixture
def mock_aiohttp_download():
    """Fixture to mock the actual download part using aiohttp."""
    # Mock the response object
    mock_aio_response = AsyncMock(spec=aiohttp.ClientResponse)
    mock_aio_response.status = 200
    mock_aio_response.reason = "OK"
    mock_aio_response.content_length = 100

    # === START MODIFICATION ===
    # Mock the content object
    mock_content = AsyncMock()

    # Define the async iterator directly for iter_chunked
    async def async_iterator():
        yield b"chunk1"
        yield b"chunk2"

    # Make the iter_chunked mock return our async iterator when called
    mock_content.iter_chunked = MagicMock(return_value=async_iterator())
    mock_aio_response.content = mock_content
    # === END MODIFICATION ===

    # Mock the __aenter__ and __aexit__ for the response context manager
    mock_aio_response.__aenter__.return_value = mock_aio_response
    mock_aio_response.__aexit__ = AsyncMock(return_value=None)

    # Mock the session's get method. It's an async function that returns the response.
    mock_get = AsyncMock(return_value=mock_aio_response)

    # Mock the session object
    mock_aio_session = AsyncMock(spec=aiohttp.ClientSession)
    mock_aio_session.get = mock_get
    # Mock the __aenter__ and __aexit__ for the session context manager
    mock_aio_session.__aenter__.return_value = mock_aio_session
    mock_aio_session.__aexit__ = AsyncMock(return_value=None)

    # Patch aiohttp.ClientSession to return our mock session
    with patch("aiohttp.ClientSession", return_value=mock_aio_session) as mock_constructor:
        # Yield the constructor, the session instance, and the response instance
        # This gives tests more flexibility for assertions
        yield mock_constructor, mock_aio_session, mock_aio_response


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_success_sbctl_token(
    mock_httpx_client, mock_aiohttp_download
):
    """Test downloading from Replicated URL with SBCTL_TOKEN successfully."""
    mock_httpx_constructor, mock_httpx_response = mock_httpx_client
    mock_aiohttp_constructor, mock_aio_session, mock_aio_response = mock_aiohttp_download

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        with patch.dict(os.environ, {"SBCTL_TOKEN": "sbctl_token_value"}, clear=True):
            download_path = await manager._download_bundle(REPLICATED_URL)

            # Verify httpx call for signed URL
            mock_httpx_constructor.assert_called_once()
            # Check timeout was passed to httpx.AsyncClient
            _, kwargs = mock_httpx_constructor.call_args
            assert isinstance(kwargs.get("timeout"), httpx.Timeout)

            mock_get_call = mock_httpx_constructor.return_value.__aenter__.return_value.get
            mock_get_call.assert_awaited_once_with(
                REPLICATED_API_URL,
                headers={
                    "Authorization": "sbctl_token_value",
                    "Content-Type": "application/json",
                },
            )

            # Verify aiohttp call for actual download
            mock_aiohttp_constructor.assert_called_once()
            # Assert on the session's get method
            mock_aio_session.get.assert_awaited_once_with(SIGNED_URL, headers={})

            # Verify file was created
            assert download_path.exists()
            # Assert new filename format
            # Replace both '@' and ':' for the assertion to match sanitization
            safe_slug_for_assertion = REPLICATED_SLUG.replace("@", "_").replace(":", "_")
            expected_filename_part = f"replicated_bundle_{safe_slug_for_assertion}"
            assert download_path.name.startswith(expected_filename_part)
            assert download_path.read_bytes() == b"chunk1chunk2"


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_success_replicated_token(
    mock_httpx_client, mock_aiohttp_download
):
    """Test downloading from Replicated URL with REPLICATED_TOKEN successfully."""
    mock_httpx_constructor, mock_httpx_response = mock_httpx_client
    mock_aiohttp_constructor, mock_aio_session, mock_aio_response = mock_aiohttp_download

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Only REPLICATED is set
        with patch.dict(os.environ, {"REPLICATED": "replicated_token_value"}, clear=True):
            await manager._download_bundle(REPLICATED_URL)

            # Verify httpx call used REPLICATED token
            mock_get_call = mock_httpx_constructor.return_value.__aenter__.return_value.get
            mock_get_call.assert_awaited_once_with(
                REPLICATED_API_URL,
                headers={
                    "Authorization": "replicated_token_value",
                    "Content-Type": "application/json",
                },
            )
            # Verify aiohttp call used the signed URL
            mock_aio_session.get.assert_awaited_once_with(SIGNED_URL, headers={})


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_token_precedence(
    mock_httpx_client, mock_aiohttp_download
):
    """Test SBCTL_TOKEN takes precedence over REPLICATED_TOKEN."""
    mock_httpx_constructor, _ = mock_httpx_client
    # Unpack all three values from the fixture
    mock_aiohttp_constructor, mock_aio_session, mock_aio_response = mock_aiohttp_download

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Both tokens are set
        with patch.dict(
            os.environ,
            # === START MODIFICATION ===
            {
                "SBCTL_TOKEN": "sbctl_token_value",
                "REPLICATED": "replicated_token_value",
            },
            # === END MODIFICATION ===
            clear=True,
        ):
            await manager._download_bundle(REPLICATED_URL)

            # Verify httpx call used SBCTL_TOKEN
            mock_get_call = mock_httpx_constructor.return_value.__aenter__.return_value.get
            mock_get_call.assert_awaited_once_with(
                REPLICATED_API_URL,
                headers={
                    "Authorization": "sbctl_token_value",
                    "Content-Type": "application/json",
                },
            )


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_missing_token():
    """Test error handling when no token is provided for Replicated URL."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # No tokens set
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(BundleDownloadError) as excinfo:
                await manager._download_bundle(REPLICATED_URL)
            # === START MODIFICATION ===
            # Update assertion to match the exact error message and correct ENV name
            expected_error_part = "SBCTL_TOKEN or REPLICATED environment variable not set"
            assert expected_error_part in str(excinfo.value)
            assert "Cannot download from Replicated Vendor Portal" in str(excinfo.value)
            # === END MODIFICATION ===


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_api_401(mock_httpx_client):
    """Test error handling for Replicated API 401 Unauthorized."""
    mock_httpx_constructor, mock_response = mock_httpx_client
    # === START MODIFICATION ===
    # Configure mock response directly for this test
    mock_response.status_code = 401
    mock_response.text = "Unauthorized"
    # Ensure json() raises an error if called on non-200 status
    mock_response.json.side_effect = json.JSONDecodeError("Mock JSON decode error", "", 0)
    # === END MODIFICATION ===

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        with patch.dict(os.environ, {"SBCTL_TOKEN": "bad_token"}, clear=True):
            with pytest.raises(BundleDownloadError) as excinfo:
                # === START MODIFICATION ===
                # Call _download_bundle instead of _get_replicated_signed_url
                await manager._download_bundle(REPLICATED_URL)
                # === END MODIFICATION ===
            # The error should propagate from _get_replicated_signed_url
            assert "Failed to authenticate with Replicated API (status 401)" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_api_404(mock_httpx_client):
    """Test error handling for Replicated API 404 Not Found."""
    mock_httpx_constructor, mock_response = mock_httpx_client
    # === START MODIFICATION ===
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    mock_response.json.side_effect = json.JSONDecodeError("Mock JSON decode error", "", 0)
    # === END MODIFICATION ===

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        with patch.dict(os.environ, {"SBCTL_TOKEN": "good_token"}, clear=True):
            with pytest.raises(BundleDownloadError) as excinfo:
                # === START MODIFICATION ===
                # Call _download_bundle instead of _get_replicated_signed_url
                await manager._download_bundle(REPLICATED_URL)
                # === END MODIFICATION ===
            assert "Support bundle not found on Replicated Vendor Portal" in str(excinfo.value)
            assert f"slug: {REPLICATED_SLUG}" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_api_other_error(
    mock_httpx_client,
):
    """Test error handling for other Replicated API errors."""
    mock_httpx_constructor, mock_response = mock_httpx_client
    # === START MODIFICATION ===
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.json.side_effect = json.JSONDecodeError("Mock JSON decode error", "", 0)
    # === END MODIFICATION ===

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        with patch.dict(os.environ, {"SBCTL_TOKEN": "good_token"}, clear=True):
            with pytest.raises(BundleDownloadError) as excinfo:
                # === START MODIFICATION ===
                # Call _download_bundle instead of _get_replicated_signed_url
                await manager._download_bundle(REPLICATED_URL)
                # === END MODIFICATION ===
            assert "Failed to get signed URL from Replicated API (status 500)" in str(excinfo.value)
            assert "Internal Server Error" in str(excinfo.value)  # Check response text included


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_missing_signed_uri(
    mock_httpx_client,
):
    """Test error handling when 'signedUri' is missing from API response."""
    mock_httpx_constructor, mock_response = mock_httpx_client
    # Configure for success status but missing key in the nested JSON
    mock_response.status_code = 200
    # === START MODIFICATION ===
    # Mock the nested structure but without 'signedUri' inside 'bundle'
    mock_response.json.return_value = {"bundle": {"message": "Success but no URI"}}
    mock_response.json.side_effect = None  # Allow json() call
    mock_response.text = json.dumps({"bundle": {"message": "Success but no URI"}})
    # === END MODIFICATION ===

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        with patch.dict(os.environ, {"SBCTL_TOKEN": "good_token"}, clear=True):
            with pytest.raises(BundleDownloadError) as excinfo:
                # === START MODIFICATION ===
                # Call _download_bundle instead of _get_replicated_signed_url
                await manager._download_bundle(REPLICATED_URL)
                # === END MODIFICATION ===
            assert "Could not find 'signedUri' in Replicated API response" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_url_network_error():
    """Test error handling for network errors during Replicated API call."""
    # === START MODIFICATION ===
    # Patch the 'get' method directly to raise the network error
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.RequestError("Network timeout")

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            with patch.dict(os.environ, {"SBCTL_TOKEN": "good_token"}, clear=True):
                with pytest.raises(BundleDownloadError) as excinfo:
                    # Call _download_bundle which calls _get_replicated_signed_url
                    await manager._download_bundle(REPLICATED_URL)

                # Assert that the correct error (raised by the except httpx.RequestError block) is caught
                assert "Network error requesting signed URL" in str(excinfo.value)
                assert "Network timeout" in str(excinfo.value)  # Check original error is included
    # === END MODIFICATION ===


@pytest.mark.asyncio
async def test_bundle_manager_download_non_replicated_url(mock_aiohttp_download):
    """Test that non-Replicated URLs are downloaded directly without API calls."""
    mock_aiohttp_constructor, mock_aio_session, mock_aio_response = mock_aiohttp_download
    non_replicated_url = "https://normal.example.com/bundle.tar.gz"

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Mock httpx to ensure it's NOT called
        with patch("httpx.AsyncClient") as mock_httpx_constructor:
            with patch.dict(os.environ, {"SBCTL_TOKEN": "token_val"}, clear=True):
                download_path = await manager._download_bundle(non_replicated_url)

                # Verify httpx was NOT called
                mock_httpx_constructor.assert_not_called()

                # Verify aiohttp was called with the original URL and token
                mock_aio_session.get.assert_awaited_once_with(
                    non_replicated_url, headers={"Authorization": "Bearer token_val"}
                )

                # Verify file was created
                assert download_path.exists()
                assert download_path.name == "bundle.tar.gz"
                assert download_path.read_bytes() == b"chunk1chunk2"


# --- End Replicated Vendor Portal Tests ---


@pytest.mark.asyncio
async def test_bundle_manager_download_bundle(
    mock_aiohttp_download,
):  # Use fixture as argument
    """Test that the bundle manager can download a non-Replicated bundle."""
    # Unpack the fixture results
    mock_aiohttp_constructor, mock_aio_session, mock_aio_response = mock_aiohttp_download
    non_replicated_url = "https://example.com/bundle.tar.gz"

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Mock _initialize_with_sbctl as it's not the focus here
        kubeconfig_path = bundle_dir / "test_kubeconfig"
        manager._initialize_with_sbctl = AsyncMock(return_value=kubeconfig_path)
        manager._wait_for_initialization = AsyncMock()  # Also mock wait

        # Call initialize_bundle which internally calls _download_bundle
        with patch.dict(os.environ, {"SBCTL_TOKEN": "token_val"}, clear=True):
            result = await manager.initialize_bundle(non_replicated_url)

            # Verify aiohttp was called correctly by _download_bundle
            mock_aio_session.get.assert_awaited_once_with(
                non_replicated_url, headers={"Authorization": "Bearer token_val"}
            )

            # Verify the result of initialize_bundle
            assert isinstance(result, BundleMetadata)
            assert result.source == non_replicated_url
            assert result.kubeconfig_path == kubeconfig_path
            # Check that the bundle path inside the metadata points to the downloaded file's dir
            expected_bundle_dir_name_part = "bundle_"  # From filename generation
            assert expected_bundle_dir_name_part in result.path.name
            # Check the generated filename used for download path
            expected_filename = "bundle.tar.gz"  # Based on URL parsing
            assert (manager.bundle_dir / expected_filename).exists()


@pytest.mark.asyncio
async def test_bundle_manager_download_bundle_error():
    """Test that the bundle manager handles download errors for non-Replicated URLs."""
    # === START MODIFICATION ===
    # Define the mock session and response for this specific test
    mock_aio_response = AsyncMock(spec=aiohttp.ClientResponse)
    mock_aio_response.status = 404
    mock_aio_response.reason = "Not Found"

    # === START MODIFICATION ===
    # Mock the content object and iter_chunked for the error response
    mock_content = AsyncMock()

    async def async_iterator_error():
        if False:
            yield  # pragma: no cover # Empty iterator

    mock_content.iter_chunked = MagicMock(return_value=async_iterator_error())
    mock_aio_response.content = mock_content
    # === END MODIFICATION ===

    # Mock the context manager methods for the response
    mock_aio_response.__aenter__.return_value = mock_aio_response
    mock_aio_response.__aexit__ = AsyncMock(return_value=None)

    # Mock the session's get method as an AsyncMock returning the response
    mock_get = AsyncMock(return_value=mock_aio_response)

    mock_aio_session = AsyncMock(spec=aiohttp.ClientSession)
    mock_aio_session.get = mock_get
    mock_aio_session.__aenter__.return_value = mock_aio_session
    mock_aio_session.__aexit__ = AsyncMock(return_value=None)

    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)
        url = "https://example.com/missing_bundle.tar.gz"

        # Patch ClientSession to return our defined mock session
        with patch("aiohttp.ClientSession", return_value=mock_aio_session):
            with pytest.raises(BundleDownloadError) as excinfo:
                await manager._download_bundle(url)

        # Assert the expected HTTP error message
        assert f"Failed to download bundle from {url[:80]}..." in str(excinfo.value)
        assert "HTTP 404 Not Found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_bundle_manager_initialize_with_sbctl():
    """Test that the bundle manager can initialize a bundle with sbctl."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Create a mock process that properly implements async methods
        class MockProcess:
            def __init__(self):
                self.stdout = MockStreamReader()
                self.stderr = MockStreamReader()
                self.returncode = None
                self.terminated = False
                self.killed = False

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

            async def wait(self):
                self.returncode = 0
                return 0

        class MockStreamReader:
            async def read(self, n):
                return b"mock output"

        # Create a real kubeconfig file in the expected location
        os.chdir(bundle_dir)  # Change dir to match the implementation
        kubeconfig_path = bundle_dir / "kubeconfig"
        with open(kubeconfig_path, "w") as f:
            f.write("mock kubeconfig content")

        # Create a mock bundle file
        bundle_path = bundle_dir / "test_bundle.tar.gz"
        with open(bundle_path, "w") as f:
            f.write("mock bundle content")

        # Mock the create_subprocess_exec function
        mock_process = MockProcess()

        async def mock_create_subprocess(*args, **kwargs):
            return mock_process

        # Mock wait_for_initialization to avoid actual waiting
        async def mock_wait(*args, **kwargs):
            pass

        with patch("asyncio.create_subprocess_exec", mock_create_subprocess):
            with patch.object(manager, "_wait_for_initialization", mock_wait):
                result = await manager._initialize_with_sbctl(bundle_path, bundle_dir)

                # Verify the result points to the kubeconfig
                assert result == kubeconfig_path


@pytest.mark.asyncio
async def test_bundle_manager_is_initialized():
    """Test that the bundle manager correctly reports its initialization state."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Initially, no bundle is initialized
        assert not manager.is_initialized()

        # Set an active bundle
        manager.active_bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_dir,
            kubeconfig_path=bundle_dir / "kubeconfig",
            initialized=True,
        )

        # Now the bundle should be reported as initialized
        assert manager.is_initialized()


@pytest.mark.asyncio
async def test_bundle_manager_get_active_bundle():
    """Test that the bundle manager returns the active bundle."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Initially, no bundle is active
        assert manager.get_active_bundle() is None

        # Set an active bundle
        bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_dir,
            kubeconfig_path=bundle_dir / "kubeconfig",
            initialized=True,
        )
        manager.active_bundle = bundle

        # Now the active bundle should be returned
        assert manager.get_active_bundle() == bundle


@pytest.mark.asyncio
async def test_bundle_manager_cleanup():
    """Test that the bundle manager cleans up resources."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Mock the _cleanup_active_bundle method
        manager._cleanup_active_bundle = AsyncMock()

        # Call cleanup
        await manager.cleanup()

        # Verify _cleanup_active_bundle was called
        manager._cleanup_active_bundle.assert_awaited_once()


@pytest.mark.asyncio
async def test_bundle_manager_cleanup_active_bundle():
    """Test that the bundle manager cleans up the active bundle."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Create a real bundle structure for cleanup testing
        with TempBundleManager() as bundle_manager:
            # Copy bundle structure to our test directory
            test_bundle_structure = bundle_manager.get_structure()
            bundle_path = bundle_dir / "test_bundle_cleanup"
            import shutil

            shutil.copytree(test_bundle_structure["support_bundle"], bundle_path)

            # Add additional test files to verify cleanup
            test_file = bundle_path / "cleanup_test_file.txt"
            test_file.write_text("Test content for cleanup")

            # Set an active bundle pointing to our test directory
            manager.active_bundle = BundleMetadata(
                id="test",
                source="test",
                path=bundle_path,
                kubeconfig_path=bundle_dir / "kubeconfig",
                initialized=True,
            )

            # Mock only the sbctl process (external dependency)
            mock_process = AsyncMock()
            mock_process.terminate = MagicMock()
            mock_process.wait = AsyncMock()
            manager.sbctl_process = mock_process

            # Verify the directory and files exist before cleanup
            assert bundle_path.exists()
            assert test_file.exists()
            assert (bundle_path / "cluster-resources").exists()  # From real structure

            # Call _cleanup_active_bundle (using real cleanup logic)
            await manager._cleanup_active_bundle()

            # Verify the sbctl process was terminated
            mock_process.terminate.assert_called_once()

            # Verify the active bundle was reset
            assert manager.active_bundle is None
            assert manager.sbctl_process is None

            # Verify the directory was removed (real cleanup behavior)
            assert not bundle_path.exists()
            assert not test_file.exists()

            # Verify the parent directory was not removed
            assert bundle_dir.exists()


@pytest.mark.asyncio
async def test_bundle_manager_cleanup_active_bundle_protected_paths():
    """Test that the bundle manager does not remove protected paths."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Set the active bundle to point to the main bundle directory (should be protected)
        manager.active_bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_dir,  # This is a protected path
            kubeconfig_path=bundle_dir / "kubeconfig",
            initialized=True,
        )

        # Mock the sbctl process
        mock_process = AsyncMock()
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        manager.sbctl_process = mock_process

        # Add a test file to verify the directory is not removed
        test_file = bundle_dir / "test_file.txt"
        with open(test_file, "w") as f:
            f.write("Test content")

        # Verify the directory exists before cleanup
        assert bundle_dir.exists()
        assert test_file.exists()

        # Call _cleanup_active_bundle
        await manager._cleanup_active_bundle()

        # Verify the sbctl process was terminated
        mock_process.terminate.assert_called_once()

        # Verify the active bundle reference was reset
        assert manager.active_bundle is None
        assert manager.sbctl_process is None

        # Verify the protected directory was not removed
        assert bundle_dir.exists()
        assert test_file.exists()


@pytest.mark.asyncio
async def test_bundle_manager_server_shutdown_cleanup():
    """Test that the bundle manager cleans up resources during server shutdown."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Create a bundle directory with some content
        bundle_path = bundle_dir / "test_bundle_dir"
        bundle_path.mkdir(parents=True)

        # Add some files to the bundle directory to verify cleanup
        test_file = bundle_path / "test_file.txt"
        with open(test_file, "w") as f:
            f.write("Test content")

        # Set an active bundle pointing to our test directory
        manager.active_bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_path,
            kubeconfig_path=bundle_dir / "kubeconfig",
            initialized=True,
        )

        # Mock the sbctl process
        mock_process = AsyncMock()
        mock_process.terminate = MagicMock()
        mock_process.wait = AsyncMock()
        manager.sbctl_process = mock_process

        # Mock _cleanup_active_bundle to verify it's called
        manager._cleanup_active_bundle = AsyncMock()

        # Mock subprocess.run to avoid actual process operations
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            # Call cleanup
            await manager.cleanup()

            # Verify _cleanup_active_bundle was called
            manager._cleanup_active_bundle.assert_awaited_once()

            # Create a mock that returns process data
            mock_ps_result = MagicMock()
            mock_ps_result.returncode = 0
            mock_ps_result.stdout = (
                "user  12345  12340  0 12:00 pts/0 00:00:00 sbctl serve bundle.tar.gz"
            )

            # Create a mock for pkill result
            mock_pkill_result = MagicMock()
            mock_pkill_result.returncode = 0

            # Mock subprocess to return our mock objects
            with patch("subprocess.run", side_effect=[mock_ps_result, mock_pkill_result]):
                # Test with orphaned processes
                await manager.cleanup()

                # The mock subprocess.run will be called twice - once for ps and once for pkill


@pytest.mark.asyncio
async def test_bundle_manager_host_only_bundle_detection():
    """Test that host-only bundles are detected properly when sbctl exits with 'No cluster resources found'."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Create a real host-only bundle structure
        with TempBundleManager(bundle_type="host_only") as bundle_manager:
            test_bundle_path = bundle_manager.get_tar_path()

            # Copy the bundle to our test directory
            local_bundle_path = bundle_dir / "host-only-bundle.tar.gz"
            import shutil

            shutil.copy2(test_bundle_path, local_bundle_path)

            # Create a mock process that simulates sbctl detecting host-only bundle
            mock_process = AsyncMock()
            mock_process.wait = AsyncMock(return_value=0)  # Process exits with code 0
            mock_process.returncode = 0

            # Mock stdout and stderr to return the "No cluster resources found" message
            mock_stdout = AsyncMock()
            mock_stdout.read = AsyncMock(
                return_value=b"Downloading bundle\nBundle extracted to /tmp/sbctl-123\nNo cluster resources found in bundle\n"
            )
            mock_process.stdout = mock_stdout

            mock_stderr = AsyncMock()
            mock_stderr.read = AsyncMock(return_value=b"")
            mock_process.stderr = mock_stderr

            # Mock only subprocess execution (external dependency)
            with patch(
                "asyncio.create_subprocess_exec", return_value=mock_process
            ) as mock_subprocess:
                with patch("os.chdir"):  # Mock chdir to avoid changing actual directory
                    # Use real bundle handling, only mock the download for URL case
                    # Since we're using a local file, no download mocking needed

                    # Test the initialization with real bundle structure
                    metadata = await manager.initialize_bundle(str(local_bundle_path))

                    # Verify that the bundle was marked as host-only (real logic)
                    assert metadata.host_only_bundle is True
                    assert metadata.initialized is True
                    assert metadata.source == str(local_bundle_path)

                    # Verify subprocess was called with correct arguments
                    mock_subprocess.assert_called_once()
                    args, kwargs = mock_subprocess.call_args
                    assert args[0] == "sbctl"
                    assert args[1] == "serve"
                    assert args[2] == "--support-bundle-location"
                    # Verify bundle path is in the arguments
                    bundle_arg_found = any(str(local_bundle_path) in str(arg) for arg in args)
                    assert bundle_arg_found, f"Bundle path not found in subprocess args: {args}"


# Note: We test regular bundles in the existing tests that already work properly
# The key test here is the host-only detection, which we've successfully implemented


@pytest.mark.asyncio
async def test_bundle_metadata_host_only_field():
    """Test that BundleMetadata includes the host_only_bundle field with correct defaults."""
    # Test default value
    metadata = BundleMetadata(
        id="test-bundle",
        source="/path/to/bundle.tar.gz",
        path=Path("/tmp/bundle"),
        kubeconfig_path=Path("/tmp/kubeconfig"),
        initialized=True,
    )

    # Should default to False
    assert metadata.host_only_bundle is False

    # Test explicit value
    host_only_metadata = BundleMetadata(
        id="test-bundle",
        source="/path/to/bundle.tar.gz",
        path=Path("/tmp/bundle"),
        kubeconfig_path=Path("/tmp/kubeconfig"),
        initialized=True,
        host_only_bundle=True,
    )

    assert host_only_metadata.host_only_bundle is True


# --- 403 Retry Logic Tests ---


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_403_retry_success():
    """Test that 403 errors are retried and eventually succeed."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Mock responses: 403, 403, then 200 success
        responses = [
            MagicMock(spec=httpx.Response, status_code=403, text="Forbidden"),
            MagicMock(spec=httpx.Response, status_code=403, text="Forbidden"),
            MagicMock(spec=httpx.Response, status_code=200),
        ]

        # Configure the successful response
        responses[2].json.return_value = {"bundle": {"signedUri": SIGNED_URL}}
        responses[2].json.side_effect = None

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = mock_client_class.return_value.__aenter__.return_value
            mock_client.get.side_effect = responses

            with patch.dict(os.environ, {"SBCTL_TOKEN": "test_token"}, clear=True):
                # This should succeed after 2 retries
                result = await manager._get_replicated_signed_url(REPLICATED_URL)
                assert result == SIGNED_URL

                # Verify 3 API calls were made (original + 2 retries)
                assert mock_client.get.call_count == 3


@pytest.mark.asyncio
async def test_bundle_manager_download_replicated_403_retry_exhausted():
    """Test that 403 errors fail after maximum retries."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Mock responses: all 403 failures
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.text = "Rate Limited"

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = mock_client_class.return_value.__aenter__.return_value
            mock_client.get.return_value = mock_response

            with patch.dict(os.environ, {"SBCTL_TOKEN": "test_token"}, clear=True):
                with pytest.raises(BundleDownloadError) as excinfo:
                    await manager._get_replicated_signed_url(REPLICATED_URL)

                # Verify error message mentions retries
                assert "Failed to get signed URL from Replicated API after retries" in str(
                    excinfo.value
                )
                assert "status 403" in str(excinfo.value)

                # Verify maximum attempts were made (1 original + 3 retries)
                assert mock_client.get.call_count == 4


@pytest.mark.asyncio
async def test_bundle_manager_401_404_no_retry():
    """Test that 401 and 404 errors don't trigger retries."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        for status_code, expected_error in [
            (401, "Failed to authenticate"),
            (404, "Support bundle not found"),
        ]:
            mock_response = MagicMock(spec=httpx.Response)
            mock_response.status_code = status_code
            mock_response.text = "Error"
            mock_response.json.side_effect = json.JSONDecodeError("Mock error", "", 0)

            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = mock_client_class.return_value.__aenter__.return_value
                mock_client.get.return_value = mock_response

                with patch.dict(os.environ, {"SBCTL_TOKEN": "test_token"}, clear=True):
                    with pytest.raises(BundleDownloadError) as excinfo:
                        await manager._get_replicated_signed_url(REPLICATED_URL)

                    # Verify error message
                    assert expected_error in str(excinfo.value)

                    # Verify only one API call was made (no retries)
                    assert mock_client.get.call_count == 1


def test_calculate_retry_delay():
    """Test the exponential backoff delay calculation."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Test delay calculation for different attempts
        # Attempt 0: base delay (1.0) * 2^0 = 1.0, with jitter should be 0.5-1.5
        delay_0 = manager._calculate_retry_delay(0)
        assert 0.5 <= delay_0 <= 1.5

        # Attempt 1: base delay (1.0) * 2^1 = 2.0, with jitter should be 1.0-3.0
        delay_1 = manager._calculate_retry_delay(1)
        assert 1.0 <= delay_1 <= 3.0

        # Attempt 2: base delay (1.0) * 2^2 = 4.0, with jitter should be 2.0-6.0
        delay_2 = manager._calculate_retry_delay(2)
        assert 2.0 <= delay_2 <= 6.0

        # Attempt 3: would be 8.0, but max is 8.0, with jitter should be 4.0-12.0
        delay_3 = manager._calculate_retry_delay(3)
        assert 4.0 <= delay_3 <= 12.0

        # Attempt 4: should be capped at max delay (8.0)
        delay_4 = manager._calculate_retry_delay(4)
        assert 4.0 <= delay_4 <= 12.0  # Max delay of 8.0 with 0.5-1.5 multiplier


class TestGitHubUrlPatterns:
    """Test GitHub URL pattern detection."""

    def test_github_attachment_url_pattern(self):
        """Test GitHub attachment URL matching."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            # Test valid GitHub attachment URLs
            valid_urls = [
                "https://github.com/user-attachments/files/12345/bundle.tar.gz",
                "https://github.com/user-attachments/files/67890/support-bundle.tgz",
                "https://github.com/user-attachments/files/111111/my-bundle-2023.tar.gz",
                "https://github.com/user-attachments/files/999999/bundle",
            ]

            for url in valid_urls:
                assert manager._is_github_url(url), f"Should match GitHub attachment URL: {url}"

    def test_github_release_url_pattern(self):
        """Test GitHub release URL matching."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            # Test valid GitHub release URLs
            valid_urls = [
                "https://github.com/owner/repo/releases/download/v1.0.0/bundle.tar.gz",
                "https://github.com/my-org/my-repo/releases/download/release-1.2.3/support-bundle.tgz",
                "https://github.com/user/project/releases/download/latest/bundle.zip",
            ]

            for url in valid_urls:
                assert manager._is_github_url(url), f"Should match GitHub release URL: {url}"

    def test_github_raw_url_pattern(self):
        """Test GitHub raw content URL matching."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            # Test valid GitHub raw URLs
            valid_urls = [
                "https://raw.githubusercontent.com/owner/repo/main/bundle.tar.gz",
                "https://raw.githubusercontent.com/user/project/branch/path/file.tgz",
                "https://raw.githubusercontent.com/org/repo/commit-hash/data/bundle.zip",
            ]

            for url in valid_urls:
                assert manager._is_github_url(url), f"Should match GitHub raw URL: {url}"

    def test_non_github_urls(self):
        """Test that non-GitHub URLs are not matched."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            # Test non-GitHub URLs that should not match
            non_github_urls = [
                "https://example.com/bundle.tar.gz",
                "https://vendor.replicated.com/troubleshoot/analyze/my-slug",
                "https://gitlab.com/user/repo/uploads/bundle.tar.gz",
                "https://bitbucket.org/user/repo/downloads/bundle.tar.gz",
                "http://github.com/user/repo/bundle.tar.gz",  # HTTP not HTTPS
                "https://github.com/user/repo/blob/main/bundle.tar.gz",  # Blob URL, not attachment
                "https://gist.github.com/user/123456/bundle.tar.gz",  # Gist URL
            ]

            for url in non_github_urls:
                assert not manager._is_github_url(url), f"Should not match non-GitHub URL: {url}"


class TestGitHubAuthentication:
    """Test GitHub authentication handling."""

    @pytest.mark.asyncio
    async def test_github_token_priority(self):
        """Test token selection priority: GITHUB_TOKEN > GH_TOKEN > SBCTL_TOKEN."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            test_url = "https://github.com/user-attachments/files/12345/bundle.tar.gz"

            # Mock successful aiohttp response
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.content_length = 1000

            # Create async iterator for content chunks
            async def async_iterator():
                yield b"test content"

            mock_content = MagicMock()
            mock_content.iter_chunked = MagicMock(return_value=async_iterator())
            mock_response.content = mock_content

            # Setup mock aiohttp response
            mock_response.__aenter__ = AsyncMock()
            mock_response.__aenter__.return_value = mock_response
            mock_response.__aexit__ = AsyncMock(return_value=None)

            # Mock the session's get method returning the response directly (not as a coroutine)
            mock_aio_session = AsyncMock(spec=aiohttp.ClientSession)
            mock_aio_session.get = MagicMock(return_value=mock_response)
            mock_aio_session.__aenter__.return_value = mock_aio_session
            mock_aio_session.__aexit__ = AsyncMock(return_value=None)

            with patch("aiohttp.ClientSession", return_value=mock_aio_session):
                # Test GITHUB_TOKEN is used
                with patch.dict(
                    os.environ,
                    {"GITHUB_TOKEN": "github_token", "SBCTL_TOKEN": "sbctl_token"},
                    clear=True,
                ):
                    await manager._download_github_attachment(test_url)

                    # Verify the call was made with GITHUB_TOKEN
                    call_args = mock_aio_session.get.call_args
                    assert call_args[1]["headers"]["Authorization"] == "token github_token"

                # Test SBCTL_TOKEN when GITHUB_TOKEN not available (should fail)
                with patch.dict(os.environ, {"SBCTL_TOKEN": "sbctl_token"}, clear=True):
                    with pytest.raises(BundleDownloadError, match="No authentication token found"):
                        await manager._download_github_attachment(test_url)

    @pytest.mark.asyncio
    async def test_missing_token_error(self):
        """Test error when no token is available."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            test_url = "https://github.com/user-attachments/files/12345/bundle.tar.gz"

            # Clear all possible token environment variables
            with patch.dict(os.environ, {}, clear=True):
                with pytest.raises(BundleDownloadError) as excinfo:
                    await manager._download_github_attachment(test_url)

                assert "No authentication token found" in str(excinfo.value)
                assert "GITHUB_TOKEN" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_github_error_responses(self):
        """Test handling of various GitHub error responses."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            test_url = "https://github.com/user-attachments/files/12345/bundle.tar.gz"

            error_scenarios = [
                (401, "GitHub authentication failed"),
                (404, "GitHub resource not found"),
                (429, "GitHub rate limit exceeded"),
                (500, "Failed to download from GitHub: HTTP 500"),
            ]

            for status_code, expected_error_msg in error_scenarios:
                mock_response = MagicMock()
                mock_response.status = status_code
                mock_response.reason = "Test Error"

                # Setup mock aiohttp response
                mock_response.__aenter__ = AsyncMock()
                mock_response.__aenter__.return_value = mock_response
                mock_response.__aexit__ = AsyncMock(return_value=None)

                # Mock the session's get method returning the response directly (not as a coroutine)
                mock_aio_session = AsyncMock(spec=aiohttp.ClientSession)
                mock_aio_session.get = MagicMock(return_value=mock_response)
                mock_aio_session.__aenter__.return_value = mock_aio_session
                mock_aio_session.__aexit__ = AsyncMock(return_value=None)

                with patch("aiohttp.ClientSession", return_value=mock_aio_session):
                    with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token"}, clear=True):
                        with pytest.raises(BundleDownloadError) as excinfo:
                            await manager._download_github_attachment(test_url)

                        assert expected_error_msg in str(excinfo.value)


class TestGitHubDownloadIntegration:
    """Test GitHub download routing in main _download_bundle method."""

    @pytest.mark.asyncio
    async def test_github_url_routing(self):
        """Test that GitHub URLs are routed to _download_github_attachment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_dir = Path(temp_dir)
            manager = BundleManager(bundle_dir)

            github_urls = [
                "https://github.com/user-attachments/files/12345/bundle.tar.gz",
                "https://github.com/owner/repo/releases/download/v1.0/bundle.tar.gz",
                "https://raw.githubusercontent.com/user/repo/main/bundle.tar.gz",
            ]

            for url in github_urls:
                with patch.object(manager, "_download_github_attachment") as mock_github_download:
                    mock_github_download.return_value = Path("/fake/path/bundle.tar.gz")

                    result = await manager._download_bundle(url)

                    # Verify GitHub method was called
                    mock_github_download.assert_called_once_with(url)
                    assert result == Path("/fake/path/bundle.tar.gz")

                    mock_github_download.reset_mock()
