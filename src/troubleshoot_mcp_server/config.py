"""
Configuration utilities for MCP client integration.

This module provides recommended configurations for MCP clients connecting
to the troubleshoot MCP server.
"""

import json
import logging
import os
from pathlib import Path
import yaml
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULT_BUNDLE_STORAGE = "/data/bundles"


def get_recommended_client_config() -> Dict[str, Any]:
    """
    Returns a recommended MCP client configuration.

    This function provides the suggested configuration for MCP clients
    to connect to the troubleshoot server.

    Returns:
        A dictionary with recommended client configuration
    """
    return {
        "mcpServers": {
            "troubleshoot": {
                "command": "podman",
                "args": [
                    "run",
                    "-i",
                    "--rm",
                    "-v",
                    "${HOME}/bundles:/data/bundles",
                    "-e",
                    "SBCTL_TOKEN=${SBCTL_TOKEN}",
                    "troubleshoot-mcp-server:latest",
                ],
            }
        }
    }


def load_config_from_path(config_path: str) -> Dict[str, Any]:
    """Load MCP configuration from a file path."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r") as f:
        result: Dict[str, Any] = yaml.safe_load(f)
        return result


def load_config_from_env() -> Optional[Dict[str, Any]]:
    """Load MCP configuration from the MCP_CONFIG_PATH environment variable."""
    config_path = os.environ.get("MCP_CONFIG_PATH")
    if not config_path:
        return None

    try:
        return load_config_from_path(config_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load config from environment: {e}")
        return None
