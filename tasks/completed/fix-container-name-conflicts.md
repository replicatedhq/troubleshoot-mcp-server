# Fix Container Name Conflicts Between Development and Production

**Status**: completed  
**Priority**: high  
**Estimated Effort**: 2-4 hours  
**Category**: infrastructure  
**Started**: 2025-07-28
**Completed**: 2025-07-28
**PR**: #46

## Progress Log
- 2025-07-28: Started task, created worktree, began implementation
- 2025-07-28: Completed core changes - updated build script and test harnesses  
- 2025-07-28: Updated documentation (PODMAN.md, README.md, TESTING_STRATEGY.md)
- 2025-07-28: All tests passing, quality checks passed, PR created: #46
- 2025-07-28: PR #46 merged successfully  

## Problem Statement

Local development and testing currently use the same container image name (`troubleshoot-mcp-server:latest`) as the official production releases. This causes conflicts when users have the official container installed and want to run local development or testing on the same machine.

**Specific Issues:**
- Local builds overwrite official container images
- Testing from this repo conflicts with upstream container usage
- Users can't simultaneously use official releases and develop locally
- No clear separation between development and production containers

## Solution

Add a `-dev` suffix to local development builds while keeping official releases unchanged:

- **Local Development**: `troubleshoot-mcp-server-dev:latest`
- **Official Releases**: `troubleshoot-mcp-server:latest` 
- **CI Override**: Already works correctly via environment variables

## Implementation Plan

### Phase 1: Core Changes (HIGH PRIORITY - Use Sub-agents in PARALLEL)

**Task 1A**: Update Build Script Default
- **File**: `scripts/build.sh:6`
- **Change**: `IMAGE_NAME=${IMAGE_NAME:-"troubleshoot-mcp-server-dev"}`
- **Sub-agent**: Use general-purpose agent to make this single-line change

**Task 1B**: Update Test Harnesses (PARALLEL with 1A)
- **Files**: 
  - `tests/conftest.py:177,250,254`
  - `tests/e2e/test_container_production_validation.py:276,281,312`
  - `tests/e2e/test_container_bundle_validation.py:24`
- **Change**: Replace `troubleshoot-mcp-server:latest` → `troubleshoot-mcp-server-dev:latest`
- **Sub-agent**: Use general-purpose agent to search and replace across test files

**Task 1C**: Verify CI Workflow (PARALLEL with 1A/1B)
- **File**: `.github/workflows/publish-container.yaml`
- **Action**: Confirm existing IMAGE_NAME override works correctly
- **Sub-agent**: Use general-purpose agent to review workflow and validate override behavior

### Phase 2: Documentation Updates (MEDIUM PRIORITY)

**Task 2A**: Update Container Documentation
- **Files**: `PODMAN.md`, `docs/user_guide.md` 
- **Action**: Update examples to show development vs production image usage
- **Sub-agent**: Use general-purpose agent to update documentation examples

**Task 2B**: Update README and Quick Start
- **Files**: `README.md`
- **Action**: Add section clarifying image variants and when to use each
- **Sub-agent**: Use general-purpose agent to add clarity section about container variants

## Parallel Development Strategy

### Use Sub-agents for Maximum Speed:

1. **Launch 3 sub-agents simultaneously** for Phase 1 tasks:
   ```
   Agent A: Update build script default
   Agent B: Update all test harnesses  
   Agent C: Verify CI workflow behavior
   ```

2. **After Phase 1 completion**, launch 2 sub-agents for Phase 2:
   ```
   Agent D: Update container documentation
   Agent E: Update README and quick start
   ```

3. **Validation**: Run single comprehensive test to verify all changes work together

## Files to Modify

### Core Changes:
- `scripts/build.sh` - Change default IMAGE_NAME
- `tests/conftest.py` - Update container image references
- `tests/e2e/test_container_production_validation.py` - Update expected image name
- `tests/e2e/test_container_bundle_validation.py` - Update CONTAINER_IMAGE constant

### Documentation:
- `PODMAN.md` - Update examples for dev vs prod usage
- `docs/user_guide.md` - Clarify image variants
- `README.md` - Add container variants section

### Verification:
- `.github/workflows/publish-container.yaml` - Confirm override works

## Testing Strategy

**No new tests needed** - existing tests should pass after harness updates.

### Test Categories:
- **Unit Tests**: Already cover build script functionality
- **Integration Tests**: Will use new `-dev` image names automatically  
- **E2E Tests**: Updated to expect `-dev` suffix
- **Container Tests**: Will build and test `-dev` images locally

### Test Execution:
```bash
# Local development (uses -dev images)
uv run pytest tests/e2e/test_container*.py -v

# Verify build works with new naming
MELANGE_TEST_BUILD=true ./scripts/build.sh

# Confirm CI override still works
IMAGE_NAME="troubleshoot-mcp-server" ./scripts/build.sh
```

## Dependencies

- **Build System**: Existing melange/apko process (unchanged)
- **CI/CD**: GitHub Actions workflow (already has override mechanism)  
- **Container Runtime**: Podman (unchanged)
- **Environment Variables**: IMAGE_NAME override (already supported)

## Acceptance Criteria

✅ **Container Name Isolation**
- [ ] Local builds default to `troubleshoot-mcp-server-dev:latest`
- [ ] Official releases remain `troubleshoot-mcp-server:latest`
- [ ] Both images can coexist on same system without conflicts

✅ **Build System Compatibility**
- [ ] Default local builds use `-dev` suffix
- [ ] Environment variable `IMAGE_NAME` override works
- [ ] CI workflow produces official names (no `-dev` suffix)

✅ **Testing Reliability**
- [ ] All existing tests pass with updated image names
- [ ] Container tests build and use `-dev` images locally
- [ ] No test skipping or reliability issues

✅ **Documentation Clarity**
- [ ] Clear distinction between dev and production images
- [ ] Updated examples reflect appropriate usage
- [ ] Users understand when to use each image variant

## Risks and Mitigations

**Risk**: CI workflow doesn't override correctly
**Mitigation**: Test workflow with manual trigger before merging

**Risk**: Existing users confused by new naming
**Mitigation**: Clear documentation and migration notes

**Risk**: Tests fail after image name changes  
**Mitigation**: Comprehensive testing before merging changes

## Success Metrics

- Zero conflicts between local development and official container usage
- All tests pass with new naming convention
- CI continues to publish official releases correctly
- Clear separation between development and production environments

## Notes

This is a simple, focused change that solves the core problem without over-engineering. The parallel sub-agent approach will allow rapid implementation across multiple files simultaneously while maintaining quality and testing coverage.