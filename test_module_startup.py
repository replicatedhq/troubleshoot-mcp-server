#!/usr/bin/env python3
"""
Test the module startup vs direct import to isolate the issue.
"""

import asyncio
import json
import sys
import tempfile
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_module_startup():
    """Test starting the server via module."""
    print("=== Testing Module Startup ===")

    # Set up environment
    with tempfile.TemporaryDirectory() as temp_dir:
        env = os.environ.copy()
        env.update({"MCP_BUNDLE_STORAGE": temp_dir, "SBCTL_TOKEN": "test-token-12345"})

        # Start server via module (same as MCPTestClient)
        cmd = [sys.executable, "-m", "mcp_server_troubleshoot"]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        print(f"Module server started with PID: {process.pid}")

        # Give server time to initialize
        print("Waiting 5 seconds for server initialization...")
        await asyncio.sleep(5.0)

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

        # Try to read response
        try:
            if process.stdout:
                print("Waiting for response...")
                response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=10.0)
                response_line = response_bytes.decode().strip()
                print(f"✅ Response: {response_line}")

                if response_line:
                    try:
                        json.loads(response_line)
                        print("✅ Valid JSON response received")
                    except json.JSONDecodeError:
                        print(f"❌ Invalid JSON: {response_line}")

        except asyncio.TimeoutError:
            print("❌ No response received within timeout")

            # Check stderr for errors
            if process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(process.stderr.read(2048), timeout=1.0)
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


if __name__ == "__main__":
    asyncio.run(test_module_startup())
