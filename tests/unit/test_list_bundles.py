"""
Tests for the list_available_bundles method in BundleManager.
"""

import tarfile
from pathlib import Path

import pytest

from troubleshoot_mcp_server.bundle import BundleManager


@pytest.fixture
def temp_bundle_dir(tmp_path: Path):
    """Create a temporary directory for test bundles."""
    temp_dir = tmp_path / "bundles"
    temp_dir.mkdir()
    yield temp_dir
    # No manual cleanup needed - tmp_path handles it automatically


@pytest.fixture
def mock_valid_bundle(temp_bundle_dir):
    """Create a mock valid support bundle file."""
    # Create a tar.gz file with the expected structure
    bundle_path = temp_bundle_dir / "valid_bundle.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as tar:
        # Add a cluster-resources directory
        info = tarfile.TarInfo("support-bundle-2023/cluster-resources/pods.json")
        info.size = 0
        tar.addfile(info)
    return bundle_path


@pytest.fixture
def mock_invalid_bundle(temp_bundle_dir):
    """Create a mock invalid bundle file."""
    # Create a tar.gz file without the expected structure
    bundle_path = temp_bundle_dir / "invalid_bundle.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as tar:
        # Add a file but not the expected structure
        info = tarfile.TarInfo("some_file.txt")
        info.size = 0
        tar.addfile(info)
    return bundle_path


@pytest.fixture
def mock_non_tar_file(temp_bundle_dir):
    """Create a mock file that is not a tar.gz."""
    # Create a file that is not a tar.gz
    file_path = temp_bundle_dir / "not_a_bundle.txt"
    with open(file_path, "w") as f:
        f.write("This is not a tar.gz file")
    return file_path


@pytest.mark.asyncio
async def test_list_available_bundles_empty_dir(temp_bundle_dir):
    """Test listing bundles with an empty directory."""
    bundle_manager = BundleManager(temp_bundle_dir)
    bundles = await bundle_manager.list_available_bundles()

    assert len(bundles) == 0


@pytest.mark.asyncio
async def test_list_available_bundles_valid_bundle(temp_bundle_dir, mock_valid_bundle):
    """Test listing bundles with a valid bundle."""
    bundle_manager = BundleManager(temp_bundle_dir)
    bundles = await bundle_manager.list_available_bundles()

    assert len(bundles) == 1
    assert bundles[0].name == "valid_bundle.tar.gz"
    assert bundles[0].path == str(mock_valid_bundle)
    assert bundles[0].relative_path == "valid_bundle.tar.gz"  # Check relative path
    assert bundles[0].valid is True
    assert bundles[0].validation_message is None


@pytest.mark.asyncio
async def test_list_available_bundles_invalid_bundle(temp_bundle_dir, mock_invalid_bundle):
    """Test listing bundles with an invalid bundle."""
    bundle_manager = BundleManager(temp_bundle_dir)

    # By default invalid bundles are excluded
    bundles = await bundle_manager.list_available_bundles()
    assert len(bundles) == 0

    # With include_invalid=True they should be included
    bundles = await bundle_manager.list_available_bundles(include_invalid=True)
    assert len(bundles) == 1
    assert bundles[0].name == "invalid_bundle.tar.gz"
    assert bundles[0].path == str(mock_invalid_bundle)
    assert bundles[0].relative_path == "invalid_bundle.tar.gz"  # Check relative path
    assert bundles[0].valid is False
    assert bundles[0].validation_message is not None


@pytest.mark.asyncio
async def test_list_available_bundles_mixed(
    temp_bundle_dir, mock_valid_bundle, mock_invalid_bundle
):
    """Test listing bundles with both valid and invalid bundles."""
    bundle_manager = BundleManager(temp_bundle_dir)

    # By default only valid bundles should be included
    bundles = await bundle_manager.list_available_bundles()
    assert len(bundles) == 1
    assert bundles[0].name == "valid_bundle.tar.gz"

    # With include_invalid=True, both should be included
    bundles = await bundle_manager.list_available_bundles(include_invalid=True)
    assert len(bundles) == 2

    # Sort the bundles by name for predictable test results regardless of file timing
    # which can vary between systems (especially in CI)
    sorted_bundles = sorted(bundles, key=lambda x: x.name)
    assert sorted_bundles[0].name == "invalid_bundle.tar.gz"
    assert sorted_bundles[1].name == "valid_bundle.tar.gz"


@pytest.mark.asyncio
async def test_list_available_bundles_non_existing_dir(temp_bundle_dir):
    """Test listing bundles with a non-existing directory."""
    non_existing_dir = temp_bundle_dir / "non_existent_subdir"
    # Don't create the directory, but it's in a valid parent
    bundle_manager = BundleManager(non_existing_dir)
    bundles = await bundle_manager.list_available_bundles()

    assert len(bundles) == 0


