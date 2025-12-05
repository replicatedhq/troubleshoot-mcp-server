"""
Entry point for the MCP server.
This comment was added to test Docker cache invalidation.
"""

import argparse
import atexit
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from .config import get_recommended_client_config
from .lifecycle import setup_signal_handlers, is_shutdown_requested
# NOTE: .server import delayed to after env var configuration

logger = logging.getLogger(__name__)


def setup_logging(
    verbose: bool = False, mcp_mode: bool = False, log_dir: Optional[Path] = None
) -> None:
    """
    Set up logging configuration.

    Args:
        verbose: Whether to enable verbose logging
        mcp_mode: Whether the server is running in MCP mode
        log_dir: Optional directory to write log files to
    """
    # Set log level based on environment, verbose flag, and mode
    if mcp_mode and not verbose:
        # In MCP mode, use ERROR or the level from env var
        env_log_level = os.environ.get("MCP_LOG_LEVEL", "ERROR").upper()
        log_levels = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        log_level = log_levels.get(env_log_level, logging.ERROR)
    else:
        # In normal mode or verbose mode, use normal levels
        log_level = logging.DEBUG if verbose else logging.INFO

    # Create formatter
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Clear existing handlers
    root_logger.handlers.clear()

    # Always add stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(log_level)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    # Add file handler if log_dir is provided (for non-stdio mode)
    if log_dir and not mcp_mode:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "mcp-server.log"
        try:
            # Use RotatingFileHandler to prevent unbounded log growth
            from logging.handlers import RotatingFileHandler

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=3,
            )
            file_handler.setLevel(logging.DEBUG)  # Always log DEBUG to file
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
            # Log that file logging is active (to stderr only to avoid chicken/egg)
            stderr_handler.stream.write(f"Logging to file: {log_file}\n")
        except Exception as e:
            stderr_handler.stream.write(f"Warning: Could not set up file logging: {e}\n")


def handle_show_config() -> None:
    """Output recommended client configuration."""
    config = get_recommended_client_config()
    json.dump(config, sys.stdout, separators=(",", ":"))
    sys.exit(0)


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parse command line arguments.

    Args:
        args: Command line arguments (defaults to sys.argv[1:])

    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(description="MCP server for Kubernetes support bundles")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--bundle-dir", type=str, help="Directory to store support bundles")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show recommended MCP client configuration",
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["stdio", "sse", "streamable-http", "http"],
        help="Transport protocol (stdio for local/subprocess, sse/streamable-http for MCP server, http for REST API)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind SSE server (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9000,
        help="Port for SSE server (default: 9000)",
    )
    return parser.parse_args(args)


def main(args: Optional[List[str]] = None) -> None:
    """
    Main entry point for the application.

    Args:
        args: Command line arguments (defaults to sys.argv[1:])
    """
    parsed_args = parse_args(args)

    # Handle special commands first
    if parsed_args.show_config:
        handle_show_config()
        return  # This should never be reached as handle_show_config exits

    # Determine transport mode
    if parsed_args.transport:
        # Explicit transport specified via CLI
        transport_mode = parsed_args.transport
        mcp_mode = transport_mode == "stdio"
    else:
        # Auto-detect: stdin is not a terminal = stdio mode
        mcp_mode = not sys.stdin.isatty()
        transport_mode = "stdio" if mcp_mode else "sse"

    # CRITICAL: Configure FastMCP settings BEFORE importing server module
    # FastMCP reads these when the module is imported
    if transport_mode != "stdio":
        os.environ["FASTMCP_HOST"] = parsed_args.host
        os.environ["FASTMCP_PORT"] = str(parsed_args.port)

    # Process bundle directory BEFORE logging setup (so logs can go to bundle dir)
    bundle_dir = None
    if parsed_args.bundle_dir:
        bundle_dir = Path(parsed_args.bundle_dir)
        bundle_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Check environment variables
        env_bundle_dir = os.environ.get("MCP_BUNDLE_STORAGE")
        if env_bundle_dir:
            bundle_dir = Path(env_bundle_dir)
            bundle_dir.mkdir(parents=True, exist_ok=True)

        # If still no bundle directory, use the default /data/bundles in container
        elif os.path.exists("/data/bundles"):
            bundle_dir = Path("/data/bundles")

    # Set up logging (with file logging to bundle_dir if available)
    setup_logging(parsed_args.verbose, mcp_mode, log_dir=bundle_dir)

    # Log startup information
    if transport_mode == "stdio":
        logger.debug("Starting MCP server in stdio mode")
    else:
        logger.info(
            f"Starting MCP server with {transport_mode} transport on {parsed_args.host}:{parsed_args.port}"
        )

    # Log bundle directory info
    if bundle_dir:
        if not mcp_mode:
            logger.info(f"Using bundle directory: {bundle_dir}")
        else:
            logger.debug(f"Using bundle directory: {bundle_dir}")

    # Configure the MCP server based on the mode
    # In stdio mode, we use environment variable to control behavior
    if mcp_mode:
        logger.debug("Configuring MCP server for stdio mode")
        os.environ["MCP_USE_STDIO"] = "true"
        # Set up signal handlers specifically for stdio mode
        setup_signal_handlers()

    # CRITICAL: Import server AFTER configuring environment variables
    # FastMCP Settings are read when the module is imported
    from .server import mcp, shutdown as server_shutdown, initialize_with_bundle_dir

    # Register shutdown function with atexit to ensure cleanup on normal exit
    logger.debug("Registering atexit shutdown handler")
    atexit.register(server_shutdown)

    # Run the server with specified transport
    try:
        if transport_mode == "http":
            # HTTP REST API mode (for Temporal workflows)
            logger.debug(f"Starting HTTP REST server on {parsed_args.host}:{parsed_args.port}")

            # Import HTTP server module
            from .http_server import run_http_server
            import anyio

            # Run HTTP server
            anyio.run(
                run_http_server,
                parsed_args.host,
                parsed_args.port,
                bundle_dir or Path("/tmp/bundles"),
            )

        elif transport_mode in ["sse", "streamable-http"]:
            # SSE or Streamable HTTP MCP protocol mode
            logger.debug(f"Starting FastMCP server with {transport_mode} transport")
            initialize_with_bundle_dir(bundle_dir)
            mcp.run(transport=transport_mode)

        else:
            # stdio MCP protocol mode
            logger.debug("Starting FastMCP server with stdio transport")
            mcp.run()

        # After server returns, check if shutdown was requested via signal
        if is_shutdown_requested():
            logger.info("Shutdown requested via signal, performing cleanup")
            server_shutdown()
            # Let Python exit naturally without sys.exit()
            return
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        # Explicitly call shutdown here to handle Ctrl+C case
        server_shutdown()
    except Exception as e:
        logger.exception(f"Error running server: {e}")
        # Ensure cleanup on error exit
        server_shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
