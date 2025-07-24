# Fix AsyncIO Transport Cleanup and Curl Dependency Issues

## Task Metadata
- **Status**: backlog
- **Priority**: high
- **Estimated Effort**: 8-12 hours
- **Dependencies**: None
- **Labels**: bug, testing, subprocess, asyncio

## Problem Statement

The MCP server is experiencing critical asyncio transport cleanup issues and external dependency failures:

1. **Transport Cleanup Issue**: `_UnixReadPipeTransport` objects aren't being properly cleaned up when subprocess operations complete, causing Python's garbage collector to warn about unclosed transports missing the `_closing` attribute.

2. **External Dependency Failure**: The MCP server uses external `curl` command in `bundle.py:1751` to check API server availability, but `curl` may not be available in all runtime environments, causing cascading failures.

## Error Evidence

```
Traceback (most recent call last):
  File "/usr/lib/python3.13/asyncio/unix_events.py", line 607, in __del__
    _warn(f"unclosed transport {self!r}", ResourceWarning, source=self)
  File "/usr/lib/python3.13/asyncio/unix_events.py", line 541, in __repr__
    elif self._closing:
AttributeError: '_UnixReadPipeTransport' object has no attribute '_closing'

WARNING  Error using curl to check API server: [Errno 2] No such file or directory: 'curl'
WARNING  API server is not available at any endpoint
ERROR    API server not available for kubectl command
```

## Root Cause Analysis

1. **Transport Cleanup**: Subprocess operations using `asyncio.create_subprocess_exec()` are not properly closing transport objects, leading to resource leaks and warnings during garbage collection.

2. **curl Dependency**: The `check_api_server_available()` method in `bundle.py` deliberately uses external `curl` command instead of Python's HTTP libraries, creating an unnecessary external dependency.

## Implementation Plan

### CRITICAL: Use Parallel Sub-Agents Aggressively

**AGENT INSTRUCTION**: Use the Task tool to create multiple parallel sub-agents for different aspects of this work. DO NOT work sequentially. Create at least 3-4 parallel sub-agents immediately:

1. **Sub-Agent 1**: Create reproduction tests for transport cleanup issues
2. **Sub-Agent 2**: Create reproduction tests for curl dependency failures  
3. **Sub-Agent 3**: Analyze existing subprocess usage patterns across codebase
4. **Sub-Agent 4**: Research aiohttp integration patterns in the existing codebase

Work in parallel to maximize efficiency and reduce total implementation time.

### Phase 1: Reproduce Issues with Tests (Priority: Critical)

**Before making any fixes**, create tests that reliably reproduce both issues:

1. **Transport Cleanup Reproduction Test**
   - Create `tests/unit/test_transport_cleanup_reproduction.py`
   - Write test that triggers `_UnixReadPipeTransport` cleanup warnings
   - Focus on subprocess operations that don't clean up properly
   - Ensure test fails with the current codebase

2. **Curl Dependency Reproduction Test**  
   - Create test that reproduces curl dependency failure
   - Mock environment where `curl` command is not available
   - Verify the exact error message matches production issue
   - Test should demonstrate the cascading failure pattern

### Phase 2: Implement Fixes

1. **Replace curl with aiohttp**
   - Modify `bundle.py:1751` `check_api_server_available()` method
   - Replace `asyncio.create_subprocess_exec("curl", ...)` with `aiohttp.ClientSession`
   - Maintain identical functionality for API server health checks
   - Preserve timeout behavior (currently 3.0 seconds)

2. **Implement Proper Transport Cleanup**
   - Create `src/mcp_server_troubleshoot/subprocess_utils.py` with cleanup utilities
   - Add explicit transport cleanup for all subprocess operations
   - Implement context managers for subprocess lifecycle management
   - Update existing subprocess calls in `bundle.py` and `kubectl.py`

### Phase 3: Comprehensive Testing

1. **Transport Cleanup Tests**
   - Verify all subprocess operations properly close transports
   - Test subprocess error scenarios and cleanup paths
   - Test subprocess cancellation and timeout cleanup

2. **HTTP Client Tests**
   - Test new aiohttp-based API server checks
   - Verify identical behavior to previous curl implementation
   - Test error handling and timeout scenarios

## Files to Create/Modify

