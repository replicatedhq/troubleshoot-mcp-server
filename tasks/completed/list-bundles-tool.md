# Task: Add List Bundles Tool for Improved Bundle Discovery

## Objective
Create a new tool that scans the bundle storage directory to find available compressed bundle files and lists them, making it easier for users to identify which bundles are available for initialization.

## Context
Currently, users need to know the exact path to a bundle file to initialize it with the `initialize_bundle` tool. There's no easy way to discover which bundles have been already downloaded to the server. Additionally, the documentation for tools doesn't clearly explain the workflow where initialization must be done before file exploration tools can be used.

The new tool will help users by:
1. Finding all compressed bundle files in the bundle storage directory
2. Listing them in a format that makes it easy to copy the path for use with `initialize_bundle`
3. Making the workflow more intuitive

Related files:
- `/src/mcp_server_troubleshoot/bundle.py` - Contains bundle manager implementation
- `/src/mcp_server_troubleshoot/server.py` - Contains MCP tool definitions
- `/src/mcp_server_troubleshoot/files.py` - Contains file exploration functionality

## Success Criteria
- [x] Create a new tool called `list_available_bundles` that scans the bundle storage directory
- [x] The tool should find compressed files that are likely to be support bundles (e.g., .tar.gz, .tgz files)
- [x] Peek inside the likely bundles to confirm the support-bundle layout, checking for things like cluster-info folder, use the test bundle as an example
- [x] Return bundle details including file name, size, and modification time
- [x] Update docstrings for file exploration tools to clearly indicate they require a bundle to be initialized first
- [x] Add error messages that suggest using the new tool when file operations are attempted without an initialized bundle
- [x] Write tests for the new functionality

## Dependencies
N/A

## Implementation Plan
1. Create tests for any changes before implementing, and validate implementation against them.
2. Create a new `ListAvailableBundlesArgs` class in a suitable module (likely `bundle.py`)
3. Implement a method in the `BundleManager` class to scan the bundle directory for bundle files
4. Add a new tool in `server.py` to expose this functionality via the MCP protocol
5. Update docstrings for all file exploration tools to clearly state they require an initialized bundle
6. Enhance error messages to guide users to try the new tool if they haven't initialized a bundle

## Validation Plan
- Test the tool with various bundle directory contents containing different types of files
- Verify it correctly identifies only valid bundle files
- Ensure the tool's output includes all necessary information for users to select a bundle
- Test error messages are clear when file operations are attempted without initialization
- Verify the docstrings are clear and comprehensive

## Evidence of Completion
- [x] Output of pytest, and linting/formatting tests:
  ```
  $ python -m pytest
  ============================= 72 passed in 42.99s ==============================
  
  $ ruff check src/mcp_server_troubleshoot/bundle.py src/mcp_server_troubleshoot/server.py tests/unit/test_list_bundles.py
  All checks passed!
  
  $ ruff format .
  All done! ✨ 🍰 ✨
  20 files reformatted, 11 files left unchanged.
  ```

- [x] List of all manual testing performed:
  - Verified the new tool scans the bundle directory and identifies valid bundles
  - Tested error messages when attempting file operations without an initialized bundle
  - Verified docstrings updates to clearly indicate workflow requirements
  - Tested the tool with both valid and invalid bundle files

- [x] Explicit explanation of testing performed for this feature:
  - Created a test file `tests/unit/test_list_bundles.py` with 6 test cases:
    - Test listing bundles with an empty directory
    - Test listing bundles with a valid bundle
    - Test listing bundles with an invalid bundle
    - Test listing bundles with both valid and invalid bundles
    - Test listing bundles with a non-existing directory
    - Test the bundle validity checker functionality
  - All tests pass successfully
  - Added a test case in the integration test script `mcp_client_test.py` to verify the new MCP tool works correctly
  - Added comprehensive validation in the bundle manager to verify support bundle structure

## Notes
The bundle storage directory is typically `/data/bundles` in the container environment, but can be configured via command-line arguments or environment variables. The implementation respects this configuration by reading the storage directory location from the bundle manager, which follows the configuration hierarchy.

## Progress Updates
1. Analyzed bundle structures and existing code to understand implementation requirements
2. Added `ListAvailableBundlesArgs` and `BundleFileInfo` models to represent arguments and results
3. Implemented `list_available_bundles` method in BundleManager with validation logic
4. Added MCP tool in server.py with formatted output and usage instructions
5. Updated docstrings to indicate workflow requirements
6. Improved error messages to guide users
7. Added comprehensive test suite to validate functionality
8. Fixed code style issues with ruff format and ruff check
9. Verified implementation passes all tests