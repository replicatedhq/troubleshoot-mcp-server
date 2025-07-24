#!/usr/bin/env python3
"""
Test script to verify bundle path resolution behavior.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import Mock

from mcp_server_troubleshoot.bundle import BundleManager, BundleMetadata
from mcp_server_troubleshoot.files import FileExplorer

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_bundle_path_resolution(tmp_path: Path):
    """Test that the file explorer correctly resolves paths within the bundle."""
    # Create a temporary test directory
    temp_dir = tmp_path / "bundle_test"
    temp_dir.mkdir()
    print(f"Created test directory: {temp_dir}")

    # No manual cleanup needed - tmp_path handles it automatically

    # Set up test files that mimic a real support bundle structure
    bundle_dir = Path(temp_dir)

    # Create a structure similar to a real bundle
    # bundle_dir/
    #   extracted/
    #     support-bundle-2025-04-11T14_05_31/
    #       cluster-resources/
    #         pods/
    #           kube-system.json

    extracted_dir = bundle_dir / "extracted"
    extracted_dir.mkdir()

    support_bundle_dir = extracted_dir / "support-bundle-2025-04-11T14_05_31"
    support_bundle_dir.mkdir()

    cluster_resources_dir = support_bundle_dir / "cluster-resources"
    cluster_resources_dir.mkdir()

    pods_dir = cluster_resources_dir / "pods"
    pods_dir.mkdir()

    kube_system_file = pods_dir / "kube-system.json"
    kube_system_file.write_text('{"items": [{"metadata": {"name": "test-pod"}}]}')

    print(f"Created test structure: {bundle_dir}")

    # Mock the bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=bundle_dir,
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Create the explorer
    explorer = FileExplorer(bundle_manager)

    # Test 1: List files at root
    print("\nTest 1: List files at root")
    try:
        result = await explorer.list_files("", False)
        print(f"Files at root: {[e.name for e in result.entries]}")
        assert result.total_dirs > 0, "Should find directories at root"
    except Exception as e:
        print(f"Error listing root: {e}")

    # Test 2: List files in cluster-resources using absolute path
    print("\nTest 2: List files with absolute path")
    try:
        result = await explorer.list_files("/cluster-resources", False)
        print(f"Files in /cluster-resources: {[e.name for e in result.entries]}")
        assert "pods" in [e.name for e in result.entries], "Should find pods directory"
    except Exception as e:
        print(f"Error listing cluster-resources: {e}")

    # Test 3: Read file using absolute path
    print("\nTest 3: Read file with absolute path")
    try:
        result = await explorer.read_file("/cluster-resources/pods/kube-system.json")
        print(f"Content length: {len(result.content)}")
        assert len(result.content) > 0, "File should have content"
        assert "test-pod" in result.content, "Content should contain expected data"
    except Exception as e:
        print(f"Error reading file: {e}")

    print("\nAll tests completed")


if __name__ == "__main__":
    asyncio.run(test_bundle_path_resolution())
