"""
Simplified server lifecycle integration tests that focus on testable server behaviors.

This module tests server startup, bundle discovery, and lifecycle management without
requiring complex internal mocking.
"""

import asyncio
import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from mcp.server.fastmcp import FastMCP

from troubleshoot_mcp_server.lifecycle import (
    app_lifespan,
    AppContext,
    create_temp_directory,
    periodic_bundle_cleanup,
    setup_signal_handlers,
)
from troubleshoot_mcp_server.bundle import BundleManager
from troubleshoot_mcp_server.server import (
    get_app_context,
    set_app_context,
    cleanup_resources,
)
from tests.test_utils.bundle_helpers import create_mock_bundle


@pytest.mark.integration
class TestServerLifecycleSimplified:
    """Test server lifecycle scenarios that don't require complex mocking."""

    @pytest.fixture(autouse=True)
    def clean_app_context(self):
        """Ensure app context and global state is cleaned before and after each test."""
        # Import the server module to access its globals
        import troubleshoot_mcp_server.server as server_module

        # Clean before test
        set_app_context(None)
        server_module._bundle_manager = None
        server_module._is_shutting_down = False

        yield

        # Clean after test
        set_app_context(None)
        server_module._bundle_manager = None
        server_module._is_shutting_down = False

    @pytest.fixture
    def mock_server(self):
        """Create a mock FastMCP server for testing."""
        server = MagicMock(spec=FastMCP)
        server.use_stdio = False
        return server

    @pytest.mark.asyncio
    async def test_server_startup_sequence_success(self, tmp_path: Path, mock_server):
        """Test complete server startup sequence with no issues."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        # Set environment variable for bundle directory
        original_env = os.environ.get("MCP_BUNDLE_STORAGE")
        os.environ["MCP_BUNDLE_STORAGE"] = str(bundle_dir)

        try:
            # Test the complete startup sequence
            async with app_lifespan(mock_server) as context:
                # Verify all components are initialized
                assert isinstance(context, AppContext)
                assert context.bundle_manager is not None
                assert context.file_explorer is not None
                assert context.kubectl_executor is not None
                assert context.temp_dir != ""
                assert Path(context.temp_dir).exists()

                # Verify bundle manager is configured with correct directory
                assert context.bundle_manager.bundle_dir == bundle_dir

                # Verify metadata is populated
                assert "start_time" in context.metadata
                assert "stdio_mode" in context.metadata
                assert context.metadata["stdio_mode"] is False

                # Verify context is set globally for tool access
                assert get_app_context() is not None
                assert get_app_context() == context

            # Verify cleanup occurred
            if Path(context.temp_dir).exists():
                # Small delay to allow cleanup to complete
                await asyncio.sleep(0.1)
                assert not Path(context.temp_dir).exists()

        finally:
            # Restore environment
            if original_env:
                os.environ["MCP_BUNDLE_STORAGE"] = original_env
            else:
                os.environ.pop("MCP_BUNDLE_STORAGE", None)

    @pytest.mark.asyncio
    async def test_server_startup_with_bundle_directory_scanning(self, tmp_path: Path, mock_server):
        """Test server startup automatically scans bundle directory for available bundles."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        # Create several bundle files
        bundle_files = []
        for i in range(3):
            bundle_file = bundle_dir / f"test-bundle-{i}.tar.gz"
            create_mock_bundle(bundle_file)
            bundle_files.append(bundle_file)

        # Set environment variable
        original_env = os.environ.get("MCP_BUNDLE_STORAGE")
        os.environ["MCP_BUNDLE_STORAGE"] = str(bundle_dir)

        try:
            async with app_lifespan(mock_server) as context:
                bundle_manager = context.bundle_manager

                # Test that bundle manager can discover bundles
                bundles = await bundle_manager.list_available_bundles()

                # Should find all created bundles
                assert len(bundles) >= 3
                bundle_names = [b.name for b in bundles]
                for i in range(3):
                    assert f"test-bundle-{i}.tar.gz" in bundle_names

        finally:
            if original_env:
                os.environ["MCP_BUNDLE_STORAGE"] = original_env
            else:
                os.environ.pop("MCP_BUNDLE_STORAGE", None)

    @pytest.mark.asyncio
    async def test_server_startup_no_bundles_directory(self, mock_server):
        """Test server startup with no bundles directory (should handle gracefully)."""
        # Use non-existent directory
        non_existent_dir = "/tmp/does_not_exist_mcp_test"

        original_env = os.environ.get("MCP_BUNDLE_STORAGE")
        os.environ["MCP_BUNDLE_STORAGE"] = non_existent_dir

        try:
            async with app_lifespan(mock_server) as context:
                # Server should start successfully even without bundle directory
                assert isinstance(context, AppContext)
                assert context.bundle_manager is not None

                # Bundle manager should handle missing directory gracefully
                bundles = await context.bundle_manager.list_available_bundles()
                assert isinstance(bundles, list)
                # Should return empty list for non-existent directory
                assert len(bundles) == 0

        finally:
            if original_env:
                os.environ["MCP_BUNDLE_STORAGE"] = original_env
            else:
                os.environ.pop("MCP_BUNDLE_STORAGE", None)

    @pytest.mark.asyncio
    async def test_server_startup_invalid_bundles_handling(self, tmp_path: Path, mock_server):
        """Test server startup with invalid bundles (should handle errors gracefully)."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        # Create invalid bundle files
        invalid_bundle1 = bundle_dir / "invalid.tar.gz"
        invalid_bundle1.write_text("not a valid tar.gz file")

        invalid_bundle2 = bundle_dir / "empty.tar.gz"
        invalid_bundle2.touch()  # Empty file

        # Create valid bundle for comparison
        valid_bundle = bundle_dir / "valid.tar.gz"
        create_mock_bundle(valid_bundle)

        original_env = os.environ.get("MCP_BUNDLE_STORAGE")
        os.environ["MCP_BUNDLE_STORAGE"] = str(bundle_dir)

        try:
            async with app_lifespan(mock_server) as context:
                bundle_manager = context.bundle_manager

                # Should be able to list bundles even with invalid ones present
                bundles = await bundle_manager.list_available_bundles()

                # Should find at least the valid bundle
                assert len(bundles) >= 1
                valid_bundles = [b for b in bundles if b.name == "valid.tar.gz"]
                assert len(valid_bundles) == 1

                # Test including invalid bundles
                all_bundles = await bundle_manager.list_available_bundles(include_invalid=True)
                assert len(all_bundles) >= len(bundles)

        finally:
            if original_env:
                os.environ["MCP_BUNDLE_STORAGE"] = original_env
            else:
                os.environ.pop("MCP_BUNDLE_STORAGE", None)

    @pytest.mark.asyncio
    async def test_server_shutdown_cleanup(self, tmp_path: Path, mock_server):
        """Test server shutdown properly cleans up all resources."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        # Create bundle
        bundle_file = bundle_dir / "test.tar.gz"
        create_mock_bundle(bundle_file)

        original_env = os.environ.get("MCP_BUNDLE_STORAGE")
        os.environ["MCP_BUNDLE_STORAGE"] = str(bundle_dir)

        temp_dirs_created = []

        try:
            async with app_lifespan(mock_server) as context:
                temp_dirs_created.append(context.temp_dir)

                # Verify basic server context setup
                assert Path(context.temp_dir).exists()
                assert context.bundle_manager is not None

                # Test that bundle manager can discover the bundle
                bundles = await context.bundle_manager.list_available_bundles()
                assert len(bundles) >= 1

            # After context exit, verify cleanup
            await asyncio.sleep(0.1)  # Allow cleanup to complete

            # Temp directories should be cleaned up
            for temp_dir in temp_dirs_created:
                assert not Path(temp_dir).exists()

        finally:
            if original_env:
                os.environ["MCP_BUNDLE_STORAGE"] = original_env
            else:
                os.environ.pop("MCP_BUNDLE_STORAGE", None)

    @pytest.mark.asyncio
    async def test_periodic_cleanup_task(self, tmp_path: Path, mock_server):
        """Test periodic cleanup background task functionality."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        original_env_storage = os.environ.get("MCP_BUNDLE_STORAGE")
        original_env_cleanup = os.environ.get("ENABLE_PERIODIC_CLEANUP")
        original_env_interval = os.environ.get("CLEANUP_INTERVAL")

        os.environ["MCP_BUNDLE_STORAGE"] = str(bundle_dir)
        os.environ["ENABLE_PERIODIC_CLEANUP"] = "true"
        os.environ["CLEANUP_INTERVAL"] = "1"  # 1 second for testing

        try:
            async with app_lifespan(mock_server) as context:
                # Verify cleanup task is started
                assert "bundle_cleanup" in context.background_tasks
                cleanup_task = context.background_tasks["bundle_cleanup"]
                assert not cleanup_task.done()

                # Wait a bit to let cleanup run
                await asyncio.sleep(1.5)

                # Task should still be running
                assert not cleanup_task.done()

            # After context exit, task should be cancelled
            assert cleanup_task.cancelled() or cleanup_task.done()

        finally:
            # Restore environment
            if original_env_storage:
                os.environ["MCP_BUNDLE_STORAGE"] = original_env_storage
            else:
                os.environ.pop("MCP_BUNDLE_STORAGE", None)

            if original_env_cleanup:
                os.environ["ENABLE_PERIODIC_CLEANUP"] = original_env_cleanup
            else:
                os.environ.pop("ENABLE_PERIODIC_CLEANUP", None)

            if original_env_interval:
                os.environ["CLEANUP_INTERVAL"] = original_env_interval
            else:
                os.environ.pop("CLEANUP_INTERVAL", None)


@pytest.mark.integration
class TestBundleManagementBasics:
    """Test basic bundle management without complex initialization."""

    @pytest.fixture
    def bundle_manager_context(self, tmp_path: Path):
        """Create bundle manager with test context."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()
        return bundle_dir

    @pytest.mark.asyncio
    async def test_automatic_bundle_discovery_on_startup(self, bundle_manager_context: Path):
        """Test automatic bundle discovery when server starts."""
        # Create multiple bundles
        bundles_created = []
        for i in range(5):
            bundle_file = bundle_manager_context / f"bundle-{i}.tar.gz"
            create_mock_bundle(bundle_file)
            bundles_created.append(bundle_file.name)

        # Initialize bundle manager
        bundle_manager = BundleManager(bundle_manager_context)

        # Test discovery
        discovered_bundles = await bundle_manager.list_available_bundles()

        # Should discover all created bundles
        assert len(discovered_bundles) == 5
        discovered_names = {b.name for b in discovered_bundles}
        for bundle_name in bundles_created:
            assert bundle_name in discovered_names

    @pytest.mark.asyncio
    async def test_concurrent_bundle_operations(self, tmp_path: Path):
        """Test server handles concurrent bundle operations safely."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        # Create bundles
        for i in range(5):
            bundle_file = bundle_dir / f"bundle{i}.tar.gz"
            create_mock_bundle(bundle_file)

        bundle_manager = BundleManager(bundle_dir)

        # Test concurrent bundle listing
        tasks = []
        for _ in range(10):
            task = asyncio.create_task(bundle_manager.list_available_bundles())
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # All operations should succeed
        for result in results:
            assert isinstance(result, list)
            assert len(result) == 5

        # Test that no exceptions occurred
        assert all(not isinstance(r, Exception) for r in results)


@pytest.mark.integration
class TestUtilityFunctions:
    """Test utility functions used in server lifecycle."""

    def test_create_temp_directory(self):
        """Test temporary directory creation."""
        temp_dir = create_temp_directory()

        assert temp_dir != ""
        assert Path(temp_dir).exists()
        assert Path(temp_dir).is_dir()
        assert "mcp-troubleshoot-" in temp_dir

        # Clean up
        shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_periodic_bundle_cleanup_function(self, tmp_path: Path):
        """Test periodic cleanup function."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        bundle_manager = BundleManager(bundle_dir)

        # Create cleanup task with short interval
        cleanup_task = asyncio.create_task(periodic_bundle_cleanup(bundle_manager, interval=0.1))

        # Let it run briefly
        await asyncio.sleep(0.3)

        # Cancel and verify it can be cancelled
        cleanup_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await cleanup_task

    def test_setup_signal_handlers_skips_during_pytest(self):
        """Test signal handler setup is skipped during pytest runs."""
        # Should not raise any errors
        setup_signal_handlers()

        # Function should complete without issues
        # (Implementation skips registration during pytest)

    @pytest.mark.asyncio
    async def test_cleanup_resources_function(self, tmp_path: Path):
        """Test explicit cleanup resources function."""
        bundle_dir = tmp_path / "bundles"
        bundle_dir.mkdir()

        # Create bundle manager and set context
        bundle_manager = BundleManager(bundle_dir)
        mock_context = MagicMock()
        mock_context.bundle_manager = bundle_manager
        set_app_context(mock_context)

        try:
            # Should not raise errors
            await cleanup_resources()

            # Should be safe to call multiple times
            await cleanup_resources()

        finally:
            set_app_context(None)