@pytest.mark.asyncio
async def test_bundle_validity_checker(
    temp_bundle_dir, mock_valid_bundle, mock_invalid_bundle, mock_non_tar_file
):
    """Test the bundle validity checker."""
    bundle_manager = BundleManager(temp_bundle_dir)

    # Valid bundle
    valid, message = bundle_manager._check_bundle_validity(mock_valid_bundle)
    assert valid is True
    assert message is None

    # Invalid bundle
    valid, message = bundle_manager._check_bundle_validity(mock_invalid_bundle)
    assert valid is False
    assert message is not None

    # Non-tar file
    valid, message = bundle_manager._check_bundle_validity(mock_non_tar_file)
    assert valid is False
    assert message is not None

    # Non-existing file
    valid, message = bundle_manager._check_bundle_validity(Path("/non/existing/file.tar.gz"))
    assert valid is False
    assert message is not None


@pytest.mark.asyncio
async def test_relative_path_initialization(temp_bundle_dir, mock_valid_bundle):
    """Test that a bundle can be initialized using the relative path.

    This test verifies the user workflow of:
    1. Listing available bundles
    2. Using the relative_path from the bundle listing to initialize a bundle
    3. Successfully initializing the bundle with the relative path
    """
    import logging
    import os
    from unittest.mock import patch

    logger = logging.getLogger(__name__)

    # Create a bundle manager with our test directory
    bundle_manager = BundleManager(temp_bundle_dir)

    # List available bundles - this is the first step in the user workflow
    bundles = await bundle_manager.list_available_bundles()
    assert len(bundles) == 1

    # Get the relative path - this is what a user would use from the UI
    relative_path = bundles[0].relative_path
    assert relative_path == "valid_bundle.tar.gz"

    # Instead of monkeypatching internal methods, we'll mock at a higher level
    # This focuses on the behavior (initializing a bundle) rather than implementation
    with patch.object(bundle_manager, "_initialize_with_sbctl", autospec=False) as mock_init:
        # Set up the mock to create the kubeconfig file and return its path
        async def side_effect(bundle_path, output_dir):
            logger.info(f"Creating mock kubeconfig in {output_dir}")
            # Ensure the output directory exists
            os.makedirs(output_dir, exist_ok=True)
            # Create a minimal kubeconfig file
            kubeconfig_path = output_dir / "kubeconfig"
            with open(kubeconfig_path, "w") as f:
                f.write("{}")
            return kubeconfig_path

        mock_init.side_effect = side_effect

        # Test initializing with relative path (the actual user workflow)
        metadata = await bundle_manager.initialize_bundle(relative_path)

        # Verify the behavior (not implementation details)
        assert metadata is not None
        assert metadata.initialized is True
        assert metadata.source == relative_path
        assert metadata.kubeconfig_path.exists()

        # Clean up for the next part of the test
        await bundle_manager._cleanup_active_bundle()

        # Test also works with full path
        metadata = await bundle_manager.initialize_bundle(str(mock_valid_bundle))
        assert metadata is not None
        assert metadata.initialized is True
        assert metadata.source == str(mock_valid_bundle)


@pytest.mark.asyncio
async def test_bundle_path_resolution_behavior(temp_bundle_dir, mock_valid_bundle):
    """Test that the bundle manager correctly resolves different path formats.

    This test verifies the behavior of the bundle path resolution logic:
    1. Absolute paths are used directly
    2. Relative paths are resolved within the bundle directory
    3. Filenames are found within the bundle directory
    """
    import os
    from unittest.mock import patch

    # Create the bundle manager
    bundle_manager = BundleManager(temp_bundle_dir)

    # Create patch for _initialize_with_sbctl to avoid actual initialization
    with patch.object(bundle_manager, "_initialize_with_sbctl", autospec=False) as mock_init:
        # Set up the mock to return a valid kubeconfig path
        async def side_effect(bundle_path, output_dir):
            os.makedirs(output_dir, exist_ok=True)
            kubeconfig_path = output_dir / "kubeconfig"
            with open(kubeconfig_path, "w") as f:
                f.write("{}")
            return kubeconfig_path

        mock_init.side_effect = side_effect

        # Test 1: Absolute path - should be used directly
        metadata = await bundle_manager.initialize_bundle(str(mock_valid_bundle))
        assert metadata.source == str(mock_valid_bundle)
        await bundle_manager._cleanup_active_bundle()

        # Test 2: Relative path - should be resolved within bundle directory
        # Create a subdirectory and move the bundle there
        subdir = temp_bundle_dir / "subdir"
        os.makedirs(subdir, exist_ok=True)
        rel_bundle = subdir / "subdir_bundle.tar.gz"
        import shutil

        shutil.copy(mock_valid_bundle, rel_bundle)

        # Now try to initialize with a relative path from the bundle_dir
        rel_path = "subdir/subdir_bundle.tar.gz"
        metadata = await bundle_manager.initialize_bundle(rel_path)
        assert metadata.source == rel_path
        await bundle_manager._cleanup_active_bundle()

        # Test 3: Just filename - should be found within bundle directory
        metadata = await bundle_manager.initialize_bundle("valid_bundle.tar.gz")
        assert metadata.source == "valid_bundle.tar.gz"
