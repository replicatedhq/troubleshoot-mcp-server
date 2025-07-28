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

from .server import mcp, shutdown
from .config import get_recommended_client_config
from .lifecycle import setup_signal_handlers, is_shutdown_requested

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False, mcp_mode: bool = False) -> None:
    """
    Set up logging configuration.

    Args:
        verbose: Whether to enable verbose logging
        mcp_mode: Whether the server is running in MCP mode
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

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )

    # When in MCP mode, ensure all loggers use stderr
    if mcp_mode:
        # Configure root logger to use stderr
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if hasattr(handler, "stream"):
                handler.stream = sys.stderr


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

    # Detect if we're running in MCP mode (stdin is not a terminal)
    mcp_mode = not sys.stdin.isatty()

    # Set up logging
    setup_logging(parsed_args.verbose, mcp_mode)

    # Log startup information
    if not mcp_mode:
        logger.info("Starting MCP server for Kubernetes support bundles")
    else:
        logger.debug("Starting MCP server for Kubernetes support bundles (stdio mode)")

    # Process bundle directory
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

    # Register shutdown function with atexit to ensure cleanup on normal exit
    logger.debug("Registering atexit shutdown handler")
    atexit.register(shutdown)

    # Run the FastMCP server - this handles stdin/stdout automatically
    try:
        logger.debug("Starting FastMCP server")
        mcp.run()

        # After mcp.run() returns, check if shutdown was requested via signal
        if is_shutdown_requested():
            logger.info("Shutdown requested via signal, performing cleanup")
            shutdown()
            # Let Python exit naturally without sys.exit()
            return
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        # Explicitly call shutdown here to handle Ctrl+C case
        shutdown()
    except Exception as e:
        logger.exception(f"Error running server: {e}")
        # Ensure cleanup on error exit
        shutdown()
        sys.exit(1)


if __name__ == "__main__":
    main()