### Files to Create:
- `tests/unit/test_transport_cleanup_reproduction.py`: Reproduction tests for transport issues
- `tests/unit/test_curl_dependency_reproduction.py`: Reproduction tests for curl dependency
- `tests/unit/test_transport_cleanup.py`: Comprehensive transport cleanup tests
- `src/mcp_server_troubleshoot/subprocess_utils.py`: Subprocess lifecycle utilities

### Files to Modify:
- `src/mcp_server_troubleshoot/bundle.py`: Replace curl with aiohttp, enhance cleanup
- `src/mcp_server_troubleshoot/kubectl.py`: Add explicit transport cleanup
- `tests/unit/test_bundle.py`: Add transport cleanup test coverage
- `tests/unit/test_kubectl.py`: Add subprocess lifecycle test coverage

## Technical Requirements

### Dependencies
- `aiohttp` (already in pyproject.toml)
- Python's `asyncio.subprocess` module
- Existing `clean_asyncio` fixture for test isolation

### Code Standards
- Use UV for all Python operations (`uv run pytest`, etc.)
- Follow existing code patterns and conventions
- Add type annotations and docstrings
- Handle errors with specific exceptions

## Acceptance Criteria

### Critical Success Metrics:
1. **Reproduction Tests Pass**: New reproduction tests demonstrate both issues
2. **No Transport Warnings**: Eliminate `_UnixReadPipeTransport` cleanup warnings
3. **No External Dependencies**: Replace curl with Python HTTP client
4. **Identical Functionality**: API server checks work identically to before

### Verification Steps:
1. Run reproduction tests - they should fail initially, pass after fixes
2. Run existing test suite - no new warnings or failures
3. Start MCP server in test environment - no transport errors
4. Execute subprocess operations - proper cleanup occurs
5. API server availability checks work without curl dependency

## Testing Strategy

### Unit Tests:
- **Reproduction Tests**: Demonstrate issues before fixes
- **Transport Cleanup Tests**: Verify proper resource cleanup
- **HTTP Client Tests**: Verify aiohttp-based API server checks
- **Subprocess Lifecycle Tests**: Test error scenarios and edge cases

### Integration Tests:
- **End-to-End Subprocess Tests**: Complete subprocess lifecycle testing
- **Resource Cleanup Tests**: Verify no resource leaks
- **API Server Integration Tests**: Real HTTP request testing

## Implementation Notes

### Transport Cleanup Implementation:
```python
# Example pattern for proper cleanup
async def safe_subprocess_exec(*args, **kwargs):
    """Execute subprocess with proper transport cleanup."""
    process = None
    try:
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        stdout, stderr = await process.communicate()
        return process.returncode, stdout, stderr
    finally:
        if process and process.returncode is None:
            process.terminate()
            await process.wait()
```

### aiohttp Replacement Pattern:
```python
# Replace curl subprocess with aiohttp
async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3.0)) as session:
    async with session.get(url) as response:
        return response.status == 200
```

## Progress Tracking

- [ ] Create parallel sub-agents for different work streams
- [ ] Reproduce transport cleanup issue with test
- [ ] Reproduce curl dependency failure with test  
- [ ] Analyze existing subprocess patterns
- [ ] Research aiohttp integration
- [ ] Replace curl with aiohttp in bundle.py
- [ ] Implement subprocess cleanup utilities
- [ ] Update all subprocess calls with proper cleanup
- [ ] Create comprehensive test coverage
- [ ] Verify all tests pass
- [ ] Verify MCP server runs without errors
- [ ] Complete code quality checks (black, ruff, mypy)

## Risk Mitigation

- **Regression Risk**: Comprehensive test coverage before and after changes
- **Performance Risk**: aiohttp should be faster than subprocess curl calls
- **Compatibility Risk**: Maintain identical API server check behavior
- **Resource Risk**: Explicit cleanup prevents resource leaks

## Definition of Done

- [ ] All reproduction tests initially fail, then pass after fixes
- [ ] Zero transport cleanup warnings in test runs
- [ ] MCP server operates without asyncio transport errors  
- [ ] API server checks work without external curl dependency
- [ ] All existing tests continue to pass
- [ ] Code quality checks pass (black, ruff, mypy)
- [ ] Documentation updated for any API changes