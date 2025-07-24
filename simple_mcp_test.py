#!/usr/bin/env python3
"""
Simple test to manually check MCP server communication.
"""

import subprocess
import json
import sys
import time
import tempfile
from pathlib import Path


def test_mcp_server():
    """Test MCP server manually with basic requests."""
    print("=== Starting MCP Server Manually ===")

    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_bundle_dir = Path(temp_dir)
        print(f"Temp directory: {temp_bundle_dir}")

        env = {
            "MCP_BUNDLE_STORAGE": str(temp_bundle_dir),
            "SBCTL_TOKEN": "test-token-12345",
        }

        # Start server
        cmd = [sys.executable, "-m", "mcp_server_troubleshoot"]
        print(f"Starting: {' '.join(cmd)}")

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                bufsize=0,
            )

            # Give it time to start
            time.sleep(1)

            # Check if it started
            returncode = process.poll()
            if returncode is not None:
                stderr = process.stderr.read()
                stdout = process.stdout.read()
                print(f"❌ Server died immediately with code {returncode}")
                print(f"STDERR: {stderr}")
                print(f"STDOUT: {stdout}")
                return

            print("✅ Server appears to be running")

            # Send MCP initialize request
            init_request = {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            }

            print(f"Sending: {json.dumps(init_request)}")
            process.stdin.write(json.dumps(init_request) + "\n")
            process.stdin.flush()

            # Wait for response with timeout
            print("Waiting for response...")

            # Set a shorter timeout to see what happens
            import select

            # Use select to wait for output with timeout
            ready, _, _ = select.select([process.stdout], [], [], 5.0)  # 5 second timeout

            if ready:
                response = process.stdout.readline()
                print(f"✅ Got response: {response.strip()}")

                # Try parsing response
                try:
                    resp_data = json.loads(response.strip())
                    print(f"Parsed response: {resp_data}")
                except json.JSONDecodeError as e:
                    print(f"❌ Invalid JSON response: {e}")

            else:
                print("❌ No response within 5 seconds")

                # Check if process is still alive
                returncode = process.poll()
                if returncode is not None:
                    stderr = process.stderr.read()
                    print(f"❌ Server died with code {returncode}")
                    print(f"STDERR: {stderr}")
                else:
                    print("Server is still running but not responding")

        except Exception as e:
            print(f"❌ Error: {e}")

        finally:
            if "process" in locals():
                print("Terminating server...")
                process.terminate()
                process.wait()


if __name__ == "__main__":
    test_mcp_server()
