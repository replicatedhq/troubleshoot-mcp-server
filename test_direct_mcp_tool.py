#!/usr/bin/env python3
"""
Test MCP tool directly to isolate the issue.
"""

import asyncio
import tempfile
import os
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.mcp_server_troubleshoot.server import initialize_bundle
from src.mcp_server_troubleshoot.bundle import InitializeBundleArgs
from tests.integration.mcp_test_utils import get_test_bundle_path


async def test_direct_mcp_tool():
    """Test MCP tool directly without JSON-RPC layer."""
    print("=== Testing MCP Tool Directly ===")

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

        # Set environment (simulate MCP server environment)
        os.environ["SBCTL_TOKEN"] = "test-token-12345"
        os.environ["MCP_BUNDLE_STORAGE"] = str(temp_bundle_dir)

        print("\n=== Testing MCP tool call directly ===")

        try:
            import time

            start_time = time.time()

            print("Creating InitializeBundleArgs...")
            args = InitializeBundleArgs(source=str(test_bundle_copy))
            print(f"Args: source={args.source}, force={args.force}")

            print("Calling initialize_bundle tool...")

            # Call the MCP tool directly
            result = await asyncio.wait_for(
                initialize_bundle(args), timeout=15.0  # 15 second timeout
            )

            elapsed = time.time() - start_time
            print(f"✅ MCP tool call completed in {elapsed:.2f} seconds")
            print(f"Result type: {type(result)}")
            print(f"Result length: {len(result)}")

            if result:
                for i, content in enumerate(result):
                    print(f"Content {i}: type={content.type}")
                    print(
                        f"Content {i}: text={content.text[:200]}..."
                        if len(content.text) > 200
                        else f"Content {i}: text={content.text}"
                    )

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            print(f"❌ MCP tool call timed out after {elapsed:.2f} seconds")
            print("This suggests the issue is in the MCP tool layer")

        except Exception as e:
            elapsed = time.time() - start_time
            print(f"❌ MCP tool call failed after {elapsed:.2f} seconds: {e}")
            print(f"Exception type: {type(e)}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_direct_mcp_tool())
