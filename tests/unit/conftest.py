"""
Configuration for unit tests including async test support.
"""

import os
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from typing import Any, Dict, List, Optional
import asyncio

# Helper functions for async tests are defined in the main conftest.py


# Define test assertion helpers
class TestAssertions:
    """
    Collection of reusable test assertion helpers for common patterns in tests.

    These utilities make assertions more consistent, reduce duplication, and
    provide better error messages when tests fail.
    """

    @staticmethod
    def assert_attributes_exist(obj: Any, attributes: List[str]) -> None:
        """
        Assert that an object has all the specified attributes.

        Args:
            obj: The object to check
            attributes: List of attribute names to verify

        Raises:
            AssertionError: If any attribute is missing
        """
        for attr in attributes:
            assert hasattr(obj, attr), f"Object should have attribute '{attr}'"

    @staticmethod
    def assert_api_response_valid(
        response: List[Any],
        expected_type: str = "text",
        contains: Optional[List[str]] = None,
    ) -> None:
        """
        Assert that an MCP API response is valid and contains expected content.

        Args:
            response: The API response to check
            expected_type: Expected response type (e.g., 'text')
            contains: List of strings that should be in the response text

        Raises:
            AssertionError: If response is invalid or missing expected content
        """
        assert isinstance(response, list), "Response should be a list"
        assert len(response) > 0, "Response should not be empty"
        assert hasattr(response[0], "type"), "Response item should have 'type' attribute"
        assert response[0].type == expected_type, f"Response type should be '{expected_type}'"

        if contains and hasattr(response[0], "text"):
            for text in contains:
                assert text in response[0].text, f"Response should contain '{text}'"

    @staticmethod
    def assert_object_matches_attrs(obj: Any, expected_attrs: Dict[str, Any]) -> None:
        """
        Assert that an object has attributes matching expected values.

        Args:
            obj: The object to check
            expected_attrs: Dictionary of attribute names and expected values

        Raises:
            AssertionError: If any attribute doesn't match the expected value
        """
        for attr, expected in expected_attrs.items():
            assert hasattr(obj, attr), f"Object should have attribute '{attr}'"
            actual = getattr(obj, attr)
            assert actual == expected, (
                f"Attribute '{attr}' value mismatch. Expected: {expected}, Got: {actual}"
            )

    @staticmethod
    async def assert_asyncio_timeout(coro, timeout: float = 0.1) -> None:
        """
        Assert that an async coroutine times out.

        Args:
            coro: Coroutine to execute
            timeout: Timeout in seconds

        Raises:
            AssertionError: If the coroutine doesn't time out
        """
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(coro, timeout=timeout)


# Make assertions available as a fixture
@pytest.fixture
def test_assertions() -> TestAssertions:
    """
    Provides test assertion helpers for common test patterns.

    These helpers improve test readability and consistency.
    """
    return TestAssertions


# Factory functions for creating test objects
class TestFactory:
    """
    Factory functions for creating test objects with minimal boilerplate.

    These factories create common test objects with default values,
    allowing tests to focus on the specific values that matter for that test.
    """

    @staticmethod
    def create_bundle_metadata(
        id: str = "test_bundle",
        source: str = "test_source",
        path: Optional[Path] = None,
        kubeconfig_path: Optional[Path] = None,
        initialized: bool = True,
        tmp_path: Optional[Path] = None,
    ):
        """
        Create a BundleMetadata instance with sensible defaults.

        Args:
            id: Bundle ID
            source: Bundle source
            path: Bundle path
            kubeconfig_path: Path to kubeconfig
            initialized: Whether the bundle is initialized
            tmp_path: Temporary path for creating bundle directory

        Returns:
            BundleMetadata instance
        """
        from troubleshoot_mcp_server.bundle import BundleMetadata

        if path is None:
            if tmp_path is None:
                raise ValueError("Either path or tmp_path must be provided")
            path = tmp_path / "test_bundle"
            path.mkdir(parents=True, exist_ok=True)

        if kubeconfig_path is None:
            kubeconfig_path = path / "kubeconfig"
            # Create the kubeconfig file if it doesn't exist
            if not kubeconfig_path.exists():
                kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
                with open(kubeconfig_path, "w") as f:
                    f.write(
                        '{"apiVersion": "v1", "clusters": [{"cluster": {"server": "http://localhost:8001"}}]}'
                    )

        return BundleMetadata(
            id=id,
            source=source,
            path=path,
            kubeconfig_path=kubeconfig_path,
            initialized=initialized,
        )

    @staticmethod
    def create_kubectl_result(
        command: str = "get pods",
        exit_code: int = 0,
        stdout: str = '{"items": []}',
        stderr: str = "",
        is_json: bool = True,
        duration_ms: int = 100,
    ):
        """
        Create a KubectlResult instance with sensible defaults.

        Args:
            command: The kubectl command
            exit_code: Command exit code
            stdout: Command standard output
            stderr: Command standard error
            is_json: Whether the output is JSON
            duration_ms: Command execution duration

        Returns:
            KubectlResult instance
        """
        from troubleshoot_mcp_server.kubectl import KubectlResult

        # Process output based on is_json
        output = stdout
        if is_json and stdout:
            import json

            try:
                output = json.loads(stdout)
            except json.JSONDecodeError:
                output = stdout
                is_json = False

        return KubectlResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            output=output,
            is_json=is_json,
            duration_ms=duration_ms,
        )


