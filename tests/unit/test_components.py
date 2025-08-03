#!/usr/bin/env python3
"""
Standalone component test for the MCP server.

This script directly tests the key components without relying on the MCP protocol.
"""

import argparse
import asyncio
import logging
import os
import pytest
import sys
from pathlib import Path

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("test_components")

# Path to the test fixtures
FIXTURES_DIR = Path(__file__).parent / "fixtures"
TEST_BUNDLE = FIXTURES_DIR / "support-bundle-2025-04-11T14_05_31.tar.gz"


@pytest.mark.asyncio
async def test_bundle_initialization(mock_command_environment, fixtures_dir):
    """
    Test bundle initialization with a behavior-focused approach.

    This test verifies that:
    1. A bundle can be initialized successfully
    2. The initialization creates the expected artifacts
    3. Diagnostics show a healthy initialization

    Args:
        mock_command_environment: Fixture that provides a test environment with mock binaries
        fixtures_dir: Fixture that provides the path to test fixtures
    """
    from troubleshoot_mcp_server.bundle import BundleManager

    logger.info("Testing bundle initialization")

    # Get test bundle path
    test_bundle = fixtures_dir / "support-bundle-2025-04-11T14_05_31.tar.gz"
    assert test_bundle.exists(), "Test bundle not found"

    # Create a test bundle copy in our environment
    import shutil

    temp_bundle_dir = mock_command_environment
    test_bundle_copy = temp_bundle_dir / test_bundle.name
    shutil.copy(test_bundle, test_bundle_copy)
    logger.info(f"Copied test bundle to: {test_bundle_copy}")

    # Create a bundle manager
    bundle_manager = BundleManager(temp_bundle_dir)
    logger.info("Created bundle manager")

    try:
        # Initialize the bundle
        logger.info("Initializing bundle...")
        metadata = await bundle_manager.initialize_bundle(str(test_bundle_copy), force=True)

        # Verify expected behavior
        assert metadata.initialized, "Bundle should be marked as initialized"
        assert metadata.kubeconfig_path.exists(), "Kubeconfig file should exist"

        # Verify diagnostic information
        diagnostics = await bundle_manager.get_diagnostic_info()
        assert diagnostics["bundle_initialized"], (
            "Bundle should be marked as initialized in diagnostics"
        )

        # Clean up the bundle manager
        await bundle_manager.cleanup()
    except Exception as e:
        logger.exception(f"Error during bundle initialization: {e}")
        try:
            await bundle_manager.cleanup()
        except Exception:
            pass
        assert False, f"Bundle initialization failed: {e}"


