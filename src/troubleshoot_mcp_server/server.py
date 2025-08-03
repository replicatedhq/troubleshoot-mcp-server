"""
MCP server implementation for Kubernetes support bundles.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Callable, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .bundle import (
    BundleManager,
    BundleManagerError,
)
from .kubectl import KubectlError, KubectlExecutor
from .files import (
    FileExplorer,
    FileSystemError,
)
from .lifecycle import app_lifespan, AppContext
from .formatters import get_formatter, ResponseFormatter
from .size_limiter import SizeLimiter

logger = logging.getLogger(__name__)

# Create FastMCP server with lifecycle management
# We don't enable stdio mode here - it will be configured in __main__.py
mcp = FastMCP("troubleshoot-mcp-server", lifespan=app_lifespan)

# Flag to track if we're shutting down
_is_shutting_down = False

# Global variables for singleton pattern (initialized as None)
_bundle_manager: Optional[BundleManager] = None
_kubectl_executor: Optional[KubectlExecutor] = None
_file_explorer: Optional[FileExplorer] = None

# Global app context for legacy function compatibility
_app_context = None


def set_app_context(context: AppContext) -> None:
    """Set the global app context for legacy function compatibility."""
    global _app_context
    _app_context = context


def get_app_context() -> Optional[AppContext]:
    """Get the global app context."""
    return _app_context


def get_bundle_manager(bundle_dir: Optional[Path] = None) -> BundleManager:
    """
    Get the bundle manager instance.

    In the lifecycle context version, this returns the bundle manager from the context.
    In the legacy version, this creates a new bundle manager if needed.
    """
    # First try to get from app context
    if _app_context is not None:
        return _app_context.bundle_manager

    # Legacy fallback - create a new instance
    global _bundle_manager
    if _bundle_manager is None:
        _bundle_manager = BundleManager(bundle_dir)
    return _bundle_manager


def get_kubectl_executor() -> KubectlExecutor:
    """
    Get the kubectl executor instance.

    In the lifecycle context version, this returns the executor from the context.
    In the legacy version, this creates a new executor if needed.
    """
    # First try to get from app context
    if _app_context is not None:
        return _app_context.kubectl_executor

    # Legacy fallback - create a new instance
    global _kubectl_executor
    if _kubectl_executor is None:
        _kubectl_executor = KubectlExecutor(get_bundle_manager())
    return _kubectl_executor


def get_file_explorer() -> FileExplorer:
    """
    Get the file explorer instance.

    In the lifecycle context version, this returns the explorer from the context.
    In the legacy version, this creates a new explorer if needed.
    """
    # First try to get from app context
    if _app_context is not None:
        return _app_context.file_explorer

    # Legacy fallback - create a new instance
    global _file_explorer
    if _file_explorer is None:
        _file_explorer = FileExplorer(get_bundle_manager())
    return _file_explorer


def check_response_size(
    content: str, tool_name: str, formatter: ResponseFormatter
) -> List[TextContent]:
    """
    Single centralized function to check all MCP tool responses for size limits.

    Args:
        content: The content to check for size
        tool_name: Name of the tool generating the response (for tool-specific guidance)
        formatter: Response formatter instance for verbosity and overflow messages

    Returns:
        List of TextContent with either the original content or an overflow message
    """
    size_limiter = SizeLimiter()
    tokens = size_limiter.estimate_tokens(content)

    if not size_limiter.enabled:
        # Size checking disabled - return content as-is
        return [TextContent(type="text", text=content)]

    if tokens <= size_limiter.token_limit:
        # Content within limits - return as-is
        return [TextContent(type="text", text=content)]
    else:
        # Content exceeds limits - return overflow message
        overflow_msg = formatter.format_overflow_message(tool_name, tokens, content)
        return [TextContent(type="text", text=overflow_msg)]


@mcp.tool()
async def initialize_bundle(
    source: str, force: bool = False, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    Initialize a Kubernetes support bundle for analysis. This tool loads a bundle
    and makes it available for exploration with other tools.

    Args:
        source: (string, required) The source of the bundle (URL or local file path)
        force: (boolean, optional) Whether to force re-initialization if a bundle
            is already active. Defaults to False.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        Metadata about the initialized bundle including path and kubeconfig location.
        If the API server is not available, also returns diagnostic information.
    """
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    try:
        # Check if sbctl is available before attempting to initialize
        sbctl_available = await bundle_manager._check_sbctl_available()
        if not sbctl_available:
            error_message = "sbctl is not available in the environment. This is required for bundle initialization."
            logger.error(error_message)
            formatted_error = formatter.format_error(error_message)
            return [TextContent(type="text", text=formatted_error)]

        # Initialize the bundle
        result = await bundle_manager.initialize_bundle(source, force)

        # Check if the API server is available
        api_server_available = await bundle_manager.check_api_server_available()

        # Get diagnostic information
        diagnostics = await bundle_manager.get_diagnostic_info()

        # Format response using the formatter
        response = formatter.format_bundle_initialization(result, api_server_available, diagnostics)
        return check_response_size(response, "initialize_bundle", formatter)

    except BundleManagerError as e:
        error_message = f"Failed to initialize bundle: {str(e)}"
        logger.error(error_message)

        # Try to get diagnostic information even on failure
        diagnostics = None
        try:
            diagnostics = await bundle_manager.get_diagnostic_info()
        except Exception as diag_error:
            logger.error(f"Failed to get diagnostics: {diag_error}")

        formatted_error = formatter.format_error(error_message, diagnostics)
        return check_response_size(formatted_error, "initialize_bundle", formatter)
    except Exception as e:
        error_message = f"Unexpected error initializing bundle: {str(e)}"
        logger.exception(error_message)

        # Try to get diagnostic information even on failure
        diagnostics = None
        try:
            diagnostics = await bundle_manager.get_diagnostic_info()
        except Exception as diag_error:
            logger.error(f"Failed to get diagnostics: {diag_error}")

        formatted_error = formatter.format_error(error_message, diagnostics)
        return check_response_size(formatted_error, "initialize_bundle", formatter)


