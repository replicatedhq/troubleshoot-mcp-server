"""
Tests for the File Explorer.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from mcp_server_troubleshoot.bundle import BundleManager, BundleMetadata
from mcp_server_troubleshoot.files import (
    FileContentResult,
    FileExplorer,
    FileInfo,
    FileListResult,
    GrepFilesArgs,
    GrepResult,
    InvalidPathError,
    ListFilesArgs,
    PathNotFoundError,
    ReadFileArgs,
    ReadFileError,
)
from tests.test_utils import TempBundleManager

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


# We use the test_file_setup fixture from conftest.py instead of this function


def test_file_explorer_initialization():
    """Test that the file explorer can be initialized."""
    bundle_manager = Mock(spec=BundleManager)
    explorer = FileExplorer(bundle_manager)
    assert explorer.bundle_manager == bundle_manager


def test_list_files_args_validation():
    """Test that ListFilesArgs validates paths correctly."""
    # Valid path
    args = ListFilesArgs(path="dir1")
    assert args.path == "dir1"
    assert args.recursive is False  # Default value

    # Empty path
    with pytest.raises(ValidationError):
        ListFilesArgs(path="")

    # Path with directory traversal
    with pytest.raises(ValidationError):
        ListFilesArgs(path="../outside")

    with pytest.raises(ValidationError):
        ListFilesArgs(path="dir1/../../../outside")


def test_read_file_args_validation():
    """Test that ReadFileArgs validates arguments correctly."""
    # Valid path and line range
    args = ReadFileArgs(path="dir1/file1.txt", start_line=0, end_line=10)
    assert args.path == "dir1/file1.txt"
    assert args.start_line == 0
    assert args.end_line == 10

    # Empty path
    with pytest.raises(ValidationError):
        ReadFileArgs(path="")

    # Path with directory traversal
    with pytest.raises(ValidationError):
        ReadFileArgs(path="../outside.txt")

    # Negative start_line
    with pytest.raises(ValidationError):
        ReadFileArgs(path="file.txt", start_line=-1)

    # Negative end_line
    with pytest.raises(ValidationError):
        ReadFileArgs(path="file.txt", end_line=-1)


def test_grep_files_args_validation():
    """Test that GrepFilesArgs validates arguments correctly."""
    # Valid arguments
    args = GrepFilesArgs(
        pattern="test",
        path="dir1",
        recursive=True,
        glob_pattern="*.txt",
        case_sensitive=False,
        max_results=100,
    )
    assert args.pattern == "test"
    assert args.path == "dir1"
    assert args.recursive is True
    assert args.glob_pattern == "*.txt"
    assert args.case_sensitive is False
    assert args.max_results == 100

    # Empty path
    with pytest.raises(ValidationError):
        GrepFilesArgs(pattern="test", path="")

    # Empty pattern
    with pytest.raises(ValidationError):
        GrepFilesArgs(pattern="", path="dir1")

    # Path with directory traversal
    with pytest.raises(ValidationError):
        GrepFilesArgs(pattern="test", path="../outside")

    # Non-positive max_results
    with pytest.raises(ValidationError):
        GrepFilesArgs(pattern="test", path="dir1", max_results=0)

    # Test new parameters with defaults
    args_defaults = GrepFilesArgs(pattern="test", path="dir1")
    assert args_defaults.max_results_per_file == 5  # Default value
    assert args_defaults.max_files == 10  # Default value

    # Test new parameters with custom values
    args_custom = GrepFilesArgs(pattern="test", path="dir1", max_results_per_file=3, max_files=5)
    assert args_custom.max_results_per_file == 3
    assert args_custom.max_files == 5

    # Non-positive max_results_per_file
    with pytest.raises(ValidationError):
        GrepFilesArgs(pattern="test", path="dir1", max_results_per_file=0)

    # Non-positive max_files
    with pytest.raises(ValidationError):
        GrepFilesArgs(pattern="test", path="dir1", max_files=0)


@pytest.mark.asyncio
async def test_file_explorer_list_files():
    """
    Test that the file explorer can list files and directories using real file structures.

    This test verifies the behavior:
    1. Root directory listing returns expected directories and files
    2. Recursive listing finds all nested files
    3. Result objects have the correct structure and attributes
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: List root directory non-recursively
        result = await explorer.list_files(path="", recursive=False)

        # Verify behavior expectations
        assert isinstance(result, FileListResult), "Result should be a FileListResult"
        assert result.path == "", "Path should be preserved in result"
        assert result.recursive is False, "Recursive flag should be preserved"
        # Real bundle structure should have cluster-resources, host-info, logs directories
        assert result.total_dirs >= 2, "Should find at least 2 directories"
        assert result.total_files >= 0, "Should have valid file count"

        # Test 2: List subdirectory recursively
        result = await explorer.list_files(path="cluster-resources", recursive=True)

        # Verify behavior expectations for recursive listing
        assert result.path == "cluster-resources", "Path should match requested directory"
        assert result.recursive is True, "Recursive flag should be preserved"
        assert result.total_files >= 1, "Should find at least 1 file in cluster-resources"

        # Test 3: Verify result structure is correct (behavior contracts)
        for entry in result.entries:
            assert isinstance(entry, FileInfo), "Each entry should be a FileInfo"
            assert hasattr(entry, "name"), "Entry should have a name"
            assert hasattr(entry, "path"), "Entry should have a path"
            assert hasattr(entry, "type"), "Entry should have a type"
            assert hasattr(entry, "size"), "Entry should have a size"
            assert entry.type in ["file", "dir"], "Type should be file or dir"


