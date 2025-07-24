#!/usr/bin/env python3
"""
Test with minimal MCP server to isolate initialization issues.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent


# Create a minimal MCP server without the complex lifespan
minimal_mcp = FastMCP("test-mcp-server")


@minimal_mcp.tool()
async def test_tool() -> list[TextContent]:
    """A simple test tool."""
    return [TextContent(type="text", text="Hello from test tool!")]


async def test_minimal_mcp():
    """Test minimal MCP server."""
    print("=== Testing Minimal MCP Server ===")

    # Try to start the server manually
    cmd = [
        sys.executable,
        "-c",
        """
import asyncio
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

# Create minimal server
mcp = FastMCP("minimal-test")

@mcp.tool()
async def hello() -> list[TextContent]:
    return [TextContent(type="text", text="Hello!")]

# Run the server
mcp.run()
""",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    print(f"Minimal server started: PID {process.pid}")

    try:
        # Send initialization request
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "test", "version": "1.0.0"},
            },
        }

        import json

        request_json = json.dumps(init_request)
        print(f"Sending: {request_json}")

        if process.stdin:
            process.stdin.write((request_json + "\\n").encode())
            await process.stdin.drain()

        # Try to get response
        try:
            if process.stdout:
                response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
                response_line = response_bytes.decode().strip()
                print(f"Response: {response_line}")

                # Parse response
                response = json.loads(response_line)
                if "error" not in response:
                    print("✅ Minimal MCP server responds correctly!")
                else:
                    print(f"❌ Error: {response['error']}")
        except asyncio.TimeoutError:
            print("❌ Minimal server also doesn't respond")

            # Check stderr
            if process.stderr:
                stderr_data = await process.stderr.read(1024)
                if stderr_data:
                    print(f"STDERR: {stderr_data.decode()}")

    finally:
        process.terminate()
        await process.wait()
        print("Process terminated")


if __name__ == "__main__":
    asyncio.run(test_minimal_mcp())
