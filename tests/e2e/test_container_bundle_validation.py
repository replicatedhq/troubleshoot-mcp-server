"""
Container-based validation tests for bundle initialization.

These tests use the actual production container built with melange/apko
to validate bundle initialization functionality that hangs in subprocess tests.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import pytest

from tests.integration.mcp_test_utils import get_test_bundle_path

logger = logging.getLogger(__name__)

# Container image name (matches build.sh defaults)
CONTAINER_IMAGE = "troubleshoot-mcp-server:latest"
CONTAINER_RUNTIME = "podman"  # Could be "docker" if preferred


class ContainerMCPClient:
    """MCP client that communicates with containerized server."""

    def __init__(self, bundle_dir: Path, sbctl_token: str = "test-token-12345"):
        self.bundle_dir = bundle_dir
        self.sbctl_token = sbctl_token
        self.process: Optional[asyncio.subprocess.Process] = None
        self.request_id = 0

    async def start_container(self, timeout: float = 10.0) -> None:
        """Start the containerized MCP server."""
        # Container run command using the same volumes as build.sh suggests
        cmd = [
            CONTAINER_RUNTIME,
            "run",
            "--rm",
            "-i",  # Remove on exit, interactive for stdin/stdout
            "--volume",
            f"{self.bundle_dir}:/data/bundles",
            "--env",
            f"SBCTL_TOKEN={self.sbctl_token}",
            "--env",
            "MCP_BUNDLE_STORAGE=/data/bundles",
            CONTAINER_IMAGE,
        ]

        logger.info(f"Starting container: {' '.join(cmd)}")

        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Give container time to start
        await asyncio.sleep(2.0)

        # Check if container is still running
        if self.process.returncode is not None:
            stderr_data = b""
            if self.process.stderr:
                stderr_data = await self.process.stderr.read()
            raise RuntimeError(
                f"Container exited early with code {self.process.returncode}. "
                f"Stderr: {stderr_data.decode()}"
            )

    async def send_request(self, method: str, params: Optional[dict] = None) -> dict:
        """Send JSON-RPC request to container."""
        if not self.process or not self.process.stdin:
            raise RuntimeError("Container not started")

        self.request_id += 1
        request = {"jsonrpc": "2.0", "id": self.request_id, "method": method}
        if params:
            request["params"] = params

        # Send request
        request_json = json.dumps(request)
        logger.debug(f"Sending: {request_json}")

        self.process.stdin.write((request_json + "\n").encode())
        await self.process.stdin.drain()

        # Read response with timeout
        if not self.process.stdout:
            raise RuntimeError("Container stdout not available")

        try:
            response_bytes = await asyncio.wait_for(self.process.stdout.readline(), timeout=60.0)
            response_line = response_bytes.decode().strip()
            logger.debug(f"Received: {response_line}")

            if not response_line:
                raise RuntimeError("Empty response from container")

            return json.loads(response_line)

        except asyncio.TimeoutError:
            # Get stderr for debugging
            stderr_data = b""
            if self.process.stderr:
                try:
                    stderr_data = await asyncio.wait_for(
                        self.process.stderr.read(2048), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    pass

            raise RuntimeError(
                f"Timeout waiting for response from container. " f"Stderr: {stderr_data.decode()}"
            )

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call an MCP tool in the container."""
        return await self.send_request("tools/call", {"name": tool_name, "arguments": arguments})

    async def stop(self) -> None:
        """Stop the container."""
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Container didn't stop gracefully, killing...")
                self.process.kill()
                await self.process.wait()