@pytest.mark.asyncio
async def test_file_explorer_list_files_errors():
    """
    Test that the file explorer handles listing errors correctly using real file operations.

    This test verifies the behavior when errors occur:
    1. Listing non-existent paths raises PathNotFoundError
    2. Trying to list a file raises an error
    3. Using the explorer without a bundle raises an error
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Listing a non-existent path raises an error
        with pytest.raises(PathNotFoundError):
            await explorer.list_files(path="nonexistent_path", recursive=False)

        # Test 2: Listing a file (should raise an error)
        # We know from the real structure that cluster-resources/pods/kube-system.json exists
        with pytest.raises(Exception):
            await explorer.list_files(
                path="cluster-resources/pods/kube-system.json", recursive=False
            )

        # Test 3: Without an active bundle should raise an error
        mock_bundle_manager.get_active_bundle.return_value = None
        with pytest.raises(Exception):
            await explorer.list_files(path="", recursive=False)


@pytest.mark.asyncio
async def test_file_explorer_read_file():
    """
    Test that the file explorer can read files correctly using real files.

    This test verifies the behavior:
    1. Text files can be read with correct content
    2. Line ranges can be selected for reading
    3. Binary files are detected properly
    """
    # Create a real bundle structure with binary files for testing
    with TempBundleManager(bundle_type="with_binaries") as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Reading a text file (use real JSON file from bundle structure)
        result = await explorer.read_file(path="cluster-resources/pods/kube-system.json")

        # Verify behavior expectations
        assert isinstance(result, FileContentResult), "Result should be a FileContentResult"
        assert result.path == "cluster-resources/pods/kube-system.json", (
            "Path should be preserved in result"
        )
        assert "test-pod" in result.content, "Content should match expected JSON content"
        assert result.binary is False, "JSON file should not be marked as binary"
        assert result.total_lines > 0, "Line count should be available"

        # Test 2: Reading a line range from the same file
        result = await explorer.read_file(
            path="cluster-resources/pods/kube-system.json", start_line=1, end_line=3
        )

        # Verify behavior expectations for line ranges
        assert result.start_line == 1, "Start line should match requested value"
        assert result.end_line >= 1, "End line should be at least start line"
        assert len(result.content.split("\n")) <= 4, "Should have limited lines based on range"

        # Test 3: Reading binary file (from the with_binaries structure)
        result = await explorer.read_file(path="binaries/fake_binary")

        # Verify behavior expectations for binary files
        assert result.path == "binaries/fake_binary", "Path should be preserved in result"
        assert result.binary is True, "Binary file should be marked as binary"


@pytest.mark.asyncio
async def test_file_explorer_read_file_errors():
    """
    Test that the file explorer handles read errors correctly using real file operations.

    This test verifies the behavior:
    1. Reading non-existent files raises appropriate errors
    2. Reading directories raises appropriate errors
    3. Using file explorer without a bundle raises an error
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Reading a non-existent file raises PathNotFoundError
        with pytest.raises(PathNotFoundError):
            await explorer.read_file(path="nonexistent.txt")

        # Test 2: Reading a directory raises ReadFileError
        with pytest.raises(ReadFileError):
            await explorer.read_file(path="cluster-resources")

        # Test 3: Without an active bundle should raise an error
        mock_bundle_manager.get_active_bundle.return_value = None
        with pytest.raises(Exception):
            await explorer.read_file(path="cluster-resources/pods/kube-system.json")


