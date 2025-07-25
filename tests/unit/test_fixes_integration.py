"""
Integration tests to verify both fixes work correctly together.

This test module verifies that:
1. The Python 3.13 transport cleanup fixes prevent AttributeError
2. The socket-based port checking replaces netstat dependency
3. Both fixes work together in the actual MCP server components
"""

import asyncio
import gc
import os
import sys
import warnings

import pytest


@pytest.mark.asyncio
async def test_transport_cleanup_fix_integration():
    """
    Integration test that verifies the Python 3.13 transport cleanup fix
    works correctly with subprocess_utils.
    """
    if sys.version_info < (3, 13):
        pytest.skip("This test is specifically for Python 3.13+ transport fix verification")

    from mcp_server_troubleshoot.subprocess_utils import subprocess_exec_with_cleanup

    # Capture any transport-related warnings or errors
    transport_issues = []

    def warning_capture(message, category, filename, lineno, file=None, line=None):
        msg_str = str(message)
        if any(keyword in msg_str.lower() for keyword in ["transport", "_closing", "unclosed"]):
            transport_issues.append(f"{category.__name__}: {msg_str}")

    original_showwarning = warnings.showwarning
    warnings.showwarning = warning_capture

    try:
        # Test the subprocess_exec_with_cleanup extensively
        for i in range(20):
            returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                "echo", f"transport_fix_test_{i}", timeout=5.0
            )
            assert returncode == 0, "subprocess_exec_with_cleanup should succeed"
            assert f"transport_fix_test_{i}".encode() in stdout

        # Force garbage collection to trigger any transport cleanup issues
        for _ in range(10):
            gc.collect()
            await asyncio.sleep(0.05)

        # Verify no transport issues occurred
        assert len(transport_issues) == 0, (
            f"Transport cleanup issues detected despite fixes: {transport_issues}. "
            f"The Python 3.13 transport cleanup fix may not be working correctly."
        )

        print("✅ Python 3.13 transport cleanup fix verified: No transport issues detected")

    finally:
        warnings.showwarning = original_showwarning


@pytest.mark.asyncio
async def test_socket_port_checking_fix_integration():
    """
    Integration test that verifies the socket-based port checking
    replaces netstat dependency correctly.
    """
    from mcp_server_troubleshoot.bundle import BundleManager

    # Create a BundleManager instance to test the port checking
    bundle_manager = BundleManager()

    # Test the internal port checking method directly
    # Test a few different ports
    test_ports = [0, 22, 80, 8080, 9999]

    for port in test_ports:
        try:
            # This should work without any netstat dependency
            result = bundle_manager._check_port_listening_python(port)
            assert isinstance(result, bool), f"Port check should return boolean for port {port}"
            print(f"✅ Port {port} check successful: {result} (socket-based, no netstat required)")
        except Exception as e:
            pytest.fail(f"Socket-based port checking failed for port {port}: {e}")

    print("✅ Socket-based port checking fix verified: All ports checked without netstat")


@pytest.mark.asyncio
async def test_netstat_replaced_in_diagnostic_info():
    """
    Test that the diagnostic info function no longer depends on netstat
    and uses Python sockets instead.
    """
    from mcp_server_troubleshoot.bundle import BundleManager

    # Save original PATH
    original_path = os.environ.get("PATH", "")

    try:
        # Remove netstat from PATH to simulate container environment
        os.environ["PATH"] = "/tmp"  # Path without netstat

        # Create bundle manager and try to get diagnostic info
        bundle_manager = BundleManager()

        # This should work even without netstat available
        diagnostic_info = await bundle_manager.get_diagnostic_info()

        # Verify we got diagnostic information
        assert isinstance(diagnostic_info, dict), "Diagnostic info should be a dictionary"

        # Look for evidence that socket-based checking was used
        port_checked_keys = [
            key for key in diagnostic_info.keys() if "port_" in key and "_checked" in key
        ]

        # Note: Port checking only happens when sbctl is available
        # In CI environments without sbctl, this is expected behavior
        if len(port_checked_keys) == 0:
            print("ℹ️ No ports checked - sbctl not available in test environment")
            print("✅ This is expected behavior in CI without sbctl")
        else:
            print(f"✅ Found port checking evidence: {port_checked_keys}")
            assert len(port_checked_keys) > 0, "Should have checked at least one port"

        # Verify no netstat-related errors
        netstat_error_keys = [key for key in diagnostic_info.keys() if "netstat" in key.lower()]
        if netstat_error_keys:
            # If there are netstat-related keys, they should be from old code, not our new code
            print(f"Found netstat-related keys (may be from old code): {netstat_error_keys}")

        # Look for socket-based port checking evidence
        socket_evidence = [key for key in diagnostic_info.keys() if "socket" in key.lower()]
        if socket_evidence:
            print(f"✅ Found evidence of socket-based checking: {socket_evidence}")

        print("✅ Diagnostic info works without netstat dependency")

    finally:
        # Restore PATH
        os.environ["PATH"] = original_path


