# Task: Smart File Reading with Preview and Pattern Context

## Objective
Implement intelligent file reading that provides useful previews by default while preventing LLM context overflow, with enhanced pattern-based search capabilities for large log files.

## Context
The current `read_file` implementation returns entire file content by default, causing context overflow issues when LLMs request large log files (potentially 100MB+). The MCP server can handle loading files into memory, but we need to prevent overwhelming the LLM with unnecessary context while still allowing full access when explicitly requested.

## Success Criteria
- [ ] Files ≤200 lines return full content (preserve existing behavior for small files)
- [ ] Files >200 lines return first 50 + last 50 lines by default with clear gap indicator
- [ ] Pattern search functionality with configurable context lines before/after matches
- [ ] Explicit line range requests override preview mode and return exact ranges
- [ ] Minimal metadata included (total lines, what was returned) without excess context usage
- [ ] Comprehensive test coverage for all modes and edge cases
- [ ] Updated tool documentation reflecting new parameters

## Dependencies
- Existing `read_file` implementation in `src/troubleshoot_mcp_server/files.py`
- `ReadFileArgs` model needs updating
- `FileContentResult` model needs updating

## Implementation Plan
1. Update `ReadFileArgs` model with new parameters:
   - `pattern`: Optional regex pattern for search
   - `lines_before`: Context lines before pattern matches (default: 10)
   - `lines_after`: Context lines after pattern matches (default: 10)
   - `preview_head`: Lines from start in preview mode (default: 50)
   - `preview_tail`: Lines from end in preview mode (default: 50)
   - Keep existing `start_line`/`end_line` for explicit ranges

2. Implement reading logic with priority order:
   - **Explicit range**: If `start_line` or `end_line` provided → return exact range
   - **Pattern search**: If `pattern` provided → return matches + context
   - **Auto preview**: If file > 200 lines → return head + tail
   - **Full file**: If file ≤ 200 lines → return entire file

3. Update `FileContentResult` model:
   - Include total line count
   - Include clear indication of what portion was returned
   - Add gap indicators like `... [1,850 lines omitted] ...`
   - Remove file size to minimize context usage

4. Update server.py tool documentation for new parameters

## Validation Plan
- **Preview Mode Tests**:
  - Small files (≤200 lines): verify full content returned
  - Large files (>200 lines): verify head+tail preview with gap indicator
  - Custom preview sizes: test different `preview_head`/`preview_tail` values

- **Pattern Matching Tests**:
  - Single pattern match with default context (10 lines before/after)
  - Multiple pattern matches in same file
  - Custom context sizes (`lines_before`/`lines_after`)
  - Pattern with no matches
  - Invalid regex patterns

- **Explicit Range Tests**:
  - Existing `start_line`/`end_line` behavior unchanged
  - Range overrides preview mode
  - Edge cases: out-of-bounds ranges, invalid ranges

- **Priority Logic Tests**:
  - Explicit range takes precedence over pattern
  - Pattern takes precedence over preview
  - Preview as fallback for large files

- **Metadata Tests**:
  - Total line count accuracy
  - Clear indication of what portion was returned
  - Gap indicators format correctly

- **Test File Setup**:
  - Small test file (50 lines)
  - Medium test file (300 lines)
  - Large test file (2000+ lines) with known patterns
  - Log file with realistic content for pattern testing

## Evidence of Completion
(To be filled by AI)
- [ ] Command output or logs demonstrating completion
- [ ] Path to created/modified files
- [ ] Summary of changes made

## Notes
- No backwards compatibility requirement - breaking changes are acceptable
- Focus on simplicity - avoid complex "mode" parameters that could confuse LLMs
- Minimize context usage in responses - only include essential metadata
- Pattern matching combines file reading + searching in a single tool operation

## Progress Updates
(To be filled by AI during implementation)