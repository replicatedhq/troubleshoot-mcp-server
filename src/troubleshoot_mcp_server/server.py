"""
MCP server implementation for Kubernetes support bundles.
"""

import asyncio
import logging
import os
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
# Read host/port from environment if provided (for SSE transport)
# __main__.py sets FASTMCP_HOST and FASTMCP_PORT before importing this module
_host = os.getenv("FASTMCP_HOST", "127.0.0.1")
_port = int(os.getenv("FASTMCP_PORT", "8000"))

mcp = FastMCP(
    "troubleshoot-mcp-server",
    lifespan=app_lifespan,
    host=_host,
    port=_port,
)

# Check if list_bundles tool should be enabled
# Hidden by default to avoid confusing AI agents about bundle persistence
ENABLE_LIST_BUNDLES_TOOL = os.environ.get("ENABLE_LIST_BUNDLES_TOOL", "false").lower() in (
    "true",
    "1",
    "yes",
)

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


def _format_crash_recovery_message(crash_info: dict) -> str:
    """Format crash recovery information for display to the LLM."""
    lines = ["⚠️ SBCTL PROCESS RECOVERY:"]

    exit_code = crash_info.get("exit_code", "unknown")
    lines.append(f"The API server crashed (exit code {exit_code}) but was automatically restarted.")

    last_command = crash_info.get("last_timeout_command")
    if last_command:
        lines.append(f"Last command before crash: {last_command}")

    stderr_lines = crash_info.get("stderr_lines", [])
    if stderr_lines:
        lines.append("Error output:")
        # Show last few lines of stderr
        for line in stderr_lines[-5:]:  # Last 5 lines
            lines.append(f"  {line}")

    timestamp = crash_info.get("timestamp", "")
    if timestamp:
        lines.append(f"Recovery timestamp: {timestamp}")

    return "\n".join(lines)


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


def get_bundle_scope_id() -> Optional[str]:
    """
    Extract the bundle scope ID from the current request context.

    This is the stable, workflow-level identifier used for bundle persistence,
    NOT the transport-level session_id which rotates per request.

    Priority order:
    1. X-Bundle-Scope header (workflow_run_id from client)
    2. x-mcp-session-id header (fallback for older clients)
    3. Query param ?session_id=... (legacy support)

    Returns:
        Bundle scope ID if found, None otherwise
    """
    try:
        ctx = mcp.get_context()
        if ctx and ctx.request_context and ctx.request_context.request:
            req = ctx.request_context.request

            # Try stable scope headers first
            from_bundle_scope = req.headers.get("x-bundle-scope")
            from_custom_header = req.headers.get("x-mcp-session-id")
            from_query = req.query_params.get("session_id")

            scope_id = from_bundle_scope or from_custom_header or from_query

            if scope_id:
                source = "x-bundle-scope" if from_bundle_scope else ("x-mcp-session-id" if from_custom_header else "query")
                logger.info(f"[Bundle Scope] Using scope_id from {source}: {scope_id[:16]}...")
                return scope_id

    except Exception as e:
        logger.debug(f"Could not extract bundle scope: {e}")

    return None


def get_session_id() -> str:
    """
    Extract the MCP session_id from the current request context.

    This is the TRANSPORT correlation ID that rotates per request.
    DO NOT use this for bundle persistence - use get_bundle_scope_id() instead.

    Priority order:
    1. Query param: ?session_id=... (explicit from client)
    2. x-mcp-session-id header (custom header, if MCP SDK preserves it)
    3. mcp-session-id header (SDK's auto-generated session ID)
    4. Fallback: "default-session" when no session context available

    Returns:
        Session ID string (always returns a valid session ID)
    """
    try:
        ctx = mcp.get_context()
        if ctx and ctx.request_context and ctx.request_context.request:
            req = ctx.request_context.request

            # Try all possible sources
            from_query = req.query_params.get("session_id")
            from_custom_header = req.headers.get("x-mcp-session-id")
            from_sdk_header = req.headers.get("mcp-session-id")

            # Debug logging
            logger.debug(f"[Session] query={from_query}, x-mcp-session-id={from_custom_header}, mcp-session-id={from_sdk_header}")

            # Prefer explicit query param, then custom header, then fall back to SDK session
            session_id = from_query or from_custom_header or from_sdk_header

            if session_id:
                source = "query" if from_query else ("custom_header" if from_custom_header else "sdk_header")
                logger.info(f"[Session] Using session_id from {source}: {session_id[:16]}...")
                return session_id

    except Exception as e:
        logger.error(f"Could not extract session_id from context: {e}", exc_info=True)

    # Fallback: provide default session for stdio/test clients
    # This supports cases where SDK doesn't provide session context
    logger.debug("[Session] No session_id in context, using default-session")
    return "default-session"


