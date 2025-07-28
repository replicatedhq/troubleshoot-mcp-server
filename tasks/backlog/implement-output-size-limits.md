# Task: Implement Output Size Limits for MCP Server Responses

## Metadata
**Status**: ready
**Created**: 2025-07-28
**Priority**: high
**Complexity**: medium
**Estimated effort**: 5 phases
**Labels**: optimization, token-usage, user-experience

## Objective
Implement size limits (~25k tokens) on all MCP server responses with helpful overflow messages directing users to filtering and formatting commands to reduce output size.

## Context
The MCP server can return very large responses that exceed reasonable token limits for LLM consumption. When responses are too large, the LLM should receive a helpful message explaining that the output was truncated and providing specific guidance on how to use filtering parameters to get the information they need in smaller chunks.

## Problem Statement
Currently, the MCP server can return unlimited amounts of data in responses, which can:
- Exceed LLM context windows or token limits
- Cause poor performance in AI interactions
- Waste tokens on overly verbose responses
- Provide no guidance when content is too large to process

Users need a way to get helpful guidance when responses are too large, with specific suggestions for how to filter or paginate the data.

## Success Criteria
- [ ] All MCP tool responses respect a configurable token limit (default ~25k tokens)
- [ ] When content exceeds limits, return helpful overflow messages with specific filtering suggestions
- [ ] Overflow messages are tool-specific and actionable
- [ ] Content within limits remains completely unchanged
- [ ] Performance impact is minimal (<5% overhead for normal responses)
- [ ] Token counting is reasonably accurate (±10% acceptable)
- [ ] Configurable via environment variables

## Implementation Plan

**IMPORTANT**: Use sub-agents for parallel development to maximize efficiency. Launch multiple agents simultaneously for independent work streams.

### Phase 1: Core Infrastructure - **PARALLEL DEVELOPMENT**

**Sub-Agent A**: Core Size Limiting Module
- Create `src/mcp_server_troubleshoot/size_limiter.py`
- Implement `SizeLimiter` class with token estimation methods
- Fast character-based token approximation (~4 chars per token)
- Configurable limits via environment variables
- Content size checking and overflow detection

**Sub-Agent B**: Formatter Extensions (can work in parallel)
- Extend `src/mcp_server_troubleshoot/formatters.py`
- Add `format_overflow_message()` method to `ResponseFormatter`
- Include content preview/summary capabilities
- Respect existing verbosity levels in overflow messages

**Sub-Agent C**: Unit Test Foundation (can work in parallel)
- Create `tests/unit/test_size_limiter.py` structure
- Set up test fixtures and basic test framework
- Prepare parameterized test patterns

### Phase 2: Tool-Specific Implementation - **PARALLEL DEVELOPMENT**

**Sub-Agent A**: Tool-Specific Overflow Messages
- Implement tool-specific guidance in `ResponseFormatter`:
  - **list_files**: Suggest `recursive=false`, more specific paths
  - **read_file**: Suggest `start_line`/`end_line` parameters, smaller ranges  
  - **grep_files**: Suggest `max_results`, `max_files`, `glob_pattern` filtering

**Sub-Agent B**: More Tool Messages + Server Integration
- Complete remaining tool-specific messages:
  - **kubectl**: Suggest more specific resource queries, different output formats
  - **initialize_bundle/list_bundles**: Suggest `verbosity=minimal`
- Begin server.py integration work

**Sub-Agent C**: Unit Test Implementation (parallel to above)
- Implement token counting accuracy tests
- Size limit threshold testing
- Overflow message generation tests
- Environment variable configuration tests

### Phase 3: Server Integration - **COORDINATED DEVELOPMENT**

**Primary Agent**: Server Response Wrapping
- Modify `src/mcp_server_troubleshoot/server.py`
- Create single centralized `check_response_size()` wrapper function
- Apply wrapper to all existing MCP tools at response return point
- Coordinate with sub-agents' completed components

**Sub-Agent**: Integration Test Preparation (parallel)
- Prepare test data and fixtures for integration testing
- Set up test scenarios with large response data
- Begin modifying `tests/unit/test_server.py` for size limiting tests

### Phase 4: Testing & Validation - **PARALLEL DEVELOPMENT**

**Sub-Agent A**: Unit Test Completion
- Complete all unit tests in `test_size_limiter.py`
- Update existing tool tests in `tests/unit/test_server.py`
- Add overflow scenarios using parameterized tests

