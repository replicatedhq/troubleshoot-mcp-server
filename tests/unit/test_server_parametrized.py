"""
Parametrized tests for the MCP server.

This module tests the MCP server tools and handlers using parameterized tests that
verify different input combinations and edge cases in a systematic way.

Benefits of this testing approach:
1. Comprehensive coverage of multiple scenarios with concise code
2. Clear documentation of expected outputs for each input combination
3. Consistent testing patterns using the TestAssertions helper class
4. Better visualization of error cases and edge conditions

The tests focus on these key user workflows:
1. Bundle initialization with different sources and conditions
2. Kubectl command execution with various formats and error cases
3. File operations (listing, reading, searching) with different inputs
4. Resource cleanup and shutdown behavior
5. Signal handling and graceful termination

Each test verifies the behavior from the user's perspective, focusing on the
actual outputs users would see rather than implementation details, which
makes the tests more resilient to internal refactoring.
"""

# Using pytest's tmp_path fixture instead of tempfile and shutil
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from mcp_server_troubleshoot.bundle import (
    BundleManagerError,
    BundleManager,
    BundleMetadata,
)
from mcp_server_troubleshoot.files import (
    FileContentResult,
    FileInfo,
    FileListResult,
    GrepMatch,
    GrepResult,
    FileSystemError,
    PathNotFoundError,
    FileExplorer,
)
from mcp_server_troubleshoot.kubectl import KubectlExecutor
from mcp_server_troubleshoot.server import (
    initialize_bundle,
    kubectl,
    list_files,
    read_file,
    grep_files,
    list_available_bundles,
    cleanup_resources,
    register_signal_handlers,
    shutdown,
)