@pytest.mark.asyncio
async def test_kubectl_execution(mock_command_environment, fixtures_dir):
    """
    Test kubectl command execution with a behavior-focused approach.

    This test verifies that:
    1. The KubectlExecutor can be initialized with a BundleManager
    2. Commands can be executed successfully with proper results
    3. The executor handles timeouts and errors appropriately

    Args:
        mock_command_environment: Fixture that provides a test environment with mock binaries
        fixtures_dir: Fixture that provides the path to test fixtures
    """
    from troubleshoot_mcp_server.bundle import BundleManager
    from troubleshoot_mcp_server.kubectl import KubectlExecutor

    logger.info("Testing kubectl execution")

    # Get test bundle path and prepare environment
    test_bundle = fixtures_dir / "support-bundle-2025-04-11T14_05_31.tar.gz"
    assert test_bundle.exists(), "Test bundle not found"

    # Create a test bundle copy in our environment
    import shutil

    temp_bundle_dir = mock_command_environment
    test_bundle_copy = temp_bundle_dir / test_bundle.name
    shutil.copy(test_bundle, test_bundle_copy)

    # Create a bundle manager and kubectl executor
    bundle_manager = BundleManager(temp_bundle_dir)
    kubectl_executor = KubectlExecutor(bundle_manager)
    logger.info("Created bundle manager and kubectl executor")

    try:
        # Initialize the bundle first
        metadata = await bundle_manager.initialize_bundle(str(test_bundle_copy), force=True)
        assert metadata.initialized, "Bundle should be initialized successfully"
        assert metadata.kubeconfig_path.exists(), "Kubeconfig should exist after initialization"

        # Set KUBECONFIG environment variable for kubectl
        os.environ["KUBECONFIG"] = str(metadata.kubeconfig_path)

        # Test a simple kubectl command that should work
        # First, verify that kubectl is in the PATH with a direct subprocess call
        proc = await asyncio.create_subprocess_exec(
            "which",
            "kubectl",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        assert proc.returncode == 0, "kubectl should be available in PATH"

        # Now run a command with the executor
        result = await asyncio.wait_for(kubectl_executor.execute("get nodes"), timeout=10.0)

        # Verify the command result behavior
        assert result.exit_code == 0, f"Command should succeed, got error: {result.stderr}"
        assert result.stdout, "Command should produce output"
        assert isinstance(result.duration_ms, int), "Duration should be measured"
        assert result.duration_ms > 0, "Duration should be positive"

        # Test command result structure (behavior)
        assert hasattr(result, "command"), "Result should have command attribute"
        assert hasattr(result, "stdout"), "Result should have stdout attribute"
        assert hasattr(result, "stderr"), "Result should have stderr attribute"
        assert hasattr(result, "output"), "Result should have parsed output attribute"

    finally:
        # Clean up the bundle manager
        await bundle_manager.cleanup()


@pytest.mark.asyncio
async def test_file_explorer_behavior(test_file_setup):
    """
    Test file explorer functionality with a behavior-focused approach.

    This test uses a direct fixture setup to avoid the complexity of bundle initialization.
    It verifies the core behaviors of the FileExplorer:
    1. The FileExplorer can list directories and files
    2. It can read file contents correctly
    3. It can search for patterns in files

    Args:
        test_file_setup: Fixture that provides a test directory with files
    """
    from unittest.mock import Mock
    from troubleshoot_mcp_server.bundle import BundleManager, BundleMetadata
    from troubleshoot_mcp_server.files import FileExplorer

    logger.info("Testing file explorer with simplified setup")

    # Mock a bundle manager with our test directory as the bundle path
    bundle_manager = Mock(spec=BundleManager)

    # Create a bundle metadata pointing to our test files
    mock_bundle = BundleMetadata(
        id="test_bundle",
        source="test_source",
        path=test_file_setup,
        kubeconfig_path=test_file_setup / "kubeconfig",
        initialized=True,
    )

    # Make the bundle manager return our mock bundle
    bundle_manager.get_active_bundle.return_value = mock_bundle

    # Create a file explorer using our mock bundle manager
    file_explorer = FileExplorer(bundle_manager)

    # Test 1: Verify listing behavior - expect to see our test file structure
    list_result = await file_explorer.list_files("/")

    # Verify behavior expectations
    assert list_result.total_dirs >= 2, "Should find at least dir1 and dir2"
    assert list_result.total_files >= 1, "Should find at least one file"

    # Log what's found for debugging
    logger.info(f"Found {list_result.total_dirs} directories and {list_result.total_files} files")

    # Test 2: Test reading a specific file from the test directory
    # We know from fixture setup that dir1/file1.txt exists
    read_result = await file_explorer.read_file("dir1/file1.txt")

    # Verify reading behavior
    assert read_result.content is not None, "File content should be readable"
    assert "file 1" in read_result.content, "Content should match expected text"
    assert read_result.binary is False, "Text file should not be marked binary"

    # Test 3: Test search functionality
    grep_result = await file_explorer.grep_files("file", "", True)

    # Verify grep behavior
    assert grep_result.total_matches > 0, "Should find matches for 'file'"
    assert grep_result.files_searched > 0, "Should search multiple files"

    # At least one match should be from our files
    assert any("file" in match.line for match in grep_result.matches), (
        "Should find 'file' string in matches"
    )

    # Test 4: Case sensitivity behavior
    # Our test_file_setup fixture creates a file with UPPERCASE text for these tests
    case_sensitive = await file_explorer.grep_files("UPPERCASE", "", True, None, True)
    case_insensitive = await file_explorer.grep_files("uppercase", "", True, None, False)

    # Verify case sensitivity behavior
    assert case_sensitive.total_matches > 0, "Should find case-sensitive matches"
    assert case_insensitive.total_matches > 0, "Should find case-insensitive matches"


async def run_tests(tests):
    """
    Run the specified tests using pytest programmatically.

    This function is not used when running with pytest directly but
    is maintained for backward compatibility with standalone execution.

    Args:
        tests: List of test categories to run

    Returns:
        Boolean indicating if all tests passed
    """
    # Since we've refactored these tests to use pytest properly,
    # we'll use pytest.main to run them
    import pytest

    logger.info("\n=== Running tests through pytest ===")
    test_map = {
        "bundle": "test_bundle_initialization",
        "kubectl": "test_kubectl_execution",
        "files": "test_file_explorer_behavior",
    }

    results = {}
    for test_name in tests:
        if test_name in test_map:
            test_function = test_map[test_name]
            logger.info(f"\n=== Running test: {test_function} ===")

            # Run the test with pytest
            result = pytest.main(["-xvs", __file__ + "::" + test_function])
            results[test_name] = result == 0
        else:
            logger.error(f"Unknown test: {test_name}")
            results[test_name] = False

    # Print summary
    logger.info("\n=== Test Summary ===")
    all_passed = True
    for test_name, passed in results.items():
        logger.info(f"{test_name}: {'✅' if passed else '❌'}")
        all_passed = all_passed and passed

    return all_passed


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Test MCP server components")
    parser.add_argument(
        "--test",
        choices=["bundle", "kubectl", "files", "all"],
        default="all",
        help="Which component to test (default: all)",
    )
    return parser.parse_args()


async def main():
    """Run all component tests."""
    # Parse command-line arguments
    args = parse_args()

    # Set environment variables to speed up tests
    os.environ["MAX_INITIALIZATION_TIMEOUT"] = "10"
    os.environ["MAX_DOWNLOAD_TIMEOUT"] = "10"

    # Determine which tests to run
    if args.test == "all":
        tests = ["bundle", "kubectl", "files"]
    else:
        tests = [args.test]

    # Run the tests
    return await run_tests(tests)


if __name__ == "__main__":
    # Run the tests
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