@mcp.tool()
async def list_available_bundles(
    include_invalid: bool = False, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    Scan the bundle storage directory to find available compressed bundle files and list them.
    This tool helps discover which bundles are available for initialization.

    Args:
        include_invalid: (boolean, optional) Whether to include invalid or inaccessible
            bundles in the results. Defaults to False.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        A list of available bundle files with details including path, size, and modification time.
        Bundles are validated to ensure they have the expected support bundle structure.
    """
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    try:
        # List available bundles
        bundles = await bundle_manager.list_available_bundles(include_invalid)

        response = formatter.format_bundle_list(bundles)
        return check_response_size(response, "list_bundles", formatter)

    except BundleManagerError as e:
        error_message = f"Failed to list bundles: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "list_bundles", formatter)
    except Exception as e:
        error_message = f"Unexpected error listing bundles: {str(e)}"
        logger.exception(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "list_bundles", formatter)


@mcp.tool()
async def kubectl(
    command: str, timeout: int = 30, json_output: bool = False, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    Execute kubectl commands against the initialized bundle's API server. Allows
    running Kubernetes CLI commands to explore resources in the support bundle.

    Args:
        command: (string, required) The kubectl command to execute (e.g., "get pods",
            "get nodes -o wide", "describe deployment nginx")
        timeout: (integer, optional) Timeout in seconds for the command. Defaults to 30.
        json_output: (boolean, optional) Whether to format the output as JSON.
            Defaults to False. Set to True for JSON output.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        The formatted output from the kubectl command, along with execution metadata
        including exit code and execution time. Returns error and diagnostic
        information if the command fails or API server is not available.
    """
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    try:
        # Check if a bundle is initialized first
        active_bundle = bundle_manager.get_active_bundle()
        if active_bundle is None or not active_bundle.initialized:
            error_message = (
                "No bundle is initialized. kubectl commands cannot be executed. "
                "Please initialize a bundle with the initialize_bundle tool first."
            )
            logger.error("No bundle initialized for kubectl command")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "kubectl", formatter)

        # Check if this is a host-only bundle
        if active_bundle.host_only_bundle:
            error_message = (
                "This support bundle contains only host resources and no cluster resources. "
                "kubectl commands are not available for host-only bundles. "
                "Use the file exploration tools (list_files, read_file, grep_files) to analyze host data instead."
            )
            logger.info("kubectl command attempted on host-only bundle")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "kubectl", formatter)

        # Check if the API server is available before attempting kubectl
        api_server_available = await bundle_manager.check_api_server_available()
        if not api_server_available:
            # Get diagnostic information
            diagnostics = await bundle_manager.get_diagnostic_info()
            error_message = (
                "Kubernetes API server is not available. kubectl commands cannot be executed. "
                "Try reinitializing the bundle with the initialize_bundle tool."
            )
            logger.error("API server not available for kubectl command")
            formatted_error = formatter.format_error(error_message, diagnostics)
            return check_response_size(formatted_error, "kubectl", formatter)

        # Execute the kubectl command
        result = await get_kubectl_executor().execute(command, timeout, json_output)

        # Format response using the formatter
        response = formatter.format_kubectl_result(result)
        return check_response_size(response, "kubectl", formatter)

    except KubectlError as e:
        error_message = f"kubectl command failed: {str(e)}"
        logger.error(error_message)

        # Try to get diagnostic information for the API server
        diagnostics = None
        try:
            diagnostics = await bundle_manager.get_diagnostic_info()

            # Check if this is a connection issue
            if "connection refused" in str(e).lower() or "could not connect" in str(e).lower():
                error_message += (
                    " This appears to be a connection issue with the Kubernetes API server. "
                    "The API server may not be running properly. "
                    "Try reinitializing the bundle with the initialize_bundle tool."
                )
        except Exception as diag_error:
            logger.error(f"Failed to get diagnostics: {diag_error}")

        formatted_error = formatter.format_error(error_message, diagnostics)
        return check_response_size(formatted_error, "kubectl", formatter)
    except BundleManagerError as e:
        error_message = f"Bundle error: {str(e)}"
        logger.error(error_message)

        # Try to get diagnostic information
        diagnostics = None
        try:
            diagnostics = await bundle_manager.get_diagnostic_info()
        except Exception as diag_error:
            logger.error(f"Failed to get diagnostics: {diag_error}")

        formatted_error = formatter.format_error(error_message, diagnostics)
        return check_response_size(formatted_error, "kubectl", formatter)
    except Exception as e:
        error_message = f"Unexpected error executing kubectl command: {str(e)}"
        logger.exception(error_message)

        # Try to get diagnostic information
        diagnostics = None
        try:
            diagnostics = await bundle_manager.get_diagnostic_info()
        except Exception as diag_error:
            logger.error(f"Failed to get diagnostics: {diag_error}")

        formatted_error = formatter.format_error(error_message, diagnostics)
        return check_response_size(formatted_error, "kubectl", formatter)


