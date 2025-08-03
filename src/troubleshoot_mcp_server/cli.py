"""
CLI entry points for the MCP server.
"""

import atexit
import json
import logging
import sys
from pathlib import Path
import argparse
import os

from .server import mcp, shutdown
from .config import get_recommended_client_config
from .lifecycle import setup_signal_handlers

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


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the MCP server."""
    parser = argparse.ArgumentParser(description="MCP server for Kubernetes support bundles")
    parser.add_argument("--bundle-dir", type=Path, help="Directory to store bundles")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Show recommended MCP client configuration",
    )
    parser.add_argument(
        "--use-stdio",
        action="store_true",
        help="Use stdio mode for communication (instead of detecting from tty)",
    )
    parser.add_argument(
        "--enable-periodic-cleanup",
        action="store_true",
        help="Enable periodic cleanup of bundle resources",
    )
    parser.add_argument(
        "--cleanup-interval",
        type=int,
        default=3600,
        help="Interval in seconds for periodic cleanup (default: 3600)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show version information",
    )

    return parser.parse_args()


def handle_show_config() -> None:
    """Output recommended client configuration."""
    config = get_recommended_client_config()
    json.dump(config, sys.stdout, separators=(",", ":"))
    sys.exit(0)


def handle_version() -> None:
    """Output version information."""
    from troubleshoot_mcp_server import __version__

    print(f"troubleshoot-mcp-server version {__version__}")
    sys.exit(0)


def main() -> None:
    """
    Main entry point that adapts based on how it's called.
    This allows the module to be used both as a direct CLI and
    as an MCP server that responds to JSON-RPC over stdio.
    """
    args = parse_args()

    # Handle special commands first
    if args.show_config:
        handle_show_config()
        return  # This should never be reached as handle_show_config exits

    if args.version:
        handle_version()
        return  # This should never be reached as handle_version exits

    # Determine if we're in stdio mode
    # Use explicit flag or detect from terminal
    mcp_mode = args.use_stdio or not sys.stdin.isatty()

    # Set up logging based on whether we're in MCP mode
    setup_logging(verbose=args.verbose, mcp_mode=mcp_mode)

    # Log information about startup
    if not mcp_mode:
        logger.info("Starting MCP server for Kubernetes support bundles")
    else:
        logger.debug("Starting MCP server for Kubernetes support bundles (stdio mode)")

    # Use the specified bundle directory or the default from environment
    bundle_dir = args.bundle_dir
    if not bundle_dir:
        env_bundle_dir = os.environ.get("MCP_BUNDLE_STORAGE")
        if env_bundle_dir:
            bundle_dir = Path(env_bundle_dir)

    # If still no bundle directory, use the default /data/bundles in container
    if not bundle_dir and os.path.exists("/data/bundles"):
        bundle_dir = Path("/data/bundles")

    if bundle_dir:
        if not mcp_mode:
            logger.info(f"Using bundle directory: {bundle_dir}")
        else:
            logger.debug(f"Using bundle directory: {bundle_dir}")

    # Configure the MCP server for stdio mode if needed
    if mcp_mode:
        logger.debug("Configuring MCP server for stdio mode")
        os.environ["MCP_USE_STDIO"] = "true"
        # Set up signal handlers specifically for stdio mode
        setup_signal_handlers()

    # Register shutdown handler for cleanup
    logger.debug("Registering atexit shutdown handler")
    atexit.register(shutdown)

    # Set environment variables for lifecycle parameters
    if args.enable_periodic_cleanup:
        os.environ["ENABLE_PERIODIC_CLEANUP"] = "true"
        os.environ["CLEANUP_INTERVAL"] = str(args.cleanup_interval)

    # Run the FastMCP server - this handles stdin/stdout automatically
    try:
        logger.debug("Starting FastMCP server")
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server interrupted, shutting down")
        # Explicitly call shutdown here to handle Ctrl+C case
        shutdown()
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        # Ensure cleanup on error exit
        shutdown()
        sys.exit(1)


# Entry point when run as a module
if __name__ == "__main__":
    main()
