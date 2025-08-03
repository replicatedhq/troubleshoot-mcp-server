"""
Tests for MCP client configuration.
"""

import pytest


@pytest.mark.integration
def test_config_provides_recommended_defaults():
    """Test that the config module provides recommended defaults."""
    # Import the config module directly
    from troubleshoot_mcp_server.config import get_recommended_client_config

    # Get the recommended configuration
    config = get_recommended_client_config()

    # Check for expected structure and values
    assert "mcpServers" in config
    assert "troubleshoot" in config["mcpServers"]
    assert config["mcpServers"]["troubleshoot"]["command"] == "podman"

    # Check for important flags and arguments
    args = config["mcpServers"]["troubleshoot"]["args"]
    assert "-i" in args
    assert "--rm" in args
    assert any("SBCTL_TOKEN" in arg for arg in args)
    assert any("/data/bundles" in arg for arg in args)