@pytest.mark.asyncio
async def test_file_explorer_grep_files():
    """
    Test that the file explorer can search files with different patterns using real files.

    This test verifies the behavior:
    1. Global search finds matches across all files
    2. Path-restricted search only looks in specific directories
    3. Glob patterns filter which files are searched
    4. Case sensitivity works as expected
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

        # Add some additional test files with specific patterns for grep testing
        test_dir = bundle_path / "test_files"
        test_dir.mkdir(exist_ok=True)

        # Create files with specific content for pattern matching
        (test_dir / "case_test.txt").write_text("This contains UPPERCASE and lowercase text\n")
        (test_dir / "pattern_test.txt").write_text(
            "This is a test file with patterns\nAnother line with test word\n"
        )

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Global search for common pattern
        result = await explorer.grep_files(pattern="test", path="", recursive=True)

        # Verify behavior expectations
        assert isinstance(result, GrepResult), "Result should be a GrepResult"
        assert result.pattern == "test", "Pattern should be preserved in result"
        assert result.path == "", "Path should be preserved in result"
        assert result.total_matches >= 1, "Should find matches in test files"
        assert result.files_searched > 0, "Should report number of files searched"
        assert not result.truncated, "Result should not be truncated"

        # Verify match objects structure (behavior contract)
        if result.matches:
            match = result.matches[0]
            assert "test" in match.line.lower(), "Line should contain the pattern"
            assert hasattr(match, "line_number"), "Match should have line number"
            assert hasattr(match, "offset"), "Match should have offset"
            assert hasattr(match, "path"), "Match should have path"

        # Test 2: Directory-restricted search with glob pattern
        result = await explorer.grep_files(
            pattern="test", path="test_files", recursive=True, glob_pattern="*.txt"
        )

        # Verify behavior expectations
        assert result.pattern == "test", "Pattern should be preserved"
        assert result.path == "test_files", "Path should be preserved"
        assert result.glob_pattern == "*.txt", "Glob pattern should be preserved"
        assert result.total_matches >= 1, "Should find matches in test_files directory"

        # Test 3: Case sensitivity behavior
        # First test with case sensitive search
        case_sensitive = await explorer.grep_files(
            pattern="UPPERCASE", path="", recursive=True, glob_pattern=None, case_sensitive=True
        )

        # Now test with case insensitive search
        case_insensitive = await explorer.grep_files(
            pattern="uppercase", path="", recursive=True, glob_pattern=None, case_sensitive=False
        )

        # Verify behavior expectations for case sensitivity
        assert case_sensitive.total_matches >= 1, "Should find exact case matches"
        assert case_insensitive.total_matches >= 1, "Should find case-insensitive matches"
        assert case_insensitive.case_sensitive is False, "Should preserve case sensitivity flag"


@pytest.mark.asyncio
async def test_file_explorer_grep_files_with_kubeconfig():
    """
    Test searching for specific patterns across multiple files using real file operations.

    This test verifies the behavior:
    1. Grep can find patterns in both file content and filenames
    2. Multiple matches in the same file are found correctly
    3. Results contain the expected number of matches
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

        # Create additional test files with specific patterns
        kubeconfig_path = bundle_path / "kubeconfig"
        kubeconfig_path.write_text(
            "apiVersion: v1\nkind: Config\nclusters:\n- name: test-cluster\n"
        )

        # Create a file with repeating specific patterns
        ref_file = bundle_path / "reference.txt"
        ref_file.write_text(
            "This file refers to a specific pattern.\n"
            "It contains the word multiple times.\n"
            "specific is important.\n"
            "Very specific indeed.\n"
        )

        # Create a mock bundle manager with real paths
        mock_bundle_manager = Mock(spec=BundleManager)
        bundle = BundleMetadata(
            id="test",
            source="test",
            path=bundle_path,
            kubeconfig_path=kubeconfig_path,
            initialized=True,
        )
        mock_bundle_manager.get_active_bundle.return_value = bundle

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test searching for "specific" pattern
        result = await explorer.grep_files(
            pattern="specific", path="", recursive=True, glob_pattern=None, case_sensitive=False
        )

        # Verify behavior expectations
        assert isinstance(result, GrepResult), "Result should be a GrepResult"
        assert result.pattern == "specific", "Pattern should be preserved"
        assert result.path == "", "Root path should be preserved"

        # Should find multiple matches in the reference file
        assert result.total_matches >= 3, "Should find multiple pattern instances"
        assert result.files_searched > 0, "Should report number of files searched"

        # There should be matches in our reference file
        ref_file_matches = [m for m in result.matches if "reference.txt" in m.path]
        assert len(ref_file_matches) >= 3, "Should find multiple matches in reference.txt"


