"""
File Explorer for Kubernetes support bundles.

This module implements the File Explorer component, which is responsible for
listing, reading, and searching files within support bundles.
"""

import fnmatch
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from .bundle import BundleManager

logger = logging.getLogger(__name__)


class FileSystemError(Exception):
    """Base exception for file system errors."""

    pass


class PathNotFoundError(FileSystemError):
    """Exception raised when a path cannot be found."""

    pass


class InvalidPathError(FileSystemError):
    """Exception raised when a path is invalid or outside the bundle."""

    pass


class ReadFileError(FileSystemError):
    """Exception raised when a file cannot be read."""

    pass


class DirectoryAccessError(ReadFileError):
    """Exception raised when attempting to read a directory, with file suggestions."""

    def __init__(self, message: str, suggestions: Optional[List[str]] = None):
        """
        Initialize the DirectoryAccessError.

        Args:
            message: The error message
            suggestions: List of suggested file paths
        """
        super().__init__(message)
        self.suggestions = suggestions or []


class SearchError(FileSystemError):
    """Exception raised when a file search fails."""

    pass


class ListFilesArgs(BaseModel):
    """
    Arguments for listing files and directories.
    """

    path: str = Field(description="The path within the bundle to list")
    recursive: bool = Field(False, description="Whether to list recursively")
    verbosity: Optional[str] = Field(
        None,
        description="Verbosity level for response formatting (minimal|standard|verbose|debug)",
    )

    @field_validator("path")
    def validate_path(cls, v: str) -> str:
        """
        Validate the path.

        Args:
            v: The path string to validate

        Returns:
            The validated path string

        Raises:
            ValueError: If the path is invalid
        """
        if not v:
            raise ValueError("Path cannot be empty")

        # Remove leading/trailing whitespace
        v = v.strip()

        # Normalize path to avoid directory traversal
        path = os.path.normpath(v)

        # Ensure the path doesn't try to escape the bundle
        if path.startswith("..") or "/../" in path:
            raise ValueError("Path cannot contain directory traversal")

        # Remove leading slashes
        while path.startswith("/"):
            path = path[1:]

        return path


class ReadFileArgs(BaseModel):
    """
    Arguments for reading a file.
    """

    path: str = Field(description="The path to the file within the bundle")
    start_line: int = Field(0, description="The line number to start reading from (0-indexed)")
    end_line: Optional[int] = Field(
        None, description="The line number to end reading at (0-indexed, inclusive)"
    )
    verbosity: Optional[str] = Field(
        None,
        description="Verbosity level for response formatting (minimal|standard|verbose|debug)",
    )

    @field_validator("path")
    def validate_path(cls, v: str) -> str:
        """Validate the path."""
        if not v:
            raise ValueError("Path cannot be empty")

        # Remove leading/trailing whitespace
        v = v.strip()

        # Normalize path to avoid directory traversal
        path = os.path.normpath(v)

        # Ensure the path doesn't try to escape the bundle
        if path.startswith("..") or "/../" in path:
            raise ValueError("Path cannot contain directory traversal")

        # Remove leading slashes
        while path.startswith("/"):
            path = path[1:]

        return path

    @field_validator("start_line")
    def validate_start_line(cls, v: int) -> int:
        """Validate the start line."""
        if v < 0:
            raise ValueError("start_line must be non-negative")
        return v

    @field_validator("end_line")
    def validate_end_line(cls, v: Optional[int]) -> Optional[int]:
        """Validate the end line."""
        if v is not None and v < 0:
            raise ValueError("end_line must be non-negative or None")
        return v