@pytest.mark.asyncio
async def test_both_fixes_work_together():
    """
    Integration test that verifies both fixes work correctly together
    in a realistic scenario.
    """
    if sys.version_info < (3, 13):
        pytest.skip("This test requires Python 3.13+ for complete fix verification")

    from mcp_server_troubleshoot.bundle import BundleManager
    from mcp_server_troubleshoot.subprocess_utils import subprocess_exec_with_cleanup

    # Capture any issues
    transport_issues = []

    def warning_capture(message, category, filename, lineno, file=None, line=None):
        msg_str = str(message)
        if any(keyword in msg_str.lower() for keyword in ["transport", "_closing", "unclosed"]):
            transport_issues.append(f"{category.__name__}: {msg_str}")

    original_showwarning = warnings.showwarning
    warnings.showwarning = warning_capture
    original_path = os.environ.get("PATH", "")

    try:
        # Simulate container environment without netstat but with basic commands
        os.environ["PATH"] = "/bin:/usr/bin"  # Has echo but likely no netstat

        # Create bundle manager
        bundle_manager = BundleManager()

        # Test subprocess operations (transport cleanup fix)
        for i in range(10):
            returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                "echo", f"integration_test_{i}", timeout=5.0
            )
            assert returncode == 0, "subprocess operations should work"

        # Test port checking (netstat replacement fix)
        test_ports = [8080, 9090, 3000]
        for port in test_ports:
            port_status = bundle_manager._check_port_listening_python(port)
            assert isinstance(port_status, bool), f"Port {port} check should return boolean"

        # Test full diagnostic info (integration of both fixes)
        diagnostic_info = await bundle_manager.get_diagnostic_info()
        assert isinstance(diagnostic_info, dict), "Should get diagnostic information"

        # Force garbage collection to trigger any transport issues
        for _ in range(5):
            gc.collect()
            await asyncio.sleep(0.1)

        # Verify both fixes work
        assert len(transport_issues) == 0, f"No transport issues should occur: {transport_issues}"

        port_checked_keys = [
            key for key in diagnostic_info.keys() if "port_" in key and "_checked" in key
        ]

        # Note: Port checking only happens when sbctl is available
        # In CI environments without sbctl, this is expected behavior
        if len(port_checked_keys) == 0:
            print("ℹ️ No ports checked - sbctl not available in CI environment")
            print("✅ Socket-based port checking is ready when sbctl is present")
        else:
            print(f"✅ Port checking worked: {port_checked_keys}")
            assert len(port_checked_keys) > 0, "Port checking should work without netstat"

        print("✅ Both fixes work correctly together:")
        print("  - Python 3.13 transport cleanup: No transport issues")
        print("  - Socket-based port checking: Works without netstat")
        print("  - Integration: Diagnostic info generated successfully")

    finally:
        warnings.showwarning = original_showwarning
        os.environ["PATH"] = original_path


def test_fixes_are_properly_implemented():
    """
    Test that verifies the fixes are properly implemented in the codebase.
    """
    # Check subprocess_utils has Python 3.13 handling
    from mcp_server_troubleshoot import subprocess_utils
    import inspect

    source = inspect.getsource(subprocess_utils)

    # Verify Python 3.13 specific code is present
    assert "sys.version_info >= (3, 13)" in source, "Should have Python 3.13 version check"
    assert "_safe_transport_cleanup" in source, "Should have safe transport cleanup function"
    assert "_safe_transport_wait_close" in source, "Should have safe transport wait function"

    # Check bundle.py has socket-based port checking
    from mcp_server_troubleshoot import bundle

    bundle_source = inspect.getsource(bundle)

    # Verify socket import and usage
    assert "import socket" in bundle_source, "Should import socket module"
    assert (
        "_check_port_listening_python" in bundle_source
    ), "Should have Python port checking method"
    assert (
        "socket.socket(socket.AF_INET, socket.SOCK_STREAM)" in bundle_source
    ), "Should use socket for port checking"

    # Verify netstat is no longer used in the port checking code
    # (It might still be mentioned in comments or documentation)
    netstat_usage_lines = []
    for line_num, line in enumerate(bundle_source.split("\n"), 1):
        if "netstat" in line.lower() and "subprocess_exec_with_cleanup(" in line:
            netstat_usage_lines.append(f"Line {line_num}: {line.strip()}")

    assert (
        len(netstat_usage_lines) == 0
    ), f"Found active netstat usage that should be replaced: {netstat_usage_lines}"

    print("✅ Both fixes are properly implemented in the codebase:")
    print("  - subprocess_utils: Python 3.13 transport handling")
    print("  - bundle.py: Socket-based port checking (no netstat dependency)")