@pytest.mark.asyncio
async def test_file_explorer_grep_files_errors():
    """
    Test that the file explorer handles search errors correctly using real file operations.

    This test verifies the behavior:
    1. Searching non-existent paths raises appropriate errors
    2. Using the explorer without a bundle raises an error
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Searching a non-existent path raises an error
        with pytest.raises(PathNotFoundError):
            await explorer.grep_files(pattern="test", path="nonexistent_path", recursive=True)

        # Test 2: Without an active bundle should raise an error
        mock_bundle_manager.get_active_bundle.return_value = None
        with pytest.raises(Exception):
            await explorer.grep_files(pattern="test", path="", recursive=True)


def test_file_explorer_is_binary():
    """
    Test that the file explorer can detect binary files correctly using real files.

    This test verifies the behavior of the binary file detection:
    1. Text files are correctly identified as non-binary
    2. Binary files are correctly identified as binary
    """
    # Create a real bundle structure with binary files for testing
    with TempBundleManager(bundle_type="with_binaries") as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()
        bundle_structure = bundle_manager.get_structure()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Text file should not be marked as binary (JSON file from real structure)
        text_file_path = bundle_structure["kube_system_pods"]
        assert not explorer._is_binary(text_file_path), "JSON file should not be detected as binary"

        # Test 2: Binary file should be marked as binary (real binary file)
        binary_file_path = bundle_structure["fake_binary"]
        assert explorer._is_binary(binary_file_path), "Binary file should be detected as binary"


def test_file_explorer_normalize_path():
    """
    Test that the file explorer normalizes paths correctly and securely using real paths.

    This test verifies the behavior of path normalization:
    1. Relative paths are resolved correctly to absolute paths
    2. Paths with leading slashes are handled properly
    3. Nested paths are resolved correctly
    4. Directory traversal attempts are blocked for security
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Test 1: Normalizing a relative path
        normalized = explorer._normalize_path("cluster-resources")
        assert normalized == bundle_path / "cluster-resources", (
            "Relative path should be resolved to absolute path"
        )

        # Test 2: Normalizing a path with leading slashes
        normalized = explorer._normalize_path("/cluster-resources")
        assert normalized == bundle_path / "cluster-resources", (
            "Leading slashes should be handled properly"
        )

        # Test 3: Normalizing a nested path
        normalized = explorer._normalize_path("cluster-resources/pods")
        assert normalized == bundle_path / "cluster-resources" / "pods", (
            "Nested paths should be resolved correctly"
        )

        # Test 4: Security check - block directory traversal attempts
        with pytest.raises(InvalidPathError):
            explorer._normalize_path("../outside")

        with pytest.raises(InvalidPathError):
            explorer._normalize_path("cluster-resources/../../../outside")


