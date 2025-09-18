"""
Functional tests for MCP tool discovery and registration.

Tests validate that all required tools are properly registered with the MCP server
and expose correct schemas through the protocol layer.
"""

import time
from typing import Dict

import pytest

from tests.integration.mcp_test_utils import MCPTestClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_tool_discovery_performance(
    mcp_protocol_client: MCPTestClient, performance_threshold: Dict[str, int]
) -> None:
    """Test that tool discovery completes within performance threshold."""
    start_time = time.time()

    # List all available tools
    response = await mcp_protocol_client.send_request("tools/list")
    tools = response.get("result", {}).get("tools", [])

    end_time = time.time()
    duration_ms = (end_time - start_time) * 1000

    # Verify performance
    max_duration = performance_threshold["tool_discovery_max_ms"]
    assert duration_ms < max_duration, (
        f"Tool discovery took {duration_ms:.1f}ms, expected under {max_duration}ms"
    )

    # Verify we got tools
    assert len(tools) > 0, "Server should return at least one tool"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_required_tools_registered(mcp_protocol_client: MCPTestClient) -> None:
    """Test that all 5 required tools are registered with the MCP server."""
    # Get list of tools from server
    response = await mcp_protocol_client.send_request("tools/list")
    tools = response.get("result", {}).get("tools", [])

    # Extract tool names
    tool_names = {tool["name"] for tool in tools}

    # Define required tools per task specification
    required_tools = {"initialize_bundle", "kubectl", "list_files", "read_file", "grep_files"}

    # Verify all required tools are present
    missing_tools = required_tools - tool_names
    assert not missing_tools, f"Missing required tools: {missing_tools}"

    # Verify we have exactly the expected number or more
    assert len(tool_names) >= len(required_tools), (
        f"Expected at least {len(required_tools)} tools, got {len(tool_names)}"
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_tool_schemas_valid(mcp_protocol_client: MCPTestClient) -> None:
    """Test that all tool schemas follow MCP standard format."""
    # Get list of tools from server
    response = await mcp_protocol_client.send_request("tools/list")
    tools = response.get("result", {}).get("tools", [])

    for tool in tools:
        tool_name = tool.get("name")

        # Verify required fields
        assert tool_name, f"Tool missing name: {tool}"
        assert "description" in tool, f"Tool {tool_name} missing description"
        assert "inputSchema" in tool, f"Tool {tool_name} missing inputSchema"

        # Verify schema structure
        schema = tool["inputSchema"]
        assert schema.get("type") == "object", (
            f"Tool {tool_name} schema type should be 'object', got {schema.get('type')}"
        )

        # Verify properties exist for tools with parameters
        if tool_name in ["initialize_bundle", "kubectl", "list_files", "read_file", "grep_files"]:
            assert "properties" in schema, f"Tool {tool_name} missing properties in schema"
            properties = schema["properties"]
            assert isinstance(properties, dict), (
                f"Tool {tool_name} properties should be dict, got {type(properties)}"
            )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_initialize_bundle_schema(mcp_protocol_client: MCPTestClient) -> None:
    """Test initialize_bundle tool has correct schema structure."""
    response = await mcp_protocol_client.send_request("tools/list")
    tools = response.get("result", {}).get("tools", [])

    # Find initialize_bundle tool
    init_tool = None
    for tool in tools:
        if tool["name"] == "initialize_bundle":
            init_tool = tool
            break

    assert init_tool is not None, "initialize_bundle tool not found"

    # Verify schema has required properties
    schema = init_tool["inputSchema"]
    properties = schema["properties"]

    # Check required parameters
    required_params = {"source", "force", "verbosity"}
    schema_params = set(properties.keys())

    assert required_params.issubset(schema_params), (
        f"Missing required parameters. Expected: {required_params}, Got: {schema_params}"
    )

    # Verify source parameter
    source_prop = properties["source"]
    assert source_prop["type"] == "string", "source parameter should be string type"

    # Verify force parameter
    force_prop = properties["force"]
    assert force_prop["type"] == "boolean", "force parameter should be boolean type"

    # Verify verbosity parameter (may use anyOf structure for nullable types)
    verbosity_prop = properties["verbosity"]
    if "type" in verbosity_prop:
        assert verbosity_prop["type"] == "string", "verbosity parameter should be string type"
    elif "anyOf" in verbosity_prop:
        # Check that string is one of the allowed types
        types = [item.get("type") for item in verbosity_prop["anyOf"]]
        assert "string" in types, f"verbosity parameter should allow string type, got: {types}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_kubectl_schema(mcp_protocol_client: MCPTestClient) -> None:
    """Test kubectl tool has correct schema structure."""
    response = await mcp_protocol_client.send_request("tools/list")
    tools = response.get("result", {}).get("tools", [])

    # Find kubectl tool
    kubectl_tool = None
    for tool in tools:
        if tool["name"] == "kubectl":
            kubectl_tool = tool
            break

    assert kubectl_tool is not None, "kubectl tool not found"

    # Verify schema has required properties
    schema = kubectl_tool["inputSchema"]
    properties = schema["properties"]

    # Check required parameters
    required_params = {"command"}
    schema_params = set(properties.keys())

    assert required_params.issubset(schema_params), (
        f"kubectl missing required parameters. Expected: {required_params}, Got: {schema_params}"
    )

    # Verify command parameter
    command_prop = properties["command"]
    assert command_prop["type"] == "string", "command parameter should be string type"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_file_tools_schemas(mcp_protocol_client: MCPTestClient) -> None:
    """Test file operation tools have correct schema structures."""
    response = await mcp_protocol_client.send_request("tools/list")
    tools = response.get("result", {}).get("tools", [])

    # Create lookup for tools
    tools_by_name = {tool["name"]: tool for tool in tools}

    # Test list_files schema
    list_files_tool = tools_by_name.get("list_files")
    assert list_files_tool is not None, "list_files tool not found"

    list_properties = list_files_tool["inputSchema"]["properties"]
    assert "path" in list_properties, "list_files missing path parameter"
    assert list_properties["path"]["type"] == "string"

    # Test read_file schema
    read_file_tool = tools_by_name.get("read_file")
    assert read_file_tool is not None, "read_file tool not found"

    read_properties = read_file_tool["inputSchema"]["properties"]
    assert "path" in read_properties, "read_file missing path parameter"
    assert read_properties["path"]["type"] == "string"

    # Test grep_files schema
    grep_files_tool = tools_by_name.get("grep_files")
    assert grep_files_tool is not None, "grep_files tool not found"

    grep_properties = grep_files_tool["inputSchema"]["properties"]
    assert "pattern" in grep_properties, "grep_files missing pattern parameter"
    assert grep_properties["pattern"]["type"] == "string"
    assert "path" in grep_properties, "grep_files missing path parameter"
    assert grep_properties["path"]["type"] == "string"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_tool_discovery(mcp_protocol_client: MCPTestClient) -> None:
    """Test that multiple sequential tool discovery requests work consistently."""
    # Note: MCPTestClient uses stdio and can't handle true concurrent requests
    # This test validates that multiple sequential requests return consistent results
    responses = []

    for i in range(5):
        response = await mcp_protocol_client.send_request("tools/list")
        responses.append(response)

    # Verify all responses are valid
    tool_names_sets = []
    for i, response in enumerate(responses):
        assert "result" in response, f"Response {i} missing result"
        tools = response["result"].get("tools", [])
        assert len(tools) > 0, f"Response {i} returned no tools"

        # Verify tool names are consistent across all responses
        tool_names = {tool["name"] for tool in tools}
        expected_tools = {"initialize_bundle", "kubectl", "list_files", "read_file", "grep_files"}
        assert expected_tools.issubset(tool_names), (
            f"Response {i} missing required tools: {expected_tools - tool_names}"
        )
        tool_names_sets.append(tool_names)

    # All responses should have the same tool names
    first_set = tool_names_sets[0]
    for i, tool_set in enumerate(tool_names_sets[1:], 1):
        assert tool_set == first_set, (
            f"Response {i} has different tools than response 0: "
            f"diff={tool_set.symmetric_difference(first_set)}"
        )
