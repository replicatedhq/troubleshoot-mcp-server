"""
Phase 4: API Server Lifecycle Testing

Tests the complete lifecycle management of the sbctl API server process,
including startup, availability checks, diagnostics, and cleanup.
"""

import asyncio
import os
import tempfile
import time
from pathlib import Path
from typing import Dict, Any
import pytest
import pytest_asyncio

from mcp_server_troubleshoot.bundle import BundleManager, BundleManagerError
from mcp_server_troubleshoot.kubectl import KubectlExecutor


class TestAPIServerLifecycle:
    """Test API server process management and lifecycle."""

    @pytest_asyncio.fixture
    async def bundle_manager(self):
        """Create a BundleManager instance for testing."""
        manager = BundleManager()
        yield manager
        # Cleanup any active bundles/processes
        await manager._cleanup_active_bundle()

    @pytest.fixture
    def test_bundle_path(self):
        """Path to test bundle fixture."""
        return (
            Path(__file__).parent.parent / "fixtures" / "support-bundle-2025-04-11T14_05_31.tar.gz"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_server_startup_shutdown(
        self, bundle_manager: BundleManager, test_bundle_path: Path
    ):
        """Test API server startup and graceful shutdown."""
        # Initialize bundle to start API server
        result = await bundle_manager.initialize_bundle(str(test_bundle_path))

        # Verify bundle initialization succeeded
        assert result.initialized is True
        assert result.path.exists()
        assert result.kubeconfig_path.exists()

        # Verify sbctl process is running
        assert bundle_manager.sbctl_process is not None
        assert bundle_manager.sbctl_process.returncode is None

        # Get process ID for validation
        process_pid = bundle_manager.sbctl_process.pid

        # Verify process is actually running in system using os.kill(pid, 0)
        process_exists = True
        try:
            os.kill(process_pid, 0)  # Signal 0 checks if process exists
        except OSError:
            process_exists = False

        assert process_exists, f"Process {process_pid} should be running"

        # Test graceful shutdown
        await bundle_manager._terminate_sbctl_process()

        # Verify process is terminated
        assert bundle_manager.sbctl_process is None

        # Verify process is no longer running in system
        if process_exists:
            # Give process time to terminate
            await asyncio.sleep(1.0)
            try:
                os.kill(process_pid, 0)
                # If we reach here, process still exists
                pytest.fail(f"Process {process_pid} should have been terminated")
            except OSError:
                # Process is gone, which is expected
                pass

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_api_server_availability_check(
        self, bundle_manager: BundleManager, test_bundle_path: Path
    ):
        """Test API server availability through kubectl commands."""
        # Initialize bundle
        result = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result.initialized is True

        # Create kubectl executor to test API availability
        kubectl_executor = KubectlExecutor(bundle_manager)

        # Test basic API availability with kubectl
        try:
            # Simple command that should work if API server is available
            result = await kubectl_executor.execute("get namespaces", json_output=True)

            # Verify we got a successful response
            assert (
                result.exit_code == 0
            ), f"kubectl command failed with exit code {result.exit_code}: {result.stderr}"

            # Verify we got valid JSON output
            assert result.is_json, "Expected JSON output from kubectl get namespaces"
            assert "items" in result.output
            assert isinstance(result.output["items"], list)

        except Exception as e:
            # Let's see what the actual error is instead of skipping
            pytest.fail(f"API server availability test failed: {e}")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_diagnostic_information_collection(
        self, bundle_manager: BundleManager, test_bundle_path: Path
    ):
        """Test diagnostic data collection from running API server."""
        # Initialize bundle
        result = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result.initialized is True

        # Collect diagnostic information
        diagnostics = await self._collect_diagnostics(bundle_manager)

        # Verify all expected diagnostic fields are present
        expected_fields = [
            "bundle_info",
            "process_info",
            "api_server_status",
            "resource_usage",
            "timestamps",
        ]

        for field in expected_fields:
            assert field in diagnostics, f"Missing diagnostic field: {field}"

        # Verify bundle info
        bundle_info = diagnostics["bundle_info"]
        assert "path" in bundle_info
        assert "kubeconfig_path" in bundle_info
        assert bundle_info["initialized"] is True

        # Verify process info
        process_info = diagnostics["process_info"]
        assert "pid" in process_info
        assert "status" in process_info
        assert process_info["running"] is True

        # Verify API server status
        api_status = diagnostics["api_server_status"]
        assert "available" in api_status

        # Verify resource usage
        resource_usage = diagnostics["resource_usage"]
        assert "pid" in resource_usage or "process_exists" in resource_usage

        # Verify timestamps
        timestamps = diagnostics["timestamps"]
        assert "bundle_initialized_at" in timestamps
        assert "diagnostic_collected_at" in timestamps

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_cleanup_verification(
        self, bundle_manager: BundleManager, test_bundle_path: Path
    ):
        """Test complete cleanup after API server shutdown."""
        # Initialize bundle
        result = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result.initialized is True

        # Record initial state
        initial_bundle_path = bundle_manager.active_bundle.path
        initial_kubeconfig = bundle_manager.active_bundle.kubeconfig_path
        initial_process_pid = (
            bundle_manager.sbctl_process.pid if bundle_manager.sbctl_process else None
        )

        # Verify files exist
        assert initial_bundle_path.exists()
        assert initial_kubeconfig.exists()

        # Perform cleanup
        await bundle_manager._cleanup_active_bundle()

        # Verify cleanup results
        assert bundle_manager.active_bundle is None
        assert bundle_manager.sbctl_process is None

        # Verify bundle directory is cleaned up (if in temp directory)
        if "/tmp" in str(initial_bundle_path) or "temp" in str(initial_bundle_path).lower():
            assert not initial_bundle_path.exists()

        # Verify process is terminated
        if initial_process_pid:
            try:
                os.kill(initial_process_pid, 0)
                pytest.fail(f"Process {initial_process_pid} should have been terminated")
            except OSError:
                # Process is gone, which is expected
                pass

        # Verify no leftover PID files
        temp_dir = Path(tempfile.gettempdir())
        pid_files = list(temp_dir.glob("**/mock_sbctl.pid"))
        # Filter to only our test files
        relevant_pid_files = [f for f in pid_files if "troubleshoot" in str(f.parent)]
        assert len(relevant_pid_files) == 0, f"Found leftover PID files: {relevant_pid_files}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_process_error_handling(self, bundle_manager: BundleManager):
        """Test error handling when API server process fails."""
        # Try to initialize with non-existent bundle
        with pytest.raises(BundleManagerError):
            await bundle_manager.initialize_bundle("/nonexistent/bundle.tar.gz")

        # Verify no process was left running
        assert bundle_manager.sbctl_process is None
        assert bundle_manager.active_bundle is None

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_multiple_initialization_cleanup(
        self, bundle_manager: BundleManager, test_bundle_path: Path
    ):
        """Test that multiple initializations properly clean up previous instances."""
        # First initialization
        result1 = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result1.initialized is True
        first_pid = bundle_manager.sbctl_process.pid if bundle_manager.sbctl_process else None

        # Second initialization should clean up first
        result2 = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result2.initialized is True
        second_pid = bundle_manager.sbctl_process.pid if bundle_manager.sbctl_process else None

        # Verify new process is different (or at least that old one is gone)
        if first_pid and second_pid:
            if first_pid != second_pid:
                # Different PIDs - verify first process is gone
                try:
                    os.kill(first_pid, 0)
                    pytest.fail(f"First process {first_pid} should have been terminated")
                except OSError:
                    # Expected - process should be gone
                    pass

    async def _collect_diagnostics(self, bundle_manager: BundleManager) -> Dict[str, Any]:
        """Collect diagnostic information from the running system."""
        diagnostics = {}

        # Bundle information
        if bundle_manager.active_bundle:
            diagnostics["bundle_info"] = {
                "path": str(bundle_manager.active_bundle.path),
                "kubeconfig_path": str(bundle_manager.active_bundle.kubeconfig_path),
                "initialized": True,
                "extracted_at": (
                    bundle_manager.active_bundle.path.stat().st_mtime
                    if bundle_manager.active_bundle.path.exists()
                    else None
                ),
            }
        else:
            diagnostics["bundle_info"] = {"initialized": False}

        # Process information
        if bundle_manager.sbctl_process:
            process_info = {
                "pid": bundle_manager.sbctl_process.pid,
                "running": bundle_manager.sbctl_process.returncode is None,
            }

            # Check if process is still running
            try:
                os.kill(bundle_manager.sbctl_process.pid, 0)
                process_info["status"] = "running"
            except OSError:
                process_info["status"] = "terminated"

            diagnostics["process_info"] = process_info
        else:
            diagnostics["process_info"] = {"running": False}

        # API server availability
        api_available = False
        api_error = None

        if bundle_manager.active_bundle and bundle_manager.sbctl_process:
            try:
                kubectl_executor = KubectlExecutor(bundle_manager)
                result = await kubectl_executor.execute("get namespaces", timeout=5)
                api_available = result.exit_code == 0
                if not api_available:
                    api_error = result.stderr or "Unknown error"
            except Exception as e:
                api_error = str(e)

        diagnostics["api_server_status"] = {"available": api_available, "error": api_error}

        # Resource usage (simplified without psutil)
        resource_usage = {}
        if bundle_manager.sbctl_process:
            try:
                os.kill(bundle_manager.sbctl_process.pid, 0)
                resource_usage = {"pid": bundle_manager.sbctl_process.pid, "process_exists": True}
            except OSError:
                resource_usage = {"process_exists": False}

        diagnostics["resource_usage"] = resource_usage

        # Timestamps
        diagnostics["timestamps"] = {
            "diagnostic_collected_at": time.time(),
            "bundle_initialized_at": (
                bundle_manager.active_bundle.path.stat().st_mtime
                if bundle_manager.active_bundle and bundle_manager.active_bundle.path.exists()
                else None
            ),
        }

        return diagnostics
