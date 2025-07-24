#!/usr/bin/env python3
"""
Debug MCP server by running it manually and testing tool calls.
"""

import asyncio
import tempfile
import json
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from tests.integration.mcp_test_utils import get_test_bundle_path


async def debug_mcp_server():
    """Debug MCP server by starting it manually."""
    print("=== Debug MCP Server ===")

    # Get test bundle
    test_bundle_path = get_test_bundle_path()
    print(f"Test bundle: {test_bundle_path}")

    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_bundle_dir = Path(temp_dir)
        print(f"Temp directory: {temp_bundle_dir}")

        # Copy bundle to temp directory
        bundle_name = test_bundle_path.name
        test_bundle_copy = temp_bundle_dir / bundle_name
        test_bundle_copy.write_bytes(test_bundle_path.read_bytes())
        print(f"Copied bundle to: {test_bundle_copy}")

        # Set up environment
        env = os.environ.copy()
        env.update({"SBCTL_TOKEN": "test-token-12345", "MCP_BUNDLE_STORAGE": str(temp_bundle_dir)})

        print("\\n=== Starting MCP Server Manually ===")

        # Start MCP server process
        cmd = [sys.executable, "-m", "mcp_server_troubleshoot"]
        print(f"Command: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        print(f"Process started with PID: {process.pid}")

        try:
            # Send initialize request
            print("\\n=== Step 1: Initialize MCP ===")
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "debug-client", "version": "1.0.0"},
                },
            }

            # Send and get response
            await send_request_debug(process, init_request)

            # Send tool call request
            print("\\n=== Step 2: Call initialize_bundle tool ===")
            tool_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "initialize_bundle",
                    "arguments": {"source": str(test_bundle_copy)},
                },
            }

            await send_request_debug(process, tool_request, timeout=15.0)

        finally:
            print("\\n=== Cleanup ===")
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            print("✅ Process terminated")


async def send_request_debug(process, request, timeout=5.0):
    """Send a request and debug the response."""
    request_json = json.dumps(request)
    print(f"Sending: {request_json[:100]}...")

    if process.stdin:
        process.stdin.write((request_json + "\\n").encode())
        await process.stdin.drain()
        print("✅ Request sent")

    # Try to get response
    try:
        if process.stdout:
            response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=timeout)
            response_line = response_bytes.decode().strip()
            print(f"Response: {response_line[:200]}...")

            # Parse response
            try:
                response = json.loads(response_line)
                if "error" in response:
                    print(f"❌ Error: {response['error']}")
                else:
                    print("✅ Success")
                    if "result" in response:
                        result = response["result"]
                        if isinstance(result, dict) and "content" in result:
                            for content in result["content"]:
                                print(f"Content: {content.get('text', '')[:100]}...")
                return response
            except json.JSONDecodeError as e:
                print(f"❌ Invalid JSON: {e}")
                return None
    except asyncio.TimeoutError:
        print(f"❌ Timeout after {timeout}s")

        # Check if process is still alive
        if process.returncode is not None:
            print(f"Process died with code: {process.returncode}")

            # Read stderr
            if process.stderr:
                stderr_data = await process.stderr.read()
                if stderr_data:
                    print(f"STDERR: {stderr_data.decode()}")
        else:
            print("Process is still running")

        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


if __name__ == "__main__":
    asyncio.run(debug_mcp_server())