@mcp.tool()
async def list_files(
    path: str, recursive: bool = False, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    List files and directories within the support bundle. This tool lets you
    explore the directory structure of the initialized bundle.

    IMPORTANT: This tool requires a bundle to be initialized first using the `initialize_bundle` tool.
    If no bundle is initialized, use the `list_available_bundles` tool to find available bundles.

    Args:
        path: (string, required) The path within the bundle to list. Use "" or "/"
            for root directory. Path cannot contain directory traversal (e.g., "../").
        recursive: (boolean, optional) Whether to list files and directories recursively.
            Defaults to False. Set to True to show nested files.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        A JSON list of entries with file/directory information including name, path, type
        (file or dir), size, access time, modification time, and whether binary.
        Also returns metadata about the directory listing like total file and directory counts.
    """
    formatter = get_formatter(verbosity)

    try:
        result = await get_file_explorer().list_files(path, recursive)
        response = formatter.format_file_list(result)
        return check_response_size(response, "list_files", formatter)

    except FileSystemError as e:
        error_message = f"File system error: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "list_files", formatter)
    except BundleManagerError as e:
        error_message = f"Bundle error: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "list_files", formatter)
    except Exception as e:
        error_message = f"Unexpected error listing files: {str(e)}"
        logger.exception(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "list_files", formatter)


@mcp.tool()
async def read_file(
    path: str, start_line: int = 0, end_line: Optional[int] = None, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    Read a file within the support bundle with optional line range filtering.
    Displays file content with line numbers.

    IMPORTANT: This tool requires a bundle to be initialized first using the `initialize_bundle` tool.
    If no bundle is initialized, use the `list_available_bundles` tool to find available bundles.

    Args:
        path: (string, required) The path to the file within the bundle to read.
            Path cannot contain directory traversal (e.g., "../").
        start_line: (integer, optional) The line number to start reading from (0-indexed).
            Defaults to 0 (the first line).
        end_line: (integer or null, optional) The line number to end reading at
            (0-indexed, inclusive). Defaults to null, which means read to the end of the file.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        The content of the file with line numbers. For text files, displays the
        specified line range with line numbers. For binary files, displays a hex dump.
    """
    formatter = get_formatter(verbosity)

    try:
        result = await get_file_explorer().read_file(path, start_line, end_line)
        response = formatter.format_file_content(result)
        return check_response_size(response, "read_file", formatter)

    except FileSystemError as e:
        error_message = f"File system error: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "read_file", formatter)
    except BundleManagerError as e:
        error_message = f"Bundle error: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "read_file", formatter)
    except Exception as e:
        error_message = f"Unexpected error reading file: {str(e)}"
        logger.exception(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "read_file", formatter)


@mcp.tool()
async def grep_files(
    pattern: str,
    path: str,
    recursive: bool = True,
    glob_pattern: Optional[str] = None,
    case_sensitive: bool = False,
    max_results: int = 1000,
    max_results_per_file: int = 5,
    max_files: int = 10,
    verbosity: Optional[str] = None,
) -> List[TextContent]:
    """
    Search for patterns in files within the support bundle. Searches both file content
    and filenames, making it useful for finding keywords, error messages, or identifying files.

    IMPORTANT: This tool requires a bundle to be initialized first using the `initialize_bundle` tool.
    If no bundle is initialized, use the `list_available_bundles` tool to find available bundles.

    Args:
        pattern: (string, required) The pattern to search for. Supports regex syntax.
        path: (string, required) The path within the bundle to search. Use "" or "/"
            to search from root. Path cannot contain directory traversal (e.g., "../").
        recursive: (boolean, optional) Whether to search recursively in subdirectories.
            Defaults to True.
        glob_pattern: (string or null, optional) File pattern to filter which files
            to search (e.g., "*.yaml", "*.{json,log}"). Defaults to null (search all files).
        case_sensitive: (boolean, optional) Whether the search is case-sensitive.
            Defaults to False (case-insensitive search).
        max_results: (integer, optional) Maximum number of results to return.
            Defaults to 1000.
        max_results_per_file: (integer, optional) Maximum number of results to return per file.
            Defaults to 5.
        max_files: (integer, optional) Maximum number of files to search/return.
            Defaults to 10.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        Matches found in file contents and filenames, grouped by file.
        Also includes search metadata such as the number of files searched
        and the total number of matches found.
    """
    formatter = get_formatter(verbosity)

    try:
        result = await get_file_explorer().grep_files(
            pattern,
            path,
            recursive,
            glob_pattern,
            case_sensitive,
            max_results,
            max_results_per_file,
            max_files,
        )

        response = formatter.format_grep_results(result)
        return check_response_size(response, "grep_files", formatter)

    except FileSystemError as e:
        error_message = f"File system error: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "grep_files", formatter)
    except BundleManagerError as e:
        error_message = f"Bundle error: {str(e)}"
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "grep_files", formatter)
    except Exception as e:
        error_message = f"Unexpected error searching files: {str(e)}"
        logger.exception(error_message)
        formatted_error = formatter.format_error(error_message)
        return check_response_size(formatted_error, "grep_files", formatter)


