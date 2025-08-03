# Task: Implement Retry Logic with Exponential Backoff for 403 Errors

**Status**: Active  
**Priority**: High  
**Assignee**: Claude  
**Created**: 2025-08-01  
**Started**: 2025-08-01  

## Problem Statement

The MCP server experiences intermittent 403 Forbidden errors when downloading bundles from Replicated Vendor Portal URLs. Analysis shows this happens approximately 1 out of 6 times when multiple processes start simultaneously, likely due to API rate limiting on the Replicated side.

**Current Behavior**: 403 errors cause immediate bundle initialization failure  
**Desired Behavior**: 403 errors should be retried with exponential backoff before failing

## Root Cause Analysis

From investigation of `bundle.py:444-462`:
- Explicit handling exists for 401 (auth) and 404 (not found) errors
- **403 Forbidden errors fall through to generic error handler** 
- No retry logic exists anywhere in the download chain
- Single HTTP request failure = immediate bundle initialization failure

## Task Requirements

### Core Implementation
1. **Add retry constants** (hardcoded values, no environment variables):
   - `RETRY_ATTEMPTS = 3`
   - `RETRY_BASE_DELAY = 1.0` (seconds)
   - `RETRY_MAX_DELAY = 8.0` (seconds)

2. **Create exponential backoff helper method**:
   - `_calculate_retry_delay(attempt: int) -> float`
   - Formula: `min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)`
   - Add jitter: `delay * (0.5 + random.random() * 0.5)`

3. **Modify `_get_replicated_signed_url` method** (lines 404-527):
   - Wrap existing API call logic in retry loop
   - Add specific 403 error handling with retry logic
   - Preserve existing 401/404 immediate failure behavior
   - Add logging for retry attempts

### Testing Requirements
1. **Unit tests only** in `tests/unit/test_bundle.py`:
   - Test 403 → 403 → 200 sequence succeeds
   - Test 403 → 403 → 403 sequence fails with original error after max retries
   - Test backoff delay calculation
   - Test that 401/404 errors don't trigger retries
   - Use existing mock patterns from the file

2. **All existing tests must continue to pass** - retry logic should be transparent

## Implementation Details

### Files to Modify
1. **`src/troubleshoot_mcp_server/bundle.py`**:
   - Add 3 retry constants at module level
   - Add `_calculate_retry_delay` helper (~5 lines)
   - Modify `_get_replicated_signed_url` with retry loop (~25 lines)

2. **`tests/unit/test_bundle.py`**:
   - Add ~30 lines of unit tests for retry behavior
   - Use existing httpx mock patterns

### Technical Approach
```python
# Retry loop structure (conceptual):
for attempt in range(RETRY_ATTEMPTS + 1):
    try:
        response = await client.get(api_url, headers=headers)
        if response.status_code == 403 and attempt < RETRY_ATTEMPTS:
            delay = _calculate_retry_delay(attempt)
            await asyncio.sleep(delay)
            continue
        # ... existing error handling
    except Exception as e:
        # ... existing exception handling
```

### Error Handling Requirements
- **403 errors**: Retry with exponential backoff, log attempts
- **401/404 errors**: Fail immediately (preserve existing behavior)
- **Other errors**: Use existing error handling logic
- **After max retries**: Raise original 403 error with context

## Acceptance Criteria

### Functional Requirements
- [ ] 403 errors trigger retry with exponential backoff (1s, 2s, 4s delays with jitter)
- [ ] 401/404 errors still fail immediately without retries
- [ ] After max retries (3), original 403 error is raised with retry context
- [ ] All existing bundle initialization functionality works unchanged

### Testing Requirements
- [ ] Unit tests cover retry sequences and backoff calculation
- [ ] Unit tests verify 401/404 errors don't trigger retries
- [ ] All existing tests in the test suite continue to pass
- [ ] No new functional/integration tests needed

### Technical Requirements
- [ ] Retry logic uses hardcoded constants (no environment variables)
- [ ] Logging shows retry attempts with delay information
- [ ] Implementation preserves existing error handling for non-403 errors
- [ ] Code follows existing patterns in `bundle.py`

## Implementation Strategy

**Use 2 Sub-Agents Simultaneously for Maximum Speed:**

**Sub-Agent 1: Core Implementation**
- Add retry constants to `bundle.py`
- Implement `_calculate_retry_delay` helper method
- Modify `_get_replicated_signed_url` with retry loop
- Add appropriate logging statements

**Sub-Agent 2: Unit Testing**
- Add unit tests to existing `tests/unit/test_bundle.py`
- Test retry mechanics and backoff calculation
- Verify existing error handling preservation
- Use existing mock patterns and test infrastructure

## Dependencies
- **Internal**: Existing `bundle.py` error handling logic (lines 438-462)
- **External**: httpx (already imported), asyncio, random modules

## Success Metrics
- **Problem Resolution**: 403 errors are retried and eventually succeed when possible
- **No Regressions**: All existing tests pass unchanged
- **Clear Observability**: Retry attempts visible in logs for debugging

## Notes
- This is a targeted fix for the specific 403 error issue
- No changes needed to existing functional tests - they should continue to pass
- Focus on making the retry logic transparent to existing functionality
- The 1-in-6 failure rate should be eliminated by this retry mechanism