"""
Integration tests for Single Bundle Mode stateless operation.

These tests verify that single bundle mode enables stateless operation
by simulating server restarts and ensuring bundles are auto-activated.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from troubleshoot_mcp_server.bundle import BundleManager
from troubleshoot_mcp_server.kubectl import KubectlExecutor

logger = logging.getLogger(__name__)

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def persistent_bundle_dir():
    """
    Fixture that provides a persistent bundle directory.

    This directory simulates persistent storage that survives server restarts.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        yield bundle_dir


@pytest.mark.asyncio
async def test_stateless_operation_server_restart(persistent_bundle_dir, test_support_bundle):
    """
    Test that bundles persist across server restarts in single bundle mode.

    Simulates:
    1. Server start → initialize_bundle → server stop
    2. New server start → auto-activates bundle → tool succeeds
    """
    # === PHASE 1: First server instance - initialize bundle ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)

        # Mock sbctl to avoid actual subprocess
        manager1._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )

        # Initialize bundle
        bundle = await manager1.initialize_bundle(str(test_support_bundle))
        assert bundle is not None
        assert manager1.active_bundle is not None
        bundle_id = manager1.active_bundle.id

        # Simulate server shutdown (but bundle preserved on disk)
        await manager1._terminate_sbctl_process()
        # Don't clean up - PRESERVE_BUNDLES is true and files remain

    # === PHASE 2: New server instance - auto-activate ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager2 = BundleManager(persistent_bundle_dir)

        # Mock sbctl for restart
        manager2._initialize_with_sbctl = AsyncMock()

        # Auto-activate should find the persisted bundle
        await manager2._auto_activate_bundle_if_exists()

        # Verify bundle was auto-activated
        assert manager2.active_bundle is not None
        assert manager2.active_bundle.id == bundle_id
        assert manager2.active_bundle.source == "<restored-from-disk>"


@pytest.mark.asyncio
async def test_concurrent_initialization_in_single_mode(persistent_bundle_dir, test_support_bundle):
    """
    Test that concurrent initialization enforces single bundle invariant.

    Verifies that when initialize_bundle is called again, it cleans up
    the first bundle before creating a new one.
    """
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager = BundleManager(persistent_bundle_dir)

        # Mock sbctl
        manager._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )

        # Initialize first bundle
        bundle1 = await manager.initialize_bundle(str(test_support_bundle))
        bundle1_id = bundle1.id
        bundle1_path = persistent_bundle_dir / bundle1_id

        # Verify first bundle directory exists
        assert bundle1_path.exists()

        # Initialize second bundle (should clean up first)
        # Use the same test bundle path for the second initialization
        await manager.initialize_bundle(str(test_support_bundle), force=True)

        # Verify first bundle was cleaned up
        assert not bundle1_path.exists()
        # Note: The key point is the cleanup happened before re-initialization


@pytest.mark.asyncio
async def test_ensure_bundle_active_across_restarts(persistent_bundle_dir, test_support_bundle):
    """
    Test that _ensure_bundle_active auto-discovers bundles across restarts.
    """
    # === PHASE 1: Initialize bundle ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)
        manager1._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )

        bundle = await manager1.initialize_bundle(str(test_support_bundle))
        bundle_id = bundle.id

        # Simulate server shutdown
        await manager1._terminate_sbctl_process()

    # === PHASE 2: New server - use _ensure_bundle_active ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager2 = BundleManager(persistent_bundle_dir)

        # Don't call auto-activate - let _ensure_bundle_active handle it
        result = manager2._ensure_bundle_active()

        # Verify bundle was auto-discovered
        assert result is not None
        assert result.id == bundle_id


@pytest.mark.asyncio
async def test_multiple_bundles_cleaned_on_startup(persistent_bundle_dir):
    """
    Test that multiple bundles are cleaned up on startup in single bundle mode.
    """
    # Create multiple bundle directories (simulating corrupted state)
    bundle1_dir = persistent_bundle_dir / "bundle1"
    bundle2_dir = persistent_bundle_dir / "bundle2"
    bundle3_dir = persistent_bundle_dir / "bundle3"
    bundle1_dir.mkdir()
    bundle2_dir.mkdir()
    bundle3_dir.mkdir()

    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true"}):
        manager = BundleManager(persistent_bundle_dir)
        await manager._auto_activate_bundle_if_exists()

        # Verify all bundles were cleaned up
        assert not bundle1_dir.exists()
        assert not bundle2_dir.exists()
        assert not bundle3_dir.exists()
        assert manager.active_bundle is None


