"""
Functional tests demonstrating parallel MCP operations using multiple client instances.

These tests show how to achieve true parallelism by using multiple MCPTestClient
instances, each with their own server subprocess and stdio transport.
"""

import asyncio
import time
from typing import List

import pytest

from tests.integration.mcp_test_utils import MCPTestClient, get_test_bundle_path


@pytest.mark.functional
@pytest.mark.asyncio
async def test_parallel_bundle_initialization() -> None:
    """Test parallel bundle initialization using multiple client instances."""
    num_clients = 3
    clients: List[MCPTestClient] = []
    test_bundle_source = get_test_bundle_path()

    try:
        # Create and start multiple clients in parallel
        start_time = time.time()

        # Start all clients concurrently
        client_tasks = []
        for i in range(num_clients):

            async def create_client(client_id: int) -> MCPTestClient:
                client = MCPTestClient()
                await client.start_server(timeout=15.0)
                await client.initialize_mcp(
                    {"name": f"parallel-test-client-{client_id}", "version": "1.0.0"}
                )
                await client.send_notification("notifications/initialized")
                return client

            client_tasks.append(create_client(i))

        clients = await asyncio.gather(*client_tasks)

        # Now run bundle initialization in parallel across all clients
        init_tasks = []
        for i, client in enumerate(clients):
            task = client.call_tool(
                "initialize_bundle",
                {
                    "source": str(test_bundle_source),
                    "force": True,  # Force fresh initialization for each
                    "verbosity": "minimal",
                },
            )
            init_tasks.append((i, task))

        # Execute bundle initialization in parallel
        results = await asyncio.gather(*[task for _, task in init_tasks])

        end_time = time.time()
        parallel_duration = end_time - start_time

        # Verify all initializations succeeded
        for i, result in enumerate(results):
            assert len(result) == 1, f"Client {i} returned invalid result"
            response_text = result[0]["text"]

            # Check for success in either format
            assert "Bundle initialized successfully" in response_text or (
                '"bundle_id":' in response_text and '"status": "ready"' in response_text
            ), f"Client {i} initialization failed: {response_text}"

        print(
            f"Parallel bundle initialization ({num_clients} clients) took {parallel_duration:.2f}s"
        )

        # Test that all clients can now use their bundles independently
        kubectl_tasks = []
        for i, client in enumerate(clients):
            task = client.call_tool(
                "kubectl", {"command": "version --client", "timeout": 15, "verbosity": "minimal"}
            )
            kubectl_tasks.append((i, task))

        kubectl_results = await asyncio.gather(*[task for _, task in kubectl_tasks])

        # Verify all kubectl commands worked
        for i, result in enumerate(kubectl_results):
            assert len(result) == 1, f"Client {i} kubectl failed"
            kubectl_text = result[0]["text"]
            assert "no bundle" not in kubectl_text.lower(), (
                f"Client {i} lost bundle state: {kubectl_text}"
            )

    finally:
        # Clean up all clients
        cleanup_tasks = []
        for client in clients:
            cleanup_tasks.append(client.cleanup())

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks)


@pytest.mark.functional
@pytest.mark.asyncio
async def test_parallel_tool_discovery_performance() -> None:
    """Test tool discovery performance with multiple parallel clients."""
    num_clients = 5
    clients: List[MCPTestClient] = []

    try:
        # Create multiple clients
        client_tasks = []
        for i in range(num_clients):

            async def create_client(client_id: int) -> MCPTestClient:
                client = MCPTestClient()
                await client.start_server(timeout=15.0)
                await client.initialize_mcp(
                    {"name": f"discovery-test-client-{client_id}", "version": "1.0.0"}
                )
                await client.send_notification("notifications/initialized")
                return client

            client_tasks.append(create_client(i))

        clients = await asyncio.gather(*client_tasks)

        # Measure parallel tool discovery performance
        start_time = time.time()

        discovery_tasks = []
        for i, client in enumerate(clients):
            task = client.send_request("tools/list")
            discovery_tasks.append((i, task))

        # Execute tool discovery in parallel
        responses = await asyncio.gather(*[task for _, task in discovery_tasks])

        end_time = time.time()
        parallel_duration = end_time - start_time

        # Verify all discoveries succeeded and are consistent
        expected_tools = {"initialize_bundle", "kubectl", "list_files", "read_file", "grep_files"}

        for i, response in enumerate(responses):
            assert "result" in response, f"Client {i} missing result"
            tools = response["result"].get("tools", [])
            assert len(tools) > 0, f"Client {i} returned no tools"

            tool_names = {tool["name"] for tool in tools}
            missing_tools = expected_tools - tool_names
            assert not missing_tools, f"Client {i} missing tools: {missing_tools}"

        print(f"Parallel tool discovery ({num_clients} clients) took {parallel_duration:.2f}s")

        # Compare with sequential baseline
        start_time = time.time()
        for client in clients[:2]:  # Test 2 clients sequentially
            await client.send_request("tools/list")
        sequential_duration = end_time - start_time

        print(f"Sequential tool discovery (2 clients) would take ~{sequential_duration:.2f}s")

    finally:
        # Clean up all clients
        cleanup_tasks = []
        for client in clients:
            cleanup_tasks.append(client.cleanup())

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks)


