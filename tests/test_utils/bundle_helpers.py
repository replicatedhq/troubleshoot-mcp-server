"""
Test utilities for creating realistic bundle structures and files.

This module provides utilities to create real test bundles and directories
without requiring heavy mocking of internal logic.
"""

import json
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Optional


def create_test_bundle_structure(base_dir: Path) -> Dict[str, Path]:
    """
    Create a realistic support bundle directory structure.

    Args:
        base_dir: Base directory to create the structure in

    Returns:
        Dictionary mapping structure names to their paths
    """
    structure = {}

    # Create extracted bundle directory structure similar to real bundles
    extracted_dir = base_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    support_bundle_dir = extracted_dir / "support-bundle-test"
    support_bundle_dir.mkdir(parents=True, exist_ok=True)
    structure["support_bundle"] = support_bundle_dir

    # Create cluster-resources directory
    cluster_resources = support_bundle_dir / "cluster-resources"
    cluster_resources.mkdir(parents=True, exist_ok=True)
    structure["cluster_resources"] = cluster_resources

    # Create pods directory with sample pod data
    pods_dir = cluster_resources / "pods"
    pods_dir.mkdir(parents=True, exist_ok=True)

    # Create sample pod JSON files
    kube_system_pods = pods_dir / "kube-system.json"
    kube_system_pods.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "items": [
                    {
                        "metadata": {"name": "test-pod", "namespace": "kube-system"},
                        "spec": {"containers": [{"name": "test", "image": "nginx"}]},
                        "status": {"phase": "Running"},
                    }
                ],
            },
            indent=2,
        )
    )
    structure["kube_system_pods"] = kube_system_pods

    # Create default namespace pods
    default_pods = pods_dir / "default.json"
    default_pods.write_text(json.dumps({"apiVersion": "v1", "items": []}, indent=2))
    structure["default_pods"] = default_pods

    # Create host-info directory with sample host data
    host_info = support_bundle_dir / "host-info"
    host_info.mkdir(parents=True, exist_ok=True)
    structure["host_info"] = host_info

    # Create sample host files
    os_info = host_info / "os-info.txt"
    os_info.write_text("Operating System: Linux\nKernel: 5.4.0\nDistribution: Ubuntu 20.04\n")
    structure["os_info"] = os_info

    memory_info = host_info / "memory.txt"
    memory_info.write_text("MemTotal: 8388608 kB\nMemFree: 4194304 kB\nMemAvailable: 6291456 kB\n")
    structure["memory_info"] = memory_info

    # Create logs directory
    logs_dir = support_bundle_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    structure["logs"] = logs_dir

    # Create sample log files
    kubelet_log = logs_dir / "kubelet.log"
    kubelet_log.write_text(
        "I1020 10:30:00.123456 kubelet.go:123] Starting kubelet\n"
        "I1020 10:30:01.234567 kubelet.go:456] Node registered successfully\n"
        "W1020 10:30:02.345678 kubelet.go:789] Warning: disk space low\n"
    )
    structure["kubelet_log"] = kubelet_log

    return structure


def create_test_tar_bundle(bundle_dir: Path, output_path: Path) -> Path:
    """
    Create a real tar.gz bundle from a directory structure.

    Args:
        bundle_dir: Directory containing the bundle structure to tar
        output_path: Path where to create the tar.gz file

    Returns:
        Path to the created tar.gz file
    """
    with tarfile.open(output_path, "w:gz") as tar:
        tar.add(bundle_dir, arcname=".")

    return output_path


def create_host_only_bundle_structure(base_dir: Path) -> Dict[str, Path]:
    """
    Create a host-only bundle structure (no cluster resources).

    Args:
        base_dir: Base directory to create the structure in

    Returns:
        Dictionary mapping structure names to their paths
    """
    structure = {}

    # Create extracted bundle directory structure
    extracted_dir = base_dir / "extracted"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    support_bundle_dir = extracted_dir / "support-bundle-host-only"
    support_bundle_dir.mkdir(parents=True, exist_ok=True)
    structure["support_bundle"] = support_bundle_dir

    # Create only host-info directory (no cluster-resources)
    host_info = support_bundle_dir / "host-info"
    host_info.mkdir(parents=True, exist_ok=True)
    structure["host_info"] = host_info

    # Create comprehensive host files
    os_info = host_info / "os-info.txt"
    os_info.write_text("Operating System: Linux\nKernel: 5.4.0\nDistribution: Ubuntu 20.04\n")
    structure["os_info"] = os_info

    processes = host_info / "processes.txt"
    processes.write_text("PID COMMAND\n1 systemd\n123 docker\n456 kubelet\n")
    structure["processes"] = processes

    network = host_info / "network.txt"
    network.write_text("Interface: eth0\nIP: 192.168.1.100\nMAC: 00:11:22:33:44:55\n")
    structure["network"] = network

    return structure


