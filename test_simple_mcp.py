#!/usr/bin/env python3
"""
Very simple test to check if MCP server responds at all.
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_simple_mcp():
    """Test the simplest possible MCP communication."""
    print("=== Simple MCP Test ===")

    # Set up environment
    env = os.environ.copy()
    env.update({"SBCTL_TOKEN": "test-token-12345"})

    # Start MCP server
    cmd = [sys.executable, "-m", "mcp_server_troubleshoot"]
    print(f"Starting: {' '.join(cmd)}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    print(f"Process PID: {process.pid}")

    try:
        # Send the simplest possible request
        simple_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",  # Try a method that might not exist but should get some response
        }

        request_json = json.dumps(simple_request)
        print(f"Sending: {request_json}")

        if process.stdin:
            process.stdin.write((request_json + "\\n").encode())
            await process.stdin.drain()
            print("✅ Request sent")

        # Wait for any response at all
        try:
            if process.stdout:
                response_bytes = await asyncio.wait_for(process.stdout.readline(), timeout=3.0)
                response_line = response_bytes.decode().strip()
                print(f"Got response: {response_line}")

                # Try to parse it
                try:
                    response = json.loads(response_line)
                    print(f"Parsed: {response}")
                except json.JSONDecodeError:
                    print("Not valid JSON, but got some response")
        except asyncio.TimeoutError:
            print("❌ No response at all")

            # Check if process is still running
            if process.returncode is not None:
                print(f"Process died: {process.returncode}")
            else:
                print("Process is still running but not responding")

        # Read stderr to see what's happening
        if process.stderr:
            try:
                stderr_data = await asyncio.wait_for(process.stderr.read(1024), timeout=1.0)
                if stderr_data:
                    print(f"STDERR: {stderr_data.decode()}")
            except asyncio.TimeoutError:
                pass

    finally:
        # Kill the process
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        print("Process terminated")


if __name__ == "__main__":
    asyncio.run(test_simple_mcp())