class GrepFilesArgs(BaseModel):
    """
    Arguments for searching files for a pattern.
    """

    pattern: str = Field(description="The pattern to search for")
    path: str = Field(description="The path within the bundle to search")
    recursive: bool = Field(True, description="Whether to search recursively")
    glob_pattern: Optional[str] = Field(None, description="The glob pattern to match files against")
    case_sensitive: bool = Field(False, description="Whether the search is case-sensitive")
    max_results: int = Field(1000, description="Maximum number of results to return")
    max_results_per_file: int = Field(5, description="Maximum number of results to return per file")
    max_files: int = Field(10, description="Maximum number of files to search/return")
    verbosity: Optional[str] = Field(
        None,
        description="Verbosity level for response formatting (minimal|standard|verbose|debug)",
    )

    @field_validator("path")
    def validate_path(cls, v: str) -> str:
        """Validate the path."""
        if not v:
            raise ValueError("Path cannot be empty")

        # Remove leading/trailing whitespace
        v = v.strip()

        # Normalize path to avoid directory traversal
        path = os.path.normpath(v)

        # Ensure the path doesn't try to escape the bundle
        if path.startswith("..") or "/../" in path:
            raise ValueError("Path cannot contain directory traversal")

        # Remove leading slashes
        while path.startswith("/"):
            path = path[1:]

        return path

    @field_validator("pattern")
    def validate_pattern(cls, v: str) -> str:
        """Validate the pattern."""
        if not v:
            raise ValueError("Pattern cannot be empty")
        return v

    @field_validator("max_results")
    def validate_max_results(cls, v: int) -> int:
        """Validate max_results."""
        if v <= 0:
            raise ValueError("max_results must be positive")
        return v

    @field_validator("max_results_per_file")
    def validate_max_results_per_file(cls, v: int) -> int:
        """Validate max_results_per_file."""
        if v <= 0:
            raise ValueError("max_results_per_file must be positive")
        return v

    @field_validator("max_files")
    def validate_max_files(cls, v: int) -> int:
        """Validate max_files."""
        if v <= 0:
            raise ValueError("max_files must be positive")
        return v


class FileInfo(BaseModel):
    """
    Information about a file or directory.
    """

    name: str = Field(description="The name of the file or directory")
    path: str = Field(description="The path of the file or directory relative to the bundle root")
    type: str = Field(description="The type of the entry ('file' or 'dir')")
    size: int = Field(description="The size of the file in bytes (0 for directories)")
    access_time: float = Field(description="The time of most recent access (seconds since epoch)")
    modify_time: float = Field(
        description="The time of most recent content modification (seconds since epoch)"
    )
    is_binary: bool = Field(description="Whether the file appears to be binary (for files)")


class FileListResult(BaseModel):
    """
    Result of a file listing operation.
    """

    path: str = Field(description="The path that was listed")
    entries: List[FileInfo] = Field(description="The entries in the directory")
    recursive: bool = Field(description="Whether the listing was recursive")
    total_files: int = Field(description="The total number of files found")
    total_dirs: int = Field(description="The total number of directories found")


class FileContentResult(BaseModel):
    """
    Result of a file read operation.
    """

    path: str = Field(description="The path of the file that was read")
    content: str = Field(description="The content of the file")
    start_line: int = Field(description="The line number that was started from (0-indexed)")
    end_line: int = Field(description="The line number that was ended at (0-indexed)")
    total_lines: int = Field(description="The total number of lines in the file")
    binary: bool = Field(description="Whether the file appears to be binary")


class GrepMatch(BaseModel):
    """
    A match from a grep operation.
    """

    path: str = Field(description="The path of the file containing the match")
    line_number: int = Field(description="The line number of the match (0-indexed)")
    line: str = Field(description="The line containing the match")
    match: str = Field(description="The actual match")
    offset: int = Field(description="The character offset of the match within the line")


class GrepResult(BaseModel):
    """
    Result of a grep operation.
    """

    pattern: str = Field(description="The pattern that was searched for")
    path: str = Field(description="The path that was searched")
    glob_pattern: Optional[str] = Field(description="The glob pattern that was used, if any")
    matches: List[GrepMatch] = Field(description="The matches found")
    total_matches: int = Field(description="The total number of matches found")
    files_searched: int = Field(description="The number of files that were searched")
    case_sensitive: bool = Field(description="Whether the search was case-sensitive")
    truncated: bool = Field(description="Whether the results were truncated due to max_results")
    files_truncated: bool = Field(
        default=False,
        description="Whether the file list was truncated due to max_files",
    )