@pytest.fixture
def container_runtime_available():
    """Check if container runtime is available."""
    try:
        result = subprocess.run(
            [CONTAINER_RUNTIME, "--version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            pytest.skip(f"{CONTAINER_RUNTIME} not available")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip(f"{CONTAINER_RUNTIME} not available")


@pytest.fixture
def container_image_available():
    """Check if container image exists."""
    try:
        result = subprocess.run(
            [CONTAINER_RUNTIME, "image", "exists", CONTAINER_IMAGE], capture_output=True, timeout=10
        )
        if result.returncode != 0:
            pytest.skip(
                f"Container image {CONTAINER_IMAGE} not found. "
                f"Build it first with: MELANGE_TEST_BUILD=true ./scripts/build.sh"
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip(f"Could not check for container image {CONTAINER_IMAGE}")


@pytest.fixture
def temp_bundle_dir():
    """Temporary directory for test bundles."""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield Path(temp_dir)


@pytest.fixture
def test_bundle_in_dir(temp_bundle_dir):
    """Copy test bundle to temporary directory."""
    test_bundle_path = get_test_bundle_path()
    bundle_copy = temp_bundle_dir / test_bundle_path.name
    bundle_copy.write_bytes(test_bundle_path.read_bytes())
    return bundle_copy


@pytest.mark.container
@pytest.mark.slow
class TestContainerBundleValidation:
    """Test bundle operations using the production container."""

    @pytest.mark.asyncio
    async def test_container_starts_and_responds(
        self, container_runtime_available, container_image_available, temp_bundle_dir
    ):
        """Test that the container starts and responds to basic requests."""
        client = ContainerMCPClient(temp_bundle_dir)

        try:
            await client.start_container()

            # Test basic initialize request
            response = await client.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "pytest-container-test", "version": "1.0.0"},
                },
            )

            assert response["jsonrpc"] == "2.0"
            assert "result" in response
            assert response["result"]["serverInfo"]["name"] == "troubleshoot-mcp-server"

        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_container_bundle_initialization(
        self,
        container_runtime_available,
        container_image_available,
        temp_bundle_dir,
        test_bundle_in_dir,
    ):
        """
        Test bundle initialization in container - the operation that hangs in subprocess tests.

        This is the key test that validates the production container can actually
        initialize bundles, which is the core functionality that users need.
        """
        client = ContainerMCPClient(temp_bundle_dir)

        try:
            await client.start_container()

            # Initialize the MCP protocol first
            await client.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "pytest-container-test", "version": "1.0.0"},
                },
            )

            # Now test bundle initialization - this should work in container
            bundle_path_in_container = f"/data/bundles/{test_bundle_in_dir.name}"

            response = await client.call_tool(
                "initialize_bundle", {"source": bundle_path_in_container}
            )

            # Validate response
            assert response["jsonrpc"] == "2.0"
            assert "result" in response, f"Expected result, got: {response}"

            result = response["result"]
            assert len(result["content"]) > 0, "Should have content in response"

            content_text = result["content"][0]["text"]
            assert any(
                indicator in content_text.lower()
                for indicator in ["bundle_id", "initialized", "ready", "success"]
            ), f"Response should indicate success: {content_text}"

            logger.info(f"✅ Bundle initialization successful: {content_text[:100]}...")

        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_container_list_bundles(
        self,
        container_runtime_available,
        container_image_available,
        temp_bundle_dir,
        test_bundle_in_dir,
    ):
        """Test listing bundles in container."""
        client = ContainerMCPClient(temp_bundle_dir)

        try:
            await client.start_container()

            # Initialize protocol
            await client.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "pytest-container-test", "version": "1.0.0"},
                },
            )

            # List available bundles
            response = await client.call_tool("list_available_bundles", {})

            assert response["jsonrpc"] == "2.0"
            assert "result" in response

            content_text = response["result"]["content"][0]["text"]
            # Should either list the bundle or indicate no bundles found
            assert len(content_text.strip()) > 0, "Should have some response"

            logger.info(f"Bundle list response: {content_text[:100]}...")

        finally:
            await client.stop()

    @pytest.mark.asyncio
    async def test_container_complete_workflow(
        self,
        container_runtime_available,
        container_image_available,
        temp_bundle_dir,
        test_bundle_in_dir,
    ):
        """
        Test complete bundle workflow in container:
        1. Initialize bundle
        2. List files
        3. Read a file

        This validates the full user workflow works in the production container.
        """
        client = ContainerMCPClient(temp_bundle_dir)

        try:
            await client.start_container()

            # Initialize protocol
            await client.send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "pytest-container-test", "version": "1.0.0"},
                },
            )

            # Step 1: Initialize bundle
            bundle_path = f"/data/bundles/{test_bundle_in_dir.name}"
            init_response = await client.call_tool("initialize_bundle", {"source": bundle_path})

            assert "result" in init_response
            logger.info("✅ Bundle initialized in container")

            # Step 2: List files
            files_response = await client.call_tool("list_files", {"path": "/", "recursive": False})

            assert "result" in files_response
            files_content = files_response["result"]["content"][0]["text"]
            assert len(files_content.strip()) > 0, "Should have file listing"
            logger.info("✅ File listing works in container")

            # Step 3: Test grep (should work even if no matches)
            grep_response = await client.call_tool(
                "grep_files",
                {"pattern": "kube", "path": "/", "recursive": True, "case_sensitive": False},
            )

            assert "result" in grep_response
            logger.info("✅ Grep functionality works in container")

        finally:
            await client.stop()


@pytest.mark.container
@pytest.mark.slow
class TestContainerBuildValidation:
    """Test that the melange/apko build process produces a working container."""

    def test_build_script_produces_working_image(self, container_runtime_available):
        """
        Test that the build script produces a working container image.

        This validates the entire build process from melange package to final image.
        """
        # Skip in CI due to melange/apko container-in-container limitations
        # The publish workflow already validates the build process works correctly
        if os.environ.get("CI") == "true":
            pytest.skip(
                "Container build tests are skipped in CI - run locally with 'pytest -m slow'"
            )

        build_script = Path("scripts/build.sh")
        if not build_script.exists():
            pytest.skip("Build script not found")

        # Run the build script with test keys
        env = os.environ.copy()
        env["MELANGE_TEST_BUILD"] = "true"
        env["IMAGE_TAG"] = "test-build"

        try:
            result = subprocess.run(
                ["./scripts/build.sh"],
                cwd=Path.cwd(),
                env=env,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout for build
            )

            if result.returncode != 0:
                pytest.fail(
                    f"Build script failed with code {result.returncode}.\n"
                    f"STDOUT: {result.stdout}\n"
                    f"STDERR: {result.stderr}"
                )

            # Verify the image was created
            check_result = subprocess.run(
                [CONTAINER_RUNTIME, "image", "exists", "troubleshoot-mcp-server:test-build"],
                capture_output=True,
                timeout=10,
            )

            assert check_result.returncode == 0, "Built image should exist"

        except subprocess.TimeoutExpired:
            pytest.fail("Build script timed out after 5 minutes")
        except Exception as e:
            pytest.fail(f"Build script execution failed: {e}")

        # Clean up test image
        try:
            subprocess.run(
                [CONTAINER_RUNTIME, "rmi", "troubleshoot-mcp-server:test-build"],
                capture_output=True,
                timeout=30,
            )
        except Exception:
            pass  # Best effort cleanup
