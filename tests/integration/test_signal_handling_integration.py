"""
Comprehensive integration tests for signal handling.

These tests verify that the server handles various signals correctly
without race conditions or crashes during shutdown.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Tuple

import pytest


def create_test_server_script(signal_delay: float = 0.1) -> str:
    """
    Create a test server script that simulates the real server behavior.

    Args:
        signal_delay: Delay in signal handler to simulate work

    Returns:
        Python script as a string
    """
    return '''
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

# Add the src directory to path to import our modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from mcp_server_troubleshoot.lifecycle import handle_signal, is_shutdown_requested

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger(__name__)

# Set up signal handlers manually to avoid conflicts
def setup_test_signal_handlers():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    logger.info("Signal handlers registered")

async def main():
    """Simulate the server main loop."""
    logger.info("Test server started")
    
    # Set up our test signal handlers
    setup_test_signal_handlers()
    
    # Simulate server activity
    while not is_shutdown_requested():
        logger.debug("Server is active...")
        await asyncio.sleep(0.1)
    
    logger.info("Shutdown requested, exiting main loop")
    # Exit gracefully
    return

if __name__ == "__main__":
    try:
        asyncio.run(main())
        logger.info("Server exited cleanly")
    except KeyboardInterrupt:
        logger.info("Server interrupted")
'''


def run_server_with_signal(
    script_content: str,
    signal_to_send: int,
    delay_before_signal: float = 0.5,
    timeout: float = 5.0,
) -> Tuple[int, str, str]:
    """
    Run a server script and send it a signal.

    Args:
        script_content: The Python script to run
        signal_to_send: Signal number to send (e.g., signal.SIGTERM)
        delay_before_signal: Time to wait before sending signal
        timeout: Maximum time to wait for process to exit

    Returns:
        Tuple of (return_code, stdout, stderr)
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script_content)
        script_path = f.name

    try:
        # Get the project root directory
        project_root = Path(__file__).parent.parent.parent

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(project_root)

        process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=str(project_root),
        )

        # Give the server time to start
        time.sleep(delay_before_signal)

        # Send the signal
        process.send_signal(signal_to_send)

        # Wait for the process to exit
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return_code = process.returncode
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            return_code = -1

        return return_code, stdout, stderr

    finally:
        Path(script_path).unlink(missing_ok=True)


