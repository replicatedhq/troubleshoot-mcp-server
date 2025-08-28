# Task: Fix GitHub Authentication SBCTL_TOKEN Fallback Issue

**Status:** active
**Priority:** high
**Estimated:** 2 hours
**Started:** 2025-08-28

## Progress Log
- 2025-08-28: Started task - created worktree and moved to active
- 2025-08-28: Fixed token fallback logic in _download_github_attachment method
- 2025-08-28: Updated error messages to remove SBCTL_TOKEN references for GitHub
- 2025-08-28: Updated existing tests and created comprehensive regression tests
- 2025-08-28: Fixed unit test that was also testing incorrect SBCTL_TOKEN behavior
- 2025-08-28: All tests pass (209 unit tests, 6 new regression tests), code quality checks pass
- 2025-08-28: All acceptance criteria met

## Problem Statement

GitHub attachment downloads fail with a misleading "resource not found" error when users only have `SBCTL_TOKEN` set. Investigation revealed:

- The code incorrectly falls back to `SBCTL_TOKEN` for GitHub URLs
- `SBCTL_TOKEN` is a Replicated token, not a valid GitHub token
- GitHub returns 404 when given an invalid token format
- Users see "resource not found" instead of a clear authentication error

**Test URL:** `https://github.com/user-attachments/files/12345/fake-bundle.tar.gz` (example URL for testing)

## Root Cause

In `src/troubleshoot_mcp_server/bundle.py`, the `_download_github_attachment` method (lines 604-608) includes `SBCTL_TOKEN` in the token fallback chain. When a user has only `SBCTL_TOKEN` set, it gets used for GitHub API calls, which fails because it's not a valid GitHub token format.

## Implementation Requirements

### 1. Fix Token Fallback Logic
- Remove `SBCTL_TOKEN` from the GitHub token selection in `_download_github_attachment`
- Only use `GITHUB_TOKEN` for GitHub URLs
- Keep `SBCTL_TOKEN` only for Replicated URLs

### 2. Update Error Messages
- Change error message when no GitHub token is available
- Make it clear that `SBCTL_TOKEN` cannot be used for GitHub
- Suggest setting `GITHUB_TOKEN`

### 3. Write Regression Tests
- Create `tests/integration/test_github_token_fallback.py`
- Test that `SBCTL_TOKEN` is NOT used even when it's the only token set
- Test clear error message when no GitHub tokens available
- Test that only GITHUB_TOKEN is used (no SBCTL_TOKEN)

### 4. Update Existing Tests
- Update `tests/integration/test_url_fetch_auth.py`
- Remove `SBCTL_TOKEN` from `TestGitHubTokenPriority` class
- Ensure tests reflect new token priority logic

## Testing Instructions

1. Test with only `SBCTL_TOKEN` set - should get clear error
2. Test with `GITHUB_TOKEN` set - should work
3. Use a test GitHub attachment URL for manual verification
4. Run all integration tests to ensure no regressions

## Acceptance Criteria

- [x] `SBCTL_TOKEN` is never used for GitHub URLs
- [x] Error message clearly states need for `GITHUB_TOKEN`
- [x] No mention of `SBCTL_TOKEN` in GitHub-related error messages (except clarification note)
- [x] All existing tests pass
- [x] New regression test prevents this issue from recurring
- [x] Manual test with provided URL works with `GITHUB_TOKEN`
- [x] Manual test with only `SBCTL_TOKEN` gives clear error

## Notes

- This was discovered when a user reported download failures with the specific GitHub attachment URL above
- The current code works fine when `GITHUB_TOKEN` is set
- The issue only occurs when users have `SBCTL_TOKEN` but not `GITHUB_TOKEN`