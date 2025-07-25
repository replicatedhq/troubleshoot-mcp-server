"""
Lifecycle management for the MCP server.

This module provides the lifecycle context manager and resource tracking
for the MCP troubleshoot server, enabling proper initialization and cleanup.
This is especially important for stdio mode operation with FastMCP.
"""

import asyncio
import logging
import os
import shutil
import signal
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from mcp.server.fastmcp import FastMCP

from .bundle import BundleManager
from .files import FileExplorer
from .kubectl import KubectlExecutor

logger = logging.getLogger(__name__)

# Global flag to track shutdown request from signal handler
_shutdown_requested = False


def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested via signal handler."""
    return _shutdown_requested


@dataclass
class AppContext:
    """Application context for the MCP troubleshoot server."""

    bundle_manager: BundleManager
    file_explorer: FileExplorer
    kubectl_executor: KubectlExecutor
    temp_dir: str = ""
    background_tasks: Dict[str, asyncio.Task[Any]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


def create_temp_directory() -> str:
    """Create a temporary directory for bundle extraction."""
    temp_dir = os.path.join(tempfile.gettempdir(), f"mcp-troubleshoot-{uuid.uuid4()}")
    os.makedirs(temp_dir, exist_ok=True)
    logger.info(f"Created temporary directory: {temp_dir}")
    return temp_dir


async def periodic_bundle_cleanup(bundle_manager: BundleManager, interval: int = 3600) -> None:
    """Periodically clean up old bundles."""
    logger.info(f"Starting periodic bundle cleanup (interval: {interval}s)")
    try:
        while True:
            await asyncio.sleep(interval)
            logger.info("Running bundle cleanup")
            await bundle_manager.cleanup()
    except asyncio.CancelledError:
        logger.info("Bundle cleanup task cancelled")
        raise


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """
    Manage application lifecycle for MCP troubleshoot server.

    This function handles:
    1. Resource initialization during startup
    2. Resource cleanup during shutdown

    Args:
        server: The FastMCP server instance

    Yields:
        AppContext instance containing all shared resources
    """
    # === STARTUP PHASE ===
    start_time = time.time()
    logger.info("Starting MCP Troubleshoot Server")

    # Get configuration from environment
    bundle_dir_str = os.environ.get("MCP_BUNDLE_STORAGE")
    bundle_dir = Path(bundle_dir_str) if bundle_dir_str else None

    enable_periodic_cleanup = os.environ.get("ENABLE_PERIODIC_CLEANUP", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    cleanup_interval = int(os.environ.get("CLEANUP_INTERVAL", "3600"))

    # Initialize bundle manager
    bundle_manager = BundleManager(bundle_dir)

    # Create temp directory for extracted bundles
    temp_dir = create_temp_directory()

    # Initialize kubectl executor
    kubectl_executor = KubectlExecutor(bundle_manager)

    # Initialize file explorer
    file_explorer = FileExplorer(bundle_manager)

    # Track background tasks
    background_tasks = {}

    # Start periodic cleanup task if configured
    if enable_periodic_cleanup:
        logger.info(f"Enabling periodic bundle cleanup every {cleanup_interval} seconds")
        background_tasks["bundle_cleanup"] = asyncio.create_task(
            periodic_bundle_cleanup(bundle_manager, cleanup_interval)
        )

    # Create context to share with tools
    context = AppContext(
        bundle_manager=bundle_manager,
        file_explorer=file_explorer,
        kubectl_executor=kubectl_executor,
        temp_dir=temp_dir,
        background_tasks=background_tasks,
        metadata={
            "start_time": start_time,
            "stdio_mode": getattr(server, "use_stdio", False),
        },
    )

    # Register with the server context for use in handler methods
    from .server import set_app_context

    set_app_context(context)

    try:
        # Yield context to FastMCP server
        yield context
    finally:
        # === SHUTDOWN PHASE ===
        elapsed = time.time() - start_time
        logger.info(
            f"Shutting down MCP Troubleshoot Server after running for {elapsed:.2f} seconds"
        )

        # Cancel background tasks with timeout
        for name, task in background_tasks.items():
            if not task.done():
                logger.info(f"Cancelling background task: {name}")
                task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.warning(f"Task {name} did not complete gracefully within timeout")

        # Clean up bundle manager resources
        try:
            logger.info("Cleaning up bundle manager resources")
            await bundle_manager.cleanup()
        except Exception as e:
            logger.error(f"Error during bundle manager cleanup: {e}")

        # Clean up temporary files
        if os.path.exists(temp_dir):
            logger.info(f"Removing temporary directory: {temp_dir}")
            try:
                shutil.rmtree(temp_dir)
            except OSError as e:
                logger.error(f"Failed to remove temp directory {temp_dir}: {e}")

        logger.info("Shutdown complete")


def handle_signal(signum: int, frame: Any) -> None:
    """
    Handle termination signals (SIGTERM, SIGINT) for graceful shutdown.

    This function is used for stdio mode to ensure proper cleanup when
    the container is stopped or interrupted.

    Args:
        signum: Signal number
        frame: Current stack frame
    """
    sig_name = signal.Signals(signum).name
    logger.info(f"Received signal {sig_name} ({signum}). Initiating shutdown...")

    # Import locally to avoid circular imports
    from .server import shutdown, get_app_context

    # Get the app context to directly access bundle manager for explicit cleanup
    app_context = get_app_context()
    if app_context and hasattr(app_context, "bundle_manager"):
        try:
            # Directly access bundle manager and perform cleanup explicitly
            # This avoids relying on atexit handlers or context manager cleanup
            if app_context.bundle_manager.active_bundle:
                bundle_id = app_context.bundle_manager.active_bundle.id
                bundle_path = app_context.bundle_manager.active_bundle.path
                logger.info(f"Performing direct cleanup of bundle: {bundle_id}")

                # Explicitly clean up the active bundle's directory
                if bundle_path and bundle_path.exists():
                    logger.info(f"Removing bundle directory: {bundle_path}")
                    try:
                        import shutil

                        shutil.rmtree(bundle_path)
                        logger.info("Successfully removed bundle directory")
                    except Exception as e:
                        logger.error(f"Error removing bundle directory: {e}")
        except Exception as e:
            logger.error(f"Error in direct cleanup: {e}")

    try:
        # Call the normal shutdown process
        logger.info("Calling explicit shutdown from signal handler")
        shutdown()
        logger.info("Explicit shutdown completed")
    except Exception as e:
        logger.error(f"Error during explicit shutdown: {e}")

    # Set a flag to indicate shutdown was requested but don't call sys.exit()
    # This allows the Python runtime to shutdown gracefully without race conditions
    logger.info("Cleanup completed, allowing graceful exit")

    # Set a global flag that the main loop can check
    global _shutdown_requested
    _shutdown_requested = True


def setup_signal_handlers() -> None:
    """
    Register signal handlers for graceful shutdown in stdio mode.

    This ensures that when the container receives termination signals,
    we properly clean up all resources.
    """
    # Don't register signal handlers during test runs to avoid interfering with test processes
    if "PYTEST_CURRENT_TEST" in os.environ:
        logger.debug("Running in pytest, skipping signal handler registration")
        return

    try:
        # Register handlers for typical termination signals
        for sig_name, sig_num in (
            ("SIGINT", signal.SIGINT),  # Keyboard interrupt (Ctrl+C)
            ("SIGTERM", signal.SIGTERM),  # Termination signal (Docker stop)
        ):
            try:
                signal.signal(sig_num, handle_signal)
                logger.debug(f"Registered {sig_name} handler for graceful shutdown")
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to register signal handler for {sig_name}: {e}")
    except Exception as e:
        logger.warning(f"Error setting up signal handlers: {e}")