@pytest.fixture
def test_factory(tmp_path) -> TestFactory:
    """
    Provides factory functions for creating common test objects.

    These factories reduce boilerplate in tests and ensure consistency
    in test object creation.
    """
    # Inject tmp_path into the factory for bundle metadata creation
    original_create_bundle_metadata = TestFactory.create_bundle_metadata

    def create_bundle_metadata_with_path(*args, **kwargs):
        if "tmp_path" not in kwargs and "path" not in kwargs:
            kwargs["tmp_path"] = tmp_path
        return original_create_bundle_metadata(*args, **kwargs)

    factory = TestFactory()
    factory.create_bundle_metadata = create_bundle_metadata_with_path
    return factory


@pytest.fixture
def error_setup(tmp_path):
    """
    Fixture for testing error scenarios with standard error conditions.

    This fixture provides a controlled environment for testing error
    handling without requiring each test to set up common error conditions.

    Returns:
        Dictionary with common error scenarios and mock objects
    """
    # Create test directory
    temp_dir = tmp_path / "error_setup"
    temp_dir.mkdir(exist_ok=True)

    # Set up non-existent paths
    nonexistent_path = temp_dir / "nonexistent"

    # Create a directory (not a file)
    directory_path = temp_dir / "directory"
    directory_path.mkdir()

    # Create an empty file
    empty_file = temp_dir / "empty.txt"
    empty_file.touch()

    # Create a text file
    text_file = temp_dir / "text.txt"
    text_file.write_text("This is a text file\nwith multiple lines\nfor testing errors")

    # Create a binary file
    binary_file = temp_dir / "binary.dat"
    with open(binary_file, "wb") as f:
        f.write(b"\x00\x01\x02\x03")

    # Create a mock bundle manager that returns None for active bundle
    from troubleshoot_mcp_server.bundle import BundleManager

    no_bundle_manager = Mock(spec=BundleManager)
    no_bundle_manager.get_active_bundle.return_value = None

    # Create a mock process that fails
    error_process = AsyncMock()
    error_process.returncode = 1
    error_process.communicate = AsyncMock(return_value=(b"", b"Command failed with an error"))

    # Create a mock asyncio client session with errors
    error_session = AsyncMock()
    error_session.get = AsyncMock(side_effect=Exception("Connection error"))

    return {
        "temp_dir": temp_dir,
        "nonexistent_path": nonexistent_path,
        "directory_path": directory_path,
        "empty_file": empty_file,
        "text_file": text_file,
        "binary_file": binary_file,
        "no_bundle_manager": no_bundle_manager,
        "error_process": error_process,
        "error_session": error_session,
        # Error classes for common exceptions in the app
        "error_classes": {
            "BundleNotFoundError": "troubleshoot_mcp_server.bundle.BundleNotFoundError",
            "BundleDownloadError": "troubleshoot_mcp_server.bundle.BundleDownloadError",
            "KubectlError": "troubleshoot_mcp_server.kubectl.KubectlError",
            "PathNotFoundError": "troubleshoot_mcp_server.files.PathNotFoundError",
            "ReadFileError": "troubleshoot_mcp_server.files.ReadFileError",
            "InvalidPathError": "troubleshoot_mcp_server.files.InvalidPathError",
        },
    }


@pytest.fixture
def fixtures_dir() -> Path:
    """
    Returns the path to the test fixtures directory.
    """
    return Path(__file__).parent.parent / "fixtures"


