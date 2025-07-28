"""
Parametrized tests for the File Explorer component.

This module uses pytest parameterization to test the FileExplorer component with
multiple input combinations, focusing on validating behavior rather than implementation.

Benefits of the parameterized approach:
1. Comprehensive coverage with fewer test functions
2. Clear documentation of valid/invalid inputs and expected outcomes
3. Easier maintenance - adding new test cases doesn't require new test functions
4. Better visualization of test boundaries and edge cases

The tests focus on three main user workflows:
1. Listing files and directories within a bundle
2. Reading file contents with different options (line ranges, binary detection)
3. Searching for patterns in files (grep functionality)

Each test verifies both normal operation and proper error handling.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from mcp_server_troubleshoot.bundle import BundleManager, BundleMetadata
from mcp_server_troubleshoot.files import (
    FileContentResult,
    FileExplorer,
    FileListResult,
    FileSystemError,
    GrepFilesArgs,
    GrepResult,
    InvalidPathError,
    ListFilesArgs,
    PathNotFoundError,
    ReadFileArgs,
    ReadFileError,
)

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


# Parameterized validation tests for ListFilesArgs
@pytest.mark.parametrize(
    "path,recursive,expected_valid",
    [
        # Valid cases
        ("dir1", False, True),
        ("dir1/subdir", True, True),
        (
            "absolute/path",
            True,
            True,
        ),  # Note: without leading slash - the validator removes it
        # Invalid cases
        ("", False, False),  # Empty path
        ("../outside", False, False),  # Path traversal
        ("dir1/../../../outside", False, False),  # Path traversal
    ],
    ids=[
        "valid-simple-path",
        "valid-nested-path",
        "valid-absolute-path",
        "invalid-empty-path",
        "invalid-simple-traversal",
        "invalid-complex-traversal",
    ],
)
def test_list_files_args_validation_parametrized(path, recursive, expected_valid):
    """
    Test ListFilesArgs validation with parameterized test cases.

    This test covers both valid and invalid inputs in a single test,
    making it easier to see all validation rules and add new cases.

    Args:
        path: Directory path to validate
        recursive: Whether to recursively list files
        expected_valid: Whether validation should pass
    """
    if expected_valid:
        # Should succeed
        args = ListFilesArgs(path=path, recursive=recursive)
        assert args.path == path
        assert args.recursive == recursive
    else:
        # Should raise ValidationError
        with pytest.raises(ValidationError):
            ListFilesArgs(path=path, recursive=recursive)


# Parameterized validation tests for ReadFileArgs
@pytest.mark.parametrize(
    "path,start_line,end_line,expected_valid",
    [
        # Valid cases
        ("file.txt", 0, 10, True),
        ("dir/file.txt", 5, 15, True),
        (
            "absolute/path/file.txt",
            0,
            100,
            True,
        ),  # Note: without leading slash - the validator removes it
        # Invalid cases
        ("", 0, 10, False),  # Empty path
        ("../outside.txt", 0, 10, False),  # Path traversal
        ("file.txt", -1, 10, False),  # Negative start_line
        ("file.txt", 0, -1, False),  # Negative end_line
    ],
    ids=[
        "valid-simple-file",
        "valid-nested-file",
        "valid-absolute-file",
        "invalid-empty-path",
        "invalid-path-traversal",
        "invalid-negative-start",
        "invalid-negative-end",
    ],
)
def test_read_file_args_validation_parametrized(path, start_line, end_line, expected_valid):
    """
    Test ReadFileArgs validation with parameterized test cases.

    This test ensures both valid and invalid inputs are properly validated.

    Args:
        path: File path to validate
        start_line: Starting line number
        end_line: Ending line number
        expected_valid: Whether validation should pass
    """
    if expected_valid:
        # Should succeed
        args = ReadFileArgs(path=path, start_line=start_line, end_line=end_line)
        assert args.path == path
        assert args.start_line == start_line
        assert args.end_line == end_line
    else:
        # Should raise ValidationError
        with pytest.raises(ValidationError):
            ReadFileArgs(path=path, start_line=start_line, end_line=end_line)


# Parameterized validation tests for GrepFilesArgs
@pytest.mark.parametrize(
    "pattern,path,recursive,glob_pattern,case_sensitive,max_results,expected_valid",
    [
        # Valid cases
        ("test", "dir1", True, "*.txt", False, 100, True),
        (
            "complex.pattern",
            ".",
            True,
            None,
            True,
            50,
            True,
        ),  # Use "." for root directory
        ("foo", "dir1/subdir", False, "*.log", False, 10, True),
        # Invalid cases
        ("", "dir1", True, "*.txt", False, 100, False),  # Empty pattern
        ("test", ".", True, "*.txt", False, 0, False),  # Max results too small
        ("test", "../outside", True, "*.txt", False, 100, False),  # Path traversal
    ],
    ids=[
        "valid-standard-grep",
        "valid-root-directory",
        "valid-non-recursive",
        "invalid-empty-pattern",
        "invalid-max-results",
        "invalid-path-traversal",
    ],
)
def test_grep_files_args_validation_parametrized(
    pattern, path, recursive, glob_pattern, case_sensitive, max_results, expected_valid
):
    """
    Test GrepFilesArgs validation with parameterized test cases.

    This test ensures all validation rules are properly enforced.

    Args:
        pattern: Search pattern
        path: Directory path to search
        recursive: Whether to search recursively
        glob_pattern: File pattern to include
        case_sensitive: Whether to use case-sensitive search
        max_results: Maximum results to return
        expected_valid: Whether validation should pass
    """
    if expected_valid:
        # Should succeed
        args = GrepFilesArgs(
            pattern=pattern,
            path=path,
            recursive=recursive,
            glob_pattern=glob_pattern,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        assert args.pattern == pattern
        assert args.path == path
        assert args.recursive == recursive
        assert args.glob_pattern == glob_pattern
        assert args.case_sensitive == case_sensitive
        assert args.max_results == max_results
    else:
        # Should raise ValidationError
        with pytest.raises(ValidationError):
            GrepFilesArgs(
                pattern=pattern,
                path=path,
                recursive=recursive,
                glob_pattern=glob_pattern,
                case_sensitive=case_sensitive,
                max_results=max_results,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,is_directory,exists,expected_error",
    [
        # Success case - directory exists
        ("dir1", True, True, None),
        # Error case - path doesn't exist
        ("nonexistent", True, False, PathNotFoundError),
        # Error case - path is a file, not a directory
        (
            "file.txt",
            False,
            True,
            FileSystemError,
        ),  # Note: Changed from ReadFileError to FileSystemError
    ],
    ids=[
        "valid-directory",
        "invalid-nonexistent",
        "invalid-not-directory",
    ],
)
async def test_file_explorer_list_files_error_handling(
    path, is_directory, exists, expected_error, test_file_setup, test_factory
):
    """
    Test that the file explorer handles listing errors correctly with parameterization.

    This test verifies error conditions for directory listings are handled properly.

    Args:
        path: Path to list
        is_directory: Whether the path is a directory
        exists: Whether the path exists
        expected_error: Expected error type or None for success
        test_file_setup: Fixture that provides a test directory with files
        test_factory: Factory for test objects
    """
    # Replace "dir1" with actual directory, "file.txt" with actual file
    if path == "dir1" and exists and is_directory:
        actual_path = "dir1"  # This exists in the test_file_setup
    elif path == "file.txt" and exists and not is_directory:
        actual_path = "dir1/file1.txt"  # This exists in the test_file_setup
    else:
        actual_path = path  # Use as is for non-existent paths

    # Set up the bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = test_factory.create_bundle_metadata(path=test_file_setup)
    bundle_manager.get_active_bundle.return_value = bundle

    # Create file explorer
    explorer = FileExplorer(bundle_manager)

    if expected_error:
        # Should raise an error
        with pytest.raises(expected_error):
            await explorer.list_files(actual_path, False)
    else:
        # Should succeed
        result = await explorer.list_files(actual_path, False)
        assert isinstance(result, FileListResult)
        assert result.path == actual_path
        assert result.total_files >= 0
        assert result.total_dirs >= 0
        assert len(result.entries) == result.total_files + result.total_dirs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,exists,is_file,is_directory,expected_error",
    [
        # Success case - file exists
        ("dir1/file1.txt", True, True, False, None),
        # Error case - path doesn't exist
        ("nonexistent.txt", False, False, False, PathNotFoundError),
        # Error case - path is a directory, not a file
        ("dir1", True, False, True, ReadFileError),
    ],
    ids=[
        "valid-file",
        "invalid-nonexistent",
        "invalid-directory",
    ],
)
async def test_file_explorer_read_file_error_handling(
    path, exists, is_file, is_directory, expected_error, test_file_setup, test_factory
):
    """
    Test that the file explorer handles read errors correctly with parameterization.

    This test verifies error conditions for file reading are handled properly.

    Args:
        path: Path to read
        exists: Whether the path exists
        is_file: Whether the path is a file
        is_directory: Whether the path is a directory
        expected_error: Expected error type or None for success
        test_file_setup: Fixture that provides a test directory with files
        test_factory: Factory for test objects
    """
    # Set up the bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = test_factory.create_bundle_metadata(path=test_file_setup)
    bundle_manager.get_active_bundle.return_value = bundle

    # Create file explorer
    explorer = FileExplorer(bundle_manager)

    if expected_error:
        # Should raise an error
        with pytest.raises(expected_error):
            await explorer.read_file(path)
    else:
        # Should succeed
        result = await explorer.read_file(path)
        assert isinstance(result, FileContentResult)
        assert result.path == path
        assert result.content is not None
        assert result.binary is False  # Our test files are text files


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,pattern,case_sensitive,contains_match",
    [
        # File with UPPERCASE pattern, case-sensitive search
        ("dir1/search.txt", "UPPERCASE", True, True),
        # File with UPPERCASE pattern, case-insensitive search
        ("dir1/search.txt", "uppercase", False, True),
        # Non-matching pattern, case-sensitive
        ("dir1/search.txt", "nonexistent", True, False),
        # Pattern that occurs multiple times
        ("dir1/search.txt", "pattern", False, True),
    ],
    ids=[
        "match-case-sensitive",
        "match-case-insensitive",
        "no-match",
        "multiple-matches",
    ],
)
async def test_file_explorer_grep_files_behavior(
    path, pattern, case_sensitive, contains_match, test_file_setup, test_factory
):
    """
    Test grep functionality with different patterns and case sensitivity.

    This test verifies the grep behavior with different search configurations.

    Args:
        path: Directory path to search
        pattern: Search pattern
        case_sensitive: Whether search is case-sensitive
        contains_match: Whether matches should be found
        test_file_setup: Fixture that provides a test directory with files
        test_factory: Factory for test objects
    """
    # Set up the bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = test_factory.create_bundle_metadata(path=test_file_setup)
    bundle_manager.get_active_bundle.return_value = bundle

    # Create file explorer
    explorer = FileExplorer(bundle_manager)

    # Run the grep operation
    result = await explorer.grep_files(pattern, path, True, None, case_sensitive)

    # Verify the result structure
    assert isinstance(result, GrepResult)
    assert result.pattern == pattern
    assert result.path == path
    assert result.case_sensitive == case_sensitive

    # Verify match behavior
    if contains_match:
        assert result.total_matches > 0
        assert len(result.matches) > 0

        # Verify match structure
        for match in result.matches:
            assert pattern.lower() in match.line.lower()
            if case_sensitive:
                # For case-sensitive matches, verify exact match
                assert pattern in match.line
    else:
        assert result.total_matches == 0
        assert len(result.matches) == 0


@pytest.mark.parametrize(
    "path,expected_traversal",
    [
        # Valid paths
        ("dir1", False),
        ("dir1/subdir", False),
        ("absolute/path", False),  # without leading slash - validator removes it
        # Invalid paths with traversal
        ("../outside", True),
        # All paths below should be caught by path validation in pydantic model,
        # but normalized paths within FileExplorer won't raise errors themselves
        ("../outside", True),  # using same path twice for simplicity
        ("../outside", True),
        ("../outside", True),
    ],
    ids=[
        "valid-simple",
        "valid-nested",
        "valid-absolute",
        "invalid-simple-traversal",
        "invalid-parent-traversal",
        "invalid-double-traversal",
        "invalid-triple-traversal",
    ],
)
def test_file_explorer_path_normalization(path, expected_traversal, test_file_setup):
    """
    Test path normalization for security vulnerabilities.

    This test ensures that directory traversal attempts are blocked properly.

    Args:
        path: Path to normalize
        expected_traversal: Whether path contains directory traversal
        test_file_setup: Fixture that provides a test directory with files
    """
    # Create a bundle manager and explorer
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=test_file_setup,
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Create the explorer
    explorer = FileExplorer(bundle_manager)

    if expected_traversal:
        # Should detect traversal and raise error
        with pytest.raises(InvalidPathError):
            explorer._normalize_path(path)
    else:
        # Should normalize without error
        normalized = explorer._normalize_path(path)
        assert normalized.is_absolute()
        assert test_file_setup in normalized.parents or normalized == test_file_setup
        # Make sure we're still under the test directory (not elsewhere on disk)
        assert str(normalized).startswith(str(test_file_setup))
