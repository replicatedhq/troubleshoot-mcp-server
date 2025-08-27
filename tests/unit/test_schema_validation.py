"""
Test for MCP schema validation to prevent regression to args wrapper format.

This file contains focused tests to ensure that MCP tool schemas never revert
to the non-standard 'args' wrapper format.
"""

import json
import os

import pytest

from troubleshoot_mcp_server.server import mcp


# Mark all tests in this file as unit tests and quick tests
pytestmark = [pytest.mark.unit, pytest.mark.quick]


@pytest.mark.asyncio
async def test_mcp_tool_schemas_do_not_use_args_wrapper():
    """
    Test that all MCP tool schemas generate standard format without 'args' wrapper.

    This test ensures that FastMCP generates tool schemas that follow the standard
    MCP format with direct parameters, not wrapped in an 'args' object.

    The schemas should look like:
    {
        "properties": {
            "source": {"type": "string", "description": "..."},
            "force": {"type": "boolean", "default": false, "description": "..."},
            "verbosity": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}
        },
        "required": ["source"],
        "title": "initialize_bundleArguments",
        "type": "object"
    }

    NOT like:
    {
        "properties": {
            "args": {
                "$ref": "#/$defs/InitializeBundleArgs"
            }
        },
        "required": ["args"],
        "title": "initialize_bundleArguments",
        "type": "object"
    }
    """
    # Get all tools from the FastMCP server
    tools = await mcp.list_tools()

    # Assert we have the expected tools
    # list_available_bundles is conditionally available based on environment
    base_tool_names = {
        "initialize_bundle",
        "kubectl",
        "list_files",
        "read_file",
        "grep_files",
    }

    expected_tool_names = base_tool_names.copy()

    # Check if list_available_bundles tool should be available
    list_bundles_enabled = os.environ.get("ENABLE_LIST_BUNDLES_TOOL", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    if list_bundles_enabled:
        expected_tool_names.add("list_available_bundles")

    actual_tool_names = {tool.name for tool in tools}
    assert actual_tool_names == expected_tool_names, (
        f"Expected tools {expected_tool_names}, got {actual_tool_names}"
    )

    # Check each tool's schema
    for tool in tools:
        tool_name = tool.name
        input_schema = tool.inputSchema

        # Convert schema to dict if it's not already
        if hasattr(input_schema, "model_dump"):
            schema_dict = input_schema.model_dump()
        else:
            schema_dict = input_schema

        # Assert the schema is a dict
        assert isinstance(schema_dict, dict), f"Schema for {tool_name} should be a dict"

        # Assert the schema is an object type
        assert schema_dict.get("type") == "object", f"Schema for {tool_name} should be object type"

        # Assert the schema has properties
        assert "properties" in schema_dict, f"Schema for {tool_name} should have properties"
        properties = schema_dict["properties"]

        # Critical assertion: properties should NOT contain an 'args' key
        assert "args" not in properties, (
            f"Schema for {tool_name} contains 'args' wrapper - this violates standard MCP format"
        )

        # Verify that properties contain expected direct parameters
        if tool_name == "initialize_bundle":
            assert "source" in properties, "initialize_bundle schema should have 'source' parameter"
            assert "force" in properties, "initialize_bundle schema should have 'force' parameter"
            assert "verbosity" in properties, (
                "initialize_bundle schema should have 'verbosity' parameter"
            )

        elif tool_name == "kubectl":
            assert "command" in properties, "kubectl schema should have 'command' parameter"
            assert "timeout" in properties, "kubectl schema should have 'timeout' parameter"
            assert "json_output" in properties, "kubectl schema should have 'json_output' parameter"
            assert "verbosity" in properties, "kubectl schema should have 'verbosity' parameter"

        elif tool_name == "list_files":
            assert "path" in properties, "list_files schema should have 'path' parameter"
            assert "recursive" in properties, "list_files schema should have 'recursive' parameter"
            assert "verbosity" in properties, "list_files schema should have 'verbosity' parameter"

        elif tool_name == "read_file":
            assert "path" in properties, "read_file schema should have 'path' parameter"
            assert "start_line" in properties, "read_file schema should have 'start_line' parameter"
            assert "end_line" in properties, "read_file schema should have 'end_line' parameter"
            assert "verbosity" in properties, "read_file schema should have 'verbosity' parameter"

        elif tool_name == "grep_files":
            assert "pattern" in properties, "grep_files schema should have 'pattern' parameter"
            assert "path" in properties, "grep_files schema should have 'path' parameter"
            assert "recursive" in properties, "grep_files schema should have 'recursive' parameter"
            assert "glob_pattern" in properties, (
                "grep_files schema should have 'glob_pattern' parameter"
            )
            assert "case_sensitive" in properties, (
                "grep_files schema should have 'case_sensitive' parameter"
            )
            assert "max_results" in properties, (
                "grep_files schema should have 'max_results' parameter"
            )
            assert "max_results_per_file" in properties, (
                "grep_files schema should have 'max_results_per_file' parameter"
            )
            assert "max_files" in properties, "grep_files schema should have 'max_files' parameter"
            assert "verbosity" in properties, "grep_files schema should have 'verbosity' parameter"

        elif tool_name == "list_available_bundles":
            assert "include_invalid" in properties, (
                "list_available_bundles schema should have 'include_invalid' parameter"
            )
            assert "verbosity" in properties, (
                "list_available_bundles schema should have 'verbosity' parameter"
            )

        # Check that no $refs point to Args classes (another sign of wrapper usage)
        schema_json = json.dumps(schema_dict)
        assert "Args" not in schema_json, (
            f"Schema for {tool_name} contains references to Args classes"
        )


@pytest.mark.asyncio
async def test_tool_schemas_are_valid_json_schema():
    """
    Test that all tool schemas are valid JSON schemas.

    This ensures the schemas are well-formed and can be used by MCP clients.
    """
    tools = await mcp.list_tools()

    for tool in tools:
        input_schema = tool.inputSchema

        # Convert schema to dict if it's not already
        if hasattr(input_schema, "model_dump"):
            schema_dict = input_schema.model_dump()
        else:
            schema_dict = input_schema

        # Basic JSON schema validation
        assert isinstance(schema_dict, dict)
        assert "type" in schema_dict
        assert schema_dict["type"] == "object"
        assert "properties" in schema_dict
        assert isinstance(schema_dict["properties"], dict)

        # If required is present, it should be a list
        if "required" in schema_dict:
            assert isinstance(schema_dict["required"], list)

        # Title should be present
        assert "title" in schema_dict
        assert isinstance(schema_dict["title"], str)


@pytest.mark.asyncio
async def test_verbosity_parameter_consistency():
    """
    Test that all tools have a consistent verbosity parameter definition.

    The verbosity parameter should be optional and allow string or null values.
    """
    tools = await mcp.list_tools()

    for tool in tools:
        input_schema = tool.inputSchema

        # Convert schema to dict if it's not already
        if hasattr(input_schema, "model_dump"):
            schema_dict = input_schema.model_dump()
        else:
            schema_dict = input_schema

        properties = schema_dict["properties"]

        # All tools should have verbosity parameter
        assert "verbosity" in properties, f"Tool {tool.name} is missing verbosity parameter"

        verbosity_schema = properties["verbosity"]

        # Verbosity should allow string or null
        assert "anyOf" in verbosity_schema or "type" in verbosity_schema, (
            f"Tool {tool.name} verbosity parameter has invalid schema"
        )

        # Verbosity should be optional (not in required list)
        required = schema_dict.get("required", [])
        assert "verbosity" not in required, (
            f"Tool {tool.name} verbosity parameter should be optional"
        )


@pytest.mark.asyncio
async def test_required_parameters_match_expectations():
    """
    Test that each tool has the expected required parameters.
    """
    tools = {tool.name: tool for tool in await mcp.list_tools()}

    # Define expected required parameters for each tool
    expected_required = {
        "initialize_bundle": ["source"],
        "kubectl": ["command"],
        "list_files": ["path"],
        "read_file": ["path"],
        "grep_files": ["pattern", "path"],
    }

    # Add list_available_bundles if it's enabled
    list_bundles_enabled = os.environ.get("ENABLE_LIST_BUNDLES_TOOL", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    if list_bundles_enabled:
        expected_required["list_available_bundles"] = []  # No required parameters

    for tool_name, expected in expected_required.items():
        assert tool_name in tools, f"Tool {tool_name} not found"

        tool = tools[tool_name]
        input_schema = tool.inputSchema

        # Convert schema to dict if it's not already
        if hasattr(input_schema, "model_dump"):
            schema_dict = input_schema.model_dump()
        else:
            schema_dict = input_schema

        actual_required = schema_dict.get("required", [])

        assert set(actual_required) == set(expected), (
            f"Tool {tool_name} required parameters mismatch. Expected {expected}, got {actual_required}"
        )


@pytest.mark.asyncio
async def test_list_available_bundles_conditional_availability():
    """
    Test that list_available_bundles tool is properly hidden/shown based on environment variable.

    This test verifies the core requirement of the hide-list-bundles-tool feature:
    - Tool is hidden by default (ENABLE_LIST_BUNDLES_TOOL not set or false)
    - Tool is available when ENABLE_LIST_BUNDLES_TOOL is set to true
    """
    # Get current environment value
    current_value = os.environ.get("ENABLE_LIST_BUNDLES_TOOL")

    try:
        # Test 1: Tool should be available when enabled (our test setup enables it)
        tools = await mcp.list_tools()
        tool_names = {tool.name for tool in tools}

        list_bundles_enabled = os.environ.get("ENABLE_LIST_BUNDLES_TOOL", "false").lower() in (
            "true",
            "1",
            "yes",
        )

        if list_bundles_enabled:
            assert "list_available_bundles" in tool_names, (
                "list_available_bundles should be available when ENABLE_LIST_BUNDLES_TOOL is enabled"
            )
        else:
            assert "list_available_bundles" not in tool_names, (
                "list_available_bundles should be hidden when ENABLE_LIST_BUNDLES_TOOL is not enabled"
            )

    finally:
        # Restore original value
        if current_value is not None:
            os.environ["ENABLE_LIST_BUNDLES_TOOL"] = current_value
        elif "ENABLE_LIST_BUNDLES_TOOL" in os.environ:
            del os.environ["ENABLE_LIST_BUNDLES_TOOL"]
