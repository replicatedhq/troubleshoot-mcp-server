"""
Shared fixtures for functional MCP protocol tests.

These fixtures provide MCP client connections and test infrastructure for validating
that the server works correctly through the MCP protocol layer.
"""

import os
import tempfile
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

from tests.integration.mcp_test_utils import MCPTestClient, get_test_bundle_path


@pytest_asyncio.fixture
async def functional_bundle_dir() -> AsyncIterator[Path]:
    """Provide a temporary directory for bundle storage during functional tests."""
    with tempfile.TemporaryDirectory(prefix="mcp_functional_test_") as temp_dir:
        bundle_dir = Path(temp_dir)

        # Set environment variable for bundle storage
        os.environ["MCP_BUNDLE_STORAGE"] = str(bundle_dir)

        yield bundle_dir

        # Clean up environment
        if "MCP_BUNDLE_STORAGE" in os.environ:
            del os.environ["MCP_BUNDLE_STORAGE"]


@pytest_asyncio.fixture
async def test_bundle_source() -> Path:
    """Provide the test bundle source file."""
    return get_test_bundle_path()


@pytest_asyncio.fixture
async def mcp_protocol_client(functional_bundle_dir: Path) -> AsyncIterator[MCPTestClient]:
    """
    Provide MCP protocol client for functional testing.

    This fixture creates an actual MCP client that communicates with the server
    through the JSON-RPC protocol over stdio transport, validating full protocol
    compatibility.
    """
    # Set up environment for functional testing
    env = {
        "MCP_BUNDLE_STORAGE": str(functional_bundle_dir),
        "ENABLE_LIST_BUNDLES_TOOL": "false",  # Keep default behavior
    }

    # Create and initialize the MCP client
    client = MCPTestClient(bundle_dir=functional_bundle_dir, env=env)

    try:
        # Start the server and establish connection
        await client.start_server(timeout=15.0)

        # Initialize the MCP protocol
        await client.initialize_mcp({"name": "functional-test-client", "version": "1.0.0"})

        # Send initialized notification to complete handshake
        await client.send_notification("notifications/initialized")

        # Yield the connected client for tests
        yield client

    finally:
        # Ensure cleanup happens even if tests fail
        await client.cleanup()


@pytest_asyncio.fixture
async def initialized_test_bundle(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> str:
    """
    Initialize a test bundle through the MCP protocol and return its ID.

    This fixture provides a pre-initialized bundle for tests that need to work
    with bundle contents.
    """
    # Initialize bundle through MCP protocol
    result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )

    # Extract bundle ID from response
    response_text = result[0]["text"]
    # Parse the response to get bundle ID (assuming format includes bundle ID)
    # This is a simplified extraction - real implementation would be more robust
    bundle_id = "test_bundle"  # Default for now

    if "Bundle initialized successfully" in response_text:
        # Look for bundle ID in response
        lines = response_text.split("\n")
        for line in lines:
            if "Bundle ID:" in line:
                bundle_id = line.split("Bundle ID:")[1].strip()
                break
            elif "bundle:" in line.lower():
                # Alternative extraction method
                parts = line.split()
                if len(parts) > 1:
                    bundle_id = parts[-1]
                    break

    return bundle_id


@pytest.fixture(scope="session")
def performance_threshold() -> dict:
    """Performance thresholds for functional tests."""
    return {
        "tool_discovery_max_ms": 100,  # Tool discovery should complete within 100ms
        "tool_call_max_ms": 5000,  # Tool calls should complete within 5 seconds
        "bundle_init_max_ms": 30000,  # Bundle initialization within 30 seconds
    }


# Pytest markers for functional tests
pytestmark = [
    pytest.mark.functional,
    pytest.mark.asyncio,
]