@pytest.mark.asyncio
async def test_normal_mode_no_auto_activation(persistent_bundle_dir, test_support_bundle):
    """
    Test that auto-activation does NOT happen when single bundle mode is disabled.
    """
    # Initialize bundle with single mode enabled
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)
        manager1._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )
        await manager1.initialize_bundle(str(test_support_bundle))
        await manager1._terminate_sbctl_process()

    # New server with single mode DISABLED
    manager2 = BundleManager(persistent_bundle_dir)
    await manager2._auto_activate_bundle_if_exists()

    # Verify NO auto-activation happened
    assert manager2.active_bundle is None


@pytest.mark.asyncio
async def test_preserve_bundles_works_with_single_mode(persistent_bundle_dir, test_support_bundle):
    """
    Test that PRESERVE_BUNDLES=true and single bundle mode work together.
    """
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)
        manager1._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )

        bundle = await manager1.initialize_bundle(str(test_support_bundle))
        bundle_path = bundle.path

        # Clean up (but preserve bundle due to env var)
        await manager1.cleanup()

        # Verify bundle directory still exists
        assert bundle_path.exists()

        # New server instance can auto-activate it
        manager2 = BundleManager(persistent_bundle_dir)
        manager2._initialize_with_sbctl = AsyncMock()
        await manager2._auto_activate_bundle_if_exists()

        assert manager2.active_bundle is not None


@pytest.mark.asyncio
async def test_kubectl_executor_uses_auto_activated_bundle(
    persistent_bundle_dir, test_support_bundle
):
    """
    Test that KubectlExecutor can use an auto-activated bundle.

    This simulates a Temporal workflow where each activity gets a fresh
    MCP server that auto-activates the persisted bundle.
    """
    # === PHASE 1: Initialize bundle ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)
        manager1._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )

        await manager1.initialize_bundle(str(test_support_bundle))
        await manager1._terminate_sbctl_process()

    # === PHASE 2: New server - kubectl executor should work ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager2 = BundleManager(persistent_bundle_dir)
        manager2._initialize_with_sbctl = AsyncMock()
        await manager2._auto_activate_bundle_if_exists()

        # Create kubectl executor
        _ = KubectlExecutor(manager2)

        # Verify bundle is available to kubectl executor
        bundle = manager2._ensure_bundle_active()
        assert bundle is not None
        assert bundle.kubeconfig_path is not None


@pytest.mark.asyncio
async def test_check_api_server_auto_restarts_sbctl_after_restore(
    persistent_bundle_dir, test_support_bundle
):
    """
    Test that check_api_server_available auto-restarts sbctl when bundle is restored.

    This addresses the scenario where:
    1. Bundle is initialized with sbctl running
    2. Server restarts (sbctl process dies)
    3. Bundle is auto-activated from disk (initialized=True, but sbctl_process=None)
    4. check_api_server_available is called and should auto-restart sbctl
    """
    # === PHASE 1: Initialize bundle with sbctl ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)
        manager1._initialize_with_sbctl = AsyncMock(
            return_value=persistent_bundle_dir / "fake_bundle" / "kubeconfig"
        )
        manager1._start_sbctl_process = AsyncMock()

        bundle = await manager1.initialize_bundle(str(test_support_bundle))
        assert bundle.initialized is True

        # Simulate server shutdown (sbctl process dies)
        await manager1._terminate_sbctl_process()

    # === PHASE 2: New server - bundle restored but sbctl not running ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager2 = BundleManager(persistent_bundle_dir)

        # Auto-activate finds the bundle but doesn't start sbctl (mocked to fail silently)
        manager2._initialize_with_sbctl = AsyncMock(
            side_effect=Exception("sbctl start failed during auto-activate")
        )
        await manager2._auto_activate_bundle_if_exists()

        # Verify bundle was restored but sbctl is not running
        assert manager2.active_bundle is not None
        assert manager2.active_bundle.initialized is True
        assert manager2.sbctl_process is None

        # Mock _restart_sbctl_process to verify it gets called
        manager2._restart_sbctl_process = AsyncMock(return_value=True)

        # Call check_api_server_available - should trigger auto-restart
        # Note: This will still return False because we're mocking, but the key
        # is that _restart_sbctl_process should be called
        result = await manager2.check_api_server_available()

        # Verify sbctl restart was attempted
        manager2._restart_sbctl_process.assert_called_once()

        # The result depends on whether the restart succeeded and API is available
        # In this mock scenario, restart returns True but API check may still fail
        assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_sbctl_auto_restart_real_bundle(persistent_bundle_dir, test_support_bundle):
    """
    REAL integration test: Verify sbctl auto-restarts after server restart.

    No mocks - uses real sbctl process to verify:
    1. Bundle initialized with real sbctl running
    2. Server restart simulated (sbctl terminated)
    3. New manager instance auto-discovers bundle
    4. check_api_server_available() auto-restarts sbctl
    5. System fully recovers with sbctl running
    """
    # === PHASE 1: Initialize bundle with REAL sbctl ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)

        # Initialize with real bundle - starts real sbctl
        bundle = await manager1.initialize_bundle(str(test_support_bundle))
        assert bundle.initialized is True

        # Verify sbctl process is actually running
        assert manager1.sbctl_process is not None
        assert manager1.sbctl_process.returncode is None, "sbctl should be running"

        bundle_id = bundle.id

        # Simulate server shutdown - terminate sbctl
        await manager1._terminate_sbctl_process()
        assert manager1.sbctl_process is None or manager1.sbctl_process.returncode is not None

    # === PHASE 2: New server - simulate restart ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager2 = BundleManager(persistent_bundle_dir)

        # Auto-activate finds bundle on disk
        await manager2._auto_activate_bundle_if_exists()

        # Bundle should be restored
        assert manager2.active_bundle is not None
        assert manager2.active_bundle.id == bundle_id
        assert manager2.active_bundle.initialized is True

        # sbctl might not be running yet (depends on _auto_activate success)
        # This is the scenario we're testing

        # Call check_api_server_available - should auto-restart sbctl if needed
        await manager2.check_api_server_available()

        # KEY ASSERTION: sbctl should now be running
        assert manager2.sbctl_process is not None, "sbctl should be running after check"
        assert manager2.sbctl_process.returncode is None, "sbctl process should be alive"

        # Cleanup
        await manager2.cleanup()


