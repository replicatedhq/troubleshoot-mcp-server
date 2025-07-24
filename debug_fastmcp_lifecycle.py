#!/usr/bin/env python3
"""
Debug FastMCP lifecycle to find where initialization hangs.
"""

import asyncio
import logging
import sys
import tempfile
import os
import json
from pathlib import Path
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Set up verbose logging to catch everything
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)

# Enable all MCP-related logging
logging.getLogger("mcp").setLevel(logging.DEBUG)
logging.getLogger("fastmcp").setLevel(logging.DEBUG)
logging.getLogger("anyio").setLevel(logging.DEBUG)


@asynccontextmanager
async def debug_lifespan(server):
    """Minimal lifespan context to isolate the issue."""
    print("🔍 DEBUG: Lifespan started")

    # Set up environment
    with tempfile.TemporaryDirectory() as temp_dir:
        os.environ["MCP_BUNDLE_STORAGE"] = temp_dir
        os.environ["SBCTL_TOKEN"] = "test-token-12345"

        print(f"🔍 DEBUG: Environment set up, temp_dir: {temp_dir}")

        # Minimal context - just yield without complex initialization
        print("🔍 DEBUG: About to yield from lifespan")
        yield {"message": "Simple context"}
        print("🔍 DEBUG: Lifespan finished")


# Create minimal MCP server with debug lifespan
debug_mcp = FastMCP("debug-mcp-server", lifespan=debug_lifespan)


@debug_mcp.tool()
async def debug_tool() -> list[TextContent]:
    """Simple debug tool."""
    print("🔍 DEBUG: debug_tool called!")
    return [TextContent(type="text", text="Debug tool response")]


async def test_fastmcp_lifecycle():
    """Test FastMCP lifecycle step by step."""
    print("=== Debug FastMCP Lifecycle ===")

    # Start server in background task
    server_task = asyncio.create_task(run_server_async())

    # Give server time to start
    await asyncio.sleep(2.0)

    # Test if server is responsive
    await test_server_communication()

    # Cancel server task
    server_task.cancel()
    try:
        await server_task
    except asyncio.CancelledError:
        pass


async def run_server_async():
    """Run the server asynchronously."""
    print("🔍 DEBUG: Starting server in background task")

    # This should trigger the lifespan and show where it hangs
    try:
        debug_mcp.run()
    except Exception as e:
        print(f"🔍 DEBUG: Server error: {e}")
        import traceback

        traceback.print_exc()


async def test_server_communication():
    """Test communication with the running server."""
    print("🔍 DEBUG: Testing server communication")

    # Create a subprocess to communicate with the server
    cmd = [
        sys.executable,
        "-c",
        f"""
import sys
sys.path.insert(0, "{Path(__file__).parent}")
from debug_fastmcp_lifecycle import debug_mcp
debug_mcp.run()
""",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    print(f"🔍 DEBUG: Server process started: PID {process.pid}")

    # Send simple request
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "debug", "version": "1.0.0"},
        },
    }

    request_json = json.dumps(request)
    print(f"🔍 DEBUG: Sending request: {request_json}")

    if process.stdin:
        process.stdin.write((request_json + "\\n").encode())
        await process.stdin.drain()
        print("🔍 DEBUG: Request sent")

    # Try to get response with short timeout
    try:
        if process.stdout:
            response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
            response_line = response_bytes.decode().strip()
            print(f"🔍 DEBUG: Got response: {response_line}")
    except asyncio.TimeoutError:
        print("🔍 DEBUG: No response received")

        # Check stderr for clues
        if process.stderr:
            stderr_data = await process.stderr.read(2048)
            if stderr_data:
                stderr_text = stderr_data.decode()
                print(f"🔍 DEBUG: STDERR output:\\n{stderr_text}")

    # Terminate process
    process.terminate()
    await process.wait()
    print("🔍 DEBUG: Server process terminated")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        # Run just the server
        print("🔍 DEBUG: Running server directly")
        debug_mcp.run()
    else:
        # Run the debug test
        asyncio.run(test_fastmcp_lifecycle())
