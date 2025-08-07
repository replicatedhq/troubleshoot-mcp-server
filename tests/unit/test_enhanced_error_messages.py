"""
Tests for enhanced error messages in File Explorer.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock

from troubleshoot_mcp_server.bundle import BundleManager, BundleMetadata
from troubleshoot_mcp_server.files import (
    FileExplorer,
    DirectoryAccessError,
    ReadFileError,
)
from tests.test_utils import TempBundleManager

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


@pytest.fixture
def temp_bundle_with_structure():
    """Create a temporary bundle with test structure."""
    with TempBundleManager() as bundle_manager:
        # Create a directory structure for testing
        bundle_path = bundle_manager.get_bundle_path()

        # Create cluster-resources/pods directory
        pods_dir = bundle_path / "cluster-resources" / "pods"
        pods_dir.mkdir(parents=True, exist_ok=True)

        # Create kube-system directory
        kube_system_dir = pods_dir / "kube-system"
        kube_system_dir.mkdir(exist_ok=True)

        # Create kube-system.json file (the suggested alternative)
        kube_system_json = pods_dir / "kube-system.json"
        kube_system_json.write_text('{"kind": "PodList", "items": []}')

        # Create kube-system.yaml file (another alternative)
        kube_system_yaml = pods_dir / "kube-system.yaml"
        kube_system_yaml.write_text("kind: PodList\nitems: []")

        # Create a logs directory with log file
        logs_dir = bundle_path / "logs"
        logs_dir.mkdir(exist_ok=True)

        app_dir = logs_dir / "app"
        app_dir.mkdir(exist_ok=True)

        app_log = logs_dir / "app.log"
        app_log.write_text("2023-01-01 10:00:00 INFO Application started")

        # Create a mock bundle manager with real paths
        mock_bundle_manager = Mock(spec=BundleManager)
        bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_path,
            kubeconfig_path=Path("/test/kubeconfig"),
            initialized=True,
        )
        mock_bundle_manager.get_active_bundle.return_value = bundle

        yield mock_bundle_manager


@pytest.mark.asyncio
async def test_directory_access_with_json_suggestion(temp_bundle_with_structure):
    """Test that accessing a directory suggests .json file when available."""
    explorer = FileExplorer(temp_bundle_with_structure)

    # Try to read the kube-system directory, should suggest kube-system.json
    with pytest.raises(DirectoryAccessError) as exc_info:
        await explorer.read_file("cluster-resources/pods/kube-system")

    error = exc_info.value
    assert "Path is not a file: cluster-resources/pods/kube-system" in str(error)
    assert "Did you mean one of these files?" in str(error)
    assert "cluster-resources/pods/kube-system.json" in str(error)
    assert "cluster-resources/pods/kube-system.yaml" in str(error)

    # Check that suggestions are available in the exception
    assert len(error.suggestions) == 2
    assert "cluster-resources/pods/kube-system.json" in error.suggestions
    assert "cluster-resources/pods/kube-system.yaml" in error.suggestions


@pytest.mark.asyncio
async def test_directory_access_with_log_suggestion(temp_bundle_with_structure):
    """Test that accessing a directory suggests .log file when available."""
    explorer = FileExplorer(temp_bundle_with_structure)

    # Try to read the app directory, should suggest app.log
    with pytest.raises(DirectoryAccessError) as exc_info:
        await explorer.read_file("logs/app")

    error = exc_info.value
    assert "Path is not a file: logs/app" in str(error)
    assert "Did you mean one of these files?" in str(error)
    assert "logs/app.log" in str(error)

    # Check suggestions
    assert len(error.suggestions) == 1
    assert "logs/app.log" in error.suggestions


@pytest.mark.asyncio
async def test_directory_access_no_suggestions(temp_bundle_with_structure):
    """Test that accessing a directory with no matching files shows standard error."""
    explorer = FileExplorer(temp_bundle_with_structure)

    # Create a directory with no matching files
    bundle_path = temp_bundle_with_structure.get_active_bundle().path
    test_dir = bundle_path / "empty-dir"
    test_dir.mkdir(exist_ok=True)

    # Try to read the directory, should fall back to standard error
    with pytest.raises(ReadFileError) as exc_info:
        await explorer.read_file("empty-dir")

    error = exc_info.value
    assert str(error) == "Path is not a file: empty-dir"
    assert not hasattr(error, "suggestions")


@pytest.mark.asyncio
async def test_non_existent_path(temp_bundle_with_structure):
    """Test that non-existent paths still raise PathNotFoundError."""
    explorer = FileExplorer(temp_bundle_with_structure)

    # Try to read a non-existent path
    with pytest.raises(Exception) as exc_info:
        await explorer.read_file("does-not-exist")

    # Should raise PathNotFoundError, not DirectoryAccessError
    assert "Path not found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_suggest_file_alternatives_method(temp_bundle_with_structure):
    """Test the _suggest_file_alternatives method directly."""
    explorer = FileExplorer(temp_bundle_with_structure)
    bundle_path = temp_bundle_with_structure.get_active_bundle().path

    # Test with kube-system directory
    kube_system_dir = bundle_path / "cluster-resources" / "pods" / "kube-system"
    suggestions = explorer._suggest_file_alternatives(kube_system_dir)

    assert len(suggestions) == 2
    assert "cluster-resources/pods/kube-system.json" in suggestions
    assert "cluster-resources/pods/kube-system.yaml" in suggestions

    # Test with app directory
    app_dir = bundle_path / "logs" / "app"
    suggestions = explorer._suggest_file_alternatives(app_dir)

    assert len(suggestions) == 1
    assert "logs/app.log" in suggestions

    # Test with directory that has no matching files
    empty_dir = bundle_path / "empty-dir"
    empty_dir.mkdir(exist_ok=True)
    suggestions = explorer._suggest_file_alternatives(empty_dir)

    assert len(suggestions) == 0


@pytest.mark.asyncio
async def test_backward_compatibility_file_reading(temp_bundle_with_structure):
    """Test that normal file reading still works as expected."""
    explorer = FileExplorer(temp_bundle_with_structure)

    # Read the actual JSON file - should work normally
    result = await explorer.read_file("cluster-resources/pods/kube-system.json")

    assert result.path == "cluster-resources/pods/kube-system.json"
    assert '{"kind": "PodList", "items": []}' in result.content
    assert result.binary is False

    # Read the actual YAML file - should work normally
    result = await explorer.read_file("cluster-resources/pods/kube-system.yaml")

    assert result.path == "cluster-resources/pods/kube-system.yaml"
    assert "kind: PodList" in result.content
    assert result.binary is False


@pytest.mark.asyncio
async def test_error_message_formatting(temp_bundle_with_structure):
    """Test the specific format of error messages."""
    explorer = FileExplorer(temp_bundle_with_structure)

    # Test the exact error message format
    with pytest.raises(DirectoryAccessError) as exc_info:
        await explorer.read_file("cluster-resources/pods/kube-system")

    error_msg = str(exc_info.value)

    # Check the specific format matches requirements
    expected_parts = [
        "Path is not a file: cluster-resources/pods/kube-system",
        "Did you mean one of these files?",
        "• cluster-resources/pods/kube-system.json",
        "• cluster-resources/pods/kube-system.yaml",
    ]

    for part in expected_parts:
        assert part in error_msg, f"Expected '{part}' in error message: {error_msg}"
