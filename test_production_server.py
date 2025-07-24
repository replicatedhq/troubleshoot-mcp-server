#!/usr/bin/env python3
"""
Test the production MCP server to see where it hangs.
"""

import asyncio
import json
import sys
import tempfile
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_production_server():
    """Test the production MCP server."""
    print("=== Testing Production MCP Server ===")

    # Set up environment
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env.update({"MCP_BUNDLE_STORAGE": temp_dir, "SBCTL_TOKEN": "test-token-12345"})

        # Start the production server
        cmd = [sys.executable, __file__, "server"]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        print(f"Production server started with PID: {process.pid}")

        # Give server more time to initialize (production server is more complex)
        print("Waiting 3 seconds for server initialization...")
        await asyncio.sleep(3.0)

        # Check if server is still running
        if process.returncode is not None:
            print(f"❌ Server exited early with code: {process.returncode}")
            if process.stderr:
                stderr_data = await process.stderr.read()
                print(f"STDERR: {stderr_data.decode()}")
            return

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

        # Try to read response with longer timeout for production
        try:
            if process.stdout:
                print("Waiting for response...")
                response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=10.0)
                response_line = response_bytes.decode().strip()
                print(f"✅ Response: {response_line}")

                # Parse and validate response
                if response_line:
                    try:
                        response_data = json.loads(response_line)
                        print("✅ Valid JSON response received")
                        print(
                            f"Server info: {response_data.get('result', {}).get('serverInfo', {})}"
                        )
                    except json.JSONDecodeError:
                        print(f"❌ Invalid JSON response: {response_line}")
                else:
                    print("❌ Empty response received")

        except asyncio.TimeoutError:
            print("❌ No response received within 10 second timeout")

            # Check stderr for errors
            if process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(process.stderr.read(4096), timeout=1.0)
                    if stderr_data:
                        stderr_text = stderr_data.decode()
                        print(f"STDERR during timeout: {stderr_text}")
                except asyncio.TimeoutError:
                    print("No stderr output available")

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


def run_production_server():
    """Run the actual production MCP server."""
    print("Starting Production MCP Server...")

    try:
        # Import the production server
        from src.mcp_server_troubleshoot.server import mcp

        print("Running production server...")
        mcp.run()
    except Exception as e:
        print(f"Production server error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "server":
        run_production_server()
    else:
        asyncio.run(test_production_server())
