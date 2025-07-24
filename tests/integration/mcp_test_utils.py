"""
Utility classes and functions for testing MCP protocol communication.

This module provides the MCPTestClient class for end-to-end testing of the
MCP server through the actual JSON-RPC protocol over stdio transport.
"""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MCPTestClient:
    """
    Utility for testing MCP protocol communication.

    This class manages a subprocess running the MCP server and communicates
    with it using the JSON-RPC 2.0 protocol over stdio transport.
    """

    def __init__(self, bundle_dir: Optional[Path] = None, env: Optional[Dict[str, str]] = None):
        """
        Initialize the MCP test client.

        Args:
            bundle_dir: Directory to use for bundle storage. If None, uses a temporary directory.
            env: Additional environment variables to pass to the server process.
        """
        self.bundle_dir = bundle_dir
        self.env = env or {}
        self.process: Optional[asyncio.subprocess.Process] = None
        self.request_id_counter = 0

    async def start_server(self, timeout: float = 10.0) -> None:
        """
        Start MCP server subprocess with stdio transport.

        Args:
            timeout: Maximum time to wait for server startup.

        Raises:
            RuntimeError: If the server fails to start within the timeout.
            subprocess.SubprocessError: If there's an error starting the process.
        """
        if self.process is not None:
            raise RuntimeError("Server is already running")

        # Set up environment
        server_env = os.environ.copy()
        server_env.update(self.env)

        # Add bundle directory if specified
        if self.bundle_dir:
            server_env["MCP_BUNDLE_STORAGE"] = str(self.bundle_dir)

        # Start the MCP server process
        cmd = [sys.executable, "-m", "mcp_server_troubleshoot"]
        logger.info(f"Starting MCP server with command: {cmd}")

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=server_env,
            )

            # Give process a moment to start and check if it's still running
            await asyncio.sleep(0.2)

            returncode = self.process.returncode
            if returncode is not None:
                stderr_output = ""
                if self.process.stderr:
                    stderr_bytes = await self.process.stderr.read()
                    stderr_output = stderr_bytes.decode()

                stdout_output = ""
                if self.process.stdout:
                    stdout_bytes = await self.process.stdout.read()
                    stdout_output = stdout_bytes.decode()

                raise subprocess.SubprocessError(
                    f"MCP server process terminated immediately with code {returncode}. "
                    f"STDERR: {stderr_output} STDOUT: {stdout_output}"
                )

        except Exception as e:
            raise subprocess.SubprocessError(f"Failed to start MCP server process: {e}")

        # Wait a moment for the process to initialize
        await asyncio.sleep(0.5)

        # Check if the process is still running
        if self.process.returncode is not None:
            stderr_output = ""
            if self.process.stderr:
                stderr_bytes = await self.process.stderr.read()
                stderr_output = stderr_bytes.decode()
            raise RuntimeError(
                f"MCP server process terminated immediately with code {self.process.returncode}. "
                f"Stderr: {stderr_output}"
            )

        logger.info("MCP server started successfully")

    async def send_request(
        self, method: str, params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Send JSON-RPC request and get response.

        Args:
            method: The RPC method name.
            params: Optional parameters for the request.

        Returns:
            The JSON-RPC response as a dictionary.

        Raises:
            RuntimeError: If the server is not running or communication fails.
            json.JSONDecodeError: If the response is not valid JSON.
        """
        if self.process is None:
            raise RuntimeError("Server is not running")

        # Generate unique request ID
        self.request_id_counter += 1
        request_id = self.request_id_counter

        # Build JSON-RPC 2.0 request
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }

        if params is not None:
            request["params"] = params

        # Convert to JSON and send
        request_json = json.dumps(request)
        logger.debug(f"Sending request: {request_json}")

        try:
            if self.process.stdin is None:
                raise RuntimeError("Server stdin is not available")

            # Send the request using async write
            self.process.stdin.write((request_json + "\n").encode())
            await self.process.stdin.drain()

            # Read the response with timeout using async readline
            if self.process.stdout is None:
                raise RuntimeError("Server stdout is not available")

            # Use proper async readline with timeout
            try:
                response_bytes = await asyncio.wait_for(
                    self.process.stdout.readline(),
                    timeout=60.0,  # 60 second timeout for responses
                )
                response_line = response_bytes.decode().strip()
            except asyncio.TimeoutError:
                raise RuntimeError("Timeout waiting for response from MCP server")

            if not response_line:
                # Check if process terminated
                returncode = self.process.returncode
                if returncode is not None:
                    stderr_output = ""
                    if self.process.stderr:
                        stderr_bytes = await self.process.stderr.read()
                        stderr_output = stderr_bytes.decode()
                    raise RuntimeError(
                        f"Server process terminated with code {returncode}. Stderr: {stderr_output}"
                    )
                raise RuntimeError("No response received from server")

            logger.debug(f"Received response: {response_line}")

            # Parse JSON response
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as e:
                raise json.JSONDecodeError(
                    f"Invalid JSON response: {response_line.strip()}", "", 0
                ) from e

            # Validate JSON-RPC 2.0 response format
            if not isinstance(response, dict):
                raise RuntimeError(f"Response is not a JSON object: {response}")

            if response.get("jsonrpc") != "2.0":
                raise RuntimeError(f"Invalid JSON-RPC version: {response.get('jsonrpc')}")

            if response.get("id") != request_id:
                raise RuntimeError(
                    f"Response ID {response.get('id')} does not match request ID {request_id}"
                )

            # Check for error
            if "error" in response:
                error = response["error"]
                error_msg = f"RPC Error {error.get('code', 'unknown')}: {error.get('message', 'Unknown error')}"
                if "data" in error:
                    error_msg += f" - {error['data']}"
                raise RuntimeError(error_msg)

            return response

        except Exception as e:
            logger.error(f"Error during RPC communication: {e}")
            raise

    async def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """
        Send JSON-RPC notification (no response expected).

        Args:
            method: The RPC method name.
            params: Optional parameters for the notification.

        Raises:
            RuntimeError: If the server is not running or communication fails.
        """
        if self.process is None:
            raise RuntimeError("Server is not running")

        # Build JSON-RPC 2.0 notification (no ID field)
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }

        if params is not None:
            notification["params"] = params

        # Convert to JSON and send
        notification_json = json.dumps(notification)
        logger.debug(f"Sending notification: {notification_json}")

        try:
            if self.process.stdin is None:
                raise RuntimeError("Server stdin is not available")

            self.process.stdin.write((notification_json + "\n").encode())
            await self.process.stdin.drain()

        except Exception as e:
            logger.error(f"Error sending notification: {e}")
            raise

    async def initialize_mcp(self, client_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Send MCP initialize request to establish connection.

        Args:
            client_info: Optional client information to send.

        Returns:
            The server's capabilities and information.
        """
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": client_info or {"name": "mcp-test-client", "version": "1.0.0"},
        }

        response = await self.send_request("initialize", params)
        return response.get("result", {})

    async def call_tool(
        self, name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Call an MCP tool and return the result.

        Args:
            name: The tool name.
            arguments: Optional tool arguments.

        Returns:
            List of content items returned by the tool.
        """
        params = {
            "name": name,
        }

        if arguments is not None:
            params["arguments"] = arguments

        response = await self.send_request("tools/call", params)
        result = response.get("result", {})
        return result.get("content", [])

    async def cleanup(self) -> None:
        """
        Gracefully shutdown server and cleanup resources.

        This method attempts to gracefully terminate the server process
        and cleans up any associated resources.
        """
        if self.process is None:
            return

        logger.info("Shutting down MCP server")

        try:
            # Try to close stdin to signal shutdown
            if self.process.stdin:
                self.process.stdin.close()

            # Wait for graceful shutdown with timeout
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info("MCP server shut down gracefully")
            except asyncio.TimeoutError:
                logger.warning("MCP server did not shut down gracefully, terminating")
                self.process.terminate()

                # Give it a moment to terminate
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    logger.warning("MCP server did not terminate, killing")
                    self.process.kill()
                    await self.process.wait()

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            # Force kill as last resort
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        finally:
            self.process = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start_server()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.cleanup()


def get_test_bundle_path() -> Path:
    """
    Get the path to the test support bundle.

    Returns:
        Path to the test bundle file.

    Raises:
        FileNotFoundError: If the test bundle is not found.
    """
    fixtures_dir = Path(__file__).parent.parent / "fixtures"
    bundle_path = fixtures_dir / "support-bundle-2025-04-11T14_05_31.tar.gz"

    if not bundle_path.exists():
        raise FileNotFoundError(f"Test bundle not found at {bundle_path}")

    return bundle_path


def get_project_root() -> Path:
    """
    Get the project root directory.

    Returns:
        Path to the project root.
    """
    return Path(__file__).parent.parent.parent
