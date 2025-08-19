# Task: Fix Container Shell Dependency Regression

## Metadata
- **Status**: active
- **Started**: 2025-08-19
- **Priority**: critical
- **Dependencies**: none

## Context

The MCP server container is failing with OCI runtime errors about missing `/bin/sh`, indicating a regression in container build process. The errors.txt file shows 254+ identical errors:

```
Error: crun: executable file `/bin/sh` not found in $PATH: No such file or directory: OCI runtime attempted to invoke a command that was not found
```

This suggests a recent change broke the shell environment in the container, affecting sbctl's ability to execute internal shell commands.

## Root Cause Analysis

**Current Issue**: The sbctl binary downloaded via melange configuration expects `/bin/sh` to be available for internal operations, but the current apko.yaml container configuration lacks a complete shell environment.

**Key Finding**: This is a REGRESSION caused by removing pre-built packages in v1.12.0. 

**Root Cause Identified**: 
- v1.11.1 had pre-built packages that accidentally included busybox in final container
- v1.12.0 removed pre-built packages (commit d38118d) forcing fresh builds
- busybox was only in .melange.yaml (build env) but NOT in apko.yaml (runtime env)  
- Fresh builds don't include busybox in final container, causing sbctl to fail

**Configuration Issue**: busybox is in build environment but missing from runtime environment.

## Implementation Plan

### Phase 1: Version Comparison and Root Cause (PRIORITY)
1. **Compare v1.11.1 vs v1.12 configurations**:
   - Check if busybox was present in v1.11.1 apko.yaml
   - Identify exact changes between working and broken versions
   - Document what configuration worked in v1.11.1

2. **Create functional test to reproduce failure** (COMPLETED):
   - Test runs INSIDE the container (not external container orchestration)
   - Test focuses on FUNCTIONALITY: "Does sbctl serve work?" not implementation details
   - Test MUST FAIL before applying fix
   - Test MUST PASS after adding busybox to apko.yaml  
   - Test uses real support bundle from fixtures (244KB real bundle already present)
   - Located: `tests/integration/test_sbctl_shell_dependency_regression.py`

3. **How to run the reproduction test**:
   ```bash
   # Build current (broken) container
   ./scripts/build.sh
   
   # Run test inside container - should FAIL with sbctl serve error
   podman run --rm -v $(pwd):/workspace -w /workspace \
     troubleshoot-mcp-server:test \
     python -m pytest tests/integration/test_sbctl_shell_dependency_regression.py::test_sbctl_serve_functionality -v
   
   # After fix: add busybox to apko.yaml, rebuild, rerun - should PASS
   ```

4. **Test Design Philosophy**:
   - Tests functionality, not dependencies
   - If sbctl stops needing shell in future, test still passes (as long as sbctl works)
   - Implementation-agnostic approach
   - Real bundle testing with actual sbctl serve process

## Test Implementation

Create `tests/integration/test_sbctl_shell_dependency_regression.py`:

```python
"""
Functional test for sbctl serve functionality in container environment.

This test verifies that sbctl serve works correctly in the container,
regardless of the underlying implementation details.
"""

import pytest
import subprocess
import time
from pathlib import Path


pytestmark = [pytest.mark.integration, pytest.mark.container]


def test_sbctl_serve_functionality():
    """
    Test that sbctl serve works in the container environment.
    
    This test focuses on FUNCTIONALITY, not implementation details.
    It should FAIL if sbctl serve doesn't work, regardless of why.
    It should PASS if sbctl serve works, regardless of how.
    """
    # Skip if not in container environment
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container:
        pytest.skip("This test must run inside the container")
    
    # Use the test bundle from fixtures  
    test_bundle_path = Path(__file__).parent.parent / "fixtures" / "support-bundle-2025-04-11T14_05_31.tar.gz"
    if not test_bundle_path.exists():
        pytest.skip("Test bundle not available")
    
    # Verify sbctl binary exists
    sbctl_path = Path("/usr/bin/sbctl")
    if not sbctl_path.exists():
        pytest.fail("sbctl binary not found at /usr/bin/sbctl")
    
    # Test sbctl serve functionality
    # We'll start it and check if it starts successfully (doesn't immediately crash)
    process = subprocess.Popen([
        "/usr/bin/sbctl", "serve", "--support-bundle-location", str(test_bundle_path)
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    
    # Give sbctl a moment to start up or fail
    try:
        stdout, stderr = process.communicate(timeout=10)
        
        # If process exited, check why
        if process.returncode != 0:
            error_output = stderr + stdout
            pytest.fail(
                f"sbctl serve failed to start or crashed immediately. "
                f"Return code: {process.returncode}. "
                f"Error output: {error_output}"
            )
        
        # If we get here, sbctl serve started and ran successfully for the timeout period
        print("SUCCESS: sbctl serve started and ran without errors")
        
    except subprocess.TimeoutExpired:
        # Timeout means sbctl is still running - this is good!
        # Kill the process and consider the test passed
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        
        print("SUCCESS: sbctl serve started successfully and continued running")
    
    except Exception as e:
        # Clean up process if something went wrong
        if process.poll() is None:
            process.terminate()
            process.wait()
        pytest.fail(f"Unexpected error testing sbctl serve: {e}")


def test_sbctl_help_command():
    """
    Basic test that sbctl --help works.
    
    This is a simple smoke test to verify the binary is functional.
    """
    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    if not in_container:
        pytest.skip("This test must run inside the container")
    
    result = subprocess.run([
        "/usr/bin/sbctl", "--help"
    ], capture_output=True, text=True, timeout=10)
    
    if result.returncode != 0:
        pytest.fail(f"sbctl --help failed: {result.stderr}")
    
    # Basic validation that help output looks reasonable
    if "sbctl" not in result.stdout and "usage" not in result.stdout.lower():
        pytest.fail(f"sbctl --help output doesn't look like help text: {result.stdout}")
    
    print("SUCCESS: sbctl --help works correctly")
```