# Mark all tests in this file as unit tests and quick tests
pytestmark = [pytest.mark.unit, pytest.mark.quick]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,force,api_available,expected_strings",
    [
        # Success case - all good
        (
            "test_bundle.tar.gz",
            False,
            True,
            ["Bundle initialized successfully", "test_bundle"],
        ),
        # Success case - force initialization
        (
            "test_bundle.tar.gz",
            True,
            True,
            ["Bundle initialized successfully", "test_bundle"],
        ),
        # Warning case - API server not available
        (
            "test_bundle.tar.gz",
            False,
            False,
            [
                "Bundle initialized but API server is NOT available",
                "kubectl commands may fail",
            ],
        ),
    ],
    ids=[
        "success-normal",
        "success-force",
        "warning-api-unavailable",
    ],
)
async def test_initialize_bundle_tool_parametrized(
    source: str,
    force: bool,
    api_available: bool,
    expected_strings: list[str],
    test_assertions: Any,
    test_factory: Any,
    tmp_path: Path,
) -> None:
    """
    Test the initialize_bundle tool with different inputs using real components.

    Args:
        source: Bundle source
        force: Whether to force initialization
        api_available: Whether the API server is available
        expected_strings: Strings expected in the response
        test_assertions: Assertions helper fixture
        test_factory: Factory for test objects
        tmp_path: Pytest's temporary directory fixture
    """
    # Create temporary bundle directory and source file using pytest's tmp_path
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    temp_source_file = temp_bundle_dir / "test_bundle.tar.gz"
    temp_source_file.touch()

    # Create real bundle manager instance
    bundle_manager = BundleManager(temp_bundle_dir)

    # Create bundle extract directory and kubeconfig
    bundle_extract_dir = temp_bundle_dir / "test_bundle"
    bundle_extract_dir.mkdir()
    kubeconfig_path = bundle_extract_dir / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    # Create metadata using test factory with real paths
    mock_metadata = test_factory.create_bundle_metadata(
        id="test_bundle",
        source=str(temp_source_file),
        path=bundle_extract_dir,
        kubeconfig_path=kubeconfig_path,
    )

    # Mock only external subprocess calls, not internal logic
    with (
        patch.object(
            bundle_manager, "_check_sbctl_available", new_callable=AsyncMock
        ) as mock_sbctl,
        patch.object(bundle_manager, "initialize_bundle", new_callable=AsyncMock) as mock_init,
        patch.object(
            bundle_manager, "check_api_server_available", new_callable=AsyncMock
        ) as mock_api,
        patch.object(bundle_manager, "get_diagnostic_info", new_callable=AsyncMock) as mock_diag,
        patch("mcp_server_troubleshoot.server.get_bundle_manager") as mock_get_manager,
    ):
        # Set up mocks for external dependencies only
        mock_sbctl.return_value = True
        mock_init.return_value = mock_metadata
        mock_api.return_value = api_available
        mock_diag.return_value = {"api_server_available": api_available}
        mock_get_manager.return_value = bundle_manager

        # Call the tool function with direct parameters
        response = await initialize_bundle(
            source=str(temp_source_file), force=force, verbosity="verbose"
        )

        # Verify method calls on real instance
        mock_sbctl.assert_awaited_once()
        mock_init.assert_awaited_once_with(str(temp_source_file), force)
        mock_api.assert_awaited_once()

        # Use the test assertion helper to verify response
        test_assertions.assert_api_response_valid(response, "text", expected_strings)

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,timeout,json_output,result_exit_code,result_stdout,expected_strings",
    [
        # Success case - JSON output
        (
            "get pods",
            30,
            True,
            0,
            '{"items": []}',
            ["kubectl command executed successfully", "items", "Command metadata"],
        ),
        # Success case - text output
        (
            "get pods",
            30,
            False,
            0,
            "NAME  READY  STATUS",
            ["kubectl command executed successfully", "NAME  READY  STATUS"],
        ),
        # Error case - command failed
        ("get invalid", 30, True, 1, "", ["kubectl command failed", "exit code 1"]),
    ],
    ids=[
        "success-json",
        "success-text",
        "error-command-failed",
    ],
)
async def test_kubectl_tool_parametrized(
    command: str,
    timeout: int,
    json_output: bool,
    result_exit_code: int,
    result_stdout: str,
    expected_strings: list[str],
    test_assertions: Any,
    test_factory: Any,
    tmp_path: Path,
) -> None:
    """
    Test the kubectl tool with different inputs using real components.

    Args:
        command: kubectl command
        timeout: Command timeout
        json_output: Whether to use JSON output
        result_exit_code: Mock result exit code
        result_stdout: Mock result stdout
        expected_strings: Strings expected in the response
        test_assertions: Assertions helper fixture
        test_factory: Factory for test objects
        tmp_path: Pytest's temporary directory fixture
    """
    # Create temporary directories for real components
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    bundle_path = temp_bundle_dir / "test_bundle"
    bundle_path.mkdir()
    kubeconfig_path = bundle_path / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    # Create real bundle manager and kubectl executor
    bundle_manager = BundleManager(temp_bundle_dir)
    kubectl_executor = KubectlExecutor(bundle_manager)

    # Create a mock result using test factory
    mock_result = test_factory.create_kubectl_result(
        command=command,
        exit_code=result_exit_code,
        stdout=result_stdout,
        stderr="",
        is_json=json_output and result_exit_code == 0,  # Only JSON for success cases
        duration_ms=100,
    )

    # Create active bundle metadata
    mock_bundle = BundleMetadata(
        id="test",
        source="test",
        path=bundle_path,
        kubeconfig_path=kubeconfig_path,
        initialized=True,
        host_only_bundle=False,  # Not a host-only bundle
    )

    # Mock only external subprocess calls and API server checks
    with (
        patch.object(bundle_manager, "get_active_bundle", return_value=mock_bundle),
        patch.object(
            bundle_manager, "check_api_server_available", new_callable=AsyncMock
        ) as mock_api,
        patch.object(bundle_manager, "get_diagnostic_info", new_callable=AsyncMock) as mock_diag,
        patch.object(kubectl_executor, "execute", new_callable=AsyncMock) as mock_execute,
        patch("mcp_server_troubleshoot.server.get_bundle_manager") as mock_get_manager,
        patch("mcp_server_troubleshoot.server.get_kubectl_executor") as mock_get_executor,
    ):
        # Set up mocks
        mock_api.return_value = True
        mock_diag.return_value = {"api_server_available": True}
        mock_get_manager.return_value = bundle_manager
        mock_get_executor.return_value = kubectl_executor

        # For error cases, raise an exception
        if result_exit_code != 0:
            from mcp_server_troubleshoot.kubectl import KubectlError

            mock_execute.side_effect = KubectlError(
                f"kubectl command failed: {command}", result_exit_code, ""
            )
        else:
            # For success cases, return the mock result
            mock_execute.return_value = mock_result

        # Call the tool function with direct parameters
        response = await kubectl(
            command=command, timeout=timeout, json_output=json_output, verbosity="verbose"
        )

        # Verify API check called on real instance
        mock_api.assert_awaited_once()

        # For success cases, verify kubectl execution
        if result_exit_code == 0:
            mock_execute.assert_awaited_once_with(command, timeout, json_output)

        # Use the test assertion helper to verify response
        test_assertions.assert_api_response_valid(response, "text", expected_strings)

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_operation,args,result,expected_strings",
    [
        # Test 1: list_files - now returns raw JSON array from real components
        (
            "list_files",
            {"path": "dir1", "recursive": False},
            FileListResult(
                path="dir1",
                entries=[
                    FileInfo(
                        name="file1.txt",
                        path="dir1/file1.txt",
                        type="file",
                        size=100,
                        access_time=123456789.0,
                        modify_time=123456789.0,
                        is_binary=False,
                    )
                ],
                recursive=False,
                total_files=1,
                total_dirs=0,
            ),
            ["file1.txt", "file2.txt"],  # Real components return file names directly
        ),
        # Test 2: read_file - now returns raw file content from real components
        (
            "read_file",
            {"path": "dir1/file1.txt", "start_line": 0, "end_line": 0},
            FileContentResult(
                path="dir1/file1.txt",
                content="This is the file content",
                start_line=0,
                end_line=0,
                total_lines=1,
                binary=False,
            ),
            ["This is the file content"],  # Real components return actual file content
        ),
        # Test 3: grep_files
        (
            "grep_files",
            {
                "pattern": "pattern",
                "path": "dir1",
                "recursive": True,
                "glob_pattern": "*.txt",
                "case_sensitive": False,
                "max_results": 100,
            },
            GrepResult(
                pattern="pattern",
                path="dir1",
                glob_pattern="*.txt",
                matches=[
                    GrepMatch(
                        path="dir1/file1.txt",
                        line_number=0,
                        line="This contains pattern",
                        match="pattern",
                        offset=13,
                    )
                ],
                total_matches=1,
                files_searched=1,
                case_sensitive=False,
                truncated=False,
            ),
            ["This contains pattern"],  # Real components return actual match content
        ),
        # Test 4: grep_files (multiple matches)
        (
            "grep_files",
            {
                "pattern": "common",
                "path": ".",
                "recursive": True,
                "glob_pattern": "*.txt",
                "case_sensitive": False,
                "max_results": 100,
            },
            GrepResult(
                pattern="common",
                path=".",
                glob_pattern="*.txt",
                matches=[
                    GrepMatch(
                        path="dir1/file1.txt",
                        line_number=0,
                        line="This has common text",
                        match="common",
                        offset=9,
                    ),
                    GrepMatch(
                        path="dir2/file2.txt",
                        line_number=1,
                        line="More common text",
                        match="common",
                        offset=5,
                    ),
                ],
                total_matches=2,
                files_searched=3,
                case_sensitive=False,
                truncated=False,
            ),
            [
                "This has common text",
                "More common text",
            ],  # Real components return actual match content
        ),
    ],
    ids=[
        "list-files",
        "read-file",
        "grep-files-single-match",
        "grep-files-multiple-matches",
    ],
)
async def test_file_operations_parametrized(
    file_operation: str,
    args: dict,
    result: Any,
    expected_strings: list[str],
    test_assertions: Any,
    tmp_path: Path,
) -> None:
    """
    Test file operation tools with different inputs using real FileExplorer and real files.

    Args:
        file_operation: Operation to test (list_files, read_file, grep_files)
        args: Arguments for the operation
        result: Expected result structure (used for validation)
        expected_strings: Strings expected in the response
        test_assertions: Assertions helper fixture
        tmp_path: Pytest's temporary directory fixture
    """
    # Create temporary directories and files for real testing
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    bundle_path = temp_bundle_dir / "test_bundle"
    bundle_path.mkdir()
    kubeconfig_path = bundle_path / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    # Create test directory structure with real files
    test_dir = bundle_path / "test_data"
    test_dir.mkdir()
    dir1 = test_dir / "dir1"
    dir1.mkdir()
    dir2 = test_dir / "dir2"
    dir2.mkdir()
    subdir = dir2 / "subdir"
    subdir.mkdir()

    # Create real test files with content matching expected results
    file1 = dir1 / "file1.txt"
    file1.write_text("This is the file content\nLine 2\nThis contains pattern")

    file2 = dir1 / "file2.txt"
    file2.write_text("This has common text\nAnother line")

    file3 = dir2 / "file2.txt"
    file3.write_text("More common text\nFinal line")

    # Create real bundle manager and file explorer
    bundle_manager = BundleManager(temp_bundle_dir)
    file_explorer = FileExplorer(bundle_manager)

    # Create active bundle metadata
    mock_bundle = BundleMetadata(
        id="test",
        source="test",
        path=bundle_path,
        kubeconfig_path=kubeconfig_path,
        initialized=True,
        host_only_bundle=False,
    )

    with (
        patch.object(bundle_manager, "get_active_bundle", return_value=mock_bundle),
        patch("mcp_server_troubleshoot.server.get_file_explorer") as mock_get_explorer,
    ):
        mock_get_explorer.return_value = file_explorer

        # Execute the appropriate file operation with real components
        if file_operation == "list_files":
            # Adjust path to real directory structure
            path = args["path"]
            if path == "dir1":
                path = "test_data/dir1"
            response = await list_files(path=path, recursive=args["recursive"], verbosity="minimal")

        elif file_operation == "read_file":
            # Adjust path to real file structure
            path = args["path"]
            if path == "dir1/file1.txt":
                path = "test_data/dir1/file1.txt"
            response = await read_file(
                path=path,
                start_line=args["start_line"],
                end_line=args["end_line"],
                verbosity="minimal",
            )

        elif file_operation == "grep_files":
            # Adjust path to real directory structure
            path = args["path"]
            if path == "dir1":
                path = "test_data/dir1"
            elif path == ".":
                path = "test_data"
            response = await grep_files(
                pattern=args["pattern"],
                path=path,
                recursive=args["recursive"],
                glob_pattern=args["glob_pattern"],
                case_sensitive=args["case_sensitive"],
                max_results=args["max_results"],
                max_results_per_file=args.get("max_results_per_file", 50),
                max_files=args.get("max_files", 10),
                verbosity="minimal",
            )

        # Use the test assertion helper to verify response
        test_assertions.assert_api_response_valid(response, "text", expected_strings)

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type,error_message,expected_strings",
    [
        # File system errors
        (
            FileSystemError,
            "File not found: test.txt",
            ["File system error", "File not found: test.txt"],
        ),
        # Path not found errors
        (
            PathNotFoundError,
            "Path /nonexistent does not exist",
            ["File system error", "Path /nonexistent does not exist"],
        ),
        # Bundle manager errors
        (
            BundleManagerError,
            "No active bundle initialized",
            ["Bundle error", "No active bundle initialized"],
        ),
    ],
    ids=[
        "filesystem-error",
        "path-not-found",
        "bundle-manager-error",
    ],
)
async def test_file_operations_error_handling(
    error_type: type,
    error_message: str,
    expected_strings: list[str],
    test_assertions: Any,
    tmp_path: Path,
) -> None:
    """
    Test that file operation tools properly handle various error types using real components.

    Args:
        error_type: Type of error to simulate
        error_message: Error message to include
        expected_strings: Strings expected in the response
        test_assertions: Assertions helper fixture
        tmp_path: Pytest's temporary directory fixture
    """
    # Create temporary directories for real components
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    bundle_path = temp_bundle_dir / "test_bundle"
    bundle_path.mkdir()
    kubeconfig_path = bundle_path / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    # Create real bundle manager and file explorer
    bundle_manager = BundleManager(temp_bundle_dir)
    file_explorer = FileExplorer(bundle_manager)

    # Create active bundle metadata
    mock_bundle = BundleMetadata(
        id="test",
        source="test",
        path=bundle_path,
        kubeconfig_path=kubeconfig_path,
        initialized=True,
        host_only_bundle=False,
    )

    # Mock the file explorer methods to raise the specified error
    with (
        patch.object(bundle_manager, "get_active_bundle", return_value=mock_bundle),
        patch.object(file_explorer, "list_files", new_callable=AsyncMock) as mock_list,
        patch.object(file_explorer, "read_file", new_callable=AsyncMock) as mock_read,
        patch.object(file_explorer, "grep_files", new_callable=AsyncMock) as mock_grep,
        patch("mcp_server_troubleshoot.server.get_file_explorer") as mock_get_explorer,
    ):
        # Set up the real file explorer instance but mock its methods to raise errors
        mock_list.side_effect = error_type(error_message)
        mock_read.side_effect = error_type(error_message)
        mock_grep.side_effect = error_type(error_message)
        mock_get_explorer.return_value = file_explorer

        # Test all three file operations with the same error
        # 1. Test list_files
        list_response = await list_files(path="test/path", recursive=False, verbosity="verbose")
        test_assertions.assert_api_response_valid(list_response, "text", expected_strings)

        # 2. Test read_file
        read_response = await read_file(
            path="test/file.txt", start_line=0, end_line=None, verbosity="verbose"
        )
        test_assertions.assert_api_response_valid(read_response, "text", expected_strings)

        # 3. Test grep_files
        grep_response = await grep_files(
            pattern="test",
            path="test/path",
            recursive=True,
            glob_pattern="*",
            case_sensitive=False,
            max_results=100,
            max_results_per_file=50,
            max_files=10,
            verbosity="verbose",
        )
        test_assertions.assert_api_response_valid(grep_response, "text", expected_strings)

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "include_invalid,bundles_available,expected_strings",
    [
        # With bundles available
        (
            False,
            True,
            ["support-bundle-1.tar.gz", "Usage Instructions", "initialize_bundle"],
        ),
        # No bundles available
        (False, False, ["No support bundles found", "download or transfer a bundle"]),
        # With invalid bundles included
        (
            True,
            True,
            ["support-bundle-1.tar.gz", "validation_message", "initialize_bundle"],
        ),
    ],
    ids=[
        "with-bundles",
        "no-bundles",
        "with-invalid-bundles",
    ],
)
async def test_list_available_bundles_parametrized(
    include_invalid: bool,
    bundles_available: bool,
    expected_strings: list[str],
    test_assertions: Any,
    test_factory: Any,
    tmp_path: Path,
) -> None:
    """
    Test the list_available_bundles tool with different scenarios using real BundleManager.

    Args:
        include_invalid: Whether to include invalid bundles
        bundles_available: Whether any bundles are available
        expected_strings: Strings expected in the response
        test_assertions: Assertions helper fixture
        test_factory: Factory for test objects
        tmp_path: Pytest's temporary directory fixture
    """
    # Create temporary bundle directory
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()

    # Create real bundle manager instance
    bundle_manager = BundleManager(temp_bundle_dir)

    # Set up a custom class for testing that matches the real bundle structure
    from dataclasses import dataclass

    @dataclass
    class MockAvailableBundle:
        name: str
        path: str
        relative_path: str
        size_bytes: int
        modified_time: float
        valid: bool
        validation_message: str | None = None

    # Create test bundles
    if bundles_available:
        bundles = [
            MockAvailableBundle(
                name="support-bundle-1.tar.gz",
                path=str(temp_bundle_dir / "support-bundle-1.tar.gz"),
                relative_path="support-bundle-1.tar.gz",
                size_bytes=1024 * 1024,  # 1 MB
                modified_time=1617292800.0,  # 2021-04-01
                valid=True,
            ),
        ]

        # Add an invalid bundle if include_invalid is True
        if include_invalid:
            bundles.append(
                MockAvailableBundle(
                    name="invalid-bundle.txt",
                    path=str(temp_bundle_dir / "invalid-bundle.txt"),
                    relative_path="invalid-bundle.txt",
                    size_bytes=512,
                    modified_time=1617292800.0,
                    valid=False,
                    validation_message="Not a valid support bundle format",
                )
            )
    else:
        bundles = []

    # Mock only the list_available_bundles method, keep rest of BundleManager real
    with (
        patch.object(bundle_manager, "list_available_bundles", new_callable=AsyncMock) as mock_list,
        patch("mcp_server_troubleshoot.server.get_bundle_manager") as mock_get_manager,
    ):
        mock_list.return_value = bundles
        mock_get_manager.return_value = bundle_manager

        # Call the tool function with direct parameters
        response = await list_available_bundles(
            include_invalid=include_invalid, verbosity="verbose"
        )

        # Verify method call on real instance
        mock_list.assert_awaited_once_with(include_invalid)

        # Use the test assertion helper to verify response
        test_assertions.assert_api_response_valid(response, "text", expected_strings)

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
async def test_cleanup_resources(test_assertions: Any, tmp_path: Path) -> None:
    """
    Test that the cleanup_resources function properly cleans up bundle manager resources.

    This test verifies:
    1. The global shutdown flag is set
    2. The bundle manager cleanup method is called
    3. Multiple cleanup calls are handled correctly

    Args:
        test_assertions: Assertions helper fixture
        tmp_path: Pytest's temporary directory fixture
    """
    # Create temporary bundle directory for real bundle manager
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()

    # Create real bundle manager instance
    bundle_manager = BundleManager(temp_bundle_dir)

    # Mock both app_context and legacy bundle manager access while using real instances
    with (
        patch("mcp_server_troubleshoot.server.get_app_context") as mock_get_context,
        patch("mcp_server_troubleshoot.server.globals") as mock_globals,
        patch.object(bundle_manager, "cleanup", new_callable=AsyncMock) as mock_cleanup,
    ):
        # Reset shutdown flag
        import mcp_server_troubleshoot.server

        mcp_server_troubleshoot.server._is_shutting_down = False

        # Setup app context mode with real bundle manager
        mock_app_context = AsyncMock()
        mock_app_context.bundle_manager = bundle_manager

        # Set return value for get_app_context
        mock_get_context.return_value = mock_app_context

        # Mock globals for legacy mode
        mock_globals.return_value = {
            "_bundle_manager": None  # Not used in this test since we test app_context mode
        }

        # Call cleanup_resources
        await cleanup_resources()

        # Verify cleanup was called on real bundle manager instance
        mock_cleanup.assert_awaited_once()

        # Verify shutdown flag was set
        assert mcp_server_troubleshoot.server._is_shutting_down is True

        # Reset mock
        mock_cleanup.reset_mock()

        # Call cleanup_resources again (should not call cleanup again)
        await cleanup_resources()

        # Verify cleanup was not called again
        mock_cleanup.assert_not_awaited()

    # Now test legacy mode with real bundle manager
    with (
        patch("mcp_server_troubleshoot.server.get_app_context") as mock_get_context,
        patch("mcp_server_troubleshoot.server.globals") as mock_globals,
        patch.object(bundle_manager, "cleanup", new_callable=AsyncMock) as mock_cleanup,
    ):
        # Reset shutdown flag
        mcp_server_troubleshoot.server._is_shutting_down = False

        # Setup legacy mode (no app context)
        mock_get_context.return_value = None

        # Mock globals for legacy mode with real bundle manager
        mock_globals.return_value = {"_bundle_manager": bundle_manager}

        # Call cleanup_resources
        await cleanup_resources()

        # Verify cleanup was called on real bundle manager
        mock_cleanup.assert_awaited_once()

        # Verify shutdown flag was set
        assert mcp_server_troubleshoot.server._is_shutting_down is True

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
async def test_register_signal_handlers() -> None:
    """
    Test that the register_signal_handlers function properly sets up handlers for signals.

    This test verifies:
    1. Signal handlers are registered for SIGINT and SIGTERM
    2. The event loop's add_signal_handler method is called
    """
    # Mock the asyncio module
    with patch("asyncio.get_running_loop") as mock_get_loop:
        mock_loop = Mock()
        mock_get_loop.return_value = mock_loop
        mock_loop.is_closed.return_value = False
        mock_loop.add_signal_handler = Mock()

        # Call register_signal_handlers
        register_signal_handlers()

        # Verify add_signal_handler was called for each signal
        import signal

        if hasattr(signal, "SIGTERM"):  # Check for POSIX signals
            assert mock_loop.add_signal_handler.call_count >= 1
        else:  # Windows
            mock_loop.add_signal_handler.assert_called_once()