**Sub-Agent B**: Integration Testing
- Add size limit tests to `tests/integration/test_real_bundle.py`
- Test with actual bundle data that triggers overflows
- Verify user experience with suggested filtering commands

**Sub-Agent C**: Performance & Documentation (parallel)
- Performance impact measurement (ensure <5% overhead)
- Update tool docstrings with size limit information
- Document environment variables

### Phase 5: Final Integration - **COORDINATED FINALIZATION**

**Primary Agent**: Final coordination and testing
- Integrate all sub-agent work
- Run full test suite
- Address any integration issues
- Final validation of all acceptance criteria

## Files to Create
- `src/mcp_server_troubleshoot/size_limiter.py` - Core size limiting functionality
- `tests/unit/test_size_limiter.py` - Unit tests for size limiter

## Files to Modify
- `src/mcp_server_troubleshoot/server.py` - Add size limiting to all tool responses
- `src/mcp_server_troubleshoot/formatters.py` - Add overflow message formatting
- Existing test files - Update to verify size limiting behavior

## Dependencies
- **Internal**: Leverages existing `ResponseFormatter` and tool patterns
- **External**: No new dependencies required (character-based estimation)
- **Optional**: `tiktoken` for precise token counting (future enhancement)

## Testing Strategy
Following the project's testing patterns:

- **Unit Tests** (`tests/unit/`): Token counting, size detection, overflow messages, individual component behavior
- **Integration Tests** (`tests/integration/`): Tool behavior with size limits using real bundle data, component interactions
- **Parameterized Tests**: Use pytest parameterization for testing multiple scenarios and edge cases
- **Performance Tests**: Overhead measurement to ensure <5% impact on normal responses

## Implementation Details

### Single Point Architecture
```python
def check_response_size(content: str, tool_name: str, formatter: ResponseFormatter) -> List[TextContent]:
    """Single centralized function to check all MCP tool responses"""
    size_limiter = SizeLimiter()  # default 25k token limit
    tokens = size_limiter.estimate_tokens(content)
    
    if tokens <= size_limiter.token_limit:
        return [TextContent(type="text", text=content)]
    else:
        overflow_msg = formatter.format_overflow_message(tool_name, tokens, content)
        return [TextContent(type="text", text=overflow_msg)]
```

### Token Estimation Approach
```python
def estimate_tokens(self, text: str) -> int:
    # Fast approximation: ~4 characters per token
    return len(text) // 4
```

### Overflow Message Example
```
Content too large (45,000 tokens, limit: 25,000).

Suggestions to reduce output size:
1. Use more specific path: grep_files(path="cluster-resources/pods")
2. Reduce max results: grep_files(max_results=50, max_files=5)  
3. Add file filtering: grep_files(glob_pattern="*.yaml")
4. Use minimal format: grep_files(verbosity="minimal")

Showing first 100 matches...
[truncated content preview]
```

### Configuration Options
- **MCP_TOKEN_LIMIT**: Default 25000, configurable per deployment
- **MCP_SIZE_CHECK_ENABLED**: Allow disabling during development/testing
- **MCP_OVERFLOW_VERBOSITY**: Control detail level in overflow messages

## Acceptance Criteria Verification
1. **Functional**: All tools respect token limits with helpful overflow guidance
2. **Performance**: <5% overhead for normal-sized responses  
3. **Usability**: Tool-specific, actionable filtering suggestions
4. **Quality**: Focused test coverage following project patterns, no regressions
5. **Configuration**: Environment variable control over behavior

## Sub-Agent Usage Guidelines

**When to Launch Sub-Agents:**
- Launch multiple sub-agents simultaneously for independent work streams
- Use sub-agents for parallel development of different components
- Each sub-agent should have a clearly defined, independent scope of work

**Coordination Points:**
- Phase 3 requires coordination between sub-agents (server integration with formatter/limiter components)
- Phase 5 requires primary agent to integrate all sub-agent work
- Regular sync points to ensure compatibility between parallel work streams

**Sub-Agent Task Sizing:**
- Each sub-agent task should be completable in 1-2 hours
- Tasks should have minimal dependencies on other parallel work
- Clear interfaces defined between components to enable parallel work

## Notes
- Build on existing limit patterns from `files.py` (max_results, max_files, etc.)
- Maintain existing verbosity level behaviors
- Preserve all current functionality for content within limits
- Focus on user experience - overflow messages should be immediately actionable
- Use parallel development with sub-agents to maximize implementation efficiency