"""
End-to-end tests for container shutdown reliability.

These tests validate that the server shuts down cleanly in container-like
environments without Python runtime errors.
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

import pytest


def run_server_as_subprocess(
    args: list[str],
    env: Optional[dict] = None,
    signal_to_send: Optional[int] = None,
    signal_delay: float = 1.0,
    timeout: float = 10.0,
    stdin_input: Optional[str] = None,
) -> Tuple[int, str, str]:
    """
    Run the MCP server as a subprocess and optionally send a signal.

    Args:
        args: Command line arguments for the server
        env: Environment variables
        signal_to_send: Signal to send (e.g., signal.SIGTERM)
        signal_delay: Time to wait before sending signal
        timeout: Maximum time to wait for process
        stdin_input: Input to send to stdin

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    # Get the project root
    project_root = Path(__file__).parent.parent.parent

    # Set up environment
    test_env = os.environ.copy()
    if env:
        test_env.update(env)

    # Always use unbuffered output for testing
    test_env["PYTHONUNBUFFERED"] = "1"

    # Run the server module directly
    cmd = [sys.executable, "-m", "troubleshoot_mcp_server"] + args

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE if stdin_input else None,
        text=True,
        env=test_env,
        cwd=str(project_root),
    )

    try:
        if signal_to_send and not stdin_input:
            # If only sending a signal, no stdin input
            time.sleep(signal_delay)
            process.send_signal(signal_to_send)
        elif stdin_input and signal_to_send:
            # If both stdin and signal, handle carefully
            # Write input but don't close stdin immediately
            process.stdin.write(stdin_input)
            process.stdin.flush()
            # Wait before sending signal
            time.sleep(signal_delay)
            # Send signal while stdin might still be open
            process.send_signal(signal_to_send)
        elif stdin_input:
            # Just stdin input
            process.stdin.write(stdin_input)
            process.stdin.close()

        # Wait for process to complete
        # Don't close stdin again if already closed, communicate will handle it
        stdout, stderr = process.communicate(timeout=timeout)
        return_code = process.returncode

    except subprocess.TimeoutExpired:
        # Kill the process if it times out
        process.kill()
        stdout, stderr = process.communicate()
        return_code = -1

    return return_code, stdout, stderr