def get_bundle_id_for_request() -> Optional[str]:
    """
    Get the bundle ID for the current request.

    Uses X-Bundle-Scope header (workflow_run_id) for bundle lookups.
    This is the ONLY supported mechanism for Temporal workflows.

    Returns:
        Bundle ID if found, None otherwise
    """
    bundle_manager = get_bundle_manager()

    # Get bundle scope ID (workflow_run_id from X-Bundle-Scope header)
    scope_id = get_bundle_scope_id()
    if not scope_id:
        logger.error("[Bundle Lookup] No X-Bundle-Scope header found - this is required for Temporal workflows")
        return None

    # Look up bundle for this workflow
    bundle_id = bundle_manager.get_bundle_for_session(scope_id)

    if bundle_id:
        logger.debug(f"[Bundle Lookup] Found bundle for scope_id={scope_id[:16]}...")
    else:
        logger.debug(f"[Bundle Lookup] No bundle found for scope_id={scope_id[:16]}...")

    return bundle_id


@mcp.tool()
async def initialize_bundle(
    source: str, force: bool = False, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    Initialize a Kubernetes support bundle for analysis.

    This tool loads a bundle and makes it automatically available for all subsequent
    tool calls in your workflow (kubectl, list_files, read_file, grep_files).
    You don't need to track or pass any bundle identifier - just call this once,
    and all other tools will use this bundle automatically within your workflow.

    Use `force=true` to switch to a different bundle or to reload the current bundle.

    Args:
        source: (string, required) The source of the bundle (URL or local file path)
        force: (boolean, optional) Whether to force re-initialization if a bundle
            is already active for your session. Defaults to False.
        verbosity: (string, optional) Verbosity level for response formatting
            (minimal|standard|verbose|debug). Defaults to minimal.

    Returns:
        Status indicating the bundle is ready. All subsequent tool calls will
        automatically use this bundle. If the API server is not available,
        returns diagnostic information.
    """
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    # Get stable bundle scope ID (workflow_run_id) for bundle persistence
    bundle_scope_id = get_bundle_scope_id()
    if not bundle_scope_id:
        error_message = "No bundle scope ID found. Ensure X-Bundle-Scope header is set."
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return [TextContent(type="text", text=formatted_error)]

    # Also get transport session_id for logging/tracking
    transport_session_id = get_session_id()
    logger.info(f"[init_bundle] transport_id={transport_session_id[:16]}..., scope_id={bundle_scope_id[:16]}...")

    # Validate source is not a SHA-256 hash (common AI agent error)
    import re
    if re.match(r'^[a-f0-9]{64}$', source.strip().lower()):
        error_message = (
            f"Invalid bundle source: '{source}' appears to be a SHA-256 hash. "
            "The source parameter must be a URL (e.g., https://...) or a local file path. "
            "SHA-256 hashes are internal identifiers and cannot be used as bundle sources."
        )
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return [TextContent(type="text", text=formatted_error)]

    try:
        # Check if sbctl is available before attempting to initialize
        sbctl_available = await bundle_manager._check_sbctl_available()
        if not sbctl_available:
            error_message = "sbctl is not available in the environment. This is required for bundle initialization."
            logger.error(error_message)
            formatted_error = formatter.format_error(error_message)
            return [TextContent(type="text", text=formatted_error)]

        # Initialize the bundle using bundle_scope_id (workflow_run_id) as bundle_id
        result = await bundle_manager.initialize_bundle(source, force, bundle_id=bundle_scope_id)

        # Associate THIS transport session with the bundle (for subsequent tool calls)
        bundle_manager.set_bundle_for_session(transport_session_id, result.id)
        bundle_manager.set_bundle_for_session(bundle_scope_id, result.id)  # Also map scope_id directly
        logger.info(f"Bundle {result.id} mapped to scope={bundle_scope_id[:16]}..., transport={transport_session_id[:16]}...")

        # Check if the API server is available (pass bundle_id for concurrent mode)
        api_server_available = await bundle_manager.check_api_server_available(bundle_id=result.id)

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


async def list_available_bundles_impl(
    include_invalid: bool = False, verbosity: Optional[str] = None
) -> List[TextContent]:
    """
    List previously downloaded/initialized support bundles stored locally. This tool shows
    bundles that have been downloaded or initialized before and are available in local
    storage for quick re-initialization.

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


# Conditionally register the list_available_bundles tool
if ENABLE_LIST_BUNDLES_TOOL:
    # Register as MCP tool with proper name
    @mcp.tool()
    async def list_available_bundles(
        include_invalid: bool = False, verbosity: Optional[str] = None
    ) -> List[TextContent]:
        """
        List previously downloaded/initialized support bundles stored locally. This tool shows
        bundles that have been downloaded or initialized before and are available in local
        storage for quick re-initialization.

        Args:
            include_invalid: (boolean, optional) Whether to include invalid or inaccessible
                bundles in the results. Defaults to False.
            verbosity: (string, optional) Verbosity level for response formatting
                (minimal|standard|verbose|debug). Defaults to minimal.

        Returns:
            A list of available bundle files with details including path, size, and modification time.
            Bundles are validated to ensure they have the expected support bundle structure.
        """
        return await list_available_bundles_impl(include_invalid, verbosity)
else:
    # Keep the function available for internal use but not as MCP tool
    async def list_available_bundles(
        include_invalid: bool = False, verbosity: Optional[str] = None
    ) -> List[TextContent]:
        """Internal function available when tool is disabled."""
        return await list_available_bundles_impl(include_invalid, verbosity)


@mcp.tool()
async def kubectl(
    command: str,
    timeout: int = 5,
    json_output: bool = False,
    verbosity: Optional[str] = None,
) -> List[TextContent]:
    """
    Execute kubectl commands against your active bundle's Kubernetes API server.

    This tool automatically uses the bundle you initialized with initialize_bundle.
    You don't need to specify which bundle - it uses your session's bundle automatically.

    IMPORTANT: Accepts kubectl arguments only, not shell commands. Shell operations
    like pipes (|), redirects (>), and command chaining (&&) are not supported.
    ❌ Invalid: 'get pods | grep nginx'
    ✅ Valid: 'get pods -l app=nginx'

    Args:
        command: (string, required) The kubectl command to execute (e.g., "get pods",
            "get nodes -o wide", "describe deployment nginx")
        timeout: (integer, optional) Timeout in seconds for the command. Defaults to 5.
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

    # Look up bundle using scope ID (workflow_run_id) or transport session ID
    bundle_id = get_bundle_id_for_request()
    if not bundle_id:
        error_message = (
            "No bundle initialized for your workflow. "
            "Please call initialize_bundle first to load a support bundle."
        )
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return [TextContent(type="text", text=formatted_error)]

    try:
        # Get the specific bundle by ID (lazy-load from disk if needed)
        bundle = await bundle_manager._load_bundle_from_disk_if_needed(bundle_id)
        if bundle is None or not bundle.initialized:
            error_message = (
                f"Bundle '{bundle_id}' not found or not initialized. "
                "Please initialize a bundle with the initialize_bundle tool first."
            )
            logger.error(f"Bundle {bundle_id} not found for kubectl command")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "kubectl", formatter)

        # Set as active bundle for this operation (thread-safe via property)
        bundle_manager.active_bundle_id = bundle_id

        # Check if this is a host-only bundle
        if bundle.host_only_bundle:
            error_message = (
                "This support bundle contains only host resources and no cluster resources. "
                "kubectl commands are not available for host-only bundles. "
                "Use the file exploration tools (list_files, read_file, grep_files) to analyze host data instead."
            )
            logger.info("kubectl command attempted on host-only bundle")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "kubectl", formatter)

        # Check if the API server is available before attempting kubectl (pass bundle_id for concurrent mode)
        api_server_available = await bundle_manager.check_api_server_available(bundle_id=bundle.id)
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

        # Check for crash recovery information
        crash_recovery_info = bundle_manager.get_crash_recovery_info()

        # Format response using the formatter
        response = formatter.format_kubectl_result(result)

        # Append crash recovery information if available
        if crash_recovery_info:
            recovery_message = _format_crash_recovery_message(crash_recovery_info)
            response += f"\n\n{recovery_message}"

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
    List files and directories within your active bundle.

    This tool automatically uses the bundle you initialized with initialize_bundle.
    You don't need to specify which bundle - it uses your session's bundle automatically.

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
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    # Look up bundle using scope ID (workflow_run_id) or transport session ID
    bundle_id = get_bundle_id_for_request()
    if not bundle_id:
        error_message = (
            "No bundle initialized for your workflow. "
            "Please call initialize_bundle first to load a support bundle."
        )
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return [TextContent(type="text", text=formatted_error)]

    try:
        # Get the specific bundle by ID (lazy-load from disk if needed)
        bundle = await bundle_manager._load_bundle_from_disk_if_needed(bundle_id)
        if bundle is None:
            error_message = f"Bundle '{bundle_id}' not found. Use initialize_bundle first."
            logger.error(f"Bundle {bundle_id} not found for list_files")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "list_files", formatter)

        # Set as active bundle for this operation
        bundle_manager.active_bundle_id = bundle_id

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
    path: str,
    start_line: int = 0,
    end_line: Optional[int] = None,
    verbosity: Optional[str] = None,
) -> List[TextContent]:
    """
    Read a file within your active bundle with optional line range filtering.

    This tool automatically uses the bundle you initialized with initialize_bundle.
    You don't need to specify which bundle - it uses your session's bundle automatically.

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
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    # Look up bundle using scope ID (workflow_run_id) or transport session ID
    bundle_id = get_bundle_id_for_request()
    if not bundle_id:
        error_message = (
            "No bundle initialized for your workflow. "
            "Please call initialize_bundle first to load a support bundle."
        )
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return [TextContent(type="text", text=formatted_error)]

    try:
        # Get the specific bundle by ID (lazy-load from disk if needed)
        bundle = await bundle_manager._load_bundle_from_disk_if_needed(bundle_id)
        if bundle is None:
            error_message = f"Bundle '{bundle_id}' not found. Use initialize_bundle first."
            logger.error(f"Bundle {bundle_id} not found for read_file")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "read_file", formatter)

        # Set as active bundle for this operation
        bundle_manager.active_bundle_id = bundle_id

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
    Search for patterns in files within your active bundle.

    This tool automatically uses the bundle you initialized with initialize_bundle.
    You don't need to specify which bundle - it uses your session's bundle automatically.

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
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)

    # Look up bundle using scope ID (workflow_run_id) or transport session ID
    bundle_id = get_bundle_id_for_request()
    if not bundle_id:
        error_message = (
            "No bundle initialized for your workflow. "
            "Please call initialize_bundle first to load a support bundle."
        )
        logger.error(error_message)
        formatted_error = formatter.format_error(error_message)
        return [TextContent(type="text", text=formatted_error)]

    try:
        # Get the specific bundle by ID (lazy-load from disk if needed)
        bundle = await bundle_manager._load_bundle_from_disk_if_needed(bundle_id)
        if bundle is None:
            error_message = f"Bundle '{bundle_id}' not found. Use initialize_bundle first."
            logger.error(f"Bundle {bundle_id} not found for grep_files")
            formatted_error = formatter.format_error(error_message)
            return check_response_size(formatted_error, "grep_files", formatter)

        # Set as active bundle for this operation
        bundle_manager.active_bundle_id = bundle_id

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