def create_mock_bundle(output_path: Path) -> Path:
    """
    Create a simple mock bundle file for testing.

    Args:
        output_path: Path where to create the mock bundle file

    Returns:
        Path to the created bundle file
    """
    import tempfile

    # Create a temporary directory with basic bundle structure
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        bundle_structure = create_test_bundle_structure(temp_path)

        # Create the tar.gz file from the bundle structure
        support_bundle_dir = bundle_structure["support_bundle"]
        return create_test_tar_bundle(support_bundle_dir.parent, output_path)


def create_minimal_kubeconfig(
    kubeconfig_path: Path, api_server_url: str = "https://127.0.0.1:6443"
) -> Path:
    """
    Create a minimal but valid kubeconfig file for testing.

    Args:
        kubeconfig_path: Path where to create the kubeconfig file
        api_server_url: API server URL for the kubeconfig

    Returns:
        Path to the created kubeconfig file
    """
    kubeconfig_content = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": "test-cluster",
                "cluster": {"server": api_server_url, "insecure-skip-tls-verify": True},
            }
        ],
        "contexts": [
            {"name": "test-context", "context": {"cluster": "test-cluster", "user": "test-user"}}
        ],
        "current-context": "test-context",
        "users": [{"name": "test-user", "user": {"token": "test-token-12345"}}],
    }

    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_path.write_text(json.dumps(kubeconfig_content, indent=2))
    return kubeconfig_path


def create_bundle_with_binary_files(base_dir: Path) -> Dict[str, Path]:
    """
    Create a bundle structure that includes binary files for binary detection tests.

    Args:
        base_dir: Base directory to create the structure in

    Returns:
        Dictionary mapping structure names to their paths
    """
    structure = create_test_bundle_structure(base_dir)

    # Add binary files
    binary_dir = structure["support_bundle"] / "binaries"
    binary_dir.mkdir(parents=True, exist_ok=True)

    # Create a fake binary file with null bytes
    fake_binary = binary_dir / "fake_binary"
    fake_binary.write_bytes(b"\x00\x01\x02\x03\xff\xfe\xfd\xfc")
    structure["fake_binary"] = fake_binary

    # Create an image file (PNG header)
    fake_image = binary_dir / "test.png"
    png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    fake_image.write_bytes(png_header + b"\x00" * 20)
    structure["fake_image"] = fake_image

    return structure


class TempBundleManager:
    """
    Context manager for creating temporary bundle structures for testing.

    This helps ensure proper cleanup of test data while providing realistic
    bundle structures.
    """

    def __init__(self, bundle_type: str = "standard", tmp_path: Optional[Path] = None):
        """
        Initialize the temporary bundle manager.

        Args:
            bundle_type: Type of bundle to create ("standard", "host_only", "with_binaries")
            tmp_path: Optional pytest tmp_path to use instead of system temp directory
        """
        self.bundle_type = bundle_type
        self.tmp_path = tmp_path
        self.temp_dir = None
        self.bundle_structure = None
        self.bundle_path = None
        self.tar_path = None

    def __enter__(self):
        """Create the temporary bundle structure."""
        if self.tmp_path:
            # Use pytest's tmp_path for better cleanup
            test_dir = self.tmp_path / "test_bundle"
            test_dir.mkdir(exist_ok=True)
            self.temp_dir = str(test_dir)
            base_path = test_dir
        else:
            # Fallback to system temp directory for compatibility
            self.temp_dir = tempfile.mkdtemp(prefix="test_bundle_")
            base_path = Path(self.temp_dir)

        if self.bundle_type == "host_only":
            self.bundle_structure = create_host_only_bundle_structure(base_path)
        elif self.bundle_type == "with_binaries":
            self.bundle_structure = create_bundle_with_binary_files(base_path)
        else:  # default to standard
            self.bundle_structure = create_test_bundle_structure(base_path)

        self.bundle_path = self.bundle_structure["support_bundle"]

        # Create tar.gz file
        self.tar_path = base_path / "test_bundle.tar.gz"
        create_test_tar_bundle(self.bundle_path.parent, self.tar_path)

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up the temporary bundle structure."""
        if self.temp_dir and not self.tmp_path:
            # Only clean up manually if we're not using pytest's tmp_path
            # When using tmp_path, pytest handles cleanup automatically
            import shutil

            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def get_bundle_path(self) -> Path:
        """Get the path to the extracted bundle directory."""
        return self.bundle_path

    def get_tar_path(self) -> Path:
        """Get the path to the tar.gz bundle file."""
        return self.tar_path

    def get_structure(self) -> Dict[str, Path]:
        """Get the dictionary of created structure paths."""
        return self.bundle_structure
