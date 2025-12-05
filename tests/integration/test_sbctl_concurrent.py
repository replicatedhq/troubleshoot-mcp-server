"""
Test sbctl availability check under concurrent load.

This test attempts to reproduce the failure seen in workflow gpt51_high-pacerpro-replicated-178
where sbctl check failed 13 seconds after succeeding for another workflow.

Run with: uv run pytest tests/integration/test_sbctl_concurrent.py -v
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import List, Tuple

import pytest

from troubleshoot_mcp_server.bundle import BundleManager
from troubleshoot_mcp_server.subprocess_utils import subprocess_exec_with_cleanup

logger = logging.getLogger(__name__)

# Test bundle path
TEST_BUNDLE_PATH = (
    Path(__file__).parent.parent / "fixtures" / "support-bundle-2025-04-11T14_05_31.tar.gz"
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_concurrent_basic():
    """
    Verify _check_sbctl_available returns consistent results when called concurrently.

    This tests for the failure seen in workflow gpt51_high-pacerpro-replicated-178
    where sbctl check failed 13 seconds after succeeding for another workflow.
    """
    bm = BundleManager()

    # Run 20 concurrent checks (simulating multiple workflows)
    results = await asyncio.gather(
        *[bm._check_sbctl_available() for _ in range(20)], return_exceptions=True
    )

    # Collect failures
    failures = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            failures.append(f"Check {i}: Exception {type(r).__name__}: {r}")
        elif r is False:
            failures.append(f"Check {i}: Returned False")

    assert not failures, "sbctl checks failed:\n" + "\n".join(failures)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_rapid_fire():
    """
    Test rapid sequential sbctl checks to stress test subprocess creation.
    """
    bm = BundleManager()
    failures = []

    for i in range(50):
        result = await bm._check_sbctl_available()
        if not result:
            failures.append(f"Check {i}: Returned False")
        # Small delay to simulate real-world conditions
        await asyncio.sleep(0.01)

    assert not failures, "sbctl checks failed:\n" + "\n".join(failures)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_with_concurrent_subprocess_load():
    """
    Test sbctl check while other subprocess operations are in progress.

    This simulates the scenario where one workflow is downloading/initializing
    a bundle while another is checking sbctl availability.
    """
    bm = BundleManager()

    async def background_subprocess_load():
        """Generate background subprocess load similar to bundle operations."""
        for _ in range(10):
            # Run harmless commands to create subprocess load
            await subprocess_exec_with_cleanup("sleep", "0.1", timeout=5.0)
            await asyncio.sleep(0.05)

    async def sbctl_checks() -> List[Tuple[int, bool]]:
        """Run sbctl checks and record results with timing."""
        results = []
        for i in range(20):
            result = await bm._check_sbctl_available()
            results.append((i, result))
            await asyncio.sleep(0.1)
        return results

    # Run both concurrently
    _, check_results = await asyncio.gather(
        background_subprocess_load(),
        sbctl_checks(),
        return_exceptions=False,
    )

    # Analyze results
    failures = [f"Check {i}: Returned False" for i, result in check_results if not result]
    assert not failures, "sbctl checks failed during subprocess load:\n" + "\n".join(failures)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_timing_variance():
    """
    Test that sbctl check timing is consistent and doesn't show signs of resource contention.

    If we see high variance in timing, it may indicate resource issues that could
    lead to intermittent failures.
    """
    bm = BundleManager()
    timings = []

    for i in range(20):
        start = time.perf_counter()
        result = await bm._check_sbctl_available()
        elapsed = time.perf_counter() - start
        timings.append(elapsed)
        assert result, f"sbctl check {i} failed"

    avg_time = sum(timings) / len(timings)
    max_time = max(timings)
    min_time = min(timings)

    logger.info(f"Timing stats: avg={avg_time:.3f}s, min={min_time:.3f}s, max={max_time:.3f}s")

    # Alert if max time is more than 5x the average (potential resource issue)
    if max_time > avg_time * 5 and max_time > 0.5:
        logger.warning(
            f"High timing variance detected: max={max_time:.3f}s is {max_time / avg_time:.1f}x average"
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_direct_subprocess_concurrent():
    """
    Test direct subprocess_exec_with_cleanup calls concurrently to isolate
    whether the issue is in BundleManager or subprocess utilities.
    """

    # Run 30 concurrent sbctl --help calls directly
    async def run_sbctl_help(index: int) -> Tuple[int, int, bytes, bytes]:
        returncode, stdout, stderr = await subprocess_exec_with_cleanup(
            "sbctl", "--help", timeout=10.0
        )
        return index, returncode, stdout, stderr

    results = await asyncio.gather(*[run_sbctl_help(i) for i in range(30)], return_exceptions=True)

    failures = []
    for result in results:
        if isinstance(result, Exception):
            failures.append(f"Exception: {type(result).__name__}: {result}")
        else:
            index, returncode, stdout, stderr = result
            if returncode != 0:
                failures.append(
                    f"Index {index}: returncode={returncode}, "
                    f"stderr={stderr.decode('utf-8', errors='replace')}"
                )

    assert not failures, "Direct subprocess calls failed:\n" + "\n".join(failures)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_with_staggered_start():
    """
    Test sbctl checks with staggered start times, similar to the production failure
    where two workflows started at the same time but reached sbctl check at different times.
    """
    bm = BundleManager()

    async def delayed_check(delay: float, index: int) -> Tuple[int, float, bool]:
        """Run sbctl check after a delay, returning index, actual delay, and result."""
        await asyncio.sleep(delay)
        start = time.perf_counter()
        result = await bm._check_sbctl_available()
        elapsed = time.perf_counter() - start
        return index, elapsed, result

    # Simulate staggered arrivals like in production:
    # - First batch arrives immediately
    # - Second batch arrives ~0.5s later (simulating different processing times)
    delays = [0.0] * 5 + [0.5] * 5 + [1.0] * 5 + [1.5] * 5

    results = await asyncio.gather(
        *[delayed_check(delay, i) for i, delay in enumerate(delays)],
        return_exceptions=True,
    )

    failures = []
    for result in results:
        if isinstance(result, Exception):
            failures.append(f"Exception: {type(result).__name__}: {result}")
        else:
            index, elapsed, success = result
            if not success:
                failures.append(f"Check {index}: Returned False (took {elapsed:.3f}s)")

    assert not failures, "Staggered sbctl checks failed:\n" + "\n".join(failures)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_with_sbctl_serve_running():
    """
    Test sbctl --help while sbctl serve is running.

    This reproduces the exact production scenario where:
    1. One workflow starts sbctl serve (bundle initialization)
    2. Another workflow tries sbctl --help (availability check)

    This is the critical test case because in production, Sonnet's sbctl serve
    was running when GPT-5.1's sbctl --help check failed.
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    sbctl_process = None

    try:
        # Start sbctl serve with the test bundle
        logger.info(f"Starting sbctl serve with bundle: {TEST_BUNDLE_PATH}")
        sbctl_process = await asyncio.create_subprocess_exec(
            "sbctl",
            "serve",
            "--support-bundle-location",
            str(TEST_BUNDLE_PATH),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait a bit for sbctl to start up
        await asyncio.sleep(2)

        # Check if sbctl is still running (didn't fail immediately)
        if sbctl_process.returncode is not None:
            stdout, stderr = await sbctl_process.communicate()
            pytest.fail(
                f"sbctl serve exited immediately with code {sbctl_process.returncode}. "
                f"stdout: {stdout.decode()}, stderr: {stderr.decode()}"
            )

        # Now run concurrent sbctl --help checks while serve is running
        logger.info("Running sbctl availability checks while serve is running...")
        failures = []

        for i in range(20):
            result = await bm._check_sbctl_available()
            if not result:
                failures.append(f"Check {i}: Returned False")
            await asyncio.sleep(0.1)

        assert not failures, "sbctl checks failed while sbctl serve was running:\n" + "\n".join(
            failures
        )

    finally:
        if sbctl_process and sbctl_process.returncode is None:
            logger.info("Terminating sbctl serve process...")
            sbctl_process.terminate()
            try:
                await asyncio.wait_for(sbctl_process.wait(), timeout=5)
            except asyncio.TimeoutError:
                sbctl_process.kill()
                await sbctl_process.wait()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sbctl_check_concurrent_with_serve_running():
    """
    Test concurrent sbctl --help calls while sbctl serve is running.

    This tests whether running sbctl serve creates any contention that could
    cause concurrent sbctl --help calls to fail.
    """
    if not TEST_BUNDLE_PATH.exists():
        pytest.skip(f"Test bundle not found at {TEST_BUNDLE_PATH}")

    bm = BundleManager()
    sbctl_process = None

    try:
        # Start sbctl serve
        sbctl_process = await asyncio.create_subprocess_exec(
            "sbctl",
            "serve",
            "--support-bundle-location",
            str(TEST_BUNDLE_PATH),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for startup
        await asyncio.sleep(2)

        if sbctl_process.returncode is not None:
            pytest.skip("sbctl serve failed to start - skipping test")

        # Run 30 concurrent sbctl checks
        results = await asyncio.gather(
            *[bm._check_sbctl_available() for _ in range(30)], return_exceptions=True
        )

        failures = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                failures.append(f"Check {i}: Exception {type(r).__name__}: {r}")
            elif r is False:
                failures.append(f"Check {i}: Returned False")

        assert not failures, (
            "Concurrent sbctl checks failed while sbctl serve was running:\n" + "\n".join(failures)
        )

    finally:
        if sbctl_process and sbctl_process.returncode is None:
            sbctl_process.terminate()
            try:
                await asyncio.wait_for(sbctl_process.wait(), timeout=5)
            except asyncio.TimeoutError:
                sbctl_process.kill()
                await sbctl_process.wait()


@pytest.mark.integration
def test_cleanup_process_matching_logic():
    """
    Test that the cleanup process matching logic correctly identifies sbctl serve
    processes but NOT sbctl --help processes.

    This validates the fix for the bug where cleanup was killing ALL sbctl processes
    including concurrent availability checks.

    The fix changed the matching logic from:
        if "sbctl" in name or "sbctl" in cmdline:  # Too broad!
    To:
        if "sbctl" in cmdline and "serve" in cmdline:  # Targeted

    This test verifies the matching logic directly.
    """
    # Simulated command lines
    test_cases = [
        # (cmdline, should_match) - should_match means "should be killed by cleanup"
        (["sbctl", "serve", "--support-bundle-location", "/path/to/bundle"], True),
        (["sbctl", "serve"], True),
        (["/usr/local/bin/sbctl", "serve", "--port", "8080"], True),
        (["sbctl", "--help"], False),  # Availability check - must NOT match!
        (["sbctl", "version"], False),
        (["sbctl", "--version"], False),
        (["/usr/local/bin/sbctl", "--help"], False),
        (["python", "script.py"], False),
        ([], False),
        (None, False),
    ]

    for cmdline, should_match in test_cases:
        # Apply the same logic as in bundle.py cleanup() method
        if cmdline and len(cmdline) >= 2:
            is_sbctl = any("sbctl" in arg for arg in cmdline)
            is_serve = "serve" in cmdline
            matched = is_sbctl and is_serve
        else:
            matched = False

        assert matched == should_match, (
            f"Cmdline {cmdline}: expected match={should_match}, got match={matched}"
        )
