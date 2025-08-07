# Enhanced Error Messages for File/Directory Confusion

**Status:** completed  
**Priority:** medium  
**Effort:** 2-3 hours  
**Created:** 2025-01-21  
**Started:** 2025-08-07  

## Problem Statement

Users are encountering confusing error messages when they try to read directories that have corresponding files with common extensions. The specific reported issue:

> "cluster-resources/pods/kube-system - still trying directory instead of .json file"

Current error messages like "Path is not a file: cluster-resources/pods/kube-system" don't provide helpful guidance when there's an obvious alternative like "kube-system.json" in the same directory.

## Scope

Enhance the `read_file()` tool to provide helpful suggestions when users try to read a directory that has corresponding files with common extensions (.json, .yaml, .log, etc.).

## Acceptance Criteria

- [ ] When `read_file("cluster-resources/pods/kube-system")` is called and `kube-system.json` exists, suggest the JSON file
- [ ] Error message format: "Path is not a file: {path}\n\nDid you mean one of these files?\n• {suggestion1}\n• {suggestion2}"
- [ ] Only suggest files that actually exist in the parent directory  
- [ ] Support common extensions: .json, .yaml, .yml, .log, .txt
- [ ] Maintain backward compatibility - no regression in existing error handling
- [ ] All existing tests continue to pass

## Technical Approach

### Files to Modify
1. **`src/troubleshoot_mcp_server/files.py`**
   - Add `_suggest_file_alternatives()` method to FileExplorer class
   - Create `DirectoryAccessError` exception extending `ReadFileError`
   - Modify `read_file()` to use suggestion logic when directory detected

### Files to Create  
2. **`tests/unit/test_enhanced_error_messages.py`** - Functional tests using real filesystem
3. **`tests/integration/test_enhanced_errors_real_bundle.py`** - Integration tests with bundle fixtures

## Parallel Implementation Strategy

### Phase 1 (Parallel Development)
**Task Agent A**: Core Enhancement Logic
- Implement `_suggest_file_alternatives()` method
- Create `DirectoryAccessError` exception class  
- NO integration yet - just helper methods

**Task Agent B**: Test Infrastructure  
- Create test files with real filesystem fixtures
- Set up temp directory structures for testing
- Create helper functions for test scenarios

### Phase 2 (Parallel Integration)  
**Task Agent C**: Integration
- Modify `read_file()` to use suggestion logic
- Integrate `DirectoryAccessError` with suggestions
- Ensure backward compatibility

**Task Agent D**: Functional Tests
- Implement test cases using real filesystem operations
- Test edge cases and integration scenarios  
- Use existing bundle fixtures for realistic testing

## Implementation Details

### New Method Structure
```python
def _suggest_file_alternatives(self, directory_path: Path) -> List[str]:
    """Find files with common extensions that match directory name"""
    common_extensions = ['.json', '.yaml', '.yml', '.log', '.txt']
    # Implementation details...
```

### Enhanced Error Message Format
```
Path is not a file: cluster-resources/pods/kube-system

Did you mean one of these files?
• cluster-resources/pods/kube-system.json
• cluster-resources/pods/kube-system.yaml
```

## Testing Strategy - Minimal & Functional

- **NO MOCKS** - Use real filesystem operations with pytest tmp_path fixtures
- **Minimal Coverage** - Focus on core functional scenarios only
- **Real Bundle Fixtures** - Leverage existing test infrastructure
- **Integration Focus** - Test end-to-end user experience

### Key Test Scenarios
1. Directory with matching .json file exists → suggest .json
2. Directory with multiple matching extensions → suggest all  
3. Directory with no matching files → standard error message
4. Integration with existing bundle structures

## Dependencies
- Existing FileExplorer class and exception hierarchy
- Bundle test fixtures from `tests/test_utils/bundle_helpers.py`  
- Current error handling and formatting system

## Quality Gates
- [ ] All new code passes ruff formatting and linting
- [ ] All new code passes mypy type checking  
- [ ] All existing tests continue to pass
- [ ] New functional tests pass with real filesystem operations
- [ ] Manual testing confirms improved user experience

## Definition of Done
- [ ] Code implemented and tested
- [ ] All quality checks pass
- [ ] Integration tests verify real-world scenarios  
- [ ] Task moved to completed with PR link
- [ ] User experience improvement validated

---

## Progress Log

### Started: 2025-08-07
- [x] Git worktree created: `trees/enhanced-error-messages`
- [x] Task moved to active status

### Development Progress:
- [x] Phase 1A: Core suggestion logic implemented (_suggest_file_alternatives method)
- [x] Phase 1B: Test infrastructure created (unit and integration tests)
- [x] Phase 2C: Integration with read_file() completed (DirectoryAccessError)
- [x] Phase 2D: Functional tests implemented (comprehensive test coverage)
- [x] Quality checks passed (ruff, mypy, pytest)
- [x] Implementation completed successfully

### Completed: 2025-08-07  
- [x] PR created: https://github.com/chris-sanders/troubleshoot-mcp-server/pull/54
- [x] Task moved to completed status