# Helper function to initialize the bundle manager with a specified directory
def initialize_with_bundle_dir(bundle_dir: Optional[Path] = None) -> None:
    """
    Initialize the bundle manager with a specific directory.

    This function is used for backwards compatibility. In the new lifecycle context
    pattern, the bundle manager is automatically initialized with the directory
    during lifespan startup.

    Args:
        bundle_dir: The directory to use for bundle storage
    """
    # Note: This is now a no-op for the lifecycle context
    # The actual initialization happens in the lifespan context
    # We keep this for backwards compatibility
    if get_app_context() is None:
        # Only initialize directly if not using the lifecycle context
        get_bundle_manager(bundle_dir)


# Signal handling and shutdown functionality


async def cleanup_resources() -> None:
    """
    Clean up all resources when the server is shutting down.

    This function:
    1. Sets the global shutdown flag to prevent new operations
    2. Cleans up the bundle manager resources

    Note: Most cleanup is now handled by the lifespan context manager,
    but this function remains for compatibility and explicit cleanup
    when the server is shut down manually.
    """
    global _is_shutting_down

    if _is_shutting_down:
        logger.info("Cleanup already in progress, skipping duplicate request")
        return

    _is_shutting_down = True
    logger.info("Server shutdown initiated, cleaning up resources...")

    # Most cleanup is now handled by the lifespan context,
    # but we still clean up the bundle manager here for additional safety
    app_context = get_app_context()
    if app_context and hasattr(app_context, "bundle_manager"):
        try:
            logger.info("Cleaning up bundle manager resources")
            await app_context.bundle_manager.cleanup()
        except Exception as e:
            logger.error(f"Error during bundle manager cleanup: {e}")
    # Fallback for legacy mode
    elif "_bundle_manager" in globals() and globals()["_bundle_manager"]:
        try:
            logger.info("Cleaning up bundle manager resources (legacy mode)")
            await globals()["_bundle_manager"].cleanup()
        except Exception as e:
            logger.error(f"Error during bundle manager cleanup: {e}")

    logger.info("Server shutdown cleanup completed")