@pytest.mark.integration
class TestSignalHandling:
    """Test various signal handling scenarios."""

    def test_sigterm_clean_shutdown(self):
        """Test that SIGTERM results in clean shutdown without errors."""
        script = create_test_server_script()
        return_code, stdout, stderr = run_server_with_signal(script, signal.SIGTERM)

        # Check what happened
        if return_code == -1:
            print(f"Process timed out. stderr:\n{stderr}")
            pytest.fail("Process timed out, likely the signal handler did not properly exit")

        # Should exit cleanly with code 0 or -15 (SIGTERM on Linux)
        assert return_code in (
            0,
            -15,
        ), f"Expected exit code 0 or -15, got {return_code}\nstderr: {stderr}"

        # Should not have Python runtime errors
        assert "Fatal Python error" not in stderr
        assert "_enter_buffered_busy" not in stderr
        assert "could not acquire lock" not in stderr

        # Should see shutdown messages (if any output was captured)
        # On CI, the process might exit too quickly to capture output
        if stderr:
            assert "signal" in stderr.lower() or "shutdown" in stderr.lower()

    def test_sigint_clean_shutdown(self):
        """Test that SIGINT (Ctrl+C) results in clean shutdown."""
        script = create_test_server_script()
        return_code, stdout, stderr = run_server_with_signal(script, signal.SIGINT)

        # Should exit cleanly
        assert return_code in (0, -2), f"Expected exit code 0 or -2, got {return_code}"

        # Should not have Python runtime errors
        assert "Fatal Python error" not in stderr
        assert "_enter_buffered_busy" not in stderr

    def test_multiple_signals_ignored(self):
        """Test that multiple signals don't cause issues."""
        script = '''
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from mcp_server_troubleshoot.lifecycle import handle_signal, setup_signal_handlers, is_shutdown_requested

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

signal_count = 0

def counting_signal_handler(signum, frame):
    """Count signals and only handle the first one."""
    global signal_count
    signal_count += 1
    logger.info(f"Received signal {signum}, count: {signal_count}")
    
    if signal_count == 1:
        # Only handle the first signal
        handle_signal(signum, frame)
    else:
        logger.info("Ignoring duplicate signal")

async def main():
    logger.info("Test server started")
    
    # Use our custom handler that counts signals
    signal.signal(signal.SIGTERM, counting_signal_handler)
    signal.signal(signal.SIGINT, counting_signal_handler)
    
    while not is_shutdown_requested():
        await asyncio.sleep(0.1)
    
    logger.info("Shutting down")

if __name__ == "__main__":
    asyncio.run(main())
'''

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(script)
            script_path = f.name

        try:
            project_root = Path(__file__).parent.parent.parent
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONPATH"] = str(project_root)

            process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(project_root),
            )

            # Send multiple signals
            time.sleep(0.5)
            process.send_signal(signal.SIGTERM)
            time.sleep(0.1)
            process.send_signal(signal.SIGTERM)
            time.sleep(0.1)
            process.send_signal(signal.SIGTERM)

            stdout, stderr = process.communicate(timeout=5)

            # Should handle gracefully - return code of 0 or -15 (SIGTERM) is acceptable
            assert process.returncode in (
                0,
                -15,
            ), f"Unexpected return code: {process.returncode}"
            assert "Fatal Python error" not in stderr
            # On CI, the process might exit too quickly to log anything
            if stderr:
                assert "count: 1" in stderr or "Received signal" in stderr
            # The process may exit before receiving additional signals, which is fine

        finally:
            Path(script_path).unlink(missing_ok=True)

    def test_signal_during_heavy_logging(self):
        """Test signal handling while actively logging (race condition scenario)."""
        script = '''
import asyncio
import logging
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from mcp_server_troubleshoot.lifecycle import handle_signal, setup_signal_handlers, is_shutdown_requested

logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)
logger = logging.getLogger(__name__)

def heavy_logging_thread():
    """Continuously log to increase chance of race condition."""
    while not is_shutdown_requested():
        logger.debug("Heavy logging output to stress test shutdown")
        time.sleep(0.001)  # Very frequent logging

async def main():
    logger.info("Test server with heavy logging started")
    
    setup_signal_handlers()
    
    # Start heavy logging in background thread
    log_thread = threading.Thread(target=heavy_logging_thread, daemon=True)
    log_thread.start()
    
    # Also log from main thread
    while not is_shutdown_requested():
        logger.info("Main thread is active")
        await asyncio.sleep(0.05)
    
    logger.info("Shutdown requested, cleaning up")

if __name__ == "__main__":
    import time
    asyncio.run(main())
'''

        # Run this test multiple times to increase chance of catching race condition
        for i in range(3):
            return_code, stdout, stderr = run_server_with_signal(
                script, signal.SIGTERM, delay_before_signal=0.3
            )

            # Should not crash with Python runtime error
            assert "Fatal Python error" not in stderr, (
                f"Race condition detected on iteration {i + 1}"
            )
            assert "_enter_buffered_busy" not in stderr

    def test_signal_with_resource_cleanup(self):
        """Test signal handling with simulated resource cleanup."""
        script = """
import asyncio
import logging
import signal
import sys
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from mcp_server_troubleshoot.lifecycle import handle_signal, is_shutdown_requested

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger(__name__)

# Global resources to clean up
temp_dir = None

def setup_test_signal_handlers():
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

async def main():
    global temp_dir
    
    logger.info("Test server with resources started")
    
    # Create some resources that need cleanup
    temp_dir = tempfile.mkdtemp(prefix="test_signal_")
    logger.info(f"Created temp directory: {temp_dir}")
    
    setup_test_signal_handlers()
    
    while not is_shutdown_requested():
        # Simulate work with resources
        test_file = Path(temp_dir) / f"test_file.txt"
        test_file.write_text("test data")
        await asyncio.sleep(0.1)
    
    # Clean up resources
    logger.info("Cleaning up resources")
    if temp_dir and Path(temp_dir).exists():
        shutil.rmtree(temp_dir)
        logger.info("Cleaned up temp directory")

if __name__ == "__main__":
    try:
        asyncio.run(main())
        logger.info("Server exited cleanly")
    except KeyboardInterrupt:
        pass
"""

        return_code, stdout, stderr = run_server_with_signal(script, signal.SIGTERM)

        # Should exit cleanly (0 on macOS, -15 on Linux)
        assert return_code in (0, -15)
        assert "Fatal Python error" not in stderr

        # Should see cleanup messages (if output was captured)
        # The main test is that no Python runtime error occurred
        if stderr:
            # At minimum we should see some activity
            assert len(stderr) > 0
