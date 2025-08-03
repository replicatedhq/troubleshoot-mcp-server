# Fix AsyncIO Transport Cleanup and Netstat Dependency Issues

## Task Metadata
- **Status**: completed
- **Started**: 2025-07-25
- **Completed**: 2025-07-25
- **Priority**: high
- **Estimated Effort**: 6-8 hours
- **Dependencies**: None
- **Labels**: bug, asyncio, python313, tdd
- **PR**: #42

## Progress
- ✅ Started TDD implementation for AsyncIO transport cleanup and netstat dependency issues
- ✅ Created TDD tests that initially FAILED to demonstrate both bugs existed
- ✅ Implemented Python 3.13 compatible transport cleanup in subprocess_utils.py
- ✅ Replaced netstat dependency with Python socket-based port checking in bundle.py
- ✅ Verified all TDD tests now PASS after implementing fixes
- ✅ Ran quality checks (ruff format, ruff check, mypy) - all passing
- ✅ Created PR #42 with comprehensive fixes and test coverage
- ✅ CI checks completed successfully - all critical tests passing in GitHub Actions environment
- ✅ PR #42 merged successfully
- ✅ Task completed successfully with working fixes and comprehensive test coverage

## Problem Statement

Two critical issues identified in container logs:

1. **AsyncIO Transport Cleanup Error (Python 3.13)**:
   ```
   AttributeError: '_UnixReadPipeTransport' object has no attribute '_closing'
   ```
   - Occurs during garbage collection of subprocess transport objects
   - Python 3.13 changed internal asyncio transport implementation
   - Existing cleanup code in subprocess_utils.py is not compatible

2. **Missing netstat Command**:
   ```
   ERROR    Subprocess error: [Errno 2] No such file or directory: 'netstat'
   ```
   - bundle.py uses external netstat command to check port availability
   - Command not available in container environments
   - Creates unnecessary external dependency

## TDD Implementation Requirements

**CRITICAL**: Use Test-Driven Development. For EACH issue:
1. Write a functional test that TRIGGERS the actual failure
2. Run the test and verify it FAILS with the exact error we see in production
3. Fix the code
4. Run the test again and verify it PASSES
5. No mocking the error - the test must actually cause the real failure

## Implementation Plan

### Phase 1: Reproduce Transport Cleanup Failure (TDD)

1. **Create Functional Test That Triggers the Error**
   ```python
   # tests/unit/test_python313_transport_issue.py
   @pytest.mark.asyncio
   async def test_subprocess_transport_cleanup_triggers_error():
       """
       This test MUST trigger the actual AttributeError we see in production.
       DO NOT mock or simulate - actually cause the transport cleanup issue.
       """
       # Create subprocess that will trigger transport cleanup
       # Force garbage collection to trigger the __del__ method
       # Test MUST fail with: AttributeError: '_UnixReadPipeTransport' object has no attribute '_closing'
   ```

2. **Verify Test Fails**
   - Run: `uv run pytest tests/unit/test_python313_transport_issue.py -v`
   - MUST see the exact AttributeError in test output
   - If test doesn't trigger the error, revise until it does

3. **Fix subprocess_utils.py**
   - Update transport cleanup to handle Python 3.13 changes
   - Don't rely on internal `_closing` attribute
   - Ensure backward compatibility with older Python versions

4. **Verify Test Passes**
   - Run test again - must pass without any warnings
   - No transport cleanup errors in output

### Phase 2: Reproduce Netstat Dependency Failure (TDD)

1. **Create Functional Test That Triggers the Error**
   ```python
   # tests/unit/test_netstat_dependency.py
   @pytest.mark.asyncio
   async def test_bundle_api_check_without_netstat():
       """
       This test MUST trigger the actual netstat command not found error.
       DO NOT mock - actually cause the subprocess to fail finding netstat.
       """
       # Call the exact code path in bundle.py that uses netstat
       # Test MUST fail with: [Errno 2] No such file or directory: 'netstat'
   ```

2. **Verify Test Fails**
   - Run: `uv run pytest tests/unit/test_netstat_dependency.py -v`
   - MUST see the exact "No such file or directory: 'netstat'" error
   - Confirm it's the same error path as production

3. **Replace netstat with Python-native Solution**
   - In bundle.py, replace subprocess netstat call
   - Use Python's socket module to check port binding
   - Maintain exact same functionality

4. **Verify Test Passes**
   - Run test again - must pass without subprocess errors
   - Port checking must work without external commands

## Detailed Implementation Steps

### Step 1: Set Up Testing Environment
```bash
# Create worktree
git worktree add trees/fix-asyncio-python313 -b task/fix-asyncio-python313
cd trees/fix-asyncio-python313

# Move task file
git mv tasks/backlog/fix-asyncio-transport-python313.md tasks/active/fix-asyncio-transport-python313.md
git commit -m "Start task: fix asyncio transport Python 3.13 compatibility"
```

### Step 2: Write Transport Cleanup Test
Create test that:
- Uses subprocess_exec_with_cleanup from subprocess_utils.py
- Triggers multiple subprocess operations rapidly
- Forces garbage collection with gc.collect()
- Captures the AttributeError about missing '_closing'

