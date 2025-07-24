#!/usr/bin/env python3
"""
Test sbctl behavior directly to understand bundle initialization issue.
"""

import asyncio
import tempfile
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.mcp_server_troubleshoot.bundle import BundleManager


async def test_sbctl_direct():
    """Test sbctl initialization directly."""
    print("=== Testing sbctl Behavior Directly ===")

    # Get test bundle
    test_bundle_path = Path("tests/fixtures/support-bundle-2025-04-11T14_05_31.tar.gz")
    print(f"Using test bundle: {test_bundle_path}")
    print(f"Bundle exists: {test_bundle_path.exists()}")

    # Create temp directory for bundles
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_bundle_dir = Path(temp_dir)
        print(f"Temp bundle directory: {temp_bundle_dir}")

        # Create bundle manager
        bundle_manager = BundleManager(temp_bundle_dir)

        # Test the bundle initialization
        print("\n=== Testing bundle initialization ===")

        try:
            # Test with reduced timeout to see what happens
            # Set a shorter timeout for testing
            original_timeout = bundle_manager.__class__.__dict__.get(
                "MAX_INITIALIZATION_TIMEOUT", 120
            )

            print(f"Starting bundle initialization (timeout: {original_timeout}s)...")

            # This should either succeed or fail quickly
            result = await bundle_manager.initialize_bundle(str(test_bundle_path))

            print("✅ Bundle initialization succeeded!")
            print(f"Bundle ID: {result.id}")
            print(f"Bundle path: {result.path}")
            print(f"Kubeconfig path: {result.kubeconfig_path}")
            print(f"Kubeconfig exists: {result.kubeconfig_path.exists()}")
            print(f"Host only: {result.host_only_bundle}")

        except Exception as e:
            print(f"❌ Bundle initialization failed: {e}")
            print(f"Exception type: {type(e)}")

            # Check if there are any alternative kubeconfig files created
            print("\n=== Checking for alternative kubeconfig files ===")

            # Check common locations where sbctl might create kubeconfig
            temp_locations = [
                "/tmp",
                "/var/folders",  # macOS temp directories
                temp_bundle_dir,
            ]

            for location in temp_locations:
                if isinstance(location, str):
                    location = Path(location)

                if location.exists():
                    print(f"Checking {location}...")
                    try:
                        # Look for kubeconfig files
                        kubeconfig_files = list(location.glob("**/kubeconfig*"))
                        if kubeconfig_files:
                            print(f"  Found kubeconfig files: {kubeconfig_files}")

                        # Look for local-kubeconfig files
                        local_kubeconfig_files = list(location.glob("**/local-kubeconfig*"))
                        if local_kubeconfig_files:
                            print(f"  Found local-kubeconfig files: {local_kubeconfig_files}")

                    except Exception as search_e:
                        print(f"  Error searching {location}: {search_e}")

        finally:
            # Cleanup
            print("\n=== Cleanup ===")
            try:
                await bundle_manager._cleanup_active_bundle()
                print("✅ Cleanup completed")
            except Exception as cleanup_e:
                print(f"❌ Cleanup error: {cleanup_e}")


if __name__ == "__main__":
    asyncio.run(test_sbctl_direct())
