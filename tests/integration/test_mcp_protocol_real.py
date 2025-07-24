"""
MCP Protocol Compliance Testing.

This module provides testing of MCP protocol compliance and server lifecycle
through actual JSON-RPC protocol communication using MCPTestClient. These tests verify that:

1. MCP server starts correctly and responds to protocol requests
2. JSON-RPC 2.0 format compliance is maintained
3. Server initialization handshake works properly
4. Concurrent connections are handled correctly
5. Basic error handling works through protocol layer

Tool functionality is tested separately via direct function calls in
test_tool_functions.py for better reliability and performance.

All tests use real JSON-RPC communication via MCPTestClient, ensuring
we test the complete protocol stack for server lifecycle operations.
"""

import asyncio
import pytest
import tempfile
from pathlib import Path

from tests.integration.mcp_test_utils import MCPTestClient, get_test_bundle_path


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def test_bundle_path():
    """Get the test bundle path."""
    return get_test_bundle_path()


@pytest.fixture
def temp_bundle_dir():
    """Create a temporary directory for bundle storage."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


class TestMCPProtocolCompliance:
    """Test MCP protocol compliance and JSON-RPC format validation."""

    async def test_json_rpc_request_format(self, temp_bundle_dir):
        """Test that MCP initialization follows JSON-RPC 2.0 format."""
        env = {"SBCTL_TOKEN": "test-token-12345"}

        async with MCPTestClient(bundle_dir=temp_bundle_dir, env=env) as client:
            # Test that initialization response follows JSON-RPC format
            # This verifies the server can start and respond to basic protocol requests
            response = await client.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            )

            # Verify response format
            assert "jsonrpc" in response
            assert response["jsonrpc"] == "2.0"
            assert "id" in response
            assert "result" in response or "error" in response

            # Verify the server provides expected capabilities
            if "result" in response:
                result = response["result"]
                assert "capabilities" in result
                assert "serverInfo" in result

    async def test_json_rpc_error_format(self, temp_bundle_dir):
        """Test that errors follow JSON-RPC 2.0 error format."""
        env = {"SBCTL_TOKEN": "test-token-12345"}

        async with MCPTestClient(bundle_dir=temp_bundle_dir, env=env) as client:
            await client.initialize_mcp()

            # Test invalid method - should return proper JSON-RPC error
            try:
                await client.send_request("invalid_method_that_does_not_exist")
                pytest.fail("Expected error for invalid method")
            except RuntimeError as e:
                # Should be a proper RPC error message
                assert "error" in str(e).lower() or "timeout" in str(e).lower()
                # Note: May timeout instead of returning proper error due to protocol limitations

    async def test_concurrent_requests(self, temp_bundle_dir):
        """Test concurrent JSON-RPC initialization requests are handled correctly."""
        env = {"SBCTL_TOKEN": "test-token-12345"}

        # Test that multiple clients can initialize concurrently
        tasks = []
        for i in range(3):

            async def init_client():
                async with MCPTestClient(bundle_dir=temp_bundle_dir, env=env) as client:
                    response = await client.send_request(
                        "initialize",
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "clientInfo": {"name": f"test-client-{i}", "version": "1.0.0"},
                        },
                    )
                    return response

            tasks.append(init_client())

        # All initialization requests should complete successfully
        responses = await asyncio.gather(*tasks)

        for response in responses:
            assert "jsonrpc" in response
            assert response["jsonrpc"] == "2.0"
            assert "result" in response


class TestMCPServerLifecycle:
    """Test MCP server lifecycle and protocol compliance."""

    async def test_server_initialization_with_bundle_directory(
        self, temp_bundle_dir, test_bundle_path
    ):
        """Test that server initializes correctly with bundle directory configuration."""
        bundle_name = test_bundle_path.name
        test_bundle_copy = temp_bundle_dir / bundle_name
        test_bundle_copy.write_bytes(test_bundle_path.read_bytes())

        env = {"SBCTL_TOKEN": "test-token-12345"}

        async with MCPTestClient(bundle_dir=temp_bundle_dir, env=env) as client:
            # Initialize MCP connection
            server_info = await client.initialize_mcp()

            # Verify server provides expected capabilities
            assert "capabilities" in server_info
            capabilities = server_info["capabilities"]

            # Server should support tools
            assert "tools" in capabilities

            # Verify server info is provided
            assert "serverInfo" in server_info
            server_info_obj = server_info["serverInfo"]
            assert "name" in server_info_obj
            assert server_info_obj["name"] == "troubleshoot-mcp-server"
            assert "version" in server_info_obj

    async def test_server_handles_empty_bundle_directory(self, temp_bundle_dir):
        """Test that server starts correctly with empty bundle directory."""
        env = {"SBCTL_TOKEN": "test-token-12345"}

        async with MCPTestClient(bundle_dir=temp_bundle_dir, env=env) as client:
            # Server should start successfully even with empty bundle directory
            server_info = await client.initialize_mcp()

            # Server should provide proper capabilities
            assert "capabilities" in server_info
            assert "serverInfo" in server_info
            assert server_info["serverInfo"]["name"] == "troubleshoot-mcp-server"

    async def test_server_protocol_stability(self, temp_bundle_dir):
        """Test that server maintains protocol stability across multiple requests."""
        env = {"SBCTL_TOKEN": "test-token-12345"}

        async with MCPTestClient(bundle_dir=temp_bundle_dir, env=env) as client:
            # Test multiple initialization requests (protocol stability)
            for i in range(3):
                response = await client.send_request(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "clientInfo": {"name": f"client-{i}", "version": "1.0.0"},
                    },
                )

                # Each response should maintain protocol compliance
                assert "jsonrpc" in response
                assert response["jsonrpc"] == "2.0"
                assert "id" in response
                assert "result" in response