@pytest.mark.asyncio
async def test_sbctl_restart_deletes_stale_kubeconfig(persistent_bundle_dir, test_support_bundle):
    """
    Test that _restart_sbctl_process deletes stale kubeconfig before restart.

    This test verifies the fix for the stale kubeconfig port bug where:
    - An old kubeconfig with a stale port survives across restarts
    - The fix ensures the stale kubeconfig is deleted before sbctl restarts

    This test should FAIL before the fix and PASS after the fix.
    """
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager = BundleManager(persistent_bundle_dir)

        # Initialize with real bundle - starts real sbctl
        bundle = await manager.initialize_bundle(str(test_support_bundle))
        assert bundle.initialized is True
        assert manager.sbctl_process is not None

        kubeconfig_path = bundle.kubeconfig_path
        assert kubeconfig_path.exists()

        # Simulate sbctl crash
        await manager._terminate_sbctl_process()
        assert manager.sbctl_process is None or manager.sbctl_process.returncode is not None

        # Create a STALE kubeconfig with a fake port that will never work
        stale_kubeconfig = """apiVersion: v1
clusters:
- cluster:
    insecure-skip-tls-verify: true
    server: http://127.0.0.1:99999
  name: sb
contexts:
- context:
    cluster: sb
    user: sb
  name: sb
current-context: sb
kind: Config
preferences: {}
users:
- name: sb
  user:
    client-certificate-data: LS0=
    client-key-data: LS0=
"""
        kubeconfig_path.write_text(stale_kubeconfig)
        assert kubeconfig_path.exists(), "Stale kubeconfig should exist"
        assert "99999" in kubeconfig_path.read_text(), "Stale kubeconfig should contain port 99999"

        # Now restart sbctl - it should DELETE the stale kubeconfig
        restart_success = await manager._restart_sbctl_process()
        assert restart_success, "sbctl restart should succeed"

        # **CRITICAL ASSERTION**: Verify the stale kubeconfig was DELETED
        # This is the core fix - ensuring the stale kubeconfig doesn't persist
        # Note: We're not testing if a NEW kubeconfig is created (that's sbctl's job)
        # We're only testing that the STALE one is removed
        await asyncio.sleep(1)  # Give a moment for any filesystem operations to complete

        if kubeconfig_path.exists():
            # If a kubeconfig exists, it should NOT be the stale one with port 99999
            current_config = kubeconfig_path.read_text()
            assert "99999" not in current_config, (
                f"Stale kubeconfig with port 99999 should have been deleted! "
                f"Current content:\n{current_config}"
            )
            logger.info("SUCCESS: New kubeconfig created without stale port 99999")
        else:
            # Kubeconfig doesn't exist - that's OK, it means the stale one was deleted
            # (sbctl might not recreate it when serving from a directory)
            logger.info("SUCCESS: Stale kubeconfig was deleted")

        # Cleanup
        await manager.cleanup()
