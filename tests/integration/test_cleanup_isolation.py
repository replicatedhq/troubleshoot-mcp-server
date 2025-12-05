"""
Test that cleanup of one bundle does NOT affect other bundles.

This is the critical functional test for the fix:
1. Start 2 sbctl serve processes (2 bundles)
2. Call cleanup on bundle 1
3. Verify bundle 2's sbctl serve is still running and working
4. Verify bundle 1 is cleaned up

Run with: uv run pytest tests/integration/test_cleanup_isolation.py -v -s
"""

import asyncio
import logging
from pathlib import Path

import psutil
import pytest

from troubleshoot_mcp_server.bundle import BundleManager

logger = logging.getLogger(__name__)

# Test bundle path
TEST_BUNDLE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "support-bundle-2025-04-11T14_05_31.tar.gz"
)


def get_sbctl_serve_processes() -> list[psutil.Process]:
    """Get all running sbctl serve processes."""
    processes = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"]
            if cmdline and len(cmdline) >= 2:
                is_sbctl = any("sbctl" in arg for arg in cmdline)
                is_serve = "serve" in cmdline
                if is_sbctl and is_serve:
                    processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return processes


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cleanup_isolation_two_bundles():
    """
    CRITICAL TEST: Verify cleanup of one bundle does NOT kill another bundle's process.

    This reproduces the exact failure scenario:
    1. Initialize bundle 1 (starts sbctl serve)
    2. Initialize bundle 2 (starts sbctl serve)
    3. Cleanup bundle 1
    4. Verify bundle 2's sbctl serve is STILL RUNNING
    5. Verify bundle 1's sbctl serve is STOPPED
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()

    # Track initial sbctl processes
    initial_processes = get_sbctl_serve_processes()
    initial_pids = {p.pid for p in initial_processes}
    logger.info(f"Initial sbctl serve PIDs: {initial_pids}")

    bundle1_id = "test-bundle-1"
    bundle2_id = "test-bundle-2"

    try:
        # 1. Initialize bundle 1
        logger.info(f"Initializing bundle 1: {bundle1_id}")
        metadata1 = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle1_id,
        )
        logger.info(f"Bundle 1 initialized: {metadata1}")

        # Wait for sbctl to start
        await asyncio.sleep(2)

        # Get bundle 1's process
        bundle1_proc = bm.sbctl_processes.get(bundle1_id)
        bundle1_pid = bundle1_proc.pid if bundle1_proc else None
        logger.info(f"Bundle 1 sbctl PID: {bundle1_pid}")
        assert bundle1_pid is not None, "Bundle 1 should have an sbctl process"

        # 2. Initialize bundle 2
        logger.info(f"Initializing bundle 2: {bundle2_id}")
        metadata2 = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle2_id,
        )
        logger.info(f"Bundle 2 initialized: {metadata2}")

        # Wait for sbctl to start
        await asyncio.sleep(2)

        # Get bundle 2's process
        bundle2_proc = bm.sbctl_processes.get(bundle2_id)
        bundle2_pid = bundle2_proc.pid if bundle2_proc else None
        logger.info(f"Bundle 2 sbctl PID: {bundle2_pid}")
        assert bundle2_pid is not None, "Bundle 2 should have an sbctl process"

        # Verify both are different processes
        assert bundle1_pid != bundle2_pid, "Bundles should have different sbctl processes"

        # Verify both are running
        current_processes = get_sbctl_serve_processes()
        current_pids = {p.pid for p in current_processes}
        logger.info(f"Current sbctl serve PIDs before cleanup: {current_pids}")

        assert bundle1_pid in current_pids, f"Bundle 1 PID {bundle1_pid} should be running"
        assert bundle2_pid in current_pids, f"Bundle 2 PID {bundle2_pid} should be running"

        # 3. Cleanup bundle 1 ONLY
        logger.info(f"Cleaning up bundle 1: {bundle1_id}")
        await bm._cleanup_bundle(bundle1_id)
        logger.info("Bundle 1 cleanup complete")

        # Give time for process to terminate
        await asyncio.sleep(1)

        # 4. Verify bundle 2 is STILL RUNNING
        current_processes = get_sbctl_serve_processes()
        current_pids = {p.pid for p in current_processes}
        logger.info(f"Current sbctl serve PIDs after cleanup: {current_pids}")

        # THIS IS THE CRITICAL CHECK
        assert bundle2_pid in current_pids, (
            f"FAILURE: Bundle 2 PID {bundle2_pid} was killed by bundle 1's cleanup! "
            f"Current PIDs: {current_pids}"
        )

        # 5. Verify bundle 1 is STOPPED
        assert bundle1_pid not in current_pids, (
            f"Bundle 1 PID {bundle1_pid} should be stopped after cleanup"
        )

        # 6. Verify bundle 2 can still serve requests (sbctl --help should work)
        result = await bm._check_sbctl_available()
        assert result, "sbctl should still be available after bundle 1 cleanup"

        logger.info("SUCCESS: Bundle 2 survived bundle 1's cleanup!")

    finally:
        # Cleanup both bundles
        logger.info("Final cleanup...")
        try:
            await bm._cleanup_bundle(bundle1_id)
        except Exception as e:
            logger.debug(f"Bundle 1 cleanup (expected if already cleaned): {e}")

        try:
            await bm._cleanup_bundle(bundle2_id)
        except Exception as e:
            logger.debug(f"Bundle 2 cleanup: {e}")

        # Verify all test processes are cleaned up
        final_processes = get_sbctl_serve_processes()
        final_pids = {p.pid for p in final_processes}
        new_pids = final_pids - initial_pids
        if new_pids:
            logger.warning(f"Leftover sbctl processes from test: {new_pids}")
            for pid in new_pids:
                try:
                    psutil.Process(pid).terminate()
                except Exception:
                    pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_cleanup_does_not_kill_untracked_external_process():
    """
    Test that full cleanup() only kills processes WE started, not external ones.

    This simulates another application running sbctl serve that we should NOT kill.
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    # Start an "external" sbctl serve process (not managed by BundleManager)
    logger.info("Starting external sbctl serve process...")
    external_proc = await asyncio.create_subprocess_exec(
        "sbctl",
        "serve",
        "--support-bundle-location",
        str(TEST_BUNDLE_PATH),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.sleep(2)

    if external_proc.returncode is not None:
        pytest.skip("Could not start external sbctl serve")

    external_pid = external_proc.pid
    logger.info(f"External sbctl serve PID: {external_pid}")

    try:
        # Create a BundleManager and initialize a bundle
        bm = BundleManager()
        bundle_id = "test-bundle-cleanup"

        _metadata = await bm.initialize_bundle(
            source=str(TEST_BUNDLE_PATH),
            force=True,
            bundle_id=bundle_id,
        )
        await asyncio.sleep(2)

        managed_proc = bm.sbctl_processes.get(bundle_id)
        managed_pid = managed_proc.pid if managed_proc else None
        logger.info(f"Managed sbctl serve PID: {managed_pid}")

        # Call full cleanup()
        logger.info("Calling full cleanup()...")
        await bm.cleanup()
        logger.info("Cleanup complete")

        await asyncio.sleep(1)

        # Verify external process is STILL RUNNING
        try:
            ext_proc = psutil.Process(external_pid)
            assert ext_proc.is_running(), "External process should still be running"
            logger.info(f"SUCCESS: External PID {external_pid} survived cleanup!")
        except psutil.NoSuchProcess:
            pytest.fail(
                f"FAILURE: External sbctl serve (PID {external_pid}) was killed by cleanup!"
            )

        # Verify managed process is stopped
        if managed_pid:
            try:
                proc = psutil.Process(managed_pid)
                if proc.is_running():
                    pytest.fail(f"Managed process PID {managed_pid} should be stopped")
            except psutil.NoSuchProcess:
                pass  # Expected - process should be gone

    finally:
        # Cleanup external process
        logger.info("Terminating external process...")
        external_proc.terminate()
        try:
            await asyncio.wait_for(external_proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            external_proc.kill()
            await external_proc.wait()
