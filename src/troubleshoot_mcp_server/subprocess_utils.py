"""
Subprocess utilities for proper asyncio transport cleanup.

This module provides utilities for managing subprocess operations with proper
transport cleanup to avoid ResourceWarning about unclosed transports.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional, Tuple

logger = logging.getLogger(__name__)


def _safe_transport_cleanup(transport: Any) -> None:
    """
    Safely cleanup transport objects with Python 3.13 compatibility.

    In Python 3.13+, _UnixReadPipeTransport objects may not have the '_closing'
    attribute when accessed during garbage collection, causing AttributeError.
    This function provides safe cleanup that works across Python versions.

    Args:
        transport: The transport object to cleanup safely
    """
    if not transport:
        return

    try:
        # Python 3.13+ compatible transport cleanup
        if sys.version_info >= (3, 13):
            # For Python 3.13+, be more careful about accessing internal attributes
            logger.debug("Performing Python 3.13+ compatible transport cleanup")

            # Try to close the transport gracefully
            try:
                if hasattr(transport, "close") and callable(transport.close):
                    transport.close()
                    logger.debug("Transport closed successfully")
            except Exception as e:
                logger.debug(f"Error during transport close: {e}, continuing cleanup")

            # Don't rely on internal _closing attribute in Python 3.13+
            # Instead, use a try/except approach for transport state checking
            try:
                if hasattr(transport, "is_closing") and callable(transport.is_closing):
                    # This method should be available and safe to call
                    is_closing = transport.is_closing()
                    logger.debug(f"Transport is_closing status: {is_closing}")
                else:
                    logger.debug("Transport doesn't have is_closing method, assuming closed")
            except AttributeError as e:
                # This is the specific error we're trying to avoid
                logger.debug(
                    f"AttributeError during transport status check (expected in Python 3.13): {e}"
                )
            except Exception as e:
                logger.debug(f"Unexpected error during transport status check: {e}")

        else:
            # For Python < 3.13, use the original cleanup approach
            logger.debug("Performing pre-Python 3.13 transport cleanup")
            transport.close()

    except Exception as e:
        # Catch any cleanup errors to prevent them from propagating
        logger.warning(f"Error during safe transport cleanup: {e}")


async def _safe_transport_wait_close(
    transport: Any, timeout_per_check: float = 0.1, max_checks: int = 10
) -> None:
    """
    Safely wait for transport to close with Python 3.13 compatibility.

    Args:
        transport: The transport object to wait for
        timeout_per_check: Time to wait between each check
        max_checks: Maximum number of checks before giving up
    """
    if not transport:
        return

    checks_done = 0

    while checks_done < max_checks:
        try:
            # Python 3.13 compatible is_closing check
            if hasattr(transport, "is_closing") and callable(transport.is_closing):
                if transport.is_closing():
                    logger.debug("Transport is closing, wait complete")
                    break
            else:
                # If is_closing is not available, assume the transport is handled
                logger.debug("Transport is_closing not available, assuming handled")
                break

        except AttributeError as e:
            # This is the _closing attribute error we're avoiding
            logger.debug(f"AttributeError during transport wait (handled safely): {e}")
            break
        except Exception as e:
            logger.debug(f"Unexpected error during transport wait: {e}")
            break

        await asyncio.sleep(timeout_per_check)
        checks_done += 1

    if checks_done >= max_checks:
        logger.debug("Transport wait timeout reached, proceeding anyway")


@asynccontextmanager
async def pipe_transport_reader(
    pipe: Any,
) -> AsyncGenerator[asyncio.StreamReader, None]:
    """
    Async context manager for managing pipe transport with proper cleanup.

    This prevents the common issue where _UnixReadPipeTransport objects
    are not properly cleaned up, causing ResourceWarning about missing
    '_closing' attribute.

    Args:
        pipe: The pipe object (e.g., process.stdout) to read from

    Yields:
        StreamReader: An asyncio.StreamReader for reading from the pipe

    Example:
        async with pipe_transport_reader(process.stdout) as reader:
            data = await reader.read(1024)
    """
    stdout_reader = asyncio.StreamReader()
    stdout_protocol = asyncio.StreamReaderProtocol(stdout_reader)
    loop = asyncio.get_event_loop()

    transport = None
    try:
        transport, _ = await loop.connect_read_pipe(lambda: stdout_protocol, pipe)
        logger.debug("Created pipe transport reader")
        yield stdout_reader
    finally:
        if transport:
            logger.debug("Closing pipe transport with Python 3.13 compatibility")
            # Use the new safe transport cleanup
            _safe_transport_cleanup(transport)

            # Wait for transport to close safely
            await _safe_transport_wait_close(transport, timeout_per_check=0.1, max_checks=10)


async def subprocess_exec_with_cleanup(
    *args: str, timeout: Optional[float] = 30.0, **kwargs: Any
) -> Tuple[int, bytes, bytes]:
    """
    Execute subprocess with guaranteed cleanup and proper error handling.

    This function ensures that subprocess operations are properly cleaned up
    even in error conditions or timeouts, preventing resource leaks.

    Args:
        *args: Command and arguments to execute
        timeout: Timeout in seconds (None for no timeout)
        **kwargs: Additional arguments passed to create_subprocess_exec

    Returns:
        Tuple of (returncode, stdout, stderr)

    Raises:
        asyncio.TimeoutError: If the subprocess times out
        Exception: For other subprocess errors

    Example:
        returncode, stdout, stderr = await subprocess_exec_with_cleanup(
            "curl", "-s", "http://example.com", timeout=5.0
        )
    """
    process = None
    try:
        # Set default subprocess options for proper pipe handling
        subprocess_kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            **kwargs,
        }

        logger.debug(f"Starting subprocess: {' '.join(args)}")
        process = await asyncio.create_subprocess_exec(*args, **subprocess_kwargs)

        if timeout is not None:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        else:
            stdout, stderr = await process.communicate()

        logger.debug(f"Subprocess completed with return code: {process.returncode}")
        return process.returncode or 0, stdout, stderr

    except asyncio.TimeoutError:
        logger.warning(f"Subprocess timeout after {timeout}s: {' '.join(args)}")
        if process:
            logger.debug("Terminating subprocess due to timeout")
            process.terminate()
            try:
                # Give process a chance to terminate gracefully
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.debug("Force killing subprocess")
                process.kill()
                await process.wait()
        raise

    except Exception as e:
        logger.error(f"Subprocess error: {e}")
        if process and process.returncode is None:
            logger.debug("Cleaning up failed subprocess")
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            except Exception as cleanup_error:
                logger.warning(f"Error during subprocess cleanup: {cleanup_error}")
        raise

    finally:
        # Extra safety check for process cleanup
        if process and process.returncode is None:
            logger.warning("Process still running in finally block, forcing cleanup")
            try:
                process.kill()
                await process.wait()
            except Exception as final_cleanup_error:
                logger.error(f"Final cleanup error: {final_cleanup_error}")


async def subprocess_shell_with_cleanup(
    command: str, timeout: Optional[float] = 30.0, **kwargs: Any
) -> Tuple[int, bytes, bytes]:
    """
    Execute shell command with guaranteed cleanup.

    Similar to subprocess_exec_with_cleanup but for shell commands.

    Args:
        command: Shell command to execute
        timeout: Timeout in seconds (None for no timeout)
        **kwargs: Additional arguments passed to create_subprocess_shell

    Returns:
        Tuple of (returncode, stdout, stderr)

    Example:
        returncode, stdout, stderr = await subprocess_shell_with_cleanup(
            "netstat -tuln | grep :8080", timeout=5.0
        )
    """
    process = None
    try:
        subprocess_kwargs = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            **kwargs,
        }

        logger.debug(f"Starting shell command: {command}")
        process = await asyncio.create_subprocess_shell(command, **subprocess_kwargs)

        if timeout is not None:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        else:
            stdout, stderr = await process.communicate()

        logger.debug(f"Shell command completed with return code: {process.returncode}")
        return process.returncode or 0, stdout, stderr

    except asyncio.TimeoutError:
        logger.warning(f"Shell command timeout after {timeout}s: {command}")
        if process:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        raise

    except Exception as e:
        logger.error(f"Shell command error: {e}")
        if process and process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            except Exception as cleanup_error:
                logger.warning(f"Error during shell cleanup: {cleanup_error}")
        raise

    finally:
        if process and process.returncode is None:
            try:
                process.kill()
                await process.wait()
            except Exception as final_cleanup_error:
                logger.error(f"Final shell cleanup error: {final_cleanup_error}")


@asynccontextmanager
async def managed_subprocess(
    *args: str, timeout: Optional[float] = None, **kwargs: Any
) -> AsyncGenerator[asyncio.subprocess.Process, None]:
    """
    Context manager for subprocess lifecycle management.

    Provides a subprocess process object with guaranteed cleanup,
    useful for long-running processes or when you need direct access
    to the process object.

    Args:
        *args: Command and arguments to execute
        timeout: Optional timeout for the entire context (not just startup)
        **kwargs: Additional arguments passed to create_subprocess_exec

    Yields:
        Process: The subprocess Process object

    Example:
        async with managed_subprocess("sbctl", "serve") as process:
            # Work with process
            if process.returncode is None:
                # Process is still running
                pass
    """
    process = None
    try:
        logger.debug(f"Starting managed subprocess: {' '.join(args)}")
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        yield process

    finally:
        if process:
            logger.debug("Cleaning up managed subprocess")
            if process.returncode is None:
                # Process is still running, terminate it
                try:
                    process.terminate()
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                    logger.debug("Subprocess terminated gracefully")
                except asyncio.TimeoutError:
                    logger.debug("Subprocess termination timeout, force killing")
                    process.kill()
                    await process.wait()
                except Exception as e:
                    logger.warning(f"Error terminating subprocess: {e}")
                    try:
                        process.kill()
                        await process.wait()
                    except Exception as kill_error:
                        logger.error(f"Error force killing subprocess: {kill_error}")
