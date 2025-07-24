"""
Tests for the config module.
"""

import tempfile

import pytest
from unittest.mock import patch, mock_open
import yaml

from mcp_server_troubleshoot.config import load_config_from_path

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


def test_load_config_invalid_yaml():
    """Test that load_config_from_path handles invalid YAML."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write("key: - value\n- another-")
        f.flush()
        with pytest.raises(yaml.YAMLError):
            load_config_from_path(f.name)


@patch("mcp_server_troubleshoot.config.Path.exists", return_value=False)
def test_load_config_not_found(mock_exists):
    """Test that load_config_from_path handles a missing file."""
    with pytest.raises(FileNotFoundError):
        load_config_from_path("/nonexistent/path")


@patch("builtins.open", new_callable=mock_open, read_data="key: value")
@patch("mcp_server_troubleshoot.config.Path.exists", return_value=True)
def test_load_config_success(mock_exists, mock_file):
    """Test that load_config_from_path successfully loads a YAML file."""
    config = load_config_from_path("/fake/path/config.yaml")
    assert config == {"key": "value"}
