#!/usr/bin/env python3
"""
Debug script to test MCP protocol step by step.
"""

import asyncio
import tempfile
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from tests.integration.mcp_test_utils import MCPTestClient, get_test_bundle_path


async def debug_mcp_protocol():
    """Debug MCP protocol communication step by step."""
    print("=== Debug MCP Protocol Communication ===")

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

        env = {"SBCTL_TOKEN": "test-token-12345"}

        print("\n=== Step 1: Starting MCP Server ===")
        client = MCPTestClient(bundle_dir=temp_bundle_dir, env=env)

        try:
            await client.start_server(timeout=10.0)
            print("✅ Server started successfully")

            print("\n=== Step 2: MCP Initialization ===")
            init_response = await client.initialize_mcp()
            print(f"✅ MCP initialized: {init_response}")

            print("\n=== Step 3: Testing initialize_bundle tool ===")
            print(f"Calling initialize_bundle with path: {test_bundle_copy}")

            # This is where it might be timing out
            try:
                content = await client.call_tool(
                    "initialize_bundle", {"bundle_path": str(test_bundle_copy)}
                )
                print(f"✅ Bundle loading result: {content}")

            except Exception as e:
                print(f"❌ Bundle loading failed: {e}")
                print(f"Exception type: {type(e)}")
                return

            print("\n=== Step 4: Testing list_available_bundles ===")
            try:
                bundles_content = await client.call_tool("list_available_bundles")
                print(f"✅ Available bundles: {bundles_content}")
            except Exception as e:
                print(f"❌ List bundles failed: {e}")

            print("\n=== Step 5: Testing file operations ===")
            try:
                files_content = await client.call_tool("list_files", {"path": "."})
                print(f"✅ File listing: {files_content}")
            except Exception as e:
                print(f"❌ List files failed: {e}")

        except Exception as e:
            print(f"❌ Error: {e}")
            print(f"Exception type: {type(e)}")

        finally:
            print("\n=== Cleanup ===")
            await client.cleanup()
            print("✅ Cleanup completed")


if __name__ == "__main__":
    asyncio.run(debug_mcp_protocol())
