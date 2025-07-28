"""
Integration tests for MCP tool functions via direct function calls.

This module tests the core MCP tool functionality by calling the tool functions
directly (NOT through the MCP protocol). These are integration tests that verify
the business logic of each tool works correctly when called programmatically.

IMPORTANT: These tests do NOT test the MCP protocol layer. They test the underlying
functions that are exposed as MCP tools. For actual MCP protocol testing, see:
- tests/e2e/test_mcp_protocol_integration.py (real protocol via MCPTestClient)

These tests are valuable for:
1. Testing tool business logic without protocol overhead
2. Fast feedback during development
3. Verifying function signatures and return formats
4. Integration testing of bundle + tool workflows
"""

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from mcp_server_troubleshoot.server import (
    initialize_bundle,
    list_available_bundles,
    list_files,
    read_file,
    grep_files,
    kubectl,
    initialize_with_bundle_dir,
)
from mcp_server_troubleshoot.bundle import (
    InitializeBundleArgs,
    ListAvailableBundlesArgs,
)
from mcp_server_troubleshoot.files import ListFilesArgs, ReadFileArgs, GrepFilesArgs
from mcp_server_troubleshoot.kubectl import KubectlCommandArgs

from .mcp_test_utils import get_test_bundle_path

# Mark all tests in this file as integration tests
pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def bundle_storage_dir():
    """
    Fixture that provides a temporary directory for bundle storage.

    This fixture creates a temporary directory and initializes the
    MCP server components to use it.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        bundle_dir = Path(temp_dir)

        # Initialize the server components with the bundle directory
        initialize_with_bundle_dir(bundle_dir)

        yield bundle_dir


@pytest.mark.asyncio
async def test_list_available_bundles_function(bundle_storage_dir):
    """
    Test list_available_bundles function directly (NOT via MCP protocol).

    This test verifies the function:
    1. Can be called successfully with valid arguments
    2. Returns proper response format (list of TextContent)
    3. Handles empty bundle directory correctly
    4. Response structure matches expected format

    NOTE: This is a direct function call test, not MCP protocol testing.
    """
    # Test with empty bundle directory first
    args = ListAvailableBundlesArgs(include_invalid=False)
    result = await list_available_bundles(args)

    # Verify result structure
    assert isinstance(result, list), "Result should be a list"
    assert len(result) > 0, "Should return at least one content item"

    content_item = result[0]
    assert content_item.type == "text", "Content should be text type"
    assert "No support bundles found" in content_item.text, "Should indicate no bundles found"


@pytest.mark.asyncio
async def test_initialize_bundle_function_local_file(bundle_storage_dir):
    """
    Test initialize_bundle function with local file (NOT via MCP protocol).

    This test verifies the function:
    1. Can initialize a bundle from a local file path
    2. Returns expected metadata in response format
    3. Provides bundle path and kubeconfig path in response
    4. Response format matches expected TextContent structure

    NOTE: This is a direct function call test, not MCP protocol testing.
    """
    # Get the test bundle path
    test_bundle = get_test_bundle_path()

    # Call initialize_bundle function directly (not via MCP protocol)
    args = InitializeBundleArgs(source=str(test_bundle), force=False)
    result = await initialize_bundle(args)

    # Verify we got a result
    assert isinstance(result, list), "Tool result should be a list"
    assert len(result) > 0, "Tool should return at least one content item"

    # Verify the first result is text content
    content_item = result[0]
    assert content_item.type == "text", "Content should be text type"

    # Verify the response contains expected information
    response_text = content_item.text
    assert (
        "Bundle initialized successfully" in response_text
        or "Bundle initialized but API server is NOT available" in response_text
    ), "Response should indicate bundle initialization status"

    # The response should contain JSON with bundle metadata
    assert "```json" in response_text, "Response should contain JSON metadata"
    assert "path" in response_text, "Response should contain bundle path"
    assert "kubeconfig_path" in response_text, "Response should contain kubeconfig path"


@pytest.mark.asyncio
async def test_initialize_bundle_function_force_flag(bundle_storage_dir):
    """
    Test initialize_bundle function force flag behavior (NOT via MCP protocol).

    This test verifies the function:
    1. Can initialize bundle normally (force=False)
    2. Can reinitialize same bundle with force=True
    3. Both operations return success responses

    NOTE: This is a direct function call test, not MCP protocol testing.
    """
    test_bundle = get_test_bundle_path()

    # First initialization
    args1 = InitializeBundleArgs(source=str(test_bundle), force=False)
    result1 = await initialize_bundle(args1)

    assert len(result1) > 0, "First initialization should succeed"
    response1_text = result1[0].text
    assert "Bundle initialized" in response1_text, "First initialization should report success"

    # Second initialization with force=True should also work
    args2 = InitializeBundleArgs(source=str(test_bundle), force=True)
    result2 = await initialize_bundle(args2)

    assert len(result2) > 0, "Second initialization with force should succeed"
    response2_text = result2[0].text
    assert "Bundle initialized" in response2_text, "Second initialization should report success"


@pytest.mark.asyncio
async def test_initialize_bundle_validation_nonexistent_file(bundle_storage_dir):
    """
    Test Pydantic validation for initialize_bundle arguments.

    This test verifies:
    1. Pydantic model validation catches nonexistent files
    2. ValidationError is raised with appropriate message

    NOTE: This tests Pydantic validation, not MCP functionality.
    This could be moved to unit tests as it's testing the framework.
    """
    from pydantic_core import ValidationError

    # Try to create InitializeBundleArgs with nonexistent file (tests Pydantic validation)
    nonexistent_path = "/tmp/definitely-does-not-exist.tar.gz"

    # Should raise ValidationError due to file not existing
    with pytest.raises(ValidationError) as exc_info:
        InitializeBundleArgs(source=nonexistent_path, force=False)

    # Verify the error message indicates the file wasn't found
    error_msg = str(exc_info.value)
    assert "Bundle source not found" in error_msg, "Should indicate bundle source not found"


@pytest.mark.asyncio
async def test_list_files_function_with_bundle(bundle_storage_dir):
    """
    Test list_files function after bundle initialization (NOT via MCP protocol).

    This test verifies the function:
    1. Works correctly after bundle is initialized
    2. Returns proper file listing in expected format
    3. Response contains JSON data and operation description

    NOTE: This is a direct function call test, not MCP protocol testing.
    """
    test_bundle = get_test_bundle_path()

    # Initialize bundle first by calling function directly
    init_args = InitializeBundleArgs(source=str(test_bundle), force=True)
    init_result = await initialize_bundle(init_args)

    assert len(init_result) > 0, "Bundle initialization should succeed"

    # Try to list files from root
    list_args = ListFilesArgs(path="/", recursive=False)
    list_result = await list_files(list_args)

    assert len(list_result) > 0, "List files should return results"

    # Verify the response structure
    content_item = list_result[0]
    assert content_item.type == "text", "Content should be text type"
    response_text = content_item.text

    # Should contain file listing information
    assert "```json" in response_text, "Response should contain JSON data"
    assert "Listed files in" in response_text, "Response should indicate listing operation"


@pytest.mark.asyncio
async def test_pydantic_validation_invalid_parameters(bundle_storage_dir):
    """
    Test Pydantic validation for list_files arguments.

    This test verifies Pydantic model validation catches invalid parameters
    like directory traversal attempts.

    NOTE: This tests Pydantic validation, not our business logic.
    Could be moved to unit tests or removed as framework testing.
    """
    from pydantic_core import ValidationError

    # Test with invalid path containing directory traversal
    with pytest.raises(ValidationError) as exc_info:
        ListFilesArgs(path="../invalid", recursive=False)

    # Verify the error indicates path validation failure
    error_msg = str(exc_info.value)
    assert "Path cannot contain directory traversal" in error_msg, (
        "Should indicate path validation error"
    )


@pytest.mark.asyncio
async def test_kubectl_function_execution(bundle_storage_dir):
    """
    Test kubectl function execution (NOT via MCP protocol).

    This test verifies the function:
    1. Can execute kubectl commands after bundle initialization
    2. Returns proper response format (TextContent)
    3. Handles API server unavailability gracefully
    4. Response indicates command execution status

    NOTE: This is a direct function call test, not MCP protocol testing.
    Despite the previous name, this does NOT test "through MCP".
    """
    test_bundle = get_test_bundle_path()

    # Initialize bundle first by calling function directly
    init_args = InitializeBundleArgs(source=str(test_bundle), force=True)
    init_result = await initialize_bundle(init_args)

    assert len(init_result) > 0, "Bundle initialization should succeed"

    # Try a simple kubectl command via direct function call
    kubectl_args = KubectlCommandArgs(command="get pods", timeout=10, json_output=True)
    kubectl_result = await kubectl(kubectl_args)

    assert len(kubectl_result) > 0, "kubectl should return results"

    # Verify the response structure
    content_item = kubectl_result[0]
    assert content_item.type == "text", "Content should be text type"
    response_text = content_item.text

    # The response should either show kubectl output or indicate API server unavailability
    assert (
        "kubectl get pods" in response_text
        or "kubectl command executed successfully" in response_text
        or "API server is not available" in response_text
        or "connection refused" in response_text.lower()
    ), "Response should indicate kubectl execution attempt or success"


@pytest.mark.asyncio
async def test_read_file_function_execution(bundle_storage_dir):
    """
    Test read_file function execution (NOT via MCP protocol).

    This test verifies the function:
    1. Can read files after bundle initialization
    2. Returns proper response format (TextContent)
    3. Handles missing files gracefully
    4. Response contains file content or appropriate error message

    NOTE: This is a direct function call test, not MCP protocol testing.
    Despite the previous name, this does NOT test "through MCP".
    """
    test_bundle = get_test_bundle_path()

    # Initialize bundle first by calling function directly
    init_args = InitializeBundleArgs(source=str(test_bundle), force=True)
    init_result = await initialize_bundle(init_args)

    assert len(init_result) > 0, "Bundle initialization should succeed"

    # Try to read a common file via direct function call
    read_args = ReadFileArgs(path="cluster-info/version.json", start_line=0, num_lines=10)

    try:
        read_result = await read_file(read_args)

        assert len(read_result) > 0, "read_file should return results"

        # Verify the response structure
        content_item = read_result[0]
        assert content_item.type == "text", "Content should be text type"
        response_text = content_item.text

        # Should contain file content with line numbers or indicate file not found
        assert (
            "Line" in response_text
            or "not found" in response_text.lower()
            or "does not exist" in response_text.lower()
        ), "Response should show file content or indicate file not found"

    except Exception as e:
        # It's OK if the specific file doesn't exist, we're testing the function integration
        assert "not found" in str(e).lower() or "does not exist" in str(e).lower()


@pytest.mark.asyncio
async def test_grep_files_function_execution(bundle_storage_dir):
    """
    Test grep_files function execution (NOT via MCP protocol).

    This test verifies the function:
    1. Can search files after bundle initialization
    2. Returns proper response format (TextContent)
    3. Handles search patterns correctly
    4. Response contains search results or appropriate messages

    NOTE: This is a direct function call test, not MCP protocol testing.
    Despite the previous name, this does NOT test "through MCP".
    """
    test_bundle = get_test_bundle_path()

    # Initialize bundle first by calling function directly
    init_args = InitializeBundleArgs(source=str(test_bundle), force=True)
    init_result = await initialize_bundle(init_args)

    assert len(init_result) > 0, "Bundle initialization should succeed"

    # Search for common pattern via direct function call
    grep_args = GrepFilesArgs(
        pattern="version",
        path="/",
        file_pattern="*.json",
        case_sensitive=False,
        recursive=True,
    )

    grep_result = await grep_files(grep_args)

    assert len(grep_result) > 0, "grep_files should return results"

    # Verify the response structure
    content_item = grep_result[0]
    assert content_item.type == "text", "Content should be text type"
    response_text = content_item.text

    # Should contain search results or indicate no matches found
    assert (
        "Found" in response_text
        or "matches" in response_text.lower()
        or "No matches found" in response_text
        or "Search completed" in response_text
    ), "Response should indicate search results"


@pytest.mark.asyncio
async def test_file_operation_error_handling(bundle_storage_dir):
    """
    Test error handling for file operation functions (NOT via MCP protocol).

    This test verifies the functions:
    1. Handle missing bundle initialization gracefully
    2. Return informative error messages in proper format
    3. Don't crash when called without proper setup

    NOTE: This is a direct function call test, not MCP protocol testing.
    """
    # Try file operations without initializing bundle first

    # Test read_file without bundle
    read_args = ReadFileArgs(path="nonexistent.txt", start_line=0, num_lines=10)
    read_result = await read_file(read_args)

    assert len(read_result) > 0, "Should return error response"
    content_item = read_result[0]
    assert content_item.type == "text", "Content should be text type"

    # Should indicate bundle not initialized or file not found
    response_text = content_item.text
    assert (
        "not initialized" in response_text.lower()
        or "not found" in response_text.lower()
        or "error" in response_text.lower()
    ), "Should indicate error condition"
