#!/usr/bin/env python3
"""
Debug tool for testing the MCP server.

This is a minimal script to diagnose issues with the MCP server's JSON-RPC implementation.
"""

import json
import os
import subprocess
import sys
import time


def main():
    """Run a minimal test of the MCP server."""
    print("Starting debug MCP test")

    # Start the MCP server process
    cmd = [sys.executable, "-m", "mcp_server_troubleshoot.cli", "--verbose"]
    print(f"Running command: {' '.join(cmd)}")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    process = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    # Start a thread to read stderr in the background
    import threading

    def read_stderr():
        while True:
            line = process.stderr.readline()
            if not line:
                break
            print(f"STDERR: {line.decode('utf-8', errors='replace').strip()}")

    stderr_thread = threading.Thread(target=read_stderr)
    stderr_thread.daemon = True
    stderr_thread.start()

    # Wait for server to start
    print("Waiting for server to start...")
    time.sleep(2)

    try:
        # Send a simple request
        request = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "get_tool_definitions",
            "params": {},
        }

        request_str = json.dumps(request) + "\n"
        print(f"Sending request: {request_str.strip()}")

        # Write the request to stdin and flush
        process.stdin.write(request_str.encode("utf-8"))
        process.stdin.flush()

        # Set a timeout for the read operation
        import select

        timeout = 10  # seconds

        # Use select to wait for data or timeout
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        if not ready:
            print("ERROR: Timeout waiting for response")
        else:
            # Read the response
            response_line = process.stdout.readline()
            print(f"Raw response: {response_line}")

            if response_line:
                try:
                    response = json.loads(response_line.decode("utf-8"))
                    print(f"Received JSON-RPC response: {json.dumps(response, indent=2)}")
                except json.JSONDecodeError as e:
                    print(f"Failed to decode response as JSON: {e}")
            else:
                print("No response received (empty)")

    finally:
        # Clean up
        print("Terminating server process")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            print("Process did not terminate, killing it")
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