def register_signal_handlers() -> None:
    """
    Register signal handlers for graceful shutdown.

    This function sets up handlers for common termination signals to ensure
    proper cleanup of resources when the server is stopped.
    """
    try:
        # New API since Python 3.10 to get the running loop
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Fallback for when there is no running loop yet
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    def signal_handler(sig_name: str) -> Callable[[], None]:
        """Create a signal handler that triggers cleanup."""

        def handler() -> None:
            logger.info(f"Received {sig_name}, initiating graceful shutdown")
            if not loop.is_closed():
                # Schedule the cleanup task
                asyncio.create_task(cleanup_resources())

                # Give cleanup time to execute, then stop the event loop
                loop.call_later(1.0, loop.stop)

        return handler

    # Register handlers for typical termination signals
    if sys.platform != "win32":  # POSIX signals
        for sig_name, sig_num in (
            ("SIGINT", signal.SIGINT),  # Keyboard interrupt (Ctrl+C)
            ("SIGTERM", signal.SIGTERM),  # Termination signal (kill)
        ):
            try:
                loop.add_signal_handler(sig_num, signal_handler(sig_name))
                logger.debug(f"Registered {sig_name} handler for graceful shutdown")
            except (NotImplementedError, RuntimeError) as e:
                logger.warning(f"Failed to add signal handler for {sig_name}: {e}")
    else:  # Windows
        # Windows doesn't support all POSIX signals, so we only use SIGINT
        try:
            loop.add_signal_handler(signal.SIGINT, signal_handler("SIGINT"))
            logger.debug("Registered SIGINT handler for graceful shutdown on Windows")
        except (NotImplementedError, RuntimeError) as e:
            logger.warning(f"Failed to add signal handler for SIGINT: {e}")


# Register signal handlers when this module is imported
try:
    register_signal_handlers()
    logger.info("Registered signal handlers for graceful shutdown")
except Exception as e:
    logger.warning(f"Failed to register signal handlers: {e}")


# Cleanup function to call from __main__ or other shutdown points
def shutdown() -> None:
    """
    Trigger the cleanup process synchronously.

    This function can be called directly to initiate the shutdown sequence from
    non-async contexts like __main__.
    """
    try:
        # Try to get the running loop
        loop = asyncio.get_running_loop()
        if not loop.is_closed():
            # We're in an async context, create a task
            asyncio.create_task(cleanup_resources())
    except RuntimeError:
        # No running loop, create one
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Run the cleanup task
            loop.run_until_complete(cleanup_resources())
        except Exception as e:
            logger.error(f"Error during shutdown cleanup: {e}")
        finally:
            loop.close()
