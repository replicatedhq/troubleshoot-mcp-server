# Task: Reduce Excessive Context Usage with Restrictive File Operation Defaults

## Metadata
- **Status**: backlog
- **Priority**: high
- **Assignee**: TBD
- **Created**: 2025-07-24
- **Labels**: optimization, context-usage, file-operations

## Description

LLMs using the MCP server tend to use overly broad commands (grep large files, list large directories) causing excessive context usage. The current file operation defaults are too permissive and need to be much more restrictive with simple guidance on how to adjust them.

## Current Problem

File operation defaults in `files.py` are too permissive:
- `max_results: 1000` (line 158) - Way too high, LLMs rarely need this many results
- `max_results_per_file: 5` (line 159) - Reasonable, could be slightly smaller  
- `max_files: 10` (line 160) - Could be smaller for initial searches

## Solution

Implement much smaller defaults with neutral guidance on parameter adjustment.

## Acceptance Criteria

### Primary Goals
- [ ] Reduce default `max_results` from 1000 to 20
- [ ] Reduce default `max_results_per_file` from 5 to 3
- [ ] Reduce default `max_files` from 10 to 5
- [ ] Add neutral guidance messages when results are truncated
- [ ] Update tool descriptions to explain parameters without bias

### Success Metrics
- [ ] 90% reduction in excessive context usage from file operations
- [ ] Clear parameter guidance when limits are hit
- [ ] No loss of functionality - just better defaults
- [ ] All existing tests pass

## Implementation Plan

### Step 1: Change File Operation Defaults
**File**: `src/mcp_server_troubleshoot/files.py` (lines 158-160)

Change:
```python
# From:
max_results: int = Field(1000, description="Maximum number of results to return")
max_results_per_file: int = Field(5, description="Maximum number of results to return per file")
max_files: int = Field(10, description="Maximum number of files to search/return")

# To:
max_results: int = Field(20, description="Maximum number of results to return")
max_results_per_file: int = Field(3, description="Maximum number of results per file")
max_files: int = Field(5, description="Maximum number of files to search")
```

### Step 2: Add Neutral Guidance Messages
**File**: `src/mcp_server_troubleshoot/formatters.py`

Add neutral parameter guidance in response messages:
```python
if result.truncated:
    response += "\nResults limited. Use max_results parameter to adjust.\n"
    
if result.files_truncated:
    response += "\nFile search limited. Use max_files parameter to adjust.\n"
```

### Step 3: Update Tool Descriptions
**File**: `src/mcp_server_troubleshoot/server.py` (around line 456)

Update grep_files tool description to include neutral parameter explanation:
```python
"""Search for patterns in files within the bundle.

Parameters:
- max_results: Controls total number of results returned
- max_files: Controls number of files searched  
- max_results_per_file: Controls results per individual file

Use specific patterns and paths to reduce noise."""
```

## Testing Strategy

### Unit Tests
- [ ] Verify new defaults are applied correctly
- [ ] Test guidance messages appear when limits are hit
- [ ] Ensure parameter validation still works with new defaults

### Integration Tests  
- [ ] Test that common use cases work with new restrictive defaults
- [ ] Verify LLMs can still get more results when needed by adjusting parameters
- [ ] Ensure no regression in existing functionality

### Manual Testing
- [ ] Test grep operations with new defaults
- [ ] Verify guidance messages are helpful and neutral
- [ ] Confirm tool descriptions are clear

## Files to Modify

1. **`src/mcp_server_troubleshoot/files.py`**
   - Change 3 default field values (lines 158-160)

2. **`src/mcp_server_troubleshoot/formatters.py`**
   - Add neutral guidance messages for truncated results

3. **`src/mcp_server_troubleshoot/server.py`**
   - Update grep_files tool description with parameter explanation

## Dependencies

- Existing verbosity system (no changes needed)
- Current file exploration functionality (no changes needed)
- Existing validation logic (no changes needed)

## Risks and Mitigation

**Risk**: New defaults too restrictive for legitimate use cases
- **Mitigation**: Parameters remain adjustable, guidance explains how
- **Testing**: Verify common workflows still work

**Risk**: Guidance messages not helpful
- **Mitigation**: Keep messages neutral and factual
- **Testing**: Manual testing with various scenarios

## Notes

- Keep changes minimal and focused
- Don't suggest specific values in guidance messages
- Avoid words like "comprehensive" that bias LLM choices
- Just state parameter names and let LLM decide values based on needs
- kubectl operations are fine as-is, only focus on file operations

## Progress Log

- 2025-07-24: Task created after analyzing current excessive context usage patterns