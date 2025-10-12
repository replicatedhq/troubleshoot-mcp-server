"""
Tests for Single Bundle Mode functionality.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from troubleshoot_mcp_server.bundle import BundleManager, BundleMetadata

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_single_bundle_mode_disabled_by_default():
    """Test that single bundle mode is disabled by default."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)
        assert manager.single_bundle_mode is False


@pytest.mark.asyncio
async def test_single_bundle_mode_enabled_via_env():
    """Test that single bundle mode can be enabled via environment variable."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)
            assert manager.single_bundle_mode is True


@pytest.mark.asyncio
async def test_auto_activate_no_bundles():
    """Test that auto-activation does nothing when no bundles exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)
            await manager._auto_activate_bundle_if_exists()
            assert manager.active_bundle is None


@pytest.mark.asyncio
async def test_auto_activate_single_bundle():
    """Test that auto-activation activates a single bundle on disk."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Create a fake bundle directory with kubeconfig
        fake_bundle_dir = bundle_dir / "test_bundle"
        fake_bundle_dir.mkdir()
        kubeconfig_path = fake_bundle_dir / "kubeconfig"
        kubeconfig_path.write_text("fake kubeconfig")

        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)

            # Mock _initialize_with_sbctl to avoid actual sbctl startup
            manager._initialize_with_sbctl = AsyncMock()

            await manager._auto_activate_bundle_if_exists()

            # Verify bundle was auto-activated
            assert manager.active_bundle is not None
            assert manager.active_bundle.id == "test_bundle"
            assert manager.active_bundle.source == "<restored-from-disk>"
            assert manager.active_bundle.initialized is True


@pytest.mark.asyncio
async def test_auto_activate_multiple_bundles_cleanup():
    """Test that auto-activation cleans up when multiple bundles exist."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Create multiple fake bundle directories
        bundle1_dir = bundle_dir / "bundle1"
        bundle2_dir = bundle_dir / "bundle2"
        bundle1_dir.mkdir()
        bundle2_dir.mkdir()

        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)
            await manager._auto_activate_bundle_if_exists()

            # Verify bundles were cleaned up
            assert not bundle1_dir.exists()
            assert not bundle2_dir.exists()
            assert manager.active_bundle is None


@pytest.mark.asyncio
async def test_ensure_bundle_active_with_active_bundle():
    """Test that _ensure_bundle_active returns active bundle if set."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        # Set an active bundle
        manager.active_bundle = BundleMetadata(
            id="test_bundle",
            source="test_source",
            path=bundle_dir / "test_bundle",
            kubeconfig_path=bundle_dir / "test_bundle" / "kubeconfig",
            initialized=True,
        )

        result = manager._ensure_bundle_active()
        assert result == manager.active_bundle


@pytest.mark.asyncio
async def test_ensure_bundle_active_no_bundle_no_single_mode():
    """Test that _ensure_bundle_active raises error when no bundle and single mode off."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        with pytest.raises(RuntimeError, match="No bundle is active"):
            manager._ensure_bundle_active()


@pytest.mark.asyncio
async def test_ensure_bundle_active_auto_discover():
    """Test that _ensure_bundle_active auto-discovers bundle in single mode."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Create a fake bundle directory
        fake_bundle_dir = bundle_dir / "discovered_bundle"
        fake_bundle_dir.mkdir()
        kubeconfig_path = fake_bundle_dir / "kubeconfig"
        kubeconfig_path.write_text("fake kubeconfig")

        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)

            result = manager._ensure_bundle_active()

            # Verify bundle was auto-discovered
            assert result is not None
            assert result.id == "discovered_bundle"
            assert result.source == "<restored-from-disk>"
            assert manager.active_bundle is not None


@pytest.mark.asyncio
async def test_initialize_bundle_cleans_up_in_single_mode():
    """Test that initialize_bundle cleans up all bundles in single mode."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Create existing bundle directories
        old_bundle1 = bundle_dir / "old_bundle1"
        old_bundle2 = bundle_dir / "old_bundle2"
        old_bundle1.mkdir()
        old_bundle2.mkdir()

        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)

            # Mock bundle initialization to avoid actual download/extraction
            manager._download_bundle = AsyncMock(return_value=Path("/fake/bundle.tar.gz"))
            manager._initialize_with_sbctl = AsyncMock()
            manager._generate_bundle_id = lambda x: "new_bundle"

            # Mock file operations
            with patch("troubleshoot_mcp_server.bundle.Path.exists", return_value=True):
                with patch("troubleshoot_mcp_server.bundle.tarfile.open"):
                    try:
                        await manager.initialize_bundle("https://example.com/bundle.tar.gz")
                    except Exception:
                        # We expect this to fail due to mocking, but cleanup should have happened
                        pass

            # Verify old bundles were cleaned up
            assert not old_bundle1.exists()
            assert not old_bundle2.exists()


@pytest.mark.asyncio
async def test_single_bundle_mode_disabled_no_cleanup():
    """Test that normal mode doesn't clean up all bundles."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Create existing bundle directories
        old_bundle = bundle_dir / "old_bundle"
        old_bundle.mkdir()

        # Single bundle mode disabled
        manager = BundleManager(bundle_dir)

        # Mock bundle initialization
        manager._download_bundle = AsyncMock(return_value=Path("/fake/bundle.tar.gz"))
        manager._initialize_with_sbctl = AsyncMock()
        manager._generate_bundle_id = lambda x: "new_bundle"

        # In normal mode, old_bundle should remain untouched
        # (only active_bundle gets cleaned up via _cleanup_active_bundle)
        with patch("troubleshoot_mcp_server.bundle.Path.exists", return_value=True):
            with patch("troubleshoot_mcp_server.bundle.tarfile.open"):
                try:
                    await manager.initialize_bundle("https://example.com/bundle.tar.gz")
                except Exception:
                    pass

        # Old bundle should still exist (not cleaned up in normal mode)
        assert old_bundle.exists()


@pytest.mark.asyncio
async def test_host_only_bundle_auto_activation():
    """Test that host-only bundles (no kubeconfig) can be auto-activated."""
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Create a fake bundle directory WITHOUT kubeconfig (host-only)
        fake_bundle_dir = bundle_dir / "host_only_bundle"
        fake_bundle_dir.mkdir()

        with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
            manager = BundleManager(bundle_dir)
            manager._initialize_with_sbctl = AsyncMock()

            await manager._auto_activate_bundle_if_exists()

            # Verify bundle was auto-activated as host-only
            assert manager.active_bundle is not None
            assert manager.active_bundle.id == "host_only_bundle"
            assert manager.active_bundle.host_only_bundle is True
