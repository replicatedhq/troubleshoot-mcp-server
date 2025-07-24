#!/usr/bin/env python3
"""
Test script to verify the grep_files function fix.
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
async def test_grep_files_with_kubeconfig(tmp_path: Path):
    """Test the grep_files function with the 'kubeconfig' pattern."""
    # Create a temporary test directory
    temp_dir = tmp_path / "grep_test"
    temp_dir.mkdir()
    print(f"Created test directory: {temp_dir}")

    # No manual cleanup needed - tmp_path handles it automatically

    # Set up test files
    bundle_dir = Path(temp_dir)

    # Create a kubeconfig file
    kubeconfig_path = bundle_dir / "kubeconfig"
    kubeconfig_path.write_text("apiVersion: v1\nkind: Config\nclusters:\n- name: test-cluster\n")
    print(f"Created kubeconfig file at: {kubeconfig_path}")

    # Create a directory with a few test files
    test_dir = bundle_dir / "test-dir"
    test_dir.mkdir()

    # Create a file with 'kubeconfig' in its contents
    ref_file = test_dir / "config-reference.txt"
    ref_file.write_text("This file refers to a kubeconfig file.\nThe kubeconfig path is important.")
    print(f"Created reference file at: {ref_file}")

    # Create a nested kubeconfig file
    nested_dir = test_dir / "nested"
    nested_dir.mkdir()
    nested_kubeconfig = nested_dir / "kubeconfig.yaml"
    nested_kubeconfig.write_text(
        "# This is another kubeconfig file\napiVersion: v1\nkind: Config\n"
    )
    print(f"Created nested kubeconfig file at: {nested_kubeconfig}")

    # Set up the explorer
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=bundle_dir,
        kubeconfig_path=bundle_dir / "kubeconfig",
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    explorer = FileExplorer(bundle_manager)

    # Test searching for "kubeconfig" (case insensitive)
    result = await explorer.grep_files("kubeconfig", "", recursive=True, case_sensitive=False)

    # Print the results
    print("\nSearch results for 'kubeconfig':")
    print(f"Total matches: {result.total_matches}")
    print(f"Files searched: {result.files_searched}")

    if result.matches:
        print("\nMatches found:")
        for i, match in enumerate(result.matches, 1):
            print(f"{i}. Match in '{match.path}': '{match.line}'")
    else:
        print("No matches found!")

    # We expect to find matches in file names and contents
    assert result.total_matches > 0, "Should find at least some matches"

    # Check for filename matches
    filename_matches = [m for m in result.matches if "File:" in m.line]
    print(f"\nFilename matches: {len(filename_matches)}")
    for match in filename_matches:
        print(f"  - {match.path}: {match.line}")

    # Check for content matches
    content_matches = [m for m in result.matches if "File:" not in m.line]
    print(f"\nContent matches: {len(content_matches)}")
    for match in content_matches:
        print(f"  - {match.path}: {match.line}")

    # Verify we found both types of matches
    assert len(filename_matches) > 0, "Should find filename matches"
    assert len(content_matches) > 0, "Should find content matches"

    print("\nTest passed successfully!")


if __name__ == "__main__":
    asyncio.run(test_grep_files_with_kubeconfig())
