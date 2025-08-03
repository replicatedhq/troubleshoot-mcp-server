# Optimize grep_files Token Usage

## Problem Statement

The `grep_files` MCP tool generated 38,761 tokens (54% over the 25,000 limit) with this command:
```
grep_files(pattern="no available devices|device.*not.*available|ceph-volume.*failed", path="cluster-resources", recursive=true, max_results=10)
```

**Root causes:**
1. **No per-file limits**: Single files with many matches consume all result slots
2. **Verbose default formatting**: Uses markdown with metadata instead of compact JSON  
3. **No file count limits**: Can search unlimited files even with low max_results

## Solution Design

### 1. Per-File Result Limiting
Add `max_results_per_file` parameter (default: 5) to prevent single files from dominating results.

### 2. File Count Limiting  
Add `max_files` parameter (default: 10) to limit total files searched/returned.

### 3. Ultra-Compact Minimal Format
Modify minimal format to use:
- Full line content (not just match snippet)
- No JSON whitespace (`separators=(',', ':')`)
- Truncation indicators when limits hit
- No other metadata or formatting

**Format change:**
```python
# BEFORE (verbose, ~140 tokens)
"""Found 4 matches for case-insensitive pattern 'apiVersion':
**File: /kubernetes/deployment.yaml**
   3 | apiVersion: apps/v1
  16 |   apiVersion: v1
Search metadata: {...}"""

# AFTER (ultra-compact, ~60 tokens estimated)
{"matches":[{"file":"/kubernetes/deployment.yaml","line":3,"content":"    apiVersion: apps/v1"},{"file":"/kubernetes/deployment.yaml","line":16,"content":"      apiVersion: v1","truncated":true}],"files_truncated":true}
```

## Implementation Plan

### Phase 1: Parameter Updates
- [ ] Add `max_results_per_file: int = Field(5)` to `GrepFilesArgs`
- [ ] Add `max_files: int = Field(10)` to `GrepFilesArgs` 
- [ ] Update parameter validation
- [ ] Update MCP tool docstring

### Phase 2: Search Logic Updates
- [ ] Implement per-file match counting in `grep_files()` method
- [ ] Implement file count limiting
- [ ] Add early termination when file limit reached
- [ ] Update truncation logic to indicate why truncation occurred

### Phase 3: Ultra-Compact Formatting
- [ ] Modify `format_grep_results()` minimal branch
- [ ] Change from `match.match` to `match.line` (full content)
- [ ] Use `json.dumps(matches, separators=(',', ':'))` for no whitespace
- [ ] Add truncation indicators: `"truncated":true` on last match per file when per-file limit hit
- [ ] Add file-level truncation: `"files_truncated":true` when file limit hit
- [ ] Remove all other metadata from minimal format

### Phase 4: Testing & Validation
- [ ] Update unit tests for new parameters
- [ ] Add tests for per-file limiting behavior
- [ ] Add tests for file count limiting
- [ ] Test ultra-compact format output
- [ ] Benchmark token usage reduction

## Expected Outcomes

### Token Reduction
- **Per-file limits**: Prevent runaway single-file matches
- **File limits**: Cap total search scope  
- **Ultra-compact format**: ~60-70% token reduction vs current

### Better Result Distribution
```
# BEFORE: max_results=10
File A: 10 matches, Files B-Z: 0 matches

# AFTER: max_results_per_file=5, max_files=10  
File A: 5 matches, File B: 5 matches, etc.
```

### Preserved Functionality
- Full line content maintained for context
- Regex patterns and filtering unchanged
- All verbosity levels still available
- LLM can override defaults when needed

## Files to Modify

### Core Implementation
- `src/troubleshoot_mcp_server/files.py`
  - `GrepFilesArgs` class: Add new parameters
  - `grep_files()` method: Implement per-file and file count limiting
  
- `src/troubleshoot_mcp_server/formatters.py`
  - `format_grep_results()` method: Ultra-compact minimal format

### Testing
- `tests/unit/test_files.py`: New parameter validation and limiting tests
- `tests/unit/test_formatters.py`: Ultra-compact format tests
- `tests/integration/test_token_optimization.py`: Token usage benchmarks

## Acceptance Criteria

### Functional
- [ ] `max_results_per_file=5` and `max_files=10` defaults enforced
- [ ] Per-file limiting prevents single files from dominating results  
- [ ] File count limiting caps total search scope
- [ ] Ultra-compact format includes full line content
- [ ] All existing functionality preserved

### Performance
- [ ] Token usage reduced by 60%+ for typical grep_files calls
- [ ] No performance degradation for small result sets
- [ ] Memory usage remains reasonable for large searches

### Quality
- [ ] All tests pass with new parameters
- [ ] Code follows project conventions
- [ ] MCP tool documentation updated
- [ ] Backward compatibility maintained

## Risk Mitigation

### Breaking Changes
- New parameters are optional with sensible defaults
- Existing verbosity levels unchanged
- Only minimal format modified (least used in production)

### Performance Impact
- Early termination reduces processing time
- File limits prevent excessive directory traversal
- Ultra-compact format reduces serialization overhead

## Metadata

- **Priority**: High
- **Effort**: Medium (2-3 hours)
- **Risk**: Low
- **Dependencies**: None
- **Category**: Performance Optimization
- **Status**: Active
- **Started**: 2025-06-18

## Progress

- [x] 2025-06-18: Task moved to active and development environment prepared
- [x] 2025-06-18: Analyzed current grep_files implementation in files.py and formatters.py
- [x] 2025-06-18: Added max_results_per_file (default: 5) and max_files (default: 10) parameters to GrepFilesArgs
- [x] 2025-06-18: Implemented per-file and file count limiting logic in grep_files method
- [x] 2025-06-18: Updated formatters.py with ultra-compact minimal format using full line content and separators=(',', ':')
- [x] 2025-06-18: Added files_truncated field to GrepResult for truncation indicators
- [x] 2025-06-18: Created comprehensive tests for new functionality (3 new test functions)
- [x] 2025-06-18: Updated existing formatter tests to verify ultra-compact format
- [x] 2025-06-18: All quality checks pass (black, ruff, mypy) and full test suite passes (240 tests)

## Implementation Summary

### New Parameters Added
- `max_results_per_file: int = Field(5)` - Limits matches per individual file
- `max_files: int = Field(10)` - Limits total number of files searched/returned

### Core Logic Changes
- Modified grep_files() to implement early termination when file limits are reached
- Added per-file match counting to prevent single files from dominating results
- Added file-level truncation tracking with files_truncated flag

### Ultra-Compact Format Changes
- Changed minimal format from array of simple objects to structured result object
- Uses full line content instead of just match snippet
- Applies compact JSON separators `(',', ':')` for minimal whitespace
- Includes truncation indicators when limits are hit

### Token Usage Optimization
**Expected Results:**
- Per-file limits prevent runaway single-file matches
- File limits cap total search scope
- Ultra-compact format provides ~60-70% token reduction vs current verbose format
- Better result distribution across multiple files instead of single file domination