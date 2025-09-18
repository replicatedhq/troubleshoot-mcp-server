"""
Functional tests for concurrent MCP protocol access.

Tests validate that multiple simultaneous tool calls work correctly
and don't interfere with each other.
"""

import asyncio
from pathlib import Path

import pytest

from tests.integration.mcp_test_utils import MCPTestClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_tool_discovery(mcp_protocol_client: MCPTestClient) -> None:
    """Test concurrent tool discovery requests."""
    # Launch multiple concurrent tool list requests
    num_requests = 10
    tasks = []

    for i in range(num_requests):
        task = mcp_protocol_client.send_request("tools/list")
        tasks.append(task)

    # Execute requests sequentially (stdio transport limitation)
    responses = []
    for task in tasks:
        response = await task
        responses.append(response)

    # Verify all responses are valid and consistent
    tool_names_sets = []
    for i, response in enumerate(responses):
        assert "result" in response, f"Response {i} missing result"
        tools = response["result"].get("tools", [])
        assert len(tools) > 0, f"Response {i} returned no tools"

        tool_names = {tool["name"] for tool in tools}
        tool_names_sets.append(tool_names)

    # All responses should have the same tool names
    first_set = tool_names_sets[0]
    for i, tool_set in enumerate(tool_names_sets[1:], 1):
        assert tool_set == first_set, (
            f"Response {i} has different tools than response 0: "
            f"diff={tool_set.symmetric_difference(first_set)}"
        )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_bundle_operations(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test concurrent bundle-related operations."""
    # First initialize a bundle
    init_result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )
    init_text = init_result[0]["text"]
    assert "Bundle initialized successfully" in init_text or (
        '"bundle_id":' in init_text and '"status": "ready"' in init_text
    )

    # Launch concurrent operations that use the bundle
    tasks = []

    # Multiple kubectl version checks (safe, read-only operations)
    for i in range(3):
        task = mcp_protocol_client.call_tool(
            "kubectl",
            {
                "command": "version --client",
                "timeout": 15,
                "json_output": False,
                "verbosity": "minimal",
            },
        )
        tasks.append(("kubectl", i, task))

    # Multiple file listings (safe, read-only operations)
    for i in range(3):
        task = mcp_protocol_client.call_tool(
            "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
        )
        tasks.append(("list_files", i, task))

    # Wait for all tasks to complete (sequentially due to stdio limitations)
    results = []
    for operation, index, task in tasks:
        result = await task
        results.append((operation, index, result))

    # Verify all operations completed successfully
    for operation, index, result in results:
        assert len(result) == 1, f"{operation}[{index}] returned invalid result count"
        response_text = result[0]["text"]

        # Should not have "no bundle" errors since bundle was initialized
        assert "no bundle" not in response_text.lower(), (
            f"{operation}[{index}] got 'no bundle' error: {response_text}"
        )

        # Should have valid responses
        assert len(response_text) > 0, f"{operation}[{index}] returned empty response"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_mixed_operations(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test concurrent mix of different operation types."""
    # Initialize bundle first
    init_result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )
    init_text = init_result[0]["text"]
    assert "Bundle initialized successfully" in init_text or (
        '"bundle_id":' in init_text and '"status": "ready"' in init_text
    )

    # Create a mix of different concurrent operations
    tasks = []

    # Tool discovery
    tasks.append(("tools/list", mcp_protocol_client.send_request("tools/list")))

    # Bundle operations
    tasks.append(
        (
            "kubectl",
            mcp_protocol_client.call_tool(
                "kubectl", {"command": "version --client", "timeout": 15, "verbosity": "minimal"}
            ),
        )
    )

    tasks.append(
        (
            "list_files",
            mcp_protocol_client.call_tool(
                "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
            ),
        )
    )

    # Another tool discovery
    tasks.append(("tools/list", mcp_protocol_client.send_request("tools/list")))

    # File operation
    tasks.append(
        (
            "grep_files",
            mcp_protocol_client.call_tool(
                "grep_files",
                {
                    "pattern": "version",
                    "path": ".",
                    "recursive": True,
                    "max_results": 10,
                    "verbosity": "minimal",
                },
            ),
        )
    )

    # Wait for all tasks
    results = []
    for operation, task in tasks:
        result = await task
        results.append((operation, result))

    # Verify all operations completed
    for operation, result in results:
        if operation == "tools/list":
            # Raw protocol response
            assert "result" in result, f"{operation} missing result"
            tools = result["result"].get("tools", [])
            assert len(tools) > 0, f"{operation} returned no tools"
        else:
            # Tool call response
            assert len(result) == 1, f"{operation} returned invalid result count"
            response_text = result[0]["text"]
            assert len(response_text) > 0, f"{operation} returned empty response"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_error_and_success_mix(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test concurrent operations mixing successful and error scenarios."""
    # Initialize bundle for some operations to succeed
    init_result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )
    init_text = init_result[0]["text"]
    assert "Bundle initialized successfully" in init_text or (
        '"bundle_id":' in init_text and '"status": "ready"' in init_text
    )

    # Mix of operations that should succeed and fail
    tasks = []

    # Should succeed - bundle is initialized
    tasks.append(
        (
            "success_kubectl",
            mcp_protocol_client.call_tool(
                "kubectl", {"command": "version --client", "timeout": 15, "verbosity": "minimal"}
            ),
        )
    )

    # Should succeed - bundle is initialized
    tasks.append(
        (
            "success_list",
            mcp_protocol_client.call_tool(
                "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
            ),
        )
    )

    # Should fail - invalid path
    tasks.append(
        (
            "fail_read",
            mcp_protocol_client.call_tool(
                "read_file", {"path": "/definitely/does/not/exist.txt", "verbosity": "minimal"}
            ),
        )
    )

    # Should succeed - tool discovery always works
    tasks.append(("success_tools", mcp_protocol_client.send_request("tools/list")))

    # Should succeed - re-initialize with same bundle (not actually expected to fail)
    tasks.append(
        (
            "reinit",
            mcp_protocol_client.call_tool(
                "initialize_bundle", {"source": str(test_bundle_source), "verbosity": "minimal"}
            ),
        )
    )

    # Wait for all tasks
    results = []
    for operation, task in tasks:
        result = await task
        results.append((operation, result))

    # Verify expected outcomes
    for operation, result in results:
        if operation.startswith("success_"):
            if operation == "success_tools":
                # Protocol response
                assert "result" in result, f"{operation} should succeed"
                tools = result["result"].get("tools", [])
                assert len(tools) > 0, f"{operation} should return tools"
            else:
                # Tool response
                assert len(result) == 1, f"{operation} should return response"
                response_text = result[0]["text"]
                assert "no bundle" not in response_text.lower(), (
                    f"{operation} should succeed: {response_text}"
                )

        elif operation == "reinit":
            # Tool response - should succeed (bundle reinitialization)
            assert len(result) == 1, f"{operation} should return response"
            response_text = result[0]["text"]
            assert "Bundle initialized successfully" in response_text or (
                '"bundle_id":' in response_text and '"status": "ready"' in response_text
            ), f"{operation} should succeed: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_high_concurrency_stress(mcp_protocol_client: MCPTestClient) -> None:
    """Test high concurrency stress with tool discovery using multiple clients."""
    import time
    from tests.integration.mcp_test_utils import MCPTestClient

    num_clients = 6
    clients = []

    try:
        # Create multiple clients for true parallel testing
        start_time = time.time()

        client_tasks = []
        for i in range(num_clients):

            async def create_client(client_id: int) -> MCPTestClient:
                client = MCPTestClient()
                await client.start_server(timeout=15.0)
                await client.initialize_mcp(
                    {"name": f"stress-test-client-{client_id}", "version": "1.0.0"}
                )
                await client.send_notification("notifications/initialized")
                return client

            client_tasks.append(create_client(i))

        clients = await asyncio.gather(*client_tasks)

        # Run tool discovery in parallel across all clients
        discovery_tasks = []
        for i, client in enumerate(clients):
            task = client.send_request("tools/list")
            discovery_tasks.append((i, task))

        responses = await asyncio.gather(*[task for _, task in discovery_tasks])

        end_time = time.time()
        duration = end_time - start_time

        print(f"High concurrency stress test ({num_clients} parallel clients) took {duration:.2f}s")

        # Verify all responses
        required_tools = {
            "initialize_bundle",
            "kubectl",
            "list_files",
            "read_file",
            "grep_files",
        }

        for i, response in enumerate(responses):
            assert "result" in response, f"Client {i} missing result"
            tools = response["result"].get("tools", [])
            assert len(tools) > 0, f"Client {i} returned no tools"

            tool_names = {tool["name"] for tool in tools}
            missing = required_tools - tool_names
            assert not missing, f"Client {i} missing tools: {missing}"

    finally:
        # Clean up all clients
        cleanup_tasks = []
        for client in clients:
            cleanup_tasks.append(client.cleanup())

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks)


@pytest.mark.functional
@pytest.mark.asyncio
async def test_concurrent_bundle_state_consistency(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test that bundle state remains consistent under concurrent access."""
    # Initialize bundle
    init_result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )
    init_text = init_result[0]["text"]
    assert "Bundle initialized successfully" in init_text or (
        '"bundle_id":' in init_text and '"status": "ready"' in init_text
    )

    # Launch multiple operations that depend on bundle state
    num_operations = 8
    tasks = []

    for i in range(num_operations):
        # Alternate between different bundle-dependent operations
        if i % 3 == 0:
            task = mcp_protocol_client.call_tool(
                "kubectl", {"command": "version --client", "timeout": 15, "verbosity": "minimal"}
            )
        elif i % 3 == 1:
            task = mcp_protocol_client.call_tool(
                "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
            )
        else:
            task = mcp_protocol_client.call_tool(
                "grep_files",
                {
                    "pattern": ".*",
                    "path": ".",
                    "recursive": False,
                    "max_results": 5,
                    "verbosity": "minimal",
                },
            )

        tasks.append((i, task))

    # Execute operations sequentially (stdio transport limitation)
    results = []
    for _, task in tasks:
        result = await task
        results.append(result)

    # Verify all operations saw consistent bundle state (i.e., no "no bundle" errors)
    for i, result in enumerate(results):
        assert len(result) == 1, f"Operation {i} returned invalid result count"
        response_text = result[0]["text"]

        assert "no bundle" not in response_text.lower(), (
            f"Operation {i} lost bundle state: {response_text}"
        )

        assert len(response_text) > 0, f"Operation {i} returned empty response"