@pytest.mark.e2e
class TestContainerShutdownReliability:
    """Test container shutdown scenarios end-to-end."""

    def test_stdio_mode_sigterm_shutdown(self):
        """Test SIGTERM shutdown in stdio mode (container-like environment)."""
        # Create a simple MCP request to send via stdin
        mcp_request = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client", "version": "1.0"},
                    },
                    "id": 1,
                }
            )
            + "\n"
        )

        # Run in stdio mode with MCP_USE_STDIO
        env = {"MCP_USE_STDIO": "true", "MCP_LOG_LEVEL": "INFO"}

        return_code, stdout, stderr = run_server_as_subprocess(
            args=[],
            env=env,
            signal_to_send=signal.SIGTERM,
            signal_delay=0.5,
            stdin_input=mcp_request,
        )

        # Should exit cleanly without Python runtime errors
        assert "Fatal Python error" not in stderr, f"Python runtime error detected: {stderr}"
        assert "_enter_buffered_busy" not in stderr
        assert "could not acquire lock" not in stderr

        # Should see proper shutdown messages
        assert (
            "Received signal SIGTERM" in stderr
            or "Initiating shutdown" in stderr
            or "Shutting down MCP Troubleshoot" in stderr
            or "graceful shutdown" in stderr
        )

        # Return code should indicate clean exit or signal termination
        assert return_code in (0, -15, 143), f"Unexpected return code: {return_code}"

    def test_stdio_mode_sigint_shutdown(self):
        """Test SIGINT (Ctrl+C) shutdown in stdio mode."""
        env = {"MCP_USE_STDIO": "true", "MCP_LOG_LEVEL": "INFO"}

        return_code, stdout, stderr = run_server_as_subprocess(
            args=[], env=env, signal_to_send=signal.SIGINT, signal_delay=0.5
        )

        # Should not have Python runtime errors
        assert "Fatal Python error" not in stderr
        assert "_enter_buffered_busy" not in stderr

        # Should handle interrupt cleanly
        assert return_code in (0, -2, 130), f"Unexpected return code: {return_code}"

    def test_container_env_with_bundle_cleanup(self):
        """Test shutdown with active bundle cleanup in container environment."""
        with tempfile.TemporaryDirectory() as bundle_dir:
            env = {
                "MCP_USE_STDIO": "true",
                "MCP_BUNDLE_STORAGE": bundle_dir,
                "MCP_LOG_LEVEL": "DEBUG",
            }

            # Initialize request to trigger bundle creation
            init_request = (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test-client", "version": "1.0"},
                        },
                        "id": 1,
                    }
                )
                + "\n"
            )

            return_code, stdout, stderr = run_server_as_subprocess(
                args=["--bundle-dir", bundle_dir],
                env=env,
                signal_to_send=signal.SIGTERM,
                signal_delay=1.0,
                stdin_input=init_request,
            )

            # Should not crash
            assert "Fatal Python error" not in stderr

            # Should see bundle cleanup messages if a bundle was active
            if "bundle" in stderr.lower():
                assert "cleanup" in stderr.lower() or "clean" in stderr.lower()

    def test_rapid_shutdown_requests(self):
        """Test handling of rapid shutdown signals (stress test)."""
        env = {
            "MCP_USE_STDIO": "true",
            "MCP_LOG_LEVEL": "ERROR",  # Reduce logging to avoid buffer issues
        }

        # Test with very quick signal after startup
        return_code, stdout, stderr = run_server_as_subprocess(
            args=[],
            env=env,
            signal_to_send=signal.SIGTERM,
            signal_delay=0.1,  # Very quick signal
        )

        # Should handle even rapid shutdown without crashes
        assert "Fatal Python error" not in stderr
        assert "_enter_buffered_busy" not in stderr

    @pytest.mark.slow
    def test_shutdown_during_heavy_load(self):
        """Test shutdown while server is under heavy load."""
        # Create multiple MCP requests to simulate load
        requests = []
        for i in range(10):
            requests.append(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "tools/list",
                        "params": {},
                        "id": i + 1,
                    }
                )
                + "\n"
            )

        env = {"MCP_USE_STDIO": "true", "MCP_LOG_LEVEL": "INFO"}

        # Send all requests at once
        stdin_input = "".join(requests)

        return_code, stdout, stderr = run_server_as_subprocess(
            args=[],
            env=env,
            signal_to_send=signal.SIGTERM,
            signal_delay=0.3,  # Send signal while processing
            stdin_input=stdin_input,
            timeout=15.0,  # Allow more time for heavy load
        )

        # Should handle shutdown gracefully even under load
        assert "Fatal Python error" not in stderr
        assert "_enter_buffered_busy" not in stderr

    def test_container_like_environment_full_lifecycle(self):
        """Test full lifecycle in a container-like environment."""
        with tempfile.TemporaryDirectory() as data_dir:
            bundle_dir = Path(data_dir) / "bundles"
            bundle_dir.mkdir()

            env = {
                "MCP_USE_STDIO": "true",
                "MCP_BUNDLE_STORAGE": str(bundle_dir),
                "MCP_LOG_LEVEL": "INFO",
                # Simulate container environment variables
                "CONTAINER_NAME": "test-container",
                "K8S_POD_NAME": "test-pod",
                "K8S_NAMESPACE": "default",
            }

            # Full MCP conversation
            conversation = [
                # Initialize
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "1.0"},
                        },
                        "id": 1,
                    }
                )
                + "\n",
                # List tools
                json.dumps({"jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 2})
                + "\n",
            ]

            return_code, stdout, stderr = run_server_as_subprocess(
                args=["--bundle-dir", str(bundle_dir)],
                env=env,
                signal_to_send=signal.SIGTERM,
                signal_delay=1.5,
                stdin_input="".join(conversation),
            )

            # Validate clean shutdown
            assert "Fatal Python error" not in stderr
            assert "_enter_buffered_busy" not in stderr

            # Should see initialization and shutdown
            assert (
                "MCP server for Kubernetes support bundles" in stderr
                or "Starting MCP Troubleshoot Server" in stderr
                or "Registered signal handlers" in stderr
            )
            assert "signal" in stderr.lower() or "shutdown" in stderr.lower()

            # Check that responses were sent before shutdown
            if stdout:
                # Should have valid JSON responses
                for line in stdout.strip().split("\n"):
                    if line:
                        try:
                            response = json.loads(line)
                            assert "jsonrpc" in response
                        except json.JSONDecodeError:
                            # Some lines might be partial due to shutdown
                            pass