@pytest_asyncio.fixture
async def mock_command_environment(fixtures_dir, tmp_path):
    """
    Creates a test environment with mock sbctl and kubectl binaries.

    This fixture:
    1. Creates a temporary directory for the environment
    2. Sets up mock sbctl and kubectl scripts
    3. Adds the mock binaries to PATH
    4. Yields the temp directory and restores PATH after test

    Args:
        fixtures_dir: Path to the test fixtures directory (pytest fixture)
        tmp_path: Temporary path for creating environment directory

    Returns:
        A tuple of (temp_dir, old_path) for use in tests
    """
    # Create a temporary directory for the environment
    temp_dir = tmp_path / "mock_command_env"
    temp_dir.mkdir(exist_ok=True)

    # Set up mock sbctl and kubectl
    mock_sbctl_path = fixtures_dir / "mock_sbctl.py"
    mock_kubectl_path = fixtures_dir / "mock_kubectl.py"
    temp_bin_dir = temp_dir / "bin"
    temp_bin_dir.mkdir(exist_ok=True)

    # Create sbctl mock
    sbctl_link = temp_bin_dir / "sbctl"
    with open(sbctl_link, "w") as f:
        f.write(
            f"""#!/bin/bash
python "{mock_sbctl_path}" "$@"
"""
        )
    os.chmod(sbctl_link, 0o755)

    # Create kubectl mock
    kubectl_link = temp_bin_dir / "kubectl"
    with open(kubectl_link, "w") as f:
        f.write(
            f"""#!/bin/bash
python "{mock_kubectl_path}" "$@"
"""
        )
    os.chmod(kubectl_link, 0o755)

    # Add mock tools to PATH
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{temp_bin_dir}:{old_path}"

    try:
        yield temp_dir
    finally:
        # Restore the PATH
        os.environ["PATH"] = old_path
        # Cleanup is handled automatically by pytest tmp_path


@pytest_asyncio.fixture
async def mock_bundle_manager(fixtures_dir, tmp_path):
    """
    Creates a mock BundleManager with controlled behavior.

    This fixture provides a consistent mock for tests that need
    a BundleManager but don't need to test its real functionality.

    Args:
        fixtures_dir: Path to the test fixtures directory (pytest fixture)
        tmp_path: Temporary path for creating mock bundle directory

    Returns:
        A Mock object with the BundleManager interface
    """
    from troubleshoot_mcp_server.bundle import BundleManager, BundleMetadata

    # Create a mock bundle manager
    mock_manager = Mock(spec=BundleManager)

    # Set up common attributes
    temp_dir = tmp_path / "mock_bundle"
    temp_dir.mkdir(exist_ok=True)
    mock_bundle = BundleMetadata(
        id="test_bundle",
        source="test_source",
        path=temp_dir,
        kubeconfig_path=temp_dir / "kubeconfig",
        initialized=True,
    )

    # Create a mock kubeconfig
    with open(mock_bundle.kubeconfig_path, "w") as f:
        f.write(
            '{"apiVersion": "v1", "clusters": [{"cluster": {"server": "http://localhost:8001"}}]}'
        )

    # Set up mock methods
    mock_manager.get_active_bundle.return_value = mock_bundle
    mock_manager.is_initialized.return_value = True
    mock_manager.check_api_server_available = AsyncMock(return_value=True)
    mock_manager.get_diagnostic_info = AsyncMock(
        return_value={
            "api_server_available": True,
            "bundle_initialized": True,
            "sbctl_available": True,
            "sbctl_process_running": True,
        }
    )

    yield mock_manager
    # Cleanup is handled automatically by pytest tmp_path


@pytest.fixture
def test_file_setup(tmp_path):
    """
    Creates a test directory with a variety of files for testing file operations.

    This fixture:
    1. Creates a temporary directory with subdirectories
    2. Populates it with different types of files (text, binary)
    3. Cleans up automatically after the test

    Returns:
        Path to the test directory
    """
    # Create a test directory
    test_dir = tmp_path / "test_files"
    test_dir.mkdir(exist_ok=True)

    # Create subdirectories
    dir1 = test_dir / "dir1"
    dir1.mkdir()

    dir2 = test_dir / "dir2"
    dir2.mkdir()
    subdir = dir2 / "subdir"
    subdir.mkdir()

    # Create text files
    file1 = dir1 / "file1.txt"
    file1.write_text("This is file 1\nLine 2\nLine 3\n")

    file2 = dir1 / "file2.txt"
    file2.write_text("This is file 2\nWith some content\n")

    file3 = subdir / "file3.txt"
    file3.write_text("This is file 3\nIn a subdirectory\n")

    # Create a file with specific search patterns
    search_file = dir1 / "search.txt"
    search_file.write_text(
        "This file contains search patterns\n"
        "UPPERCASE text for case sensitivity tests\n"
        "lowercase text for the same\n"
        "Multiple instances of the word pattern\n"
        "pattern appears again here\n"
    )

    # Create a binary file
    binary_file = test_dir / "binary_file"
    with open(binary_file, "wb") as f:
        f.write(b"\x00\x01\x02\x03\x04\x05")

    # Return the test directory
    yield test_dir
    # Cleanup is handled automatically by pytest tmp_path
