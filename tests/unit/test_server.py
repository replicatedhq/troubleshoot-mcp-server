"""
Tests for the MCP server.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from mcp.types import TextContent

from mcp_server_troubleshoot.bundle import BundleMetadata, BundleManager
from mcp_server_troubleshoot.files import (
    FileExplorer,
)
from mcp_server_troubleshoot.kubectl import KubectlResult, KubectlExecutor
from mcp_server_troubleshoot.server import (
    get_bundle_manager,
    get_file_explorer,
    get_kubectl_executor,
    initialize_bundle,
    kubectl,
    list_files,
    mcp,
    read_file,
    grep_files,
)

# Mark all tests in this file as unit tests and quick tests
pytestmark = [pytest.mark.unit, pytest.mark.quick]


def test_global_instances() -> None:
    """Test that the global instances are properly initialized."""
    # Reset the global instances first
    import mcp_server_troubleshoot.server

    mcp_server_troubleshoot.server._bundle_manager = None
    mcp_server_troubleshoot.server._kubectl_executor = None
    mcp_server_troubleshoot.server._file_explorer = None

    # Now get instances and check they're created
    bundle_manager = get_bundle_manager()
    assert bundle_manager is not None

    kubectl_executor = get_kubectl_executor()
    assert kubectl_executor is not None
    assert kubectl_executor.bundle_manager is bundle_manager

    file_explorer = get_file_explorer()
    assert file_explorer is not None
    assert file_explorer.bundle_manager is bundle_manager


@pytest.mark.asyncio
async def test_initialize_bundle_tool(tmp_path: Path) -> None:
    """Test that the initialize_bundle tool works correctly."""
    # Create a temporary bundle directory using pytest's tmp_path
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    temp_source_file = temp_bundle_dir / "test_bundle.tar.gz"
    temp_source_file.touch()  # Create an empty file for the source

    # Create a real BundleManager instance with a temp directory
    bundle_manager = BundleManager(temp_bundle_dir)

    # Create temp directories that bundle manager will use
    bundle_extract_dir = temp_bundle_dir / "test_bundle"
    bundle_extract_dir.mkdir()
    kubeconfig_path = bundle_extract_dir / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    mock_metadata = BundleMetadata(
        id="test_bundle",
        source=str(temp_source_file),
        path=bundle_extract_dir,
        kubeconfig_path=kubeconfig_path,
        initialized=True,
        host_only_bundle=False,
    )

    # Only mock external subprocess calls, not internal logic
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
        mock_api.return_value = True
        mock_diag.return_value = {"api_server_available": True}
        mock_get_manager.return_value = bundle_manager

        # Call the tool function directly with parameters
        response = await initialize_bundle(
            source=str(temp_source_file), force=False, verbosity="verbose"
        )

        # Verify the bundle manager methods were called
        mock_sbctl.assert_awaited_once()
        mock_init.assert_awaited_once_with(str(temp_source_file), False)
        mock_api.assert_awaited_once()

        # Verify the response
        assert isinstance(response, list)
        assert len(response) == 1
        assert isinstance(response[0], TextContent)
        assert response[0].type == "text"
        assert "Bundle initialized successfully" in response[0].text
        assert "test_bundle" in response[0].text

    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
async def test_kubectl_tool(tmp_path: Path) -> None:
    """Test that the kubectl tool works correctly."""
    # Create temporary directories for real components using pytest's tmp_path
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    bundle_path = temp_bundle_dir / "test_bundle"
    bundle_path.mkdir()
    kubeconfig_path = bundle_path / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    try:
        # Create real bundle manager and kubectl executor
        bundle_manager = BundleManager(temp_bundle_dir)
        kubectl_executor = KubectlExecutor(bundle_manager)

        # Mock result for subprocess execution
        mock_result = KubectlResult(
            command="get pods",
            exit_code=0,
            stdout='{"items": []}',
            stderr="",
            output={"items": []},
            is_json=True,
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
            patch.object(kubectl_executor, "execute", new_callable=AsyncMock) as mock_execute,
            patch("mcp_server_troubleshoot.server.get_bundle_manager") as mock_get_manager,
            patch("mcp_server_troubleshoot.server.get_kubectl_executor") as mock_get_executor,
        ):
            # Set up mocks for external dependencies only
            mock_api.return_value = True
            mock_execute.return_value = mock_result
            mock_get_manager.return_value = bundle_manager
            mock_get_executor.return_value = kubectl_executor

            # Call the tool function directly with parameters
            response = await kubectl(
                command="get pods", timeout=30, json_output=True, verbosity="verbose"
            )

            # Verify the API server check was called
            mock_api.assert_awaited_once()
            # Verify the kubectl executor was called
            mock_execute.assert_awaited_once_with("get pods", 30, True)

            # Verify the response
            assert isinstance(response, list)
            assert len(response) == 1
            assert isinstance(response[0], TextContent)
            assert response[0].type == "text"
            assert "kubectl command executed successfully" in response[0].text
            assert "items" in response[0].text
            assert "Command metadata" in response[0].text

    except Exception:
        # Re-raise any exceptions so test failures are properly reported
        raise
    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
async def test_kubectl_tool_host_only_bundle(tmp_path: Path) -> None:
    """Test that the kubectl tool handles host-only bundles correctly."""
    # Create temporary directories for real components using pytest's tmp_path
    temp_bundle_dir = tmp_path / "bundle_dir"
    temp_bundle_dir.mkdir()
    bundle_path = temp_bundle_dir / "test_bundle"
    bundle_path.mkdir()
    kubeconfig_path = bundle_path / "kubeconfig"
    kubeconfig_path.write_text('{"apiVersion": "v1", "clusters": []}')

    try:
        # Create real bundle manager
        bundle_manager = BundleManager(temp_bundle_dir)

        # Create host-only bundle metadata
        mock_bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_path,
            kubeconfig_path=kubeconfig_path,
            initialized=True,
            host_only_bundle=True,  # This is a host-only bundle
        )

        with (
            patch.object(bundle_manager, "get_active_bundle", return_value=mock_bundle),
            patch("mcp_server_troubleshoot.server.get_bundle_manager") as mock_get_manager,
        ):
            mock_get_manager.return_value = bundle_manager

            # Call the tool function directly with parameters
            response = await kubectl(
                command="get pods", timeout=30, json_output=True, verbosity="verbose"
            )

            # Verify the error response
            assert isinstance(response, list)
            assert len(response) == 1
            assert isinstance(response[0], TextContent)
            assert response[0].type == "text"
            assert "host resources" in response[0].text.lower()
            assert "no cluster resources" in response[0].text
            assert "file exploration tools" in response[0].text

    except Exception:
        # Re-raise any exceptions so test failures are properly reported
        raise
    # No manual cleanup needed - tmp_path handles it automatically


@pytest.mark.asyncio
async def test_file_operations(tmp_path: Path) -> None:
    """Test the file operation tools using real FileExplorer with real files."""
    # Create temporary directories and files for real testing using pytest's tmp_path
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

    # Create real test files
    file1 = dir1 / "file1.txt"
    file1.write_text("This is the file content\nLine 2\nThis contains pattern")

    try:
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

            # 1. Test list_files with real files
            list_response = await list_files(
                path="test_data/dir1", recursive=False, verbosity="verbose"
            )

            # Verify the response contains real file information
            assert len(list_response) == 1
            assert list_response[0].type == "text"
            assert "Listed files" in list_response[0].text
            assert "file1.txt" in list_response[0].text

            # 2. Test read_file with real file
            read_response = await read_file(
                path="test_data/dir1/file1.txt",
                start_line=0,
                end_line=2,
                verbosity="verbose",
            )

            # Verify the response contains real file content
            assert len(read_response) == 1
            assert read_response[0].type == "text"
            assert "Read text file" in read_response[0].text
            assert "This is the file content" in read_response[0].text

            # 3. Test grep_files with real file content
            grep_response = await grep_files(
                pattern="pattern",
                path="test_data",
                recursive=True,
                glob_pattern="*.txt",
                case_sensitive=False,
                max_results=100,
                max_results_per_file=50,
                max_files=10,
                verbosity="verbose",
            )

            # Verify the response contains real grep results
            assert len(grep_response) == 1
            assert grep_response[0].type == "text"
            assert "Found" in grep_response[0].text
            assert "pattern" in grep_response[0].text

    except Exception:
        # Re-raise any exceptions so test failures are properly reported
        raise
    # No manual cleanup needed - tmp_path handles it automatically


def test_mcp_configuration() -> None:
    """Test that the FastMCP server is properly configured."""
    # Check that the server has been created correctly
    assert mcp is not None

    # For FastMCP, we can just verify that our functions exist in the module
    # The @mcp.tool() decorator registers the functions with the FastMCP instance
    from mcp_server_troubleshoot.server import (
        initialize_bundle,
        kubectl,
        list_files,
        read_file,
        grep_files,
    )

    assert callable(initialize_bundle)
    assert callable(kubectl)
    assert callable(list_files)
    assert callable(read_file)
    assert callable(grep_files)
