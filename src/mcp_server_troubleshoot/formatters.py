"""
Response formatters for different verbosity levels.

This module implements the ResponseFormatter system that provides configurable
verbosity levels to optimize token usage while maintaining full functionality
for debugging purposes.
"""

import json
import os
from enum import Enum
from typing import Any, Dict, List, Optional

from .bundle import BundleFileInfo, BundleMetadata
from .files import FileListResult, FileContentResult, GrepResult, GrepMatch
from .kubectl import KubectlResult


class VerbosityLevel(str, Enum):
    """Verbosity levels for response formatting."""

    MINIMAL = "minimal"
    STANDARD = "standard"
    VERBOSE = "verbose"
    DEBUG = "debug"


class ResponseFormatter:
    """
    Formats MCP tool responses based on verbosity level.

    The formatter implements four verbosity levels:
    - MINIMAL: Essential data only, no metadata, compact JSON
    - STANDARD: Core data with basic metadata, simplified structures
    - VERBOSE: Current behavior maintained, full metadata included
    - DEBUG: All verbose content plus system diagnostics and performance metrics
    """

    def __init__(self, verbosity: Optional[str] = None):
        """
        Initialize the formatter with the specified verbosity level.

        Args:
            verbosity: The verbosity level (minimal|standard|verbose|debug).
                      If None, uses environment variable MCP_VERBOSITY or defaults to minimal.
        """
        if verbosity is None:
            # Check environment variables
            verbosity = os.environ.get("MCP_VERBOSITY", "minimal")
            if os.environ.get("MCP_DEBUG", "").lower() in ("true", "1", "yes"):
                verbosity = "debug"

        try:
            self.verbosity = VerbosityLevel(verbosity.lower())
        except ValueError:
            self.verbosity = VerbosityLevel.MINIMAL

    def format_bundle_initialization(
        self,
        metadata: BundleMetadata,
        api_server_available: bool,
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Format bundle initialization response."""

        if self.verbosity == VerbosityLevel.MINIMAL:
            if api_server_available:
                return json.dumps({"bundle_id": metadata.id, "status": "ready"})
            else:
                return json.dumps({"bundle_id": metadata.id, "status": "api_unavailable"})

        elif self.verbosity == VerbosityLevel.STANDARD:
            result = {
                "bundle_id": metadata.id,
                "source": metadata.source,
                "status": "ready" if api_server_available else "api_unavailable",
                "initialized": metadata.initialized,
            }
            return json.dumps(result)

        else:  # VERBOSE or DEBUG
            # Convert metadata to dict
            metadata_dict = json.loads(metadata.model_dump_json())
            metadata_dict["path"] = str(metadata_dict["path"])
            metadata_dict["kubeconfig_path"] = str(metadata_dict["kubeconfig_path"])

            if api_server_available:
                response = f"Bundle initialized successfully:\n```json\n{json.dumps(metadata_dict, separators=(',', ':'))}\n```"
            else:
                response = (
                    f"Bundle initialized but API server is NOT available. kubectl commands may fail:\n"
                    f"```json\n{json.dumps(metadata_dict, separators=(',', ':'))}\n```"
                )

                if diagnostics and self.verbosity == VerbosityLevel.DEBUG:
                    response += f"\n\nDiagnostic information:\n```json\n{json.dumps(diagnostics, separators=(',', ':'))}\n```"

            return response

    def format_bundle_list(self, bundles: List[BundleFileInfo]) -> str:
        """Format bundle list response."""

        if not bundles:
            if self.verbosity == VerbosityLevel.MINIMAL:
                return json.dumps([])
            else:
                return "No support bundles found. You may need to download or transfer a bundle to the bundle storage directory."

        if self.verbosity == VerbosityLevel.MINIMAL:
            return json.dumps([bundle.name for bundle in bundles if bundle.valid])

        elif self.verbosity == VerbosityLevel.STANDARD:
            bundle_list = []
            for bundle in bundles:
                if bundle.valid:  # Only include valid bundles in standard mode
                    bundle_list.append(
                        {
                            "name": bundle.name,
                            "source": bundle.relative_path,
                            "size_bytes": bundle.size_bytes,
                        }
                    )
            return json.dumps({"bundles": bundle_list, "count": len(bundle_list)})

        else:  # VERBOSE or DEBUG
            # Full format with usage instructions (current behavior)
            bundle_list = []
            for bundle in bundles:
                # Format human-readable size
                size_str = self._format_file_size(bundle.size_bytes)

                # Format modification time
                import datetime

                modified_time_str = datetime.datetime.fromtimestamp(bundle.modified_time).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )

                bundle_entry = {
                    "name": bundle.name,
                    "source": bundle.relative_path,
                    "full_path": bundle.path,
                    "size_bytes": bundle.size_bytes,
                    "size": size_str,
                    "modified_time": bundle.modified_time,
                    "modified": modified_time_str,
                    "valid": bundle.valid,
                }

                if not bundle.valid and bundle.validation_message:
                    bundle_entry["validation_message"] = bundle.validation_message

                bundle_list.append(bundle_entry)

            response_obj = {"bundles": bundle_list, "total": len(bundle_list)}
            response = f"```json\n{json.dumps(response_obj, separators=(',', ':'))}\n```\n\n"

            # Add usage instructions
            example_bundle = next((b for b in bundles if b.valid), bundles[0] if bundles else None)
            if example_bundle:
                response += "## Usage Instructions\n\n"
                response += "To use one of these bundles, initialize it with the `initialize_bundle` tool using the `source` value:\n\n"
                response += (
                    '```json\n{\n  "source": "' + example_bundle.relative_path + '"\n}\n```\n\n'
                )
                response += "After initializing a bundle, you can explore its contents using the file exploration tools (`list_files`, `read_file`, `grep_files`) and run kubectl commands with the `kubectl` tool."

            return response

    def format_file_list(self, result: FileListResult) -> str:
        """Format file list response."""

        if self.verbosity == VerbosityLevel.MINIMAL:
            return json.dumps(
                [entry.name + ("/" if entry.type == "dir" else "") for entry in result.entries]
            )

        elif self.verbosity == VerbosityLevel.STANDARD:
            files = []
            for entry in result.entries:
                files.append(
                    {
                        "name": entry.name,
                        "type": entry.type,
                        "size": entry.size if entry.type == "file" else None,
                    }
                )
            return json.dumps({"files": files, "count": len(files)})

        else:  # VERBOSE or DEBUG
            # Current full format
            response = (
                f"Listed files in {result.path} "
                + ("recursively" if result.recursive else "non-recursively")
                + ":\n"
            )

            entries_data = [entry.model_dump() for entry in result.entries]
            entries_json = json.dumps(entries_data, separators=(",", ":"))
            response += f"```json\n{entries_json}\n```\n"

            metadata = {
                "path": result.path,
                "recursive": result.recursive,
                "total_files": result.total_files,
                "total_dirs": result.total_dirs,
            }
            metadata_str = json.dumps(metadata, separators=(",", ":"))
            response += f"Directory metadata:\n```json\n{metadata_str}\n```"

            return response

    def format_file_content(self, result: FileContentResult) -> str:
        """Format file content response."""

        if self.verbosity == VerbosityLevel.MINIMAL:
            return result.content

        elif self.verbosity == VerbosityLevel.STANDARD:
            if result.binary:
                return f"Binary file ({result.total_lines} lines)\n{result.content}"
            else:
                return f"File content ({result.total_lines} lines):\n{result.content}"

        else:  # VERBOSE or DEBUG
            # Current full format with line numbers
            file_type = "binary" if result.binary else "text"

            if not result.binary:
                lines = result.content.splitlines()
                content_with_numbers = ""
                for i, line in enumerate(lines):
                    line_number = result.start_line + i
                    content_with_numbers += f"{line_number + 1:4d} | {line}\n"

                response = f"Read {file_type} file {result.path} (lines {result.start_line + 1}-{result.end_line + 1} of {result.total_lines}):\n"
                response += f"```\n{content_with_numbers}```"
            else:
                response = f"Read {file_type} file {result.path} (binary data shown as hex):\n"
                response += f"```\n{result.content}\n```"

            return response

    def format_grep_results(self, result: GrepResult) -> str:
        """Format grep search results."""

        if self.verbosity == VerbosityLevel.MINIMAL:
            # Ultra-compact format with no whitespace
            matches = []
            files_with_matches: Dict[str, List[GrepMatch]] = {}

            # Group matches by file and track per-file counts
            for match in result.matches:
                if match.path not in files_with_matches:
                    files_with_matches[match.path] = []
                files_with_matches[match.path].append(match)

            # Build compact match objects with full line content
            for file_path, file_matches in files_with_matches.items():
                for i, match in enumerate(file_matches):
                    match_obj = {
                        "file": match.path,
                        "line": match.line_number + 1,  # 1-indexed for display
                        "content": match.line,  # Full line content instead of just match
                    }
                    # Add truncated indicator if this is the last match for this file
                    # and we might have hit the per-file limit
                    if (
                        i == len(file_matches) - 1 and len(file_matches) >= 5
                    ):  # Default max_results_per_file
                        match_obj["truncated"] = True
                    matches.append(match_obj)

            # Create final result with truncation indicators
            compact_result: Dict[str, Any] = {"matches": matches}
            if hasattr(result, "files_truncated") and result.files_truncated:
                compact_result["files_truncated"] = True

            return json.dumps(compact_result, separators=(",", ":"))

        elif self.verbosity == VerbosityLevel.STANDARD:
            matches = []
            for match in result.matches:
                matches.append(
                    {
                        "file": match.path,
                        "line": match.line_number + 1,
                        "content": match.line,
                        "match": match.match,
                    }
                )
            return json.dumps(
                {
                    "matches": matches,
                    "total": result.total_matches,
                    "files_searched": result.files_searched,
                }
            )

        else:  # VERBOSE or DEBUG
            # Current full format
            pattern_type = "case-sensitive" if result.case_sensitive else "case-insensitive"
            path_desc = result.path + (
                f" (matching {result.glob_pattern})" if result.glob_pattern else ""
            )

            response = f"Found {result.total_matches} matches for {pattern_type} pattern '{result.pattern}' in {path_desc}:\n\n"

            if result.matches:
                # Group matches by file
                matches_by_file: Dict[str, List[GrepMatch]] = {}
                for match in result.matches:
                    if match.path not in matches_by_file:
                        matches_by_file[match.path] = []
                    matches_by_file[match.path].append(match)

                for file_path, file_matches in matches_by_file.items():
                    response += f"**File: {file_path}**\n```\n"
                    for match in file_matches:
                        response += f"{match.line_number + 1:4d} | {match.line}\n"
                    response += "```\n\n"

                if result.truncated:
                    response += "_Note: Results truncated to maximum matches._\n\n"
            else:
                response += "No matches found.\n\n"

            # Add metadata
            metadata = {
                "pattern": result.pattern,
                "path": result.path,
                "glob_pattern": result.glob_pattern,
                "total_matches": result.total_matches,
                "files_searched": result.files_searched,
                "case_sensitive": result.case_sensitive,
                "truncated": result.truncated,
            }
            metadata_str = json.dumps(metadata, separators=(",", ":"))
            response += f"Search metadata:\n```json\n{metadata_str}\n```"

            return response

    def format_kubectl_result(self, result: KubectlResult) -> str:
        """Format kubectl command result."""

        if self.verbosity == VerbosityLevel.MINIMAL:
            if result.is_json:
                return json.dumps(result.output)
            else:
                return result.stdout

        elif self.verbosity == VerbosityLevel.STANDARD:
            if result.is_json:
                return json.dumps({"output": result.output, "exit_code": result.exit_code})
            else:
                return json.dumps({"output": result.stdout, "exit_code": result.exit_code})

        else:  # VERBOSE or DEBUG
            # Current full format
            if result.is_json:
                output_str = json.dumps(result.output)
                response = f"kubectl command executed successfully:\n```json\n{output_str}\n```"
            else:
                output_str = result.stdout
                response = f"kubectl command executed successfully:\n```\n{output_str}\n```"

            metadata = {
                "command": result.command,
                "exit_code": result.exit_code,
                "duration_ms": result.duration_ms,
            }

            if self.verbosity == VerbosityLevel.DEBUG and result.stderr:
                metadata["stderr"] = result.stderr

            metadata_str = json.dumps(metadata, separators=(",", ":"))
            response += f"\nCommand metadata:\n```json\n{metadata_str}\n```"

            return response

    def format_error(self, error_message: str, diagnostics: Optional[Dict[str, Any]] = None) -> str:
        """Format error messages based on verbosity level."""

        if self.verbosity == VerbosityLevel.MINIMAL:
            return error_message.split("\n")[0]  # First line only

        elif self.verbosity == VerbosityLevel.STANDARD:
            lines = error_message.split("\n")
            return "\n".join(lines[:3])  # First 3 lines

        else:  # VERBOSE or DEBUG
            response = error_message
            if diagnostics and self.verbosity == VerbosityLevel.DEBUG:
                response += f"\n\nDiagnostic information:\n```json\n{json.dumps(diagnostics, separators=(',', ':'))}\n```"
            return response

    def format_overflow_message(self, tool_name: str, estimated_tokens: int, content: str) -> str:
        """
        Format helpful overflow message with tool-specific guidance.

        Args:
            tool_name: Name of the MCP tool that generated the content
            estimated_tokens: Estimated token count of the content
            content: The original content that was too large

        Returns:
            Formatted overflow message with tool-specific suggestions and content preview
        """
        # Default token limit (can be made configurable)
        token_limit = 25000

        # Base overflow message
        message = f"Content too large ({estimated_tokens:,} tokens, limit: {token_limit:,}).\n\n"

        # Tool-specific suggestions
        suggestions = self._get_tool_specific_suggestions(tool_name)
        if suggestions:
            message += "Suggestions to reduce output size:\n"
            for i, suggestion in enumerate(suggestions, 1):
                message += f"{i}. {suggestion}\n"
            message += "\n"

        # Add content preview based on verbosity level
        preview = self._generate_content_preview(content)
        if preview:
            message += preview

        return message

    def _get_tool_specific_suggestions(self, tool_name: str) -> List[str]:
        """Get tool-specific suggestions for reducing output size."""
        suggestions_map = {
            "list_files": [
                "Use non-recursive listing: list_files(recursive=false)",
                'Target specific directories: list_files(path="specific/subdirectory")',
                'Use minimal verbosity: list_files(verbosity="minimal")',
            ],
            "read_file": [
                "Read specific line ranges: read_file(start_line=1, end_line=100)",
                "Read smaller chunks: read_file(start_line=1, end_line=50)",
                "Target specific sections of the file",
            ],
            "grep_files": [
                "Limit results per file: grep_files(max_results=50, max_files=5)",
                'Use specific file patterns: grep_files(glob_pattern="*.yaml")',
                'Target specific directories: grep_files(path="specific/subdirectory")',
                'Use minimal verbosity: grep_files(verbosity="minimal")',
            ],
            "kubectl": [
                "Query specific resources: kubectl get pods --selector=app=myapp",
                "Use different output formats: kubectl get pods -o name",
                "Target specific namespaces: kubectl get pods -n specific-namespace",
                "Limit results: kubectl get pods --limit=50",
            ],
            "initialize_bundle": ['Use minimal verbosity: initialize_bundle(verbosity="minimal")'],
            "list_bundles": ['Use minimal verbosity: list_bundles(verbosity="minimal")'],
        }

        return suggestions_map.get(
            tool_name,
            [
                'Use minimal verbosity if supported: tool_name(verbosity="minimal")',
                "Apply filtering parameters to reduce data scope",
                "Target more specific data subsets",
            ],
        )

    def _generate_content_preview(self, content: str) -> str:
        """Generate a content preview based on verbosity level."""
        if self.verbosity == VerbosityLevel.MINIMAL:
            # Very brief preview for minimal mode
            preview_chars = 100
            preview_text = content[:preview_chars]
            if len(content) > preview_chars:
                preview_text += "..."
            return f"First {preview_chars} characters:\n{preview_text}\n"

        elif self.verbosity == VerbosityLevel.STANDARD:
            # Moderate preview for standard mode
            preview_chars = 200
            preview_text = content[:preview_chars]
            if len(content) > preview_chars:
                preview_text += "..."
            return f"Showing first {preview_chars} characters:\n{preview_text}\n"

        else:  # VERBOSE or DEBUG
            # More detailed preview with structure info
            preview_chars = 500
            preview_text = content[:preview_chars]
            if len(content) > preview_chars:
                preview_text += "..."

            # Try to provide structural information
            lines = content.split("\n")
            total_lines = len(lines)

            preview_msg = (
                f"Showing first {preview_chars} characters (content has {total_lines:,} lines):\n"
            )
            preview_msg += "```\n" + preview_text + "\n```\n"

            # Add helpful context about the content structure
            if content.strip().startswith("{") or content.strip().startswith("["):
                preview_msg += "\n*Note: Content appears to be JSON format*\n"
            elif "```" in content:
                preview_msg += "\n*Note: Content contains code blocks or formatted sections*\n"

            return preview_msg

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def get_formatter(verbosity: Optional[str] = None) -> ResponseFormatter:
    """
    Get a ResponseFormatter instance with the specified verbosity level.

    Args:
        verbosity: The verbosity level, or None to use environment defaults

    Returns:
        A configured ResponseFormatter instance
    """
    return ResponseFormatter(verbosity)