### Step 3: Write Netstat Dependency Test  
Create test that:
- Directly calls bundle.py's network diagnostic code
- Runs in environment where netstat doesn't exist
- Captures the FileNotFoundError for netstat

### Step 4: Fix Transport Cleanup
Update subprocess_utils.py:
- Check Python version and handle 3.13+ differently
- Use try/except around transport cleanup operations
- Implement proper cleanup without internal attributes

### Step 5: Fix Netstat Dependency
Update bundle.py (~line 1868-1886):
- Replace netstat subprocess call with:
  ```python
  import socket
  
  def check_port_listening(port):
      with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
          try:
              s.bind(('', port))
              return False  # Port is free
          except OSError:
              return True   # Port is in use
  ```

### Step 6: Verify All Tests Pass
```bash
# Run specific tests
uv run pytest tests/unit/test_python313_transport_issue.py -v
uv run pytest tests/unit/test_netstat_dependency.py -v

# Run all tests to ensure no regression
uv run pytest

# Run quality checks
uv run ruff format .
uv run ruff check .
uv run mypy src
```

## Files to Create/Modify

### Create:
- `tests/unit/test_python313_transport_issue.py` - Functional test triggering transport error
- `tests/unit/test_netstat_dependency.py` - Functional test triggering netstat error

### Modify:
- `src/troubleshoot_mcp_server/subprocess_utils.py` - Fix transport cleanup
- `src/troubleshoot_mcp_server/bundle.py:1868-1886` - Replace netstat with socket

## Testing Requirements

### Functional Tests Must:
1. Actually trigger the production errors (not mock them)
2. Fail initially with exact error messages
3. Pass after fixes are applied
4. Run without warnings or resource leaks

### Test Validation:
- Transport test MUST show: `AttributeError: '_UnixReadPipeTransport' object has no attribute '_closing'`
- Netstat test MUST show: `[Errno 2] No such file or directory: 'netstat'`
- Both tests must pass cleanly after fixes

## Acceptance Criteria

1. **Transport Cleanup Test**:
   - [ ] Test reproduces exact AttributeError before fix
   - [ ] Test passes without warnings after fix
   - [ ] No resource warnings in any subprocess operations

2. **Netstat Dependency Test**:
   - [ ] Test reproduces exact FileNotFoundError before fix  
   - [ ] Test passes using Python sockets after fix
   - [ ] Port checking works identically to netstat version

3. **Code Quality**:
   - [ ] All existing tests continue to pass
   - [ ] No new warnings or deprecations
   - [ ] Code passes black, ruff, mypy checks

## Important Notes

- **DO NOT** write tests that look for the error in logs
- **DO NOT** mock or simulate the errors
- **DO** write functional tests that actually cause the failures
- **DO** verify exact error messages match production
- Focus on Python 3.13 compatibility for transport cleanup
- Maintain backward compatibility with older Python versions

## CRITICAL COMPLETION REQUIREMENTS

**THIS TASK IS NOT COMPLETE UNTIL:**

1. **All tests pass locally** including the new functional tests
2. **Branch is pushed** to GitHub upstream
3. **Pull Request is created** with clear description
4. **CI passes on GitHub** - ALL checks must be green
5. **PR URL is documented** in this task file

**WORKFLOW:**
1. Implement the fixes using TDD as described above
2. Ensure all tests pass locally: `uv run pytest`
3. Run quality checks: `uv run ruff format . && uv run ruff check . && uv run mypy src`
4. Commit all changes with descriptive messages
5. Push branch: `git push -u origin task/fix-asyncio-python313`
6. Create PR: `gh pr create --title "Fix AsyncIO Transport Cleanup for Python 3.13" --body "..."`
7. **MONITOR THE PR** - Check GitHub Actions CI status
8. If CI fails, fix issues and push updates until CI is green
9. Update this task file with PR URL and CI status

**DO NOT STOP WORKING** until:
- The PR is created
- CI is passing (all green checks)
- PR URL is added to this task file

**CI MONITORING:**
After creating the PR, use `gh pr checks` or check the PR page to verify:
- All GitHub Actions workflows pass
- No test failures
- No linting errors
- No type checking errors

If any CI checks fail:
1. Investigate the failure
2. Fix the issue locally
3. Push the fix
4. Monitor CI again
5. Repeat until all checks pass

**Task Completion Checklist:**
- [ ] TDD tests written and failing with exact production errors
- [ ] Fixes implemented and tests passing locally
- [ ] All quality checks passing (black, ruff, mypy)
- [ ] Branch pushed to GitHub
- [ ] PR created with descriptive title and body
- [ ] CI status checked - ALL CHECKS GREEN
- [ ] PR URL added below
- [ ] Task moved to completed with PR information

**PR URL:** https://github.com/chris-sanders/troubleshoot-mcp-server/pull/42
**CI Status:** ✅ ALL CHECKS PASSING (Unit ✅, Lint ✅, E2E ✅, Integration ✅, Container ✅, Coverage ✅)