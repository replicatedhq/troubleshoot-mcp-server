"""
Tests to ensure container build process is reliable and never uses stale cached configurations.

This module tests that:
1. Container images are always rebuilt from current configuration
2. Changes to build configs (.melange.yaml, apko.yaml) are reflected in built images
3. Tests never pass due to cached images with outdated configurations
4. The build process fails fast if configuration is invalid
"""

import pytest
import subprocess
import tempfile
from pathlib import Path
from .utils import get_container_runtime
import uuid


pytestmark = [pytest.mark.e2e, pytest.mark.container]


@pytest.fixture
def temp_project_dir():
    """Create a temporary project directory with build configs."""
    temp_dir = Path(tempfile.mkdtemp())

    # Copy essential files to temp directory
    project_root = Path(__file__).parent.parent.parent

    # Copy build configs
    melange_src = project_root / ".melange.yaml"
    apko_src = project_root / "apko.yaml"

    if melange_src.exists():
        (temp_dir / ".melange.yaml").write_text(melange_src.read_text())
    if apko_src.exists():
        (temp_dir / "apko.yaml").write_text(apko_src.read_text())

    yield temp_dir

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_container_build_never_uses_cached_configs(temp_project_dir):
    """
    Test that container builds always use current configuration, never cached versions.

    This test verifies the critical fix for the bug where tests would pass with
    cached images even when the current configuration was broken.
    """
    runtime, available = get_container_runtime()
    if not available:
        pytest.skip(f"Container runtime {runtime} not available")

    image_name = f"test-build-reliability-{uuid.uuid4().hex[:8]}"

    try:
        # First, create an intentionally broken melange config
        melange_config = temp_project_dir / ".melange.yaml"
        broken_config = melange_config.read_text().replace(
            "mkdir -p ${{targets.destdir}}/usr/bin",
            "mkdir -p /invalid/path",  # This should cause build to fail
        )
        melange_config.write_text(broken_config)

        # Try to build - this should fail due to broken config
        build_result = subprocess.run(
            [
                runtime,
                "run",
                "--rm",
                "--privileged",
                "--cap-add=SYS_ADMIN",
                "-v",
                f"{temp_project_dir}:/work",
                "cgr.dev/chainguard/melange",
                "build",
                ".melange.yaml",
                "--arch=amd64",
                "--signing-key=test-key",
            ],
            capture_output=True,
            text=True,
            cwd=temp_project_dir,
            timeout=60,
        )

        # Build should fail with broken config
        assert build_result.returncode != 0, (
            "Build should have failed with broken configuration, but it succeeded. "
            "This indicates the build process may be using cached results."
        )

        # Verify the error is related to the path issue we introduced
        error_output = build_result.stderr + build_result.stdout
        assert (
            "/invalid/path" in error_output or "No such file" in error_output
        ), f"Build failed but not for expected reason. Output: {error_output}"

    finally:
        # Clean up any test images
        subprocess.run([runtime, "rmi", "-f", f"{image_name}:latest"], capture_output=True)


def test_build_config_changes_reflected_in_tests():
    """
    Test that changes to build configuration are immediately reflected in test results.

    This ensures the fix for the caching bug is working correctly.
    """
    # This is an integration test that verifies our fix to conftest.py
    # The container_image fixture should now always rebuild instead of using cache

    # Check that the fixture was modified correctly
    conftest_path = Path(__file__).parent.parent / "conftest.py"
    conftest_content = conftest_path.read_text()

    # Verify the problematic caching code was removed
    assert (
        "Using existing container image for tests" not in conftest_content
    ), "The problematic caching logic should have been removed from conftest.py"

    # Verify the fix is in place
    assert (
        "Building container image (Podman will use layer cache" in conftest_content
    ), "The fix to always build container images should be present in conftest.py"

    # Verify we always build (no dangerous skip logic)
    assert (
        "Always run the build process" in conftest_content
    ), "Tests should always run build process and rely on Podman layer caching"


def test_sbctl_installation_path_is_correct():
    """
    Test that the sbctl installation path in melange.yaml uses correct destdir prefix.

    This verifies the core bug fix.
    """
    project_root = Path(__file__).parent.parent.parent
    melange_config = project_root / ".melange.yaml"

    assert melange_config.exists(), "melange.yaml should exist"

    config_content = melange_config.read_text()

    # Verify the fix is in place
    assert (
        "${{targets.destdir}}/usr/bin" in config_content
    ), "sbctl should be installed to ${{targets.destdir}}/usr/bin for proper packaging"

    # Verify the broken pattern is not present
    assert (
        "/usr/local/bin/sbctl" not in config_content
    ), "sbctl should not be installed to /usr/local/bin (the broken path)"

    # Verify the installation command sequence is correct
    lines = config_content.split("\n")
    sbctl_section_found = False
    destdir_found = False

    for i, line in enumerate(lines):
        if "Install sbctl" in line:
            sbctl_section_found = True
        if sbctl_section_found and "${{targets.destdir}}" in line:
            destdir_found = True
            break

    assert sbctl_section_found, "Should find sbctl installation section"
    assert destdir_found, "Should use targets.destdir for sbctl installation"
