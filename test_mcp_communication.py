#!/usr/bin/env python3
"""
Test MCP communication to isolate the hanging issue.
"""

import asyncio
import json
import logging
import sys
import tempfile
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

# Set up minimal logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


async def test_mcp_communication():
    """Test communication with the MCP server."""
    print("=== Testing MCP Communication ===")

    # Set up environment
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env.update({"MCP_BUNDLE_STORAGE": temp_dir, "SBCTL_TOKEN": "test-token-12345"})

        # Start the server
        cmd = [sys.executable, __file__, "server"]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        print(f"Server started with PID: {process.pid}")

        # Give server time to initialize
        await asyncio.sleep(1.0)

        # Send initialize request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }

        request_json = json.dumps(request)
        print(f"Sending: {request_json}")

        if process.stdin:
            process.stdin.write((request_json + "\n").encode())
            await process.stdin.drain()
            print("Request sent successfully")

        # Try to read response with timeout
        try:
            if process.stdout:
                response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
                response_line = response_bytes.decode().strip()
                print(f"Response: {response_line}")

                # Parse and validate response
                if response_line:
                    try:
                        response_data = json.loads(response_line)
                        print(f"Valid JSON response: {response_data}")
                    except json.JSONDecodeError:
                        print(f"Invalid JSON response: {response_line}")
                else:
                    print("Empty response received")

        except asyncio.TimeoutError:
            print("❌ No response received within timeout")

            # Check stderr for errors
            if process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(process.stderr.read(4096), timeout=1.0)
                    if stderr_data:
                        stderr_text = stderr_data.decode()
                        print(f"STDERR: {stderr_text}")
                except asyncio.TimeoutError:
                    print("No stderr output")

        # Terminate server
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        print(f"Server terminated with code: {process.returncode}")


def run_server():
    """Run the actual MCP server."""
    print("Starting MCP Server")

    # Import here to avoid issues with module loading
    from debug_fastmcp_lifecycle import debug_mcp

    try:
        debug_mcp.run()
    except Exception as e:
        print(f"Server error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        run_server()
    else:
        asyncio.run(test_mcp_communication())
