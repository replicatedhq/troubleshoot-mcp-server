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

from troubleshoot_mcp_server.bundle import BundleManager, BundleManagerError
from troubleshoot_mcp_server.kubectl import KubectlExecutor


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

        # Verify sbctl process is running for THIS bundle (concurrent-safe lookup)
        sbctl_process = bundle_manager.sbctl_processes.get(result.id)
        assert sbctl_process is not None, f"sbctl process should exist for bundle {result.id}"
        assert sbctl_process.returncode is None

        # Get process ID for validation
        process_pid = sbctl_process.pid

        # Verify process is actually running in system using os.kill(pid, 0)
        process_exists = True
        try:
            os.kill(process_pid, 0)  # Signal 0 checks if process exists
        except OSError:
            process_exists = False

        assert process_exists, f"Process {process_pid} should be running"

        # Test graceful shutdown (terminate THIS bundle's process)
        await bundle_manager._terminate_sbctl_process(result.id)

        # Verify process is terminated
        terminated_process = bundle_manager.sbctl_processes.get(result.id)
        assert terminated_process is None or terminated_process.returncode is not None

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

        # In concurrent mode, set active_bundle for backward compatibility with KubectlExecutor
        # This is for testing purposes - production code should use bundle_id directly
        bundle_manager.active_bundle = result

        # Create kubectl executor to test API availability
        kubectl_executor = KubectlExecutor(bundle_manager)

        # Test basic API availability with kubectl
        try:
            # Simple command that should work if API server is available
            # Use longer timeout to account for potential resource contention from parallel tests
            kubectl_result = await kubectl_executor.execute(
                "get namespaces", timeout=15, json_output=True
            )

            # Verify we got a successful response
            assert kubectl_result.exit_code == 0, (
                f"kubectl command failed with exit code {kubectl_result.exit_code}: {kubectl_result.stderr}"
            )

            # Verify we got valid JSON output
            assert kubectl_result.is_json, "Expected JSON output from kubectl get namespaces"
            assert "items" in kubectl_result.output
            assert isinstance(kubectl_result.output["items"], list)

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

        # In concurrent mode, set active_bundle for backward compatibility
        bundle_manager.active_bundle = result

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

        # Record initial state from result metadata
        initial_bundle_path = result.path
        initial_kubeconfig = result.kubeconfig_path
        sbctl_process = bundle_manager.sbctl_processes.get(result.id)
        initial_process_pid = sbctl_process.pid if sbctl_process else None

        # Verify files exist
        assert initial_bundle_path.exists()
        assert initial_kubeconfig.exists()

        # Perform cleanup
        await bundle_manager._cleanup_active_bundle()

        # Verify cleanup results (bundle_states and sbctl_processes should be empty for running bundles)
        # Note: Failed or stopped bundles may remain in the dicts, but processes should be cleared
        assert len(bundle_manager.sbctl_processes) == 0

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

        # Verify no processes were left running (failed bundle states may remain)
        assert len(bundle_manager.sbctl_processes) == 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_multiple_initialization_cleanup(
        self, bundle_manager: BundleManager, test_bundle_path: Path
    ):
        """Test that multiple initializations properly clean up previous instances."""
        # First initialization
        result1 = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result1.initialized is True
        sbctl_process1 = bundle_manager.sbctl_processes.get(result1.id)
        first_pid = sbctl_process1.pid if sbctl_process1 else None

        # Second initialization should clean up first
        result2 = await bundle_manager.initialize_bundle(str(test_bundle_path))
        assert result2.initialized is True
        sbctl_process2 = bundle_manager.sbctl_processes.get(result2.id)
        second_pid = sbctl_process2.pid if sbctl_process2 else None

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

        # Bundle information - use first bundle from bundle_states (concurrent-safe)
        bundle_id = next(iter(bundle_manager.bundle_states), None)
        bundle_state = bundle_manager.bundle_states.get(bundle_id) if bundle_id else None

        if bundle_state and bundle_state.metadata:
            diagnostics["bundle_info"] = {
                "path": str(bundle_state.metadata.path),
                "kubeconfig_path": str(bundle_state.metadata.kubeconfig_path),
                "initialized": True,
                "extracted_at": (
                    bundle_state.metadata.path.stat().st_mtime
                    if bundle_state.metadata.path.exists()
                    else None
                ),
            }
        else:
            diagnostics["bundle_info"] = {"initialized": False}

        # Process information - use first process from sbctl_processes (concurrent-safe)
        process_id = next(iter(bundle_manager.sbctl_processes), None)
        sbctl_process = bundle_manager.sbctl_processes.get(process_id) if process_id else None

        if sbctl_process:
            process_info = {
                "pid": sbctl_process.pid,
                "running": sbctl_process.returncode is None,
            }

            # Check if process is still running
            try:
                os.kill(sbctl_process.pid, 0)
                process_info["status"] = "running"
            except OSError:
                process_info["status"] = "terminated"

            diagnostics["process_info"] = process_info
        else:
            diagnostics["process_info"] = {"running": False}

        # API server availability
        api_available = False
        api_error = None

        if bundle_state and sbctl_process:
            try:
                kubectl_executor = KubectlExecutor(bundle_manager)
                result = await kubectl_executor.execute("get namespaces", timeout=5)
                api_available = result.exit_code == 0
                if not api_available:
                    api_error = result.stderr or "Unknown error"
            except Exception as e:
                api_error = str(e)

        diagnostics["api_server_status"] = {
            "available": api_available,
            "error": api_error,
        }

        # Resource usage (simplified without psutil)
        resource_usage = {}
        if sbctl_process:
            try:
                os.kill(sbctl_process.pid, 0)
                resource_usage = {
                    "pid": sbctl_process.pid,
                    "process_exists": True,
                }
            except OSError:
                resource_usage = {"process_exists": False}

        diagnostics["resource_usage"] = resource_usage

        # Timestamps
        diagnostics["timestamps"] = {
            "diagnostic_collected_at": time.time(),
            "bundle_initialized_at": (
                bundle_state.metadata.path.stat().st_mtime
                if bundle_state and bundle_state.metadata and bundle_state.metadata.path.exists()
                else None
            ),
        }

        return diagnostics