@pytest.mark.functional
@pytest.mark.asyncio
async def test_parallel_mixed_operations() -> None:
    """Test different tools in parallel using separate client instances."""
    clients: List[MCPTestClient] = []
    test_bundle_source = get_test_bundle_path()

    try:
        # Create 3 clients for different operations
        client_tasks = []
        for i in range(3):

            async def create_client(client_id: int) -> MCPTestClient:
                client = MCPTestClient()
                await client.start_server(timeout=15.0)
                await client.initialize_mcp(
                    {"name": f"mixed-test-client-{client_id}", "version": "1.0.0"}
                )
                await client.send_notification("notifications/initialized")
                return client

            client_tasks.append(create_client(i))

        clients = await asyncio.gather(*client_tasks)

        # Initialize bundles in parallel on all clients
        init_tasks = []
        for client in clients:
            task = client.call_tool(
                "initialize_bundle",
                {"source": str(test_bundle_source), "force": True, "verbosity": "minimal"},
            )
            init_tasks.append(task)

        init_results = await asyncio.gather(*init_tasks)

        # Verify all initializations succeeded
        for i, result in enumerate(init_results):
            response_text = result[0]["text"]
            assert "Bundle initialized successfully" in response_text or (
                '"bundle_id":' in response_text and '"status": "ready"' in response_text
            ), f"Client {i} bundle init failed: {response_text}"

        # Now run different operations in parallel
        operation_tasks = [
            (
                "kubectl",
                clients[0].call_tool(
                    "kubectl",
                    {"command": "version --client", "timeout": 15, "verbosity": "minimal"},
                ),
            ),
            (
                "list_files",
                clients[1].call_tool(
                    "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
                ),
            ),
            (
                "grep_files",
                clients[2].call_tool(
                    "grep_files",
                    {
                        "pattern": "version",
                        "path": ".",
                        "recursive": True,
                        "max_results": 5,
                        "verbosity": "minimal",
                    },
                ),
            ),
        ]

        # Execute different tools in parallel
        start_time = time.time()
        operation_results = await asyncio.gather(*[task for _, task in operation_tasks])
        end_time = time.time()

        parallel_duration = end_time - start_time
        print(f"Parallel mixed operations took {parallel_duration:.2f}s")

        # Verify all operations succeeded
        for i, (operation, result) in enumerate(
            zip([op for op, _ in operation_tasks], operation_results)
        ):
            assert len(result) == 1, f"{operation} returned invalid result"
            response_text = result[0]["text"]
            assert "no bundle" not in response_text.lower(), (
                f"{operation} failed with bundle error: {response_text}"
            )
            assert len(response_text) > 0, f"{operation} returned empty response"

    finally:
        # Clean up all clients
        cleanup_tasks = []
        for client in clients:
            cleanup_tasks.append(client.cleanup())

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks)


@pytest.mark.functional
@pytest.mark.asyncio
async def test_parallel_vs_sequential_performance_comparison() -> None:
    """Compare parallel vs sequential performance for bundle operations."""
    test_bundle_source = get_test_bundle_path()
    num_operations = 3

    # Test sequential performance (single client)
    sequential_client = MCPTestClient()
    try:
        await sequential_client.start_server(timeout=15.0)
        await sequential_client.initialize_mcp({"name": "sequential-client", "version": "1.0.0"})
        await sequential_client.send_notification("notifications/initialized")

        start_time = time.time()

        for i in range(num_operations):
            await sequential_client.call_tool(
                "initialize_bundle",
                {"source": str(test_bundle_source), "force": True, "verbosity": "minimal"},
            )

        sequential_duration = time.time() - start_time

    finally:
        await sequential_client.cleanup()

    # Test parallel performance (multiple clients)
    clients: List[MCPTestClient] = []
    try:
        # Create multiple clients
        client_tasks = []
        for i in range(num_operations):

            async def create_client(client_id: int) -> MCPTestClient:
                client = MCPTestClient()
                await client.start_server(timeout=15.0)
                await client.initialize_mcp(
                    {"name": f"parallel-client-{client_id}", "version": "1.0.0"}
                )
                await client.send_notification("notifications/initialized")
                return client

            client_tasks.append(create_client(i))

        clients = await asyncio.gather(*client_tasks)

        start_time = time.time()

        # Run bundle initialization in parallel
        parallel_tasks = []
        for client in clients:
            task = client.call_tool(
                "initialize_bundle",
                {"source": str(test_bundle_source), "force": True, "verbosity": "minimal"},
            )
            parallel_tasks.append(task)

        await asyncio.gather(*parallel_tasks)

        parallel_duration = time.time() - start_time

    finally:
        # Clean up all clients
        cleanup_tasks = []
        for client in clients:
            cleanup_tasks.append(client.cleanup())

        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks)

    # Compare performance
    print(f"Sequential ({num_operations} operations): {sequential_duration:.2f}s")
    print(f"Parallel ({num_operations} operations): {parallel_duration:.2f}s")

    speedup = sequential_duration / parallel_duration if parallel_duration > 0 else 1
    print(f"Speedup: {speedup:.2f}x")

    # Parallel should be faster for I/O bound operations like bundle initialization
    # Allow for some variance in timing
    assert parallel_duration < sequential_duration * 0.8, (
        f"Parallel execution ({parallel_duration:.2f}s) should be significantly faster "
        f"than sequential ({sequential_duration:.2f}s)"
    )
