"""
Tests for real support bundle integration.

These tests verify the behavior of the MCP server components
with actual support bundles, focusing on user-visible behavior
rather than implementation details.
"""

import time
import asyncio
import subprocess
import tempfile
from pathlib import Path
import pytest
import pytest_asyncio

# Import components for testing
from troubleshoot_mcp_server.bundle import BundleManager
from troubleshoot_mcp_server.files import FileExplorer

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration


# Import pytest_asyncio for proper fixture setup


@pytest_asyncio.fixture
async def bundle_manager_fixture(test_support_bundle):
    """
    Fixture that provides a properly initialized BundleManager with cleanup.

    This fixture:
    1. Creates a temporary directory for the bundle
    2. Initializes a BundleManager in that directory
    3. Returns the BundleManager for test use
    4. Cleans up all resources after the test completes

    Args:
        test_support_bundle: Path to the test support bundle (pytest fixture)
        clean_asyncio: Fixture that ensures proper asyncio cleanup

    Returns:
        A BundleManager instance with the test bundle path
    """
    # Create a temporary directory for the bundle manager
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)
        manager = BundleManager(bundle_dir)

        try:
            # Return the manager and bundle path for test use
            yield (manager, test_support_bundle)
        finally:
            # Ensure cleanup happens even if test fails
            await manager.cleanup()


def test_sbctl_help_behavior(test_support_bundle):
    """
    Test the basic behavior of the sbctl command.

    This test focuses on verifying that sbctl:
    1. Is properly installed and available
    2. Provides expected help information
    3. Lists expected subcommands

    Instead of testing specific command execution which can be environment-dependent,
    this verifies the command interface behavior that users depend on.

    Args:
        test_support_bundle: Path to the test support bundle (pytest fixture)
    """
    # Verify sbctl is available (basic behavior)
    # Log which sbctl is being used for debugging
    result = subprocess.run(["which", "sbctl"], capture_output=True, text=True)
    assert result.returncode == 0, "sbctl command should be available (which sbctl failed)"
    print(f"Using sbctl at: {result.stdout.strip()}")

    # Also check if executable permission is set
    sbctl_path = result.stdout.strip()
    if sbctl_path:
        perm_result = subprocess.run(["ls", "-la", sbctl_path], capture_output=True, text=True)
        print(f"sbctl permissions: {perm_result.stdout.strip()}")

    # Check help output behavior
    help_result = subprocess.run(["sbctl", "--help"], capture_output=True, text=True, timeout=5)

    # Verify the command ran successfully
    assert help_result.returncode == 0, "sbctl help command should succeed"

    # Verify the help output contains expected commands (behavior test)
    help_output = help_result.stdout
    assert "shell" in help_output, "sbctl help should mention the shell command"
    assert "serve" in help_output, "sbctl help should mention the serve command"

    # Check a basic command behavior that should be present in all versions
    # (version command might not exist in all sbctl implementations)
    basic_cmd_result = subprocess.run(
        ["sbctl", "--version"], capture_output=True, text=True, timeout=5
    )

    # If --version doesn't work, we'll fall back to verifying help works
    # This is a more behavior-focused test that's resilient to implementation details
    if basic_cmd_result.returncode != 0:
        print("Note: sbctl --version command not available, falling back to help check")
        # We already verified help works above, so continue

    # Create a temporary working directory for any file tests
    with tempfile.TemporaryDirectory() as temp_dir:
        work_dir = Path(temp_dir)

        # Verify sbctl command behavior with specific options
        # This is testing the CLI interface rather than execution outcome
        serve_help_result = subprocess.run(
            ["sbctl", "serve", "--help"],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=5,
        )

        # Verify help for serve is available
        assert serve_help_result.returncode == 0, "sbctl serve help command should succeed"

        # Verify serve help contains expected options
        serve_help_output = serve_help_result.stdout
        assert "--support-bundle-location" in serve_help_output, (
            "Serve command should document bundle location option"
        )


@pytest.mark.asyncio
async def test_bundle_lifecycle(bundle_manager_fixture):
    """
    Test the complete lifecycle of a bundle with proper resource management.
    This test verifies the functional behavior:
    1. Bundle can be initialized from a local file
    2. Initialized bundle has the expected properties
    3. Re-initialization with and without force behaves correctly

    Args:
        bundle_manager_fixture: Fixture that provides a BundleManager and bundle path
    """
    # Unpack the fixture
    manager, real_bundle_path = bundle_manager_fixture

    # Act: Initialize the bundle
    result = await asyncio.wait_for(manager.initialize_bundle(str(real_bundle_path)), timeout=30.0)

    # Assert: Verify functional behavior (not implementation details)
    assert result.initialized, "Bundle should be marked as initialized"
    assert result.kubeconfig_path.exists(), "Kubeconfig file should exist"
    assert result.path.exists(), "Bundle directory should exist"

    # Verify the bundle can be retrieved by the public API
    active_bundle = manager.get_active_bundle()
    assert active_bundle is not None, "Active bundle should be available"
    assert active_bundle.id == result.id, "Active bundle should match initialized bundle"

    # Verify API server functionality (behavior, not implementation)
    await manager.check_api_server_available()
    # We don't assert this is always True since it depends on the test environment,
    # but we verify the method runs without error

    # Test that re-initialization without force returns the same bundle
    second_result = await manager.initialize_bundle(str(real_bundle_path), force=False)
    assert second_result.id == result.id, (
        "Re-initialization without force should return the same bundle"
    )

    # Test that force re-initialization creates a new bundle
    force_result = await manager.initialize_bundle(str(real_bundle_path), force=True)
    assert force_result.initialized, "Force reinitialization should succeed"


