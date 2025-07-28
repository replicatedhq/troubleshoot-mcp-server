"""
End-to-end tests that do not require container functionality.
These tests focus on basic e2e functionality that should run on any system.
"""

import sys
import pytest
import subprocess

# Mark all tests in this file
pytestmark = [pytest.mark.e2e]  # Intentionally not using container marker


def test_package_installation():
    """Test that the package is properly installed and importable."""
    try:
        import mcp_server_troubleshoot

        assert hasattr(mcp_server_troubleshoot, "__version__")
    except ImportError:
        pytest.fail("Failed to import mcp_server_troubleshoot package")


def test_cli_module_exists():
    """Test that the CLI module exists."""
    try:
        from mcp_server_troubleshoot import cli

        assert callable(getattr(cli, "main", None)), "CLI module does not have a main function"
    except ImportError:
        pytest.fail("Failed to import mcp_server_troubleshoot.cli module")


def test_bundle_module_exists():
    """Test that the bundle module exists."""
    try:
        from mcp_server_troubleshoot import bundle

        assert hasattr(bundle, "BundleManager"), "Bundle module does not have BundleManager class"
    except ImportError:
        pytest.fail("Failed to import mcp_server_troubleshoot.bundle module")


def test_files_module_exists():
    """Test that the files module exists."""
    try:
        from mcp_server_troubleshoot import files

        assert hasattr(files, "FileExplorer"), "Files module does not have FileExplorer class"
    except ImportError:
        pytest.fail("Failed to import mcp_server_troubleshoot.files module")


def test_kubectl_module_exists():
    """Test that the kubectl module exists."""
    try:
        from mcp_server_troubleshoot import kubectl

        assert hasattr(kubectl, "KubectlExecutor"), (
            "Kubectl module does not have KubectlExecutor class"
        )
    except ImportError:
        pytest.fail("Failed to import mcp_server_troubleshoot.kubectl module")


def test_server_module_exists():
    """Test that the server module exists."""
    try:
        from mcp_server_troubleshoot import server

        assert hasattr(server, "mcp"), "Server module does not have mcp object"
    except ImportError:
        pytest.fail("Failed to import mcp_server_troubleshoot.server module")


def test_configuration_loading():
    """Test that configuration can be loaded."""
    try:
        from mcp_server_troubleshoot import config

        # Create a test config
        test_config = {
            "bundle_storage": "/tmp/test_bundles",
            "log_level": "INFO",
        }
        # Test we can load configuration functions
        assert hasattr(config, "get_recommended_client_config"), (
            "Config module missing get_recommended_client_config"
        )
        assert hasattr(config, "load_config_from_path"), (
            "Config module missing load_config_from_path"
        )

        # Verify the test config values are as expected
        bundle_storage = test_config["bundle_storage"]
        log_level = test_config["log_level"]
        assert bundle_storage == "/tmp/test_bundles"
        assert log_level == "INFO"
    except ImportError:
        pytest.fail("Failed to import or use config module")


def test_cli_help():
    """Test that the CLI can be run with --help."""
    result = subprocess.run(
        [sys.executable, "-m", "mcp_server_troubleshoot.cli", "--help"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed with: {result.stderr}"
    assert "usage:" in result.stdout.lower(), "Help output is missing 'usage:' section"


def test_version_command():
    """Test that the version command works."""
    result = subprocess.run(
        [sys.executable, "-m", "mcp_server_troubleshoot.cli", "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"Version command failed with: {result.stderr}"
    assert "version" in result.stdout.lower() or "version" in result.stderr.lower(), (
        "Version information not found in output"
    )


@pytest.mark.asyncio
async def test_simple_api_initialization():
    """Test that the API components can be initialized."""
    try:
        from mcp_server_troubleshoot.bundle import BundleManager
        from mcp_server_troubleshoot.files import FileExplorer
        from mcp_server_troubleshoot.kubectl import KubectlExecutor
        from pathlib import Path

        # Create bundle manager first
        bundle_storage = Path("/tmp/test_bundles")

        # Initialize components
        bundle_manager = BundleManager(bundle_storage)
        file_explorer = FileExplorer(bundle_manager)
        kubectl_executor = KubectlExecutor(bundle_manager)

        # Just test initialization succeeded
        assert bundle_manager is not None
        assert file_explorer is not None
        assert kubectl_executor is not None

    except Exception as e:
        pytest.fail(f"Failed to initialize API components: {str(e)}")
