#!/usr/bin/env python3
"""
Test bundle manager directly to isolate the issue.
"""

import asyncio
import tempfile
import os
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.mcp_server_troubleshoot.bundle import BundleManager
from tests.integration.mcp_test_utils import get_test_bundle_path


async def test_direct_bundle_manager():
    """Test bundle manager directly without MCP layer."""
    print("=== Testing Bundle Manager Directly ===")

    # Get test bundle
    test_bundle_path = get_test_bundle_path()
    print(f"Test bundle: {test_bundle_path}")
    print(f"Bundle exists: {test_bundle_path.exists()}")
    print(f"Bundle size: {test_bundle_path.stat().st_size} bytes")

    # Create temp directory for bundles
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_bundle_dir = Path(temp_dir)
        print(f"Temp bundle directory: {temp_bundle_dir}")

        # Copy bundle to temp directory
        bundle_name = test_bundle_path.name
        test_bundle_copy = temp_bundle_dir / bundle_name
        test_bundle_copy.write_bytes(test_bundle_path.read_bytes())
        print(f"Copied bundle to: {test_bundle_copy}")

        # Set environment
        os.environ["SBCTL_TOKEN"] = "test-token-12345"

        # Create bundle manager
        bundle_manager = BundleManager(temp_bundle_dir)

        print("\n=== Testing bundle initialization directly ===")

        try:
            import time

            start_time = time.time()

            print("Starting bundle initialization...")

            # This should either succeed or fail quickly with our fix
            result = await asyncio.wait_for(
                bundle_manager.initialize_bundle(str(test_bundle_copy)),
                timeout=15.0,  # 15 second timeout
            )

            elapsed = time.time() - start_time
            print(f"✅ Bundle initialization completed in {elapsed:.2f} seconds")
            print(f"Bundle ID: {result.id}")
            print(f"Bundle path: {result.path}")
            print(f"Kubeconfig path: {result.kubeconfig_path}")
            print(f"Kubeconfig exists: {result.kubeconfig_path.exists()}")
            print(f"Host only: {result.host_only_bundle}")

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            print(f"❌ Bundle initialization timed out after {elapsed:.2f} seconds")
            print("This suggests there's still an issue with the bundle manager logic")

            # Get diagnostic info
            try:
                diagnostics = await bundle_manager.get_diagnostic_info()
                print(f"Diagnostics: {diagnostics}")
            except Exception as diag_err:
                print(f"Failed to get diagnostics: {diag_err}")

        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ Bundle initialization failed after {elapsed:.2f} seconds: {e}")
            print(f"Exception type: {type(e)}")

            # Get diagnostic info
            try:
                diagnostics = await bundle_manager.get_diagnostic_info()
                print(f"Diagnostics: {diagnostics}")
            except Exception as diag_err:
                print(f"Failed to get diagnostics: {diag_err}")

        finally:
            print("\n=== Cleanup ===")
            try:
                await bundle_manager.cleanup()
                print("✅ Cleanup completed")
            except Exception as cleanup_e:
                print(f"❌ Cleanup error: {cleanup_e}")


if __name__ == "__main__":
    asyncio.run(test_direct_bundle_manager())
