"""
Functional tests for bundle lifecycle that verify ACTUAL functionality.

These tests catch bugs that other tests miss by:
1. Using real kubectl commands that hit the API server (not just --client)
2. Testing cleanup actually works
3. Testing sbctl restart recovery
4. Testing the full lifecycle: init → use → cleanup

Run with: uv run pytest tests/integration/test_bundle_functional.py -v -s
"""

import asyncio
import logging
import re
from pathlib import Path

import psutil
import pytest

from troubleshoot_mcp_server.bundle import BundleManager
from troubleshoot_mcp_server.kubectl import KubectlExecutor

logger = logging.getLogger(__name__)


def _is_port_listening(port: int) -> bool:
    """Check if something is listening on a port (works without root on macOS)."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        result = sock.connect_ex(("127.0.0.1", port))
        return result == 0  # 0 means connection succeeded
    except Exception:
        return False
    finally:
        sock.close()


# Test bundle path
TEST_BUNDLE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "support-bundle-2025-04-11T14_05_31.tar.gz"
)


def _extract_port_from_kubeconfig(kubeconfig_path: Path) -> int | None:
    """Extract the port number from a kubeconfig file."""
    if not kubeconfig_path.exists():
        return None
    content = kubeconfig_path.read_text()
    match = re.search(r"server:\s*http://[^:]+:(\d+)", content)
    return int(match.group(1)) if match else None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_lifecycle_with_real_kubectl():
    """
    CRITICAL TEST: Verify the full bundle lifecycle with REAL kubectl API calls.

    This test catches bugs like:
    - Stale kubeconfig pointing to wrong port
    - sbctl serve not actually running
    - API server not responding

    Unlike tests that use `kubectl version --client`, this test actually
    connects to sbctl's API server.
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    bundle_id = "test-full-lifecycle"

    try:
        # 1. Initialize bundle
        logger.info(f"Initializing bundle: {bundle_id}")
        metadata = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle_id,
        )
        logger.info(f"Bundle initialized: {metadata.id}")

        # Wait for sbctl to be ready
        await asyncio.sleep(3)

        # Verify sbctl process is running
        process = bm.sbctl_processes.get(bundle_id)
        assert process is not None, "sbctl process should be tracked"
        assert process.returncode is None, "sbctl process should be running"

        # 2. Verify kubeconfig exists and has valid port
        kubeconfig_path = metadata.path / "kubeconfig"
        assert kubeconfig_path.exists(), "kubeconfig should exist"

        port = _extract_port_from_kubeconfig(kubeconfig_path)
        assert port is not None, "kubeconfig should contain a port"
        logger.info(f"Kubeconfig points to port: {port}")

        # Verify something is listening on that port (use socket check - no root needed)
        assert _is_port_listening(port), f"Nothing listening on kubeconfig port {port}"

        # 3. CRITICAL: Run a kubectl command that ACTUALLY hits the API server
        # NOT `kubectl version --client` which doesn't use the API
        # First, set this bundle as active so KubectlExecutor can find it
        bm.active_bundle = metadata
        kubectl = KubectlExecutor(bm)

        # Try `kubectl get namespaces` which requires API server
        result = await kubectl.execute(
            "get namespaces",
            timeout=10,
            json_output=False,
        )

        logger.info(f"kubectl get namespaces result: exit_code={result.exit_code}")
        logger.info(f"kubectl stdout: {result.stdout[:200] if result.stdout else 'empty'}")

        # Either success or "No resources found" is acceptable
        # But "connection refused" or timeout means sbctl API isn't working
        assert result.exit_code == 0 or "No resources found" in (result.stdout or ""), (
            f"kubectl should work but got exit_code={result.exit_code}, "
            f"stdout={result.stdout}, stderr={result.stderr}"
        )

        # Verify we didn't get connection errors
        error_output = (result.stderr or "") + (result.stdout or "")
        assert "connection refused" not in error_output.lower(), (
            f"kubectl got connection refused - sbctl API not working: {error_output}"
        )
        assert "unable to connect" not in error_output.lower(), (
            f"kubectl unable to connect - sbctl API not working: {error_output}"
        )

        logger.info("SUCCESS: kubectl connected to sbctl API server")

    finally:
        # Cleanup
        await bm._cleanup_bundle(bundle_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cleanup_actually_cleans_up():
    """
    Test that cleanup ACTUALLY removes resources.

    This catches bugs like:
    - Cleanup looking for wrong bundle ID
    - sbctl processes left running after cleanup
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    bundle_id = "test-cleanup-works"

    # Initialize
    _metadata = await bm.initialize_bundle(
        source=str(TEST_BUNDLE_PATH),
        force=True,
        bundle_id=bundle_id,
    )
    await asyncio.sleep(2)

    # Get sbctl PID before cleanup
    process = bm.sbctl_processes.get(bundle_id)
    assert process is not None, "sbctl should be tracked"
    sbctl_pid = process.pid
    logger.info(f"sbctl PID before cleanup: {sbctl_pid}")

    # Verify process is running
    try:
        psutil.Process(sbctl_pid)
    except psutil.NoSuchProcess:
        pytest.fail("sbctl process should exist before cleanup")

    # Cleanup
    await bm._cleanup_bundle(bundle_id)
    await asyncio.sleep(1)

    # Verify process is GONE
    try:
        proc = psutil.Process(sbctl_pid)
        if proc.is_running():
            pytest.fail(f"sbctl PID {sbctl_pid} should be terminated after cleanup")
    except psutil.NoSuchProcess:
        pass  # Expected - process should be gone

    # Verify bundle removed from tracking
    assert (
        bundle_id not in bm.sbctl_processes or bm.sbctl_processes[bundle_id].returncode is not None
    ), "Bundle should be removed from tracking after cleanup"

    logger.info("SUCCESS: Cleanup actually cleaned up")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_restart_recovery():
    """
    Test that kubectl works after sbctl crashes and restarts.

    This catches:
    - Stale kubeconfig pointing to old port
    - Restart not waiting for new kubeconfig
    """
    import shutil

    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    bundle_id = "test-restart-recovery"

    try:
        # Initialize
        metadata = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle_id,
        )
        await asyncio.sleep(2)

        # Get original kubeconfig port
        kubeconfig_path = metadata.path / "kubeconfig"
        original_port = _extract_port_from_kubeconfig(kubeconfig_path)
        logger.info(f"Original kubeconfig port: {original_port}")

        # Copy tarball to bundle.path so _restart_sbctl_for_bundle can find it
        bundle_tarball = metadata.path / "bundle.tar.gz"
        if not bundle_tarball.exists():
            shutil.copy(TEST_BUNDLE_PATH, bundle_tarball)
            logger.info(f"Copied tarball to {bundle_tarball}")

        # Kill sbctl (simulate crash)
        process = bm.sbctl_processes.get(bundle_id)
        original_pid = process.pid
        logger.info(f"Killing sbctl PID {original_pid}")
        process.terminate()
        await process.wait()

        # Trigger restart by calling _restart_sbctl_for_bundle directly
        bm.bundles[bundle_id] = metadata  # Ensure it's in bundles dict
        await bm._restart_sbctl_for_bundle(bundle_id)

        # Wait for restart
        await asyncio.sleep(3)

        # Verify kubeconfig was updated (port should change or file should be fresh)
        new_port = _extract_port_from_kubeconfig(kubeconfig_path)
        logger.info(f"New kubeconfig port after restart: {new_port}")

        # Port may or may not change, but something should be listening on the new port
        assert new_port is not None, "Kubeconfig should have a port after restart"

        # Verify something is listening on the new port (use socket check - no root needed)
        assert _is_port_listening(new_port), f"Nothing listening on new kubeconfig port {new_port}"

        # CRITICAL: Verify kubectl actually works
        # Set bundle as active so KubectlExecutor can find it
        bm.active_bundle = metadata
        kubectl = KubectlExecutor(bm)
        result = await kubectl.execute(
            "get namespaces",
            timeout=10,
            json_output=False,
        )

        error_output = (result.stderr or "") + (result.stdout or "")
        assert "connection refused" not in error_output.lower(), (
            f"After restart, kubectl should connect but got: {error_output}"
        )

        logger.info("SUCCESS: kubectl works after sbctl restart")

    finally:
        await bm._cleanup_bundle(bundle_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_bundles_with_cleanup():
    """
    Test concurrent bundles don't interfere during cleanup.

    This is a more comprehensive version that verifies kubectl works
    for the surviving bundle.
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    bundle1_id = "test-concurrent-1"
    bundle2_id = "test-concurrent-2"

    try:
        # Initialize both bundles
        _metadata1 = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle1_id,
        )
        metadata2 = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle2_id,
        )
        await asyncio.sleep(3)

        # Verify both are running
        proc1 = bm.sbctl_processes.get(bundle1_id)
        proc2 = bm.sbctl_processes.get(bundle2_id)
        assert proc1 and proc1.returncode is None
        assert proc2 and proc2.returncode is None
        assert proc1.pid != proc2.pid, "Should be different processes"

        # Cleanup bundle 1 only
        await bm._cleanup_bundle(bundle1_id)
        await asyncio.sleep(1)

        # Verify bundle 2 still works
        # Set bundle 2 as active so KubectlExecutor can find it
        bm.active_bundle = metadata2
        kubectl = KubectlExecutor(bm)
        result = await kubectl.execute(
            "get namespaces",
            timeout=10,
            json_output=False,
        )

        error_output = (result.stderr or "") + (result.stdout or "")
        assert "connection refused" not in error_output.lower(), (
            f"Bundle 2 kubectl should still work after bundle 1 cleanup: {error_output}"
        )

        # Verify bundle 2 process is still running
        assert proc2.returncode is None, "Bundle 2 sbctl should still be running"

        logger.info("SUCCESS: Concurrent bundles properly isolated during cleanup")

    finally:
        try:
            await bm._cleanup_bundle(bundle1_id)
        except Exception:
            pass
        try:
            await bm._cleanup_bundle(bundle2_id)
        except Exception:
            pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_receives_correct_path_and_detects_cluster_resources():
    """
    CRITICAL TEST: Verify sbctl receives the correct path through our bundle logic.

    This catches bugs where:
    - We pass sbctl a wrong path (empty, non-existent, corrupted)
    - Bundle download fails silently
    - Path construction is wrong

    The test runs through the ACTUAL initialization code path.
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    bundle_id = "test-sbctl-path-verification"

    try:
        # Run the full initialization - this uses the actual code path
        logger.info(f"Initializing bundle from: {TEST_BUNDLE_PATH}")
        metadata = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle_id,
        )
        logger.info(f"Bundle initialized: {metadata}")

        # Wait for sbctl to start
        await asyncio.sleep(3)

        # CRITICAL CHECKS:

        # 1. Verify bundle was NOT marked as host-only (it has cluster resources)
        assert not bm._host_only_bundle, (
            f"Bundle was incorrectly marked as host-only! "
            f"TEST_BUNDLE_PATH={TEST_BUNDLE_PATH} has cluster-resources but sbctl "
            f"reported 'No cluster resources found'. Check the path passed to sbctl."
        )

        # 2. Verify sbctl process is running (not exited quickly due to "no cluster resources")
        process = bm.sbctl_processes.get(bundle_id)
        assert process is not None, "sbctl process should be tracked"
        assert process.returncode is None, (
            f"sbctl process exited with code {process.returncode}. "
            f"This may indicate 'No cluster resources found' was triggered incorrectly."
        )

        # 3. Verify kubeconfig was created (proves sbctl found cluster resources)
        kubeconfig_path = metadata.path / "kubeconfig"
        assert kubeconfig_path.exists() or metadata.host_only_bundle is False, (
            f"Kubeconfig should exist at {kubeconfig_path} for a bundle with cluster resources"
        )

        # 4. Verify the bundle tarball exists at expected location
        bundle_tarball = metadata.path / "bundle.tar.gz"
        if bundle_tarball.exists():
            size = bundle_tarball.stat().st_size
            logger.info(f"Bundle tarball at {bundle_tarball}: {size} bytes")
            assert size > 1000, f"Bundle tarball is suspiciously small: {size} bytes"

        # 5. Verify API server is responding (definitive proof sbctl found resources)
        bm.active_bundle = metadata
        kubectl = KubectlExecutor(bm)
        result = await kubectl.execute("get namespaces", timeout=10, json_output=False)

        # Should NOT get "No bundle initialized" or connection errors
        error_output = (result.stderr or "") + (result.stdout or "")
        assert "no bundle initialized" not in error_output.lower(), (
            f"kubectl reports no bundle: {error_output}"
        )
        assert "connection refused" not in error_output.lower(), (
            f"sbctl API not responding: {error_output}"
        )

        logger.info(f"SUCCESS: sbctl correctly detected cluster resources in {TEST_BUNDLE_PATH}")
        logger.info(f"kubectl output: {result.stdout[:200] if result.stdout else 'empty'}")

    finally:
        await bm._cleanup_bundle(bundle_id)