### Phase 2: Test Alternative sbctl Usage Patterns
1. **Test sbctl serve variations** in container that fails:
   ```bash
   # Test different invocation methods
   sbctl serve --support-bundle-location bundle.tar.gz
   SHELL=/bin/false sbctl serve --support-bundle-location bundle.tar.gz  
   sbctl serve --help  # Does help work without shell?
   ```

2. **Test minimal shell alternatives** if needed:
   - Try adding just `ash` package instead of full busybox
   - Test with `/bin/sh` symlink to `/bin/ash`

### Phase 3: Fix Implementation
1. **Apply minimal fix** based on Phase 1 & 2 findings
2. **Add regression test** that catches this specific failure
3. **Document why shell is needed** (if it truly is needed)

### Phase 4: Ensure Fix Works
1. **Verify v1.12+ containers work** with the fix
2. **Test that no new security issues** are introduced
3. **Confirm all existing functionality** still works

### Phase 3: Regression Prevention
1. **Add container shell dependency test**:
   - Test `/bin/sh` exists and is executable
   - Test sbctl can execute internal shell commands
   - Validate full sbctl serve startup process

2. **Enhance CI validation**:
   - Test real sbctl binary (not just mock) in container environment
   - Add integration test that catches shell dependency issues
   - Ensure container tests run sbctl serve, not just --help

### Phase 4: Root Cause Resolution
1. **Document the change** that caused the regression
2. **Update build process** to prevent similar issues
3. **Review security implications** if shell was removed intentionally
4. **Balance security vs functionality** requirements

## Files to Modify

1. **apko.yaml** - Add shell dependencies
2. **tests/e2e/test_container_production_validation.py** - Add shell validation
3. **tests/integration/test_real_sbctl_container.py** - New comprehensive test
4. **Possibly .melange.yaml** - If shell deps needed at build time

## Testing Strategy

### Existing Tests to Enhance:
- Container production validation (add shell existence check)
- sbctl binary validation (test serve command, not just --help)

### New Tests to Add:
- Shell dependency validation in container
- Real sbctl serve startup in container environment
- Integration test with actual bundle processing

### Regression Prevention:
- CI stage that validates shell dependencies
- Container startup test that exercises sbctl serve
- Automated detection of missing shell environment

## Acceptance Criteria

1. ✅ No OCI runtime `/bin/sh` errors in container logs
2. ✅ sbctl serve starts successfully in production container
3. ✅ Shell dependencies properly included in container image
4. ✅ CI catches shell dependency regressions
5. ✅ Root cause of regression documented and addressed
6. ✅ Balance maintained between security and functionality
7. ✅ All existing functionality preserved

## Risk Assessment

**High Risk**: This is a critical regression affecting core container functionality

**Mitigation**: 
- Thorough testing of shell dependency fix
- Review of security implications of adding shell
- Validation that change doesn't break existing workflows

## Success Metrics

- Zero OCI runtime shell errors in container logs
- Successful sbctl serve startup in all container environments  
- CI prevents future shell dependency regressions
- Container maintains security posture while providing required functionality

## Notes

This task addresses a regression, not a new feature. The focus should be on understanding what changed and restoring working functionality while preventing similar issues in the future.