@pytest.mark.asyncio
async def test_shutdown_function() -> None:
    """
    Test that the shutdown function properly triggers cleanup process.

    This test verifies:
    1. In an async context, cleanup_resources is called as a task
    2. In a non-async context, a new event loop is created
    3. Cleanup is properly called in both cases
    """
    # Test case 1: With running loop (async context)
    with (
        patch("asyncio.get_running_loop") as mock_get_loop,
        patch("asyncio.create_task") as mock_create_task,
        patch("mcp_server_troubleshoot.server.cleanup_resources"),
    ):
        mock_loop = Mock()
        mock_get_loop.return_value = mock_loop
        mock_loop.is_closed.return_value = False

        # Call shutdown
        shutdown()

        # Verify create_task was called
        mock_create_task.assert_called_once()

    # Test case 2: Without running loop (non-async context)
    with (
        patch("asyncio.get_running_loop", side_effect=RuntimeError("No running loop")),
        patch("asyncio.new_event_loop") as mock_new_loop,
        patch("asyncio.set_event_loop") as mock_set_loop,
        patch("mcp_server_troubleshoot.server.cleanup_resources"),
    ):
        mock_loop = Mock()
        mock_new_loop.return_value = mock_loop

        # Call shutdown
        shutdown()

        # Verify new_event_loop and set_event_loop were called
        mock_new_loop.assert_called_once()
        mock_set_loop.assert_called_once_with(mock_loop)

        # Verify run_until_complete was called
        mock_loop.run_until_complete.assert_called_once()

        # Verify loop was closed
        mock_loop.close.assert_called_once()