@pytest.mark.asyncio
async def test_file_explorer_grep_files_per_file_limiting():
    """
    Test that grep_files respects per-file result limiting using real files.

    This test verifies that when a file has many matches,
    only max_results_per_file matches are returned for that file.
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

        # Create a file with many lines containing the pattern
        test_file = bundle_path / "many_matches.txt"
        test_content = "\n".join([f"line {i} with pattern match" for i in range(20)])
        test_file.write_text(test_content)

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Search with max_results_per_file=3
        result = await explorer.grep_files(
            pattern="pattern",
            path="many_matches.txt",
            max_results_per_file=3,
            max_files=10,
        )

        # Should only get 3 matches from the file (per-file limit)
        assert result.total_matches == 3
        assert len(result.matches) == 3

        # All matches should be from the same file
        for match in result.matches:
            assert match.path == "many_matches.txt"


@pytest.mark.asyncio
async def test_file_explorer_grep_files_max_files_limiting():
    """
    Test that grep_files respects max_files limiting using real files.

    This test verifies that when there are many files to search,
    only max_files are actually searched.
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

        # Create multiple files with matches
        for i in range(15):
            test_file = bundle_path / f"file{i}.txt"
            test_file.write_text(f"This is file {i} with a test pattern")

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Search with max_files=5
        result = await explorer.grep_files(
            pattern="test",
            path="",  # Search all files
            recursive=False,
            max_files=5,
            max_results_per_file=10,
        )

        # Should have searched only 5 files maximum
        assert result.files_searched <= 5
        # Should have files_truncated flag set
        assert hasattr(result, "files_truncated")
        assert result.files_truncated is True


@pytest.mark.asyncio
async def test_file_explorer_grep_files_combined_limiting():
    """
    Test grep_files with both per-file and max_files limiting using real files.

    This test verifies the interaction between both limiting mechanisms.
    """
    # Create a real bundle structure for testing
    with TempBundleManager() as bundle_manager:
        bundle_path = bundle_manager.get_bundle_path()

        # Create multiple files, each with multiple matches
        for i in range(8):
            test_file = bundle_path / f"multi{i}.txt"
            content = "\n".join([f"line {j} test match" for j in range(10)])
            test_file.write_text(content)

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

        # Create the explorer with real file operations
        explorer = FileExplorer(mock_bundle_manager)

        # Search with both limits: max_files=3, max_results_per_file=2
        result = await explorer.grep_files(
            pattern="test",
            path="",  # Search all files
            recursive=False,
            max_files=3,
            max_results_per_file=2,
        )

        # Should have searched only 3 files maximum
        assert result.files_searched <= 3
        # Should have at most 6 total matches (3 files × 2 matches per file)
        assert result.total_matches <= 6
        # Should have files_truncated flag set since we have 8 files but limit is 3
        assert hasattr(result, "files_truncated")
        assert result.files_truncated is True