class FileExplorer:
    """
    Provides file system operations for exploring support bundles.

    This class is responsible for listing directories, reading files, and searching
    for patterns within the bundle files.
    """

    def __init__(self, bundle_manager: BundleManager) -> None:
        """
        Initialize the File Explorer.

        Args:
            bundle_manager: The bundle manager that provides the bundle path
        """
        self.bundle_manager = bundle_manager

    def _get_bundle_path(self) -> Path:
        """
        Get the path to the active bundle.

        Returns:
            The path to the active bundle

        Raises:
            FileSystemError: If no bundle is active
        """
        bundle = self.bundle_manager.get_active_bundle()
        if bundle is None:
            raise FileSystemError(
                "No bundle is active. Please initialize a bundle first using the initialize_bundle tool. "
                "You can use the list_available_bundles tool to see available bundles."
            )

        # Check if we should use the extracted directory for file operations
        extract_dir = bundle.path / "extracted"
        if extract_dir.exists() and extract_dir.is_dir():
            # Check if there are actual files in the extracted directory
            support_bundle_dirs = list(extract_dir.glob("support-bundle-*"))

            # First check for support-bundle-* directories
            if support_bundle_dirs:
                support_bundle_dir = support_bundle_dirs[0]  # Use the first one found
                if support_bundle_dir.exists() and support_bundle_dir.is_dir():
                    logger.debug(f"Using extracted bundle subdirectory: {support_bundle_dir}")
                    return support_bundle_dir

            # If no support-bundle-* directory, check if the extracted directory itself has files
            any_files = False
            for _ in extract_dir.glob("*"):
                any_files = True
                break

            if any_files:
                logger.debug(f"Using extracted bundle directory: {extract_dir}")
                return extract_dir

        return bundle.path

    def _normalize_path(self, path: str) -> Path:
        """
        Normalize a path within the bundle.

        Args:
            path: The path to normalize

        Returns:
            The normalized path

        Raises:
            InvalidPathError: If the path is invalid or outside the bundle
        """
        bundle_path = self._get_bundle_path()

        # Remove leading/trailing whitespace and slashes
        path = path.strip()
        while path.startswith("/"):
            path = path[1:]

        # Normalize path to avoid directory traversal
        normalized_path = os.path.normpath(path)

        # Ensure the path doesn't try to escape the bundle
        if normalized_path.startswith("..") or "/../" in normalized_path:
            raise InvalidPathError("Path cannot contain directory traversal")

        # Combine with bundle path
        full_path = bundle_path / normalized_path

        # Ensure the path is within the bundle
        if not str(full_path).startswith(str(bundle_path)):
            raise InvalidPathError("Path must be within the bundle")

        return full_path

    def _suggest_file_alternatives(self, directory_path: Path) -> List[str]:
        """
        Find files with common extensions that match directory name.

        Args:
            directory_path: The directory path that was attempted to be read

        Returns:
            List of suggested file paths with common extensions
        """
        common_extensions = [".json", ".yaml", ".yml", ".log", ".txt"]
        suggestions: List[str] = []

        if not directory_path.exists():
            return suggestions

        # Get the directory name
        dir_name = directory_path.name

        # Check parent directory for files with matching name + extension
        parent_dir = directory_path.parent
        if parent_dir.exists():
            for ext in common_extensions:
                candidate_file = parent_dir / f"{dir_name}{ext}"
                if candidate_file.exists() and candidate_file.is_file():
                    # Make path relative to bundle root for display
                    try:
                        bundle_path = self._get_bundle_path()
                        rel_path = str(candidate_file.relative_to(bundle_path))
                        suggestions.append(rel_path)
                    except Exception:
                        # Fallback to absolute path if relative fails
                        suggestions.append(str(candidate_file))

        return suggestions

    def _is_binary(self, path: Path) -> bool:
        """
        Check if a file is binary.

        Args:
            path: The path to the file

        Returns:
            True if the file is binary, False otherwise
        """
        # Read the first 8KB of the file
        try:
            with open(path, "rb") as f:
                chunk = f.read(8192)

            # Check for null bytes, which indicate a binary file
            if b"\x00" in chunk:
                return True

            # Check for non-text bytes
            text_chars = bytes(range(32, 127)) + b"\n\r\t\f\b"
            return bool(chunk.translate(None, text_chars))
        except Exception:
            # If we can't read the file, assume it's not binary
            return False

    def _get_file_info(self, path: Path, relative_to: Path) -> FileInfo:
        """
        Get information about a file or directory.

        Args:
            path: The path to the file or directory
            relative_to: The path to make the result path relative to

        Returns:
            Information about the file or directory
        """
        stat_result = path.stat()

        is_dir = path.is_dir()
        is_binary = False if is_dir else self._is_binary(path)

        # Make the path relative to the bundle
        rel_path = str(path.relative_to(relative_to))

        return FileInfo(
            name=path.name,
            path=rel_path,
            type="dir" if is_dir else "file",
            size=0 if is_dir else stat_result.st_size,
            access_time=stat_result.st_atime,
            modify_time=stat_result.st_mtime,
            is_binary=is_binary,
        )

    async def list_files(self, path: str, recursive: bool = False) -> FileListResult:
        """
        List files and directories within the bundle.

        Args:
            path: The path within the bundle to list
            recursive: Whether to list recursively

        Returns:
            The result of the listing operation

        Raises:
            FileSystemError: If there is an error listing the files
            PathNotFoundError: If the path does not exist
            InvalidPathError: If the path is invalid or outside the bundle
        """
        try:
            bundle_path = self._get_bundle_path()
            full_path = self._normalize_path(path)

            if not full_path.exists():
                raise PathNotFoundError(f"Path not found: {path}")

            if not full_path.is_dir():
                raise FileSystemError(f"Path is not a directory: {path}")

            entries = []
            total_files = 0
            total_dirs = 0

            if recursive:
                # Walk the directory recursively
                for root, dirs, files in os.walk(full_path):
                    # Add directories
                    for dir_name in dirs:
                        dir_path = Path(root) / dir_name
                        entries.append(self._get_file_info(dir_path, bundle_path))
                        total_dirs += 1

                    # Add files
                    for file_name in files:
                        file_path = Path(root) / file_name
                        entries.append(self._get_file_info(file_path, bundle_path))
                        total_files += 1
            else:
                # List the directory non-recursively
                for entry in full_path.iterdir():
                    entries.append(self._get_file_info(entry, bundle_path))
                    if entry.is_dir():
                        total_dirs += 1
                    else:
                        total_files += 1

            # Sort entries by name
            entries.sort(key=lambda e: e.name)

            # Create the result
            result = FileListResult(
                path=path,
                entries=entries,
                recursive=recursive,
                total_files=total_files,
                total_dirs=total_dirs,
            )

            return result

        except (PathNotFoundError, InvalidPathError, FileSystemError) as e:
            # Re-raise known errors
            logger.error(f"Error listing files: {str(e)}")
            raise
        except Exception as e:
            # Wrap unknown errors
            logger.exception(f"Unexpected error listing files: {str(e)}")
            raise FileSystemError(f"Failed to list files: {str(e)}")

    async def read_file(
        self, path: str, start_line: int = 0, end_line: Optional[int] = None
    ) -> FileContentResult:
        """
        Read a file within the bundle.

        Args:
            path: The path to the file within the bundle
            start_line: The line number to start reading from (0-indexed)
            end_line: The line number to end reading at (0-indexed, inclusive)

        Returns:
            The result of the read operation

        Raises:
            FileSystemError: If there is an error reading the file
            PathNotFoundError: If the file does not exist
            InvalidPathError: If the path is invalid or outside the bundle
            ReadFileError: If the file cannot be read
        """
        try:
            full_path = self._normalize_path(path)

            if not full_path.exists():
                raise PathNotFoundError(f"Path not found: {path}")

            if not full_path.is_file():
                # Check if it's a directory and offer suggestions
                if full_path.is_dir():
                    suggestions = self._suggest_file_alternatives(full_path)
                    if suggestions:
                        suggestion_text = "\n".join(f"• {suggestion}" for suggestion in suggestions)
                        error_message = f"Path is not a file: {path}\n\nDid you mean one of these files?\n{suggestion_text}"
                        raise DirectoryAccessError(error_message, suggestions)

                # If not a directory or no suggestions, use the original error
                raise ReadFileError(f"Path is not a file: {path}")

            # Check if the file is binary
            is_binary = self._is_binary(full_path)

            # Read the file
            with open(full_path, "rb" if is_binary else "r") as f:
                # For binary files, just read the whole file
                if is_binary:
                    if start_line > 0 or end_line is not None:
                        logger.warning("Line range filtering not supported for binary files")
                    content = f.read()
                    if isinstance(content, bytes):
                        # For binary, return a hex dump
                        content_str = content.hex(" ", 16)
                    else:
                        content_str = str(content)
                    return FileContentResult(
                        path=path,
                        content=content_str,
                        start_line=0,
                        end_line=0,
                        total_lines=1,
                        binary=True,
                    )

                # For text files, handle line ranges
                lines = f.readlines()
                total_lines = len(lines)

                # Validate line ranges
                if start_line >= total_lines:
                    start_line = max(0, total_lines - 1)
                if end_line is None:
                    end_line = total_lines - 1
                elif end_line >= total_lines:
                    end_line = total_lines - 1

                # Ensure start_line <= end_line
                if start_line > end_line:
                    start_line, end_line = end_line, start_line

                # Get the requested lines
                selected_lines = lines[start_line : end_line + 1]

                # Join the lines and create the result
                return FileContentResult(
                    path=path,
                    content="".join(selected_lines),
                    start_line=start_line,
                    end_line=end_line,
                    total_lines=total_lines,
                    binary=is_binary,
                )

        except (
            PathNotFoundError,
            InvalidPathError,
            DirectoryAccessError,
            ReadFileError,
            FileSystemError,
        ) as e:
            # Re-raise known errors
            logger.error(f"Error reading file: {str(e)}")
            raise
        except Exception as e:
            # Wrap unknown errors
            logger.exception(f"Unexpected error reading file: {str(e)}")
            raise FileSystemError(f"Failed to read file: {str(e)}")

    async def grep_files(
        self,
        pattern: str,
        path: str,
        recursive: bool = True,
        glob_pattern: Optional[str] = None,
        case_sensitive: bool = False,
        max_results: int = 1000,
        max_results_per_file: int = 5,
        max_files: int = 10,
    ) -> GrepResult:
        """
        Search for a pattern in files within the bundle.

        Args:
            pattern: The pattern to search for
            path: The path within the bundle to search
            recursive: Whether to search recursively
            glob_pattern: The glob pattern to match files against
            case_sensitive: Whether the search is case-sensitive
            max_results: Maximum number of results to return
            max_results_per_file: Maximum number of results to return per file
            max_files: Maximum number of files to search/return

        Returns:
            The result of the search operation

        Raises:
            FileSystemError: If there is an error searching the files
            PathNotFoundError: If the path does not exist
            InvalidPathError: If the path is invalid or outside the bundle
            SearchError: If the search fails
        """
        try:
            bundle_path = self._get_bundle_path()
            full_path = self._normalize_path(path)

            if not full_path.exists():
                raise PathNotFoundError(f"Path not found: {path}")

            # Compile the pattern
            try:
                if case_sensitive:
                    regex = re.compile(pattern)
                else:
                    regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise SearchError(f"Invalid regular expression: {str(e)}")

            # Initialize results tracking
            matches = []
            total_matches = 0
            truncated = False
            files_searched = 0
            files_truncated = False
            files_to_search = []
            file_paths_with_matching_names = []

            if not full_path.is_dir():
                # If the path is a file, use it directly
                files_to_search = [full_path]

                # Check if the filename matches the pattern
                filename = full_path.name
                if regex.search(filename):
                    file_paths_with_matching_names.append(full_path)
            else:
                # Otherwise, walk the directory to find matching files
                for root, dirs, files in os.walk(full_path):
                    # If not recursive, skip subdirectories
                    if not recursive and root != str(full_path):
                        continue

                    for filename in files:
                        file_path = Path(root) / filename

                        # Check if the filename matches the pattern
                        if regex.search(filename):
                            file_paths_with_matching_names.append(file_path)

                        # Skip binary files for content search
                        if self._is_binary(file_path):
                            continue

                        # If a glob pattern is provided, check if the file matches
                        if glob_pattern and not fnmatch.fnmatch(filename, glob_pattern):
                            continue

                        files_to_search.append(file_path)

            # Limit total files to search (filename matches + content search files)
            all_files = list(set(file_paths_with_matching_names + files_to_search))
            if len(all_files) > max_files:
                all_files = all_files[:max_files]
                files_truncated = True

            # Separate back into filename matches and content search files
            file_paths_with_matching_names = [
                f for f in file_paths_with_matching_names if f in all_files
            ]
            files_to_search = [f for f in files_to_search if f in all_files]

            # Track files processed per file for per-file limiting
            files_processed = set()

            # First add matches from filenames
            for file_path in file_paths_with_matching_names:
                # Skip if we've hit the max results
                if total_matches >= max_results:
                    truncated = True
                    break

                rel_path = str(file_path.relative_to(bundle_path))
                filename = file_path.name
                file_matches = 0
                files_processed.add(file_path)

                # Find all matches of the pattern in the filename
                for match in regex.finditer(filename):
                    if file_matches >= max_results_per_file:
                        break

                    matches.append(
                        GrepMatch(
                            path=rel_path,
                            line_number=0,  # Use 0 for filename matches
                            line=f"File: {filename}",
                            match=match.group(0),
                            offset=match.start(),
                        )
                    )
                    total_matches += 1
                    file_matches += 1

                    # Stop if we've hit the max results
                    if total_matches >= max_results:
                        truncated = True
                        break

            # Now search file contents
            for file_path in files_to_search:
                # Skip if we've hit the max results
                if total_matches >= max_results:
                    truncated = True
                    break

                try:
                    rel_path = str(file_path.relative_to(bundle_path))
                    file_matches = 0

                    # If this file was already processed for filename matches,
                    # we need to account for those in per-file limiting
                    if file_path in files_processed:
                        # Count existing matches for this file
                        for existing_match in matches:
                            if existing_match.path == rel_path:
                                file_matches += 1
                    else:
                        files_processed.add(file_path)

                    with open(file_path, "r") as f:
                        for i, line in enumerate(f):
                            # Skip if we've hit the max results
                            if total_matches >= max_results:
                                truncated = True
                                break

                            # Skip if we've hit the per-file limit
                            if file_matches >= max_results_per_file:
                                break

                            for match in regex.finditer(line):
                                # Skip if we've hit the per-file limit
                                if file_matches >= max_results_per_file:
                                    break

                                matches.append(
                                    GrepMatch(
                                        path=rel_path,
                                        line_number=i,
                                        line=line.rstrip(),
                                        match=match.group(0),
                                        offset=match.start(),
                                    )
                                )

                                total_matches += 1
                                file_matches += 1

                                # Stop if we've hit the max results
                                if total_matches >= max_results:
                                    truncated = True
                                    break
                except (UnicodeDecodeError, IOError):
                    # Skip files that can't be read
                    continue

            files_searched = len(files_processed)

            # Create the result
            result = GrepResult(
                pattern=pattern,
                path=path,
                glob_pattern=glob_pattern,
                matches=matches,
                total_matches=total_matches,
                files_searched=files_searched,
                case_sensitive=case_sensitive,
                truncated=truncated,
            )

            # Add files_truncated info to result for ultra-compact format
            result.files_truncated = files_truncated

            return result

        except (PathNotFoundError, InvalidPathError, SearchError, FileSystemError) as e:
            # Re-raise known errors
            logger.error(f"Error searching files: {str(e)}")
            raise
        except Exception as e:
            # Wrap unknown errors
            logger.exception(f"Unexpected error searching files: {str(e)}")
            raise FileSystemError(f"Failed to search files: {str(e)}")
