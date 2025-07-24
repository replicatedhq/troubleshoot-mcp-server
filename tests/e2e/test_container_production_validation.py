"""
Production-level container validation tests.

These tests validate that the actual built container images work correctly
in production scenarios, independent of host system setup.
"""

import os
import pytest
import subprocess
import uuid
from pathlib import Path
from .utils import get_container_runtime


pytestmark = [pytest.mark.e2e, pytest.mark.container]


def test_container_has_required_tools_isolated(container_image: str):
    """
    Test that required tools exist in container, completely isolated from host.

    This test ensures tools are actually packaged in the container, not just
    available on the CI runner host system.
    """
    runtime, available = get_container_runtime()
    if not available:
        pytest.skip(f"Container runtime {runtime} not available")

    container_name = f"production-validation-{uuid.uuid4().hex[:8]}"

    required_tools = {
        "sbctl": "/usr/bin/sbctl",
        "kubectl": "/usr/bin/kubectl",
        "python3": "/usr/bin/python3",
    }

    for tool_name, expected_path in required_tools.items():
        # For distroless containers, we can't use 'test' command since it's not available.
        # Instead, we directly try to run the tools which is a more reliable test anyway.

        if tool_name == "sbctl":
            # Test sbctl exists and works
            result = subprocess.run(
                [
                    runtime,
                    "run",
                    "--name",
                    f"{container_name}-{tool_name}-run",
                    "--rm",
                    "--entrypoint",
                    "",
                    container_image,
                    expected_path,
                    "--help",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0, (
                f"Tool {tool_name} not found or not working at {expected_path} in container. "
                f"This indicates the tool is not properly packaged. "
                f"returncode: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}"
            )
            assert (
                "Usage:" in result.stdout or "usage:" in result.stdout
            ), f"sbctl --help output doesn't contain expected usage text: {result.stdout}"

        elif tool_name == "kubectl":
            # Test kubectl exists and works
            result = subprocess.run(
                [
                    runtime,
                    "run",
                    "--name",
                    f"{container_name}-{tool_name}-run",
                    "--rm",
                    "--entrypoint",
                    "",
                    container_image,
                    expected_path,
                    "version",
                    "--client",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0, (
                f"Tool {tool_name} not found or not working at {expected_path} in container. "
                f"This indicates the tool is not properly packaged. "
                f"returncode: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}"
            )
            assert (
                "Client Version:" in result.stdout
            ), f"kubectl version output doesn't contain expected version text: {result.stdout}"

        elif tool_name == "python3":
            # Test python3 exists and works
            result = subprocess.run(
                [
                    runtime,
                    "run",
                    "--name",
                    f"{container_name}-{tool_name}-run",
                    "--rm",
                    "--entrypoint",
                    "",
                    container_image,
                    expected_path,
                    "--version",
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert result.returncode == 0, (
                f"Tool {tool_name} not found or not working at {expected_path} in container. "
                f"This indicates the tool is not properly packaged. "
                f"returncode: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}"
            )
            assert (
                "Python" in result.stdout
            ), f"python3 --version output doesn't contain expected version text: {result.stdout}"


def test_container_bundle_initialization_isolated(
    container_image: str, temp_bundles_directory, tmp_path: Path
):
    """
    Test that bundle initialization works using only container tools.

    This ensures sbctl is properly packaged and functional, not just present.
    """
    runtime, available = get_container_runtime()
    if not available:
        pytest.skip(f"Container runtime {runtime} not available")

    container_name = f"bundle-init-test-{uuid.uuid4().hex[:8]}"

    # Create a minimal test bundle structure
    test_bundle_dir = tmp_path / "test_bundle"
    test_bundle_dir.mkdir()
    # No manual cleanup needed - tmp_path handles it automatically

    # Create a simple test bundle with minimal cluster resource
    cluster_resource = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {"name": "test-namespace"},
    }

    import json

    resource_file = test_bundle_dir / "cluster-resources.json"
    with open(resource_file, "w") as f:
        json.dump(cluster_resource, f)

    # Test that the MCP server can start and the bundle initialization logic works
    # We'll test the sbctl availability check specifically
    # Need to override entrypoint to run python directly
    result = subprocess.run(
        [
            runtime,
            "run",
            "--name",
            container_name,
            "--rm",
            "--entrypoint",
            "",
            "-v",
            f"{test_bundle_dir}:/test-bundle",
            container_image,
            "/usr/bin/python3",
            "-c",
            """
import asyncio
import sys
sys.path.insert(0, '/usr/lib/python3.13/site-packages')

async def test_sbctl_check():
    from mcp_server_troubleshoot.bundle import BundleManager
    from pathlib import Path
    
    # Create bundle manager
    bundle_manager = BundleManager(Path('/tmp/bundles'))
    
    # Test the critical sbctl availability check
    sbctl_available = await bundle_manager._check_sbctl_available()
    
    if not sbctl_available:
        print("FAIL: sbctl not available in container", file=sys.stderr)
        sys.exit(1)
    else:
        print("PASS: sbctl is available in container")
        sys.exit(0)

asyncio.run(test_sbctl_check())
""",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"Container sbctl availability check failed. "
        f"This indicates sbctl is not properly installed in the container. "
        f"stdout: {result.stdout}, stderr: {result.stderr}"
    )

    assert "PASS: sbctl is available" in result.stdout, (
        f"sbctl availability check didn't pass as expected. "
        f"stdout: {result.stdout}, stderr: {result.stderr}"
    )


def test_container_isolated_from_host_tools():
    """
    Verify that container tests are actually isolated from host system tools.

    This is a meta-test to ensure our testing approach is sound and won't
    give false positives due to host system tool availability.
    """
    runtime, available = get_container_runtime()
    if not available:
        pytest.skip(f"Container runtime {runtime} not available")

    container_name = f"isolation-test-{uuid.uuid4().hex[:8]}"

    # Use a minimal base image that definitely doesn't have sbctl
    base_image = "busybox:latest"

    # This should fail because busybox doesn't have sbctl
    result = subprocess.run(
        [runtime, "run", "--name", container_name, "--rm", base_image, "which", "sbctl"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0, (
        "Test isolation is broken: sbctl found in busybox container. "
        "This suggests the container is somehow accessing host system tools, "
        "which would invalidate our container testing approach."
    )


def test_production_container_mcp_protocol():
    """
    Test that the production container can serve MCP protocol correctly.

    This is an end-to-end test of the actual production container functionality.
    """
    runtime, available = get_container_runtime()
    if not available:
        pytest.skip(f"Container runtime {runtime} not available")

    # Skip in CI due to container build requirements
    # The publish workflow validates container functionality
    if os.environ.get("CI") == "true":
        pytest.skip("Container runtime tests are skipped in CI - run locally with 'pytest -m slow'")

    container_name = f"mcp-protocol-test-{uuid.uuid4().hex[:8]}"

    # Test that the container can start the MCP server and respond to a basic request
    import json

    test_request = {
        "jsonrpc": "2.0",
        "id": "test-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0.0"},
        },
    }

    request_json = json.dumps(test_request)

    result = subprocess.run(
        [
            runtime,
            "run",
            "--name",
            container_name,
            "--rm",
            "-i",
            "troubleshoot-mcp-server:latest",  # Use the built image directly
        ],
        input=request_json + "\n",
        capture_output=True,
        text=True,
        timeout=30,
    )

    # The server should start and process the request
    assert result.returncode == 0 or result.stdout.strip(), (
        f"Container MCP server failed to respond to initialize request. "
        f"returncode: {result.returncode}, stdout: {result.stdout}, stderr: {result.stderr}"
    )

    # Should get back a JSON response
    if result.stdout.strip():
        try:
            response = json.loads(result.stdout.strip())
            assert "jsonrpc" in response, f"Invalid MCP response format: {response}"
            assert response.get("id") == "test-1", f"Response ID mismatch: {response}"
        except json.JSONDecodeError as e:
            pytest.fail(f"Container returned invalid JSON response: {result.stdout}, error: {e}")
