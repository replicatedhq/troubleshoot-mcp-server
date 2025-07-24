#!/usr/bin/env python3
"""
Debug sbctl to understand why it's failing with the test bundle.
"""

import asyncio
import tempfile
import os
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from tests.integration.mcp_test_utils import get_test_bundle_path


async def debug_sbctl():
    """Debug sbctl behavior with the test bundle."""
    print("=== Debugging sbctl Behavior ===")

    # Get test bundle
    test_bundle_path = get_test_bundle_path()
    print(f"Test bundle: {test_bundle_path}")
    print(f"Bundle exists: {test_bundle_path.exists()}")
    print(f"Bundle size: {test_bundle_path.stat().st_size} bytes")

    # Create temp directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        print(f"Working directory: {temp_dir_path}")

        # Change to temp directory (where sbctl will create kubeconfig)
        original_cwd = os.getcwd()
        os.chdir(temp_dir_path)

        try:
            # Test 1: Check if sbctl can read the bundle
            print("\n=== Test 1: Running sbctl with --help ===")
            process = await asyncio.create_subprocess_exec(
                "sbctl", "--help", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            print(f"sbctl --help exit code: {process.returncode}")
            if stdout:
                print(f"STDOUT: {stdout.decode()[:500]}...")
            if stderr:
                print(f"STDERR: {stderr.decode()}")

            print("\n=== Test 2: Running sbctl serve with test bundle (long wait) ===")

            # Set up environment
            env = os.environ.copy()
            env.update(
                {"SBCTL_TOKEN": "test-token-12345", "KUBECONFIG": str(temp_dir_path / "kubeconfig")}
            )

            cmd = [
                "sbctl",
                "serve",
                "--support-bundle-location",
                str(test_bundle_path),
            ]
            print(f"Command: {' '.join(cmd)}")
            print(f"Environment: SBCTL_TOKEN={env.get('SBCTL_TOKEN')}")

            # Start the process with timeout
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
            )

            print(f"Process started with PID: {process.pid}")

            # Wait a few seconds to see if kubeconfig appears
            for i in range(20):  # Wait up to 10 seconds
                await asyncio.sleep(0.5)

                # Check if kubeconfig was created
                kubeconfig_path = temp_dir_path / "kubeconfig"
                if kubeconfig_path.exists():
                    print(f"✅ Kubeconfig appeared after {i * 0.5:.1f} seconds!")
                    break

                # Check if process exited
                if process.returncode is not None:
                    print(f"Process exited early with code: {process.returncode}")
                    break

                if i % 4 == 0:  # Print every 2 seconds
                    print(f"Waiting... ({i * 0.5:.1f}s)")

            # Terminate the process since sbctl serve runs indefinitely
            if process.returncode is None:
                print("Terminating process...")
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    print("Process didn't terminate gracefully, killing...")
                    process.kill()
                    await process.wait()
                print(f"Process terminated, final exit code: {process.returncode}")

            # Try to read any output
            try:
                if process.stdout:
                    stdout_data = await asyncio.wait_for(process.stdout.read(), timeout=1.0)
                    if stdout_data:
                        print(f"STDOUT: {stdout_data.decode()}")
            except asyncio.TimeoutError:
                pass

            try:
                if process.stderr:
                    stderr_data = await asyncio.wait_for(process.stderr.read(), timeout=1.0)
                    if stderr_data:
                        print(f"STDERR: {stderr_data.decode()}")
            except asyncio.TimeoutError:
                pass

            # Check what files were created
            files_created = list(temp_dir_path.glob("*"))
            print(f"\nFiles created in temp directory: {[f.name for f in files_created]}")

            # Check if kubeconfig was created
            kubeconfig_path = temp_dir_path / "kubeconfig"
            if kubeconfig_path.exists():
                print(f"Kubeconfig created: {kubeconfig_path}")
                try:
                    with open(kubeconfig_path, "r") as f:
                        content = f.read()
                    print(f"Kubeconfig content ({len(content)} chars):\n{content[:500]}...")
                except Exception as e:
                    print(f"Error reading kubeconfig: {e}")
            else:
                print("No kubeconfig file created")

        finally:
            os.chdir(original_cwd)


if __name__ == "__main__":
    asyncio.run(debug_sbctl())
