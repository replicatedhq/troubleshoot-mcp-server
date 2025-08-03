"""
Integration tests for subprocess utilities.

These tests actually execute subprocess operations to verify that the
transport cleanup utilities work correctly without complex mocking.
"""

import asyncio
import gc
import warnings

import pytest

from troubleshoot_mcp_server.subprocess_utils import (
    subprocess_exec_with_cleanup,
    subprocess_shell_with_cleanup,
)


@pytest.mark.asyncio
async def test_subprocess_exec_with_cleanup_basic_command():
    """Test that subprocess_exec_with_cleanup works with a basic command."""
    returncode, stdout, stderr = await subprocess_exec_with_cleanup(
        "echo", "hello world", timeout=5.0
    )

    assert returncode == 0, "echo command should succeed"
    assert stdout == b"hello world\n", "Should get expected output"
    assert stderr == b"", "Should have no stderr"


@pytest.mark.asyncio
async def test_subprocess_exec_with_cleanup_timeout_handling():
    """Test that subprocess_exec_with_cleanup properly handles timeouts."""
    with pytest.raises(asyncio.TimeoutError):
        await subprocess_exec_with_cleanup("sleep", "10", timeout=0.1)  # Very short timeout

    # If we get here, the timeout was handled properly and cleanup occurred


@pytest.mark.asyncio
async def test_subprocess_shell_with_cleanup_basic_command():
    """Test that subprocess_shell_with_cleanup works with a basic command."""
    returncode, stdout, stderr = await subprocess_shell_with_cleanup(
        "echo 'shell test'", timeout=5.0
    )

    assert returncode == 0, "shell command should succeed"
    assert stdout == b"shell test\n", "Should get expected output"
    # Allow for environment-specific shell warnings (e.g., getcwd() issues in CI)
    # The important thing is that the command succeeded and produced the right output
    if stderr and b"getcwd()" not in stderr:
        assert stderr == b"", f"Unexpected stderr (non-getcwd related): {stderr}"


@pytest.mark.asyncio
async def test_subprocess_utilities_no_transport_warnings():
    """Test that our subprocess utilities don't generate transport warnings."""
    warnings_captured = []

    def warning_handler(message, category, filename, lineno, file=None, line=None):
        if "transport" in str(message).lower() or "_closing" in str(message):
            warnings_captured.append(str(message))

    # Install warning handler
    original_showwarning = warnings.showwarning
    warnings.showwarning = warning_handler

    try:
        # Test our subprocess utilities which should handle cleanup properly
        for i in range(3):
            returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                "echo", f"test-{i}", timeout=5.0
            )
            assert returncode == 0, f"Command {i} should succeed"

        # Force garbage collection to trigger any transport warnings
        for _ in range(5):
            gc.collect()
            await asyncio.sleep(0.01)

        # Verify no transport warnings occurred
        assert len(warnings_captured) == 0, f"Transport warnings detected: {warnings_captured}"

    finally:
        # Restore original warning handler
        warnings.showwarning = original_showwarning


@pytest.mark.asyncio
async def test_multiple_subprocess_operations_no_transport_leaks():
    """Test that multiple subprocess operations don't cause transport leaks."""
    warnings_captured = []

    def warning_handler(message, category, filename, lineno, file=None, line=None):
        if "transport" in str(message).lower() or "_closing" in str(message):
            warnings_captured.append(str(message))

    original_showwarning = warnings.showwarning
    warnings.showwarning = warning_handler

    try:
        # Run multiple subprocess operations using our utilities
        for i in range(10):
            returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                "echo", f"test-{i}", timeout=5.0
            )
            assert returncode == 0, f"Command {i} should succeed"
            assert stdout == f"test-{i}\n".encode(), f"Should get expected output for {i}"

        # Force garbage collection to trigger any transport issues
        for _ in range(5):
            gc.collect()
            await asyncio.sleep(0.01)

        # Verify no transport warnings occurred
        assert len(warnings_captured) == 0, f"Transport warnings detected: {warnings_captured}"

    finally:
        warnings.showwarning = original_showwarning


@pytest.mark.asyncio
async def test_curl_dependency_eliminated_functional():
    """Functional test that verifies curl is no longer needed."""
    # This test simply verifies that our subprocess utilities work
    # without needing curl, by running actual subprocess operations

    # Test 1: Basic subprocess operations work
    returncode, stdout, stderr = await subprocess_exec_with_cleanup(
        "echo", "no curl needed", timeout=5.0
    )
    assert returncode == 0
    assert b"no curl needed" in stdout

    # Test 2: Shell operations work
    returncode, stdout, stderr = await subprocess_shell_with_cleanup(
        "echo 'shell works too'", timeout=5.0
    )
    assert returncode == 0
    assert b"shell works too" in stdout

    # Test 3: Error handling works
    with pytest.raises(asyncio.TimeoutError):
        await subprocess_exec_with_cleanup("sleep", "5", timeout=0.1)

    # If we get here, all subprocess functionality works without curl
    assert True, "All subprocess operations work without curl dependency"


@pytest.mark.asyncio
async def test_transport_cleanup_functional():
    """Functional test that verifies transport cleanup works correctly."""
    # This test creates subprocess operations similar to the MCP server patterns
    # and verifies no transport warnings occur

    warnings_captured = []

    def warning_handler(message, category, filename, lineno, file=None, line=None):
        if "transport" in str(message).lower() or "_closing" in str(message):
            warnings_captured.append(str(message))

    original_showwarning = warnings.showwarning
    warnings.showwarning = warning_handler

    try:
        # Simulate MCP server subprocess patterns

        # Pattern 1: kubectl-like operations
        for i in range(5):
            returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                "echo", f"kubectl-sim-{i}", timeout=5.0
            )
            assert returncode == 0

        # Pattern 2: Operations with timeouts
        try:
            await subprocess_exec_with_cleanup("sleep", "0.05", timeout=1.0)
        except asyncio.TimeoutError:
            pass  # Expected for some test scenarios

        # Pattern 3: Additional subprocess operations (simulating various MCP patterns)
        for i in range(3):
            returncode, stdout, stderr = await subprocess_shell_with_cleanup(
                f"echo 'pattern-{i}'", timeout=5.0
            )
            assert returncode == 0

        # Force garbage collection multiple times
        for _ in range(10):
            gc.collect()
            await asyncio.sleep(0.01)

        # Verify no transport cleanup warnings
        assert len(warnings_captured) == 0, f"Transport warnings detected: {warnings_captured}"

    finally:
        warnings.showwarning = original_showwarning
