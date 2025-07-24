#!/usr/bin/env python3
"""
Test the initialize_bundle tool call via MCP protocol to see why it hangs.
"""

import asyncio
import json
import sys
import tempfile
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from tests.integration.mcp_test_utils import get_test_bundle_path


async def test_initialize_bundle_tool():
    """Test the initialize_bundle tool call specifically."""
    print("=== Testing initialize_bundle Tool Call ===")

    # Get test bundle path
    test_bundle_path = get_test_bundle_path()
    print(f"Using test bundle: {test_bundle_path}")

    # Set up environment
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env.update({"MCP_BUNDLE_STORAGE": temp_dir, "SBCTL_TOKEN": "test-token-12345"})

        # Copy test bundle to temp dir
        bundle_name = test_bundle_path.name
        test_bundle_copy = Path(temp_dir) / bundle_name
        test_bundle_copy.write_bytes(test_bundle_path.read_bytes())
        print(f"Copied test bundle to: {test_bundle_copy}")

        # Start server via module
        cmd = [sys.executable, "-m", "mcp_server_troubleshoot"]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        print(f"Server started with PID: {process.pid}")

        try:
            # Wait for server initialization
            await asyncio.sleep(3.0)

            # Check if server is still running
            if process.returncode is not None:
                print(f"❌ Server exited early with code: {process.returncode}")
                return

            # Send initialize request first
            init_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            }

            print("Sending initialize request...")
            if process.stdin:
                process.stdin.write((json.dumps(init_request) + "\n").encode())
                await process.stdin.drain()

            # Read initialize response
            if process.stdout:
                init_response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=5.0)
                init_response = init_response_bytes.decode().strip()
                print(f"Initialize response: {init_response}")

            # Now send the initialize_bundle tool call
            bundle_request = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "initialize_bundle",
                    "arguments": {"source": str(test_bundle_copy)},
                },
            }

            request_json = json.dumps(bundle_request)
            print(f"Sending initialize_bundle tool call: {request_json}")

            if process.stdin:
                process.stdin.write((request_json + "\n").encode())
                await process.stdin.drain()
                print("Tool call sent successfully")

            # Try to read response with timeout
            try:
                if process.stdout:
                    print("Waiting for tool response (this should complete in ~6 seconds)...")
                    response_bytes = await asyncio.wait_for(
                        process.stdout.readline(), timeout=30.0  # Generous timeout
                    )
                    response_line = response_bytes.decode().strip()
                    print(f"✅ Tool response: {response_line}")

                    if response_line:
                        try:
                            response_data = json.loads(response_line)
                            print("✅ Valid JSON tool response received")
                            if "result" in response_data:
                                print(f"Tool result: {response_data['result']}")
                            elif "error" in response_data:
                                print(f"Tool error: {response_data['error']}")
                        except json.JSONDecodeError:
                            print(f"❌ Invalid JSON: {response_line}")

            except asyncio.TimeoutError:
                print("❌ Tool call timed out!")

                # Check stderr for errors
                if process.stderr:
                    try:
                        stderr_data = await asyncio.wait_for(process.stderr.read(4096), timeout=1.0)
                        if stderr_data:
                            stderr_text = stderr_data.decode()
                            print(f"STDERR during timeout: {stderr_text}")
                    except asyncio.TimeoutError:
                        print("No stderr output available")

        finally:
            # Terminate server
            if process.returncode is None:
                print("Terminating server...")
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    print("Server didn't terminate gracefully, killing...")
                    process.kill()
                    await process.wait()

            print(f"Server terminated with code: {process.returncode}")


if __name__ == "__main__":
    asyncio.run(test_initialize_bundle_tool())
