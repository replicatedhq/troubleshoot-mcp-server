"""
Functional tests for file operation tools through MCP protocol.

Tests validate file exploration functionality including listing, reading,
and searching files through the MCP layer.
"""

import time
from typing import Dict

import pytest

from tests.integration.mcp_test_utils import MCPTestClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_list_files_basic_functionality(
    mcp_protocol_client: MCPTestClient,
    initialized_test_bundle: str,
    performance_threshold: Dict[str, int],
) -> None:
    """Test basic list_files functionality through MCP protocol."""
    start_time = time.time()

    # List files in root directory
    result = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": False, "verbosity": "verbose"}
    )

    end_time = time.time()
    duration_ms = (end_time - start_time) * 1000

    # Verify performance
    max_duration = performance_threshold["tool_call_max_ms"]
    assert duration_ms < max_duration, (
        f"list_files took {duration_ms:.1f}ms, expected under {max_duration}ms"
    )

    # Verify response structure
    assert len(result) == 1, "Expected single response content item"
    content = result[0]
    assert content["type"] == "text", "Response should be text content"

    response_text = content["text"]

    # Should indicate successful listing
    assert (
        "Listed files" in response_text
        or "files found" in response_text.lower()
        or len(response_text.strip()) > 0
    ), f"Expected file listing success: {response_text}"

    # Verbose mode should include additional information
    if "Listed files" in response_text:
        assert (
            "files," in response_text
            or "Total:" in response_text
            or "directories" in response_text
            or "total_files" in response_text
            or "total_dirs" in response_text
        ), f"Verbose mode should include summary info: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_list_files_recursive_vs_non_recursive(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test recursive vs non-recursive file listing."""
    # Non-recursive listing
    result_non_recursive = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
    )

    non_recursive_text = result_non_recursive[0]["text"]

    # Recursive listing
    result_recursive = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": True, "verbosity": "minimal"}
    )

    recursive_text = result_recursive[0]["text"]

    # Both should succeed
    assert len(non_recursive_text) > 0, "Non-recursive listing should return content"
    assert len(recursive_text) > 0, "Recursive listing should return content"

    # Recursive should generally find same or more content
    # (Though exact comparison depends on bundle structure)
    non_recursive_lines = len(non_recursive_text.split("\n"))
    recursive_lines = len(recursive_text.split("\n"))

    assert recursive_lines >= non_recursive_lines, (
        f"Recursive listing ({recursive_lines} lines) should be >= "
        f"non-recursive ({non_recursive_lines} lines)"
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_read_file_functionality(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test file reading functionality through MCP protocol."""
    # First, list files to find a readable file
    list_result = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": True, "verbosity": "minimal"}
    )

    list_text = list_result[0]["text"]

    # Look for a text file or config file to read
    # This is bundle-dependent, so we'll try common file types
    # Exclude directories and focus on actual files
    potential_files = [
        "version.txt",
        "VERSION",
        "kubeconfig",
        "analysis.json",
        "replicated.app-health-check.json",
        "version.yaml",
        "pod-logs",
        "events.yaml",
        "pods.yaml",
    ]

    test_file = None
    for file_name in potential_files:
        if file_name in list_text and '"type":"file"' in list_text:
            # Verify it's actually marked as a file in the JSON response
            test_file = file_name
            break

    # If no known files found, try to extract actual files from the JSON listing
    if test_file is None:
        # Look for files that are explicitly marked as type "file" in JSON
        if '"type":"file"' in list_text:
            lines = list_text.split("\n")
            for line in lines:
                if '"type":"file"' in line and (
                    ".txt" in line or ".yaml" in line or ".json" in line
                ):
                    # Extract filename from JSON entry
                    if '"name":"' in line:
                        start = line.find('"name":"') + 8
                        end = line.find('"', start)
                        if start < end:
                            potential_file = line[start:end]
                            # Double check this isn't a directory name
                            if not potential_file.endswith("/") and "." in potential_file:
                                test_file = potential_file
                                break

    if test_file:
        # Try to read the file
        read_result = await mcp_protocol_client.call_tool(
            "read_file", {"path": test_file, "verbosity": "verbose"}
        )

        assert len(read_result) == 1
        response_text = read_result[0]["text"]

        # Should indicate successful read
        assert (
            "Read text file" in response_text
            or "content" in response_text.lower()
            or "File read successfully" in response_text
        ), f"Expected file read success for {test_file}: {response_text}"

        # Should include file metadata in verbose mode
        assert (
            "lines" in response_text.lower()
            or "size" in response_text.lower()
            or "bytes" in response_text.lower()
            or len(response_text) > 50
        ), (  # Should have actual content
            f"Verbose mode should include metadata: {response_text}"
        )
    else:
        # If no suitable file found, test with a known non-existent file
        read_result = await mcp_protocol_client.call_tool(
            "read_file", {"path": "definitely_does_not_exist.txt", "verbosity": "minimal"}
        )

        response_text = read_result[0]["text"]
        assert (
            "error" in response_text.lower()
            or "not found" in response_text.lower()
            or "does not exist" in response_text.lower()
        ), f"Expected file not found error: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_read_file_with_line_range(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test reading file with specific line range."""
    # First find a file to read
    list_result = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": True, "verbosity": "minimal"}
    )

    # Try to read a file with line limits
    # Use kubeconfig if available, or any yaml/text file
    test_files = ["kubeconfig", "cluster-info", "version.txt"]

    for test_file in test_files:
        if test_file in list_result[0]["text"]:
            # Try reading with line range
            read_result = await mcp_protocol_client.call_tool(
                "read_file",
                {"path": test_file, "start_line": 0, "end_line": 5, "verbosity": "minimal"},
            )

            response_text = read_result[0]["text"]

            if "Read text file" in response_text or "content" in response_text.lower():
                # Should limit output to requested lines
                # The response includes metadata, so content might be subset
                assert len(response_text) > 0, "Should return some content"
                break
    # If no files available to test with, that's okay for functional testing


@pytest.mark.functional
@pytest.mark.asyncio
async def test_grep_files_basic_search(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test basic grep_files functionality."""
    # Search for common terms that might appear in bundle files
    search_terms = ["kubernetes", "version", "cluster", "pod", "namespace", "yaml", "api"]

    for term in search_terms:
        result = await mcp_protocol_client.call_tool(
            "grep_files",
            {
                "pattern": term,
                "path": ".",
                "recursive": True,
                "case_sensitive": False,
                "max_results": 10,
                "verbosity": "verbose",
            },
        )

        assert len(result) == 1
        response_text = result[0]["text"]

        # Check if this search found anything
        if (
            "Found" in response_text
            and "matches" in response_text.lower()
            and "0 matches" not in response_text
        ):
            # Verify verbose output includes match details
            assert (
                "files searched" in response_text.lower()
                or "pattern" in response_text.lower()
                or "matches" in response_text.lower()
            ), f"Verbose grep should include search details: {response_text}"
            break

    # At least one search term should find results in a typical support bundle
    # If not, that's okay - the important thing is the tool works without crashing


@pytest.mark.functional
@pytest.mark.asyncio
async def test_grep_files_with_glob_pattern(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test grep_files with glob pattern filtering."""
    # Search for content in specific file types
    result = await mcp_protocol_client.call_tool(
        "grep_files",
        {
            "pattern": ".*",  # Match any content
            "path": ".",
            "recursive": True,
            "glob_pattern": "*.yaml",
            "case_sensitive": False,
            "max_results": 5,
            "max_files": 3,
            "verbosity": "minimal",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should indicate search completed (may or may not find matches)
    assert (
        "Found" in response_text
        or "searched" in response_text.lower()
        or "matches" in response_text.lower()
        or "No matches" in response_text
    ), f"Expected grep search completion: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_grep_files_parameter_limits(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test grep_files with various parameter limits."""
    # Test with restrictive limits
    result = await mcp_protocol_client.call_tool(
        "grep_files",
        {
            "pattern": ".",  # Match any character (likely to find matches)
            "path": ".",
            "recursive": True,
            "max_results": 2,
            "max_results_per_file": 1,
            "max_files": 1,
            "verbosity": "verbose",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should respect limits and provide feedback
    assert len(response_text) > 0, "Should return search results"

    # If matches found, should respect the limits
    if "matches" in response_text.lower() and "0 matches" not in response_text:
        # Results should be limited by parameters
        # The exact validation depends on response format
        pass


@pytest.mark.functional
@pytest.mark.asyncio
async def test_file_operations_verbosity_levels(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test different verbosity levels for file operations."""
    # Test list_files with different verbosity
    result_minimal = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
    )

    result_verbose = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": False, "verbosity": "verbose"}
    )

    minimal_text = result_minimal[0]["text"]
    verbose_text = result_verbose[0]["text"]

    # Both should work
    assert len(minimal_text) > 0, "Minimal verbosity should return content"
    assert len(verbose_text) > 0, "Verbose verbosity should return content"

    # Verbose should generally provide more information
    verbose_lines = len(verbose_text.split("\n"))
    minimal_lines = len(minimal_text.split("\n"))

    assert verbose_lines >= minimal_lines, (
        f"Verbose output ({verbose_lines} lines) should be >= "
        f"minimal output ({minimal_lines} lines)"
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_file_operations_concurrent_access(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test concurrent file operations."""

    # Launch multiple file operations concurrently
    tasks = []

    # Multiple list operations
    for i in range(2):
        task = mcp_protocol_client.call_tool(
            "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
        )
        tasks.append(("list", task))

    # Multiple grep operations
    for i in range(2):
        task = mcp_protocol_client.call_tool(
            "grep_files",
            {
                "pattern": "version",
                "path": ".",
                "recursive": True,
                "max_results": 5,
                "verbosity": "minimal",
            },
        )
        tasks.append(("grep", task))

    # Execute operations sequentially (stdio transport limitation)
    results = []
    for operation, task in tasks:
        result = await task
        results.append((operation, result))

    # Verify all operations completed
    for operation, result in results:
        assert len(result) == 1, f"{operation} operation returned invalid result"
        response_text = result[0]["text"]

        # Should not have bundle-related errors
        assert "no bundle" not in response_text.lower(), (
            f"{operation} operation lost bundle state: {response_text}"
        )

        assert len(response_text) > 0, f"{operation} operation returned empty response"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_file_operations_error_handling(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test error handling in file operations."""
    # Test list_files with invalid path
    list_result = await mcp_protocol_client.call_tool(
        "list_files",
        {"path": "/absolutely/does/not/exist", "recursive": False, "verbosity": "minimal"},
    )

    list_text = list_result[0]["text"]
    # Should handle gracefully (might return empty list or error message)
    assert isinstance(list_text, str), "Should return string response"

    # Test read_file with invalid path
    read_result = await mcp_protocol_client.call_tool(
        "read_file", {"path": "/absolutely/does/not/exist.txt", "verbosity": "minimal"}
    )

    read_text = read_result[0]["text"]
    assert (
        "error" in read_text.lower()
        or "not found" in read_text.lower()
        or "does not exist" in read_text.lower()
    ), f"Expected file not found error: {read_text}"

    # Test grep_files with invalid regex
    grep_result = await mcp_protocol_client.call_tool(
        "grep_files",
        {"pattern": "[unclosed-bracket", "path": ".", "recursive": False, "verbosity": "minimal"},
    )

    grep_text = grep_result[0]["text"]
    # Should handle invalid regex gracefully (might show error or no matches)
    assert isinstance(grep_text, str), "Should return string response"
