#!/usr/bin/env python3
"""
Test bundle loading via MCP protocol to debug the timeout.
"""

import asyncio
import tempfile
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from tests.integration.mcp_test_utils import MCPTestClient, get_test_bundle_path


async def test_bundle_loading():
    """Test bundle loading step by step."""
    print("=== Testing Bundle Loading via MCP Protocol ===")

    # Get test bundle
    test_bundle_path = get_test_bundle_path()
    print(f"Using test bundle: {test_bundle_path}")
    print(f"Bundle exists: {test_bundle_path.exists()}")
    print(f"Bundle size: {test_bundle_path.stat().st_size} bytes")

    # Create temp directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_bundle_dir = Path(temp_dir)
        print(f"Temp directory: {temp_bundle_dir}")

        # Copy bundle to temp directory
        bundle_name = test_bundle_path.name
        test_bundle_copy = temp_bundle_dir / bundle_name
        test_bundle_copy.write_bytes(test_bundle_path.read_bytes())
        print(f"Copied bundle to: {test_bundle_copy}")
        print(f"Copied bundle size: {test_bundle_copy.stat().st_size} bytes")

        env = {"SBCTL_TOKEN": "test-token-12345"}

        print("\n=== Step 1: Starting MCP Server ===")
        client = MCPTestClient(bundle_dir=temp_bundle_dir, env=env)

        try:
            await client.start_server(timeout=10.0)
            print("✅ Server started successfully")

            print("\n=== Step 2: MCP Initialization ===")
            await client.initialize_mcp()
            print("✅ MCP initialized")

            print("\n=== Step 3: Testing initialize_bundle tool (with timeout tracking) ===")
            print(f"Calling initialize_bundle with path: {test_bundle_copy}")

            # Add timeout tracking to see where it gets stuck
            import time

            start_time = time.time()

            try:
                print("Sending tool call request...")
                content = await asyncio.wait_for(
                    client.call_tool("initialize_bundle", {"source": str(test_bundle_copy)}),
                    timeout=30.0,  # 30 second timeout to see what happens
                )
                elapsed = time.time() - start_time
                print(f"✅ Bundle loading completed in {elapsed:.2f} seconds")
                print(f"Result: {content}")

            except asyncio.TimeoutError:
                elapsed = time.time() - start_time
                print(f"❌ Bundle loading timed out after {elapsed:.2f} seconds")
                print("This suggests the initialize_bundle tool is hanging")
                return

            except Exception as e:
                elapsed = time.time() - start_time
                print(f"❌ Bundle loading failed after {elapsed:.2f} seconds: {e}")
                print(f"Exception type: {type(e)}")
                return

        except Exception as e:
            print(f"❌ Error: {e}")
            print(f"Exception type: {type(e)}")

        finally:
            print("\n=== Cleanup ===")
            await client.cleanup()
            print("✅ Cleanup completed")


if __name__ == "__main__":
    asyncio.run(test_bundle_loading())
