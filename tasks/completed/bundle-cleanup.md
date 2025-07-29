# Task: Implement Bundle Cleanup

## Metadata
**Status**: completed
**Created**: 2025-04-13
**Started**: 2025-04-13
**Completed**: 2025-04-13
**Branch**: bundle-cleanup
**PR**: #13
**PR URL**: https://github.com/chris-sanders/troubleshoot-mcp-server/pull/13
**PR Status**: Open - Pending Review

## Objective
Implement robust bundle cleanup to ensure proper resource management when a server is shutdown or a different bundle is activated.

## Context
Currently, when a bundle is activated, it is unzipped and initialized, but there is no cleanup mechanism when either:
1. The server shuts down
2. A different bundle is activated

This can lead to resource leaks, orphaned files/directories, and accumulation of unneeded extracted bundles over time. We need to implement a comprehensive cleanup strategy to address these scenarios.

Related documentation:
- [Component: Bundle Manager](/docs/components/bundle-manager.md)
- [System Architecture](/docs/architecture.md)

## Success Criteria
- [x] Implement cleanup for the currently active bundle when the server shuts down
- [x] Implement cleanup of the previous active bundle when a new bundle is activated
- [x] Add unit tests to verify both cleanup scenarios
- [x] Handle edge cases (e.g., failed cleanups, directory permissions)
- [x] Ensure cleanup happens in logical order (terminate processes first, then delete files)
- [x] Update documentation with cleanup implementation details

## Dependencies
- Existing Bundle Manager implementation
- Understanding of the current bundle extraction and activation flow

## Implementation Plan

1. Write comprehensive tests for cleanup scenarios first:
   - Test cleanup during bundle switching
   - Test cleanup during server shutdown
   - Test cleanup error handling and recovery

2. Review existing Bundle Manager to understand the current bundle activation approach:
   - Analyze how bundles are unzipped and stored
   - Understand what resources need to be cleaned up
   - Identify the appropriate points for adding cleanup logic

3. Enhance bundle cleanup implementation:
   - Extend `_cleanup_active_bundle()` method to properly remove extracted bundle directories
   - Add proper error handling for cleanup operations

4. Implement server shutdown cleanup:
   - Register shutdown handlers to ensure cleanup when server is stopped
   - Handle signals appropriately (e.g., SIGTERM, SIGINT)

5. Update documentation to reflect the new cleanup capabilities

## Validation Plan
- Run unit tests to verify cleanup functionality
- Run the full test suite to ensure no regressions (`pytest`)
- Run linting to ensure code quality (`ruff check .`)
- Run code formatting checks (`ruff format --check .`)
- Manually test bundle switching to ensure proper cleanup
- Verify server shutdown results in proper cleanup
- Check for any resource leaks using appropriate monitoring tools

## Notes
Ensure the cleanup implementation is resilient to different environments (Linux, macOS, etc.) and handles permissions appropriately. Consider adding logging to track cleanup operations for debugging purposes.