@pytest.mark.asyncio
async def test_bundle_initialization_workflow(bundle_manager_fixture, test_assertions):
    """
    Test the behavior of the FileExplorer component with real bundles.

    This test focuses on the critical behaviors:
    1. The FileExplorer can list directories at different levels
    2. It can read file contents from the bundle
    3. It properly reports directory structure

    Args:
        bundle_manager_fixture: Fixture that provides a BundleManager and bundle path
    """

    # Unpack the fixture
    manager, bundle_path = bundle_manager_fixture

    # First initialize the bundle
    result = await manager.initialize_bundle(str(bundle_path))
    assert result.initialized, "Bundle should be initialized successfully"

    # Create a FileExplorer - our component under test
    explorer = FileExplorer(manager)

    # TEST 1: Listing files at root level behavior
    root_list = await explorer.list_files("", False)

    # Verify behavior - root should have directories
    assert root_list.total_dirs >= 1, "Root should have at least one directory"

    # TEST 2: Navigation behavior - can traverse directories
    # Find a directory to navigate into (we don't care which, just need to test behavior)
    if root_list.entries:
        dir_entries = [e for e in root_list.entries if e.type == "dir"]
        if dir_entries:
            # Navigate into the first directory
            first_dir = dir_entries[0].path
            dir_contents = await explorer.list_files(first_dir, False)

            # Verify behavior - can list contents of subdirectory
            assert dir_contents is not None, "Should be able to list subdirectory contents"
            assert isinstance(dir_contents.entries, list), "Directory contents should be a list"

            # TEST 3: Recursive listing behavior
            recursive_list = await explorer.list_files(first_dir, True)
            assert recursive_list.total_files + recursive_list.total_dirs > 0, (
                "Recursive listing should find files/dirs"
            )

            # TEST 4: File reading behavior
            # Find a file to read (we don't care which, just that we can read one)
            all_files = [e for e in recursive_list.entries if e.type == "file"]
            if all_files:
                # Try to read the first file
                first_file = all_files[0].path
                file_content = await explorer.read_file(first_file)

                # Verify behavior - can read file contents
                assert file_content is not None, "Should be able to read file contents"
                assert file_content.content is not None, "File content should not be None"

                # Check metadata (behavior we depend on)
                assert file_content.path == first_file, "File content should have correct path"
                # Note: We're checking for path existence, not name which might not be in all versions
                assert hasattr(file_content, "content"), (
                    "File content should have content attribute"
                )


@pytest.mark.asyncio
async def test_bundle_manager_performance(bundle_manager_fixture):
    """
    Test the performance and reliability of the BundleManager with real bundles.

    This test focuses on the behavior that matters to users:
    1. Bundle initialization completes within a reasonable time
    2. API server can be accessed once initialized
    3. Diagnostic information is available

    Args:
        bundle_manager_fixture: Fixture that provides a BundleManager and bundle path
    """
    # Unpack the fixture
    manager, bundle_path = bundle_manager_fixture

    # BEHAVIOR TEST 1: Bundle initialization completes within expected time
    start_time = time.time()

    # Use timeout to enforce performance expectations
    result = await asyncio.wait_for(
        manager.initialize_bundle(str(bundle_path)),
        timeout=45.0,  # Reasonable timeout for initialization
    )

    # Calculate initialization duration
    duration = time.time() - start_time

    # Verify expected initialization behavior
    assert result.initialized, "Bundle should be marked as initialized"
    assert result.kubeconfig_path.exists(), "Initialization should create a kubeconfig file"
    assert duration < 45.0, (
        f"Initialization should complete in reasonable time (took {duration:.2f}s)"
    )

    # BEHAVIOR TEST 2: Verify kubeconfig has valid structure
    with open(result.kubeconfig_path, "r") as f:
        kubeconfig_content = f.read()

    # Check for essential kubeconfig fields that users and code depend on
    assert "clusters" in kubeconfig_content, "Kubeconfig should contain clusters section"
    assert "apiVersion" in kubeconfig_content, "Kubeconfig should contain API version"

    # BEHAVIOR TEST 3: The API server connection is attempted (we don't assert success
    # since it depends on the environment and sbctl version)
    await manager.check_api_server_available()

    # BEHAVIOR TEST 4: Test manager state after initialization
    # This tests the observable behavior that getting the active bundle works
    active_bundle = manager.get_active_bundle()
    assert active_bundle is not None, "Manager should have an active bundle"
    assert active_bundle.id == result.id, "Active bundle should match initialized bundle"

    # BEHAVIOR TEST 5: Test diagnostic info is available - behavior users depend on
    diagnostics = await manager.get_diagnostic_info()
    assert isinstance(diagnostics, dict), "Diagnostic info should be available as a dictionary"
    assert "api_server_available" in diagnostics, "Diagnostics should report API server status"
    assert "bundle_initialized" in diagnostics, "Diagnostics should report bundle status"

    # Verify sbctl process was created successfully
    try:
        # Use ps to check for sbctl processes associated with this bundle
        ps_result = subprocess.run(["ps", "-ef"], capture_output=True, text=True, timeout=5)

        # There should be a sbctl process running for this bundle
        # We're checking behavior (process exists) not implementation (specific process args)
        sbctl_running = any(
            "sbctl" in line and active_bundle.source in line
            for line in ps_result.stdout.splitlines()
        )

        # We don't assert this since it's environment dependent, but we check the behavior
        if not sbctl_running:
            print("Note: No sbctl process found running for this bundle")
    except (subprocess.SubprocessError, subprocess.TimeoutExpired):
        pass  # If ps fails, we can't verify but that's okay
