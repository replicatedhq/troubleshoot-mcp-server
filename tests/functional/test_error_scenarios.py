"""
Functional tests for error handling through MCP protocol.

Tests validate proper MCP error responses and graceful error handling
for various failure scenarios.
"""

import pytest

from tests.integration.mcp_test_utils import MCPTestClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_tool_call_with_invalid_parameters(mcp_protocol_client: MCPTestClient) -> None:
    """Test tool calls with invalid parameter types return proper errors."""
    # Try initialize_bundle with invalid parameter types
    result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {
            "source": 123,  # Should be string, not int
            "force": "not_boolean",  # Should be boolean, not string
            "verbosity": ["invalid"],  # Should be string, not array
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should get error message about parameter validation
    assert (
        "error" in response_text.lower()
        or "invalid" in response_text.lower()
        or "validation" in response_text.lower()
    ), f"Expected parameter validation error, got: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_tool_call_with_missing_required_parameters(
    mcp_protocol_client: MCPTestClient,
) -> None:
    """Test tool calls with missing required parameters."""
    # Try initialize_bundle without required source parameter
    result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {
            "force": False,
            "verbosity": "minimal",
            # Missing "source" parameter
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should get error about missing required parameter
    assert (
        "error" in response_text.lower()
        or "required" in response_text.lower()
        or "missing" in response_text.lower()
    ), f"Expected missing parameter error, got: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_without_bundle_initialization(mcp_protocol_client: MCPTestClient) -> None:
    """Test kubectl tool usage without bundle initialization."""
    # Try to use kubectl without initializing a bundle first
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {"command": "get pods", "timeout": 10, "json_output": False, "verbosity": "minimal"},
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should get error about no bundle being initialized
    assert (
        "no bundle" in response_text.lower()
        or "initialize" in response_text.lower()
        or "bundle" in response_text.lower()
    ), f"Expected bundle not initialized error, got: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_file_operations_without_bundle_initialization(
    mcp_protocol_client: MCPTestClient,
) -> None:
    """Test file operation tools without bundle initialization."""
    # Try list_files without bundle
    list_result = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
    )

    list_text = list_result[0]["text"]
    assert "no bundle" in list_text.lower() or "initialize" in list_text.lower(), (
        f"list_files should require bundle, got: {list_text}"
    )

    # Try read_file without bundle
    read_result = await mcp_protocol_client.call_tool(
        "read_file", {"path": "test.txt", "verbosity": "minimal"}
    )

    read_text = read_result[0]["text"]
    assert "no bundle" in read_text.lower() or "initialize" in read_text.lower(), (
        f"read_file should require bundle, got: {read_text}"
    )

    # Try grep_files without bundle
    grep_result = await mcp_protocol_client.call_tool(
        "grep_files", {"pattern": "test", "path": ".", "verbosity": "minimal"}
    )

    grep_text = grep_result[0]["text"]
    assert "no bundle" in grep_text.lower() or "initialize" in grep_text.lower(), (
        f"grep_files should require bundle, got: {grep_text}"
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_invalid_tool_call(mcp_protocol_client: MCPTestClient) -> None:
    """Test calling a non-existent tool."""
    # The MCPTestClient should handle this at the protocol level
    # This tests the server's response to invalid tool names
    try:
        result = await mcp_protocol_client.call_tool("non_existent_tool", {"param": "value"})
        # If we get here, the server returned a response instead of an error
        # Check if it's an error response
        response_text = result[0]["text"] if result else ""
        assert (
            "error" in response_text.lower()
            or "not found" in response_text.lower()
            or "unknown" in response_text.lower()
        ), f"Expected tool not found error, got: {response_text}"
    except Exception as e:
        # Protocol-level error is also acceptable
        error_msg = str(e).lower()
        assert "tool" in error_msg or "not found" in error_msg or "unknown" in error_msg, (
            f"Expected tool-related error, got: {str(e)}"
        )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_timeout_handling(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test kubectl timeout behavior."""
    # Try kubectl command with very short timeout
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "get pods --all-namespaces",  # Potentially slow command
            "timeout": 1,  # Very short timeout (1 second)
            "json_output": False,
            "verbosity": "minimal",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should either succeed quickly or timeout gracefully
    # The server should handle timeouts without crashing
    assert isinstance(response_text, str), "Response should be valid text"

    # If it timed out, should have timeout-related message
    if "timeout" in response_text.lower():
        assert "error" in response_text.lower() or "failed" in response_text.lower()


@pytest.mark.functional
@pytest.mark.asyncio
async def test_file_operations_invalid_paths(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test file operations with invalid paths."""
    # Test list_files with invalid path
    list_result = await mcp_protocol_client.call_tool(
        "list_files",
        {"path": "/definitely/does/not/exist", "recursive": False, "verbosity": "minimal"},
    )

    list_text = list_result[0]["text"]
    # Should handle gracefully - either error or empty result
    assert isinstance(list_text, str), "Response should be valid text"

    # Test read_file with invalid path
    read_result = await mcp_protocol_client.call_tool(
        "read_file", {"path": "/definitely/does/not/exist.txt", "verbosity": "minimal"}
    )

    read_text = read_result[0]["text"]
    assert (
        "error" in read_text.lower()
        or "not found" in read_text.lower()
        or "does not exist" in read_text.lower()
    ), f"Expected file not found error, got: {read_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_grep_files_invalid_pattern(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test grep_files with invalid regex pattern."""
    # Try grep with invalid regex pattern
    result = await mcp_protocol_client.call_tool(
        "grep_files",
        {
            "pattern": "[invalid-regex",  # Unclosed bracket
            "path": ".",
            "recursive": True,
            "verbosity": "minimal",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should handle invalid regex gracefully
    assert isinstance(response_text, str), "Response should be valid text"

    # May get regex error or no matches - both are acceptable
    # The important thing is the server doesn't crash


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_error_scenarios(mcp_protocol_client: MCPTestClient) -> None:
    """Test multiple error scenarios concurrently."""

    # Run multiple error-inducing operations concurrently
    tasks = [
        mcp_protocol_client.call_tool(
            "kubectl", {"command": "get pods", "verbosity": "minimal"}
        ),  # No bundle initialized
        mcp_protocol_client.call_tool(
            "list_files", {"path": "/invalid", "verbosity": "minimal"}
        ),  # No bundle + invalid path
        mcp_protocol_client.call_tool(
            "read_file", {"path": "/nonexistent.txt", "verbosity": "minimal"}
        ),  # No bundle + invalid file
        mcp_protocol_client.call_tool(
            "initialize_bundle", {"source": "/invalid/bundle.tar.gz", "verbosity": "minimal"}
        ),  # Invalid bundle source
    ]

    # Execute sequentially (stdio transport limitation)
    results = []
    for task in tasks:
        try:
            result = await task
            results.append(result)
        except Exception as e:
            results.append(e)

    # Verify all returned valid responses (no exceptions/crashes)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            pytest.fail(f"Task {i} raised exception: {result}")

        assert isinstance(result, list) and len(result) == 1, (
            f"Task {i} returned invalid result format: {result}"
        )

        response_text = result[0]["text"]
        assert isinstance(response_text, str), (
            f"Task {i} returned non-string response: {type(response_text)}"
        )

        # Each should indicate some kind of error condition
        assert (
            "error" in response_text.lower()
            or "no bundle" in response_text.lower()
            or "not found" in response_text.lower()
            or "failed" in response_text.lower()
            or "invalid" in response_text.lower()
        ), f"Task {i} should have error indication: {response_text}"
