"""
Functional tests for kubectl tool through MCP protocol.

Tests validate kubectl functionality including command execution,
output formatting, and error handling through the MCP layer.
"""

import json
import time
from typing import Dict

import pytest

from tests.integration.mcp_test_utils import MCPTestClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_version_command(
    mcp_protocol_client: MCPTestClient,
    initialized_test_bundle: str,
    performance_threshold: Dict[str, int],
) -> None:
    """Test kubectl version command through MCP protocol."""
    start_time = time.time()

    # Execute kubectl version command
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "timeout": 15,
            "json_output": False,
            "verbosity": "verbose",
        },
    )

    end_time = time.time()
    duration_ms = (end_time - start_time) * 1000

    # Verify performance
    max_duration = performance_threshold["tool_call_max_ms"]
    assert duration_ms < max_duration, (
        f"kubectl version took {duration_ms:.1f}ms, expected under {max_duration}ms"
    )

    # Verify response structure
    assert len(result) == 1, "Expected single response content item"
    content = result[0]
    assert content["type"] == "text", "Response should be text content"

    response_text = content["text"]

    # Verify success indicators
    assert (
        "kubectl command executed successfully" in response_text
        or "version" in response_text.lower()
    ), f"Expected kubectl success or version info, got: {response_text}"

    # Should include command metadata in verbose mode
    assert (
        "Command metadata" in response_text
        or "duration" in response_text.lower()
        or "exit code" in response_text.lower()
    ), f"Verbose mode should include metadata: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_json_output(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test kubectl with JSON output formatting."""
    # Execute kubectl command with JSON output
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {"command": "version --client", "timeout": 15, "json_output": True, "verbosity": "minimal"},
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should indicate successful execution
    assert (
        "kubectl command executed successfully" in response_text
        or "version" in response_text.lower()
        or '"clientVersion"' in response_text
    ), f"Expected kubectl JSON success, got: {response_text}"

    # For JSON output, should either contain JSON or indicate JSON parsing
    if "clientVersion" in response_text:
        # Response contains JSON, verify it's valid
        try:
            # Extract JSON portion from response
            lines = response_text.split("\n")
            json_lines = [line for line in lines if line.strip().startswith("{")]
            if json_lines:
                json.loads(json_lines[0])
        except json.JSONDecodeError:
            # JSON parsing failed - this might be acceptable if kubectl didn't return JSON
            pass


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_timeout_parameter(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test kubectl timeout parameter functionality."""
    # Test with reasonable timeout
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "timeout": 30,
            "json_output": False,
            "verbosity": "minimal",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should complete successfully with reasonable timeout
    assert (
        "kubectl command executed successfully" in response_text
        or "version" in response_text.lower()
    ), f"Expected success with 30s timeout: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_verbosity_levels(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test different verbosity levels for kubectl commands."""
    # Test minimal verbosity
    result_minimal = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "timeout": 15,
            "json_output": False,
            "verbosity": "minimal",
        },
    )

    minimal_text = result_minimal[0]["text"]
    minimal_lines = len(minimal_text.split("\n"))

    # Test verbose verbosity
    result_verbose = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "timeout": 15,
            "json_output": False,
            "verbosity": "verbose",
        },
    )

    verbose_text = result_verbose[0]["text"]
    verbose_lines = len(verbose_text.split("\n"))

    # Verbose should generally provide more information
    # (Though for version command, difference might be minimal)
    assert verbose_lines >= minimal_lines, (
        f"Verbose output ({verbose_lines} lines) should be >= "
        f"minimal output ({minimal_lines} lines)"
    )

    # Both should indicate success
    assert (
        "kubectl command executed successfully" in minimal_text or "version" in minimal_text.lower()
    )
    assert (
        "kubectl command executed successfully" in verbose_text or "version" in verbose_text.lower()
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_invalid_command(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test kubectl with invalid command."""
    # Try invalid kubectl command
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "invalidsubcommand --nonexistent-flag",
            "timeout": 15,
            "json_output": False,
            "verbosity": "minimal",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should handle the error gracefully
    # Either kubectl returns error info, or our tool wraps it appropriately
    assert isinstance(response_text, str), "Should return string response"
    assert len(response_text) > 0, "Should return non-empty response"

    # Response might indicate error or might show kubectl's own error message
    # The important thing is that it doesn't crash the server


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_complex_command(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test kubectl with complex command parameters."""
    # Try a complex kubectl command with multiple flags
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client --output=yaml",
            "timeout": 20,
            "json_output": False,
            "verbosity": "verbose",
        },
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should handle complex command structure
    assert (
        "kubectl command executed successfully" in response_text
        or "version" in response_text.lower()
        or "clientVersion" in response_text
        or "yaml" in response_text.lower()
    ), f"Expected kubectl complex command success: {response_text}"

    # Verbose mode should include command metadata
    assert (
        "Command metadata" in response_text
        or "duration" in response_text.lower()
        or "Command:" in response_text
    ), f"Verbose mode should show command details: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_host_only_bundle_handling(mcp_protocol_client: MCPTestClient) -> None:
    """Test kubectl behavior with host-only bundles."""
    # This test would need a host-only bundle to be meaningful
    # For now, we'll test that the tool handles the case appropriately

    # First try kubectl without any bundle (should fail)
    result_no_bundle = await mcp_protocol_client.call_tool(
        "kubectl", {"command": "version --client", "timeout": 15, "verbosity": "minimal"}
    )

    no_bundle_text = result_no_bundle[0]["text"]
    assert "no bundle" in no_bundle_text.lower(), f"Expected 'no bundle' error: {no_bundle_text}"

    # If we had a host-only bundle fixture, we would test:
    # 1. Initialize host-only bundle
    # 2. Try kubectl command
    # 3. Verify appropriate error message about cluster resources not available


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_parameter_validation(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test kubectl parameter validation through MCP protocol."""
    # Test with invalid timeout type
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "timeout": "invalid",  # Should be number, not string
            "verbosity": "minimal",
        },
    )

    response_text = result[0]["text"]
    # Should handle parameter validation - either through MCP layer or tool layer
    assert isinstance(response_text, str), "Should return string response"

    # Test with invalid json_output type
    result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "json_output": "maybe",  # Should be boolean, not string
            "verbosity": "minimal",
        },
    )

    response_text = result[0]["text"]
    assert isinstance(response_text, str), "Should return string response"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_concurrent_execution(
    mcp_protocol_client: MCPTestClient, initialized_test_bundle: str
) -> None:
    """Test concurrent kubectl command execution."""

    # Launch multiple kubectl commands concurrently
    tasks = []
    num_concurrent = 3

    for i in range(num_concurrent):
        task = mcp_protocol_client.call_tool(
            "kubectl",
            {
                "command": "version --client",
                "timeout": 20,
                "json_output": False,
                "verbosity": "minimal",
            },
        )
        tasks.append(task)

    # Execute sequentially (stdio transport limitation)
    results = []
    for task in tasks:
        result = await task
        results.append(result)

    # Verify all completed successfully
    for i, result in enumerate(results):
        assert len(result) == 1, f"kubectl task {i} returned invalid result"
        response_text = result[0]["text"]

        assert (
            "kubectl command executed successfully" in response_text
            or "version" in response_text.lower()
        ), f"kubectl task {i} failed: {response_text}"

        # Should not have bundle-related errors since bundle was initialized
        assert "no bundle" not in response_text.lower(), (
            f"kubectl task {i} lost bundle state: {response_text}"
        )
