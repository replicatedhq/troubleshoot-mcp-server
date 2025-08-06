# Task: GitHub Attachment Download Support

**Status:** completed  
**Priority:** high  
**Estimated:** 4 hours  
**Started:** 2025-08-06  
**Completed:** 2025-08-06  
**PR:** https://github.com/chris-sanders/troubleshoot-mcp-server/pull/53  

## Problem Statement

GitHub attachment URLs (e.g., `https://github.com/user-attachments/files/XXXXXX/bundle.tar.gz`) return HTTP 404 when accessed without proper authentication. The current system treats these as generic URLs with basic Bearer token auth, but GitHub requires specific authentication headers and token formats.

## Implementation Requirements

### CRITICAL INSTRUCTIONS FOR AGENT

1. **Private URL Handling**:
   - When ready for testing with a real GitHub attachment URL, ASK THE USER for one
   - NEVER commit, reference, or include any real private URLs in code, comments, or git history
   - Use generic examples like `https://github.com/user-attachments/files/12345/example.tar.gz` in all code/tests
   - Any real URL provided is for manual testing ONLY

2. **Testing Requirements**:
   - ALL tests must pass - no exceptions, no skipping, no disabling
   - Run full test suite: `uv run pytest`
   - Run slow/container tests locally: `uv run pytest -m slow -v`
   - If any test cannot pass, STOP and negotiate with the user
   - Follow testing strategy in `./tests/README.md`

3. **CI/CD Requirements**:
   - Push changes and create PR
   - MONITOR GitHub Actions CI until it passes
   - Fix any CI failures (especially linting issues)
   - Do not consider task complete until CI is green

## Parallel Execution Opportunities

### PARALLEL AGENT TASKS

Execute these tasks using parallel agents for faster development:

**Agent 1: Pattern Detection & Core Logic**
```
Task: Implement GitHub URL pattern detection and authentication logic
Files: src/mcp_server_troubleshoot/bundle.py
Goals:
- Add GitHub URL regex patterns
- Implement URL detection logic
- Add GITHUB_TOKEN support
- Create header generation for GitHub
```

**Agent 2: Testing Infrastructure**
```
Task: Create comprehensive test suite for GitHub downloads
Files: tests/integration/test_github_downloads.py, tests/unit/test_bundle.py
Goals:
- Write GitHub URL pattern tests
- Create mock GitHub API responses
- Test authentication flows
- Test error scenarios (404, 401, 429)
```

**Agent 3: Documentation**
```
Task: Create authentication documentation
Files: docs/AUTHENTICATION.md, README.md updates
Goals:
- Document GitHub token setup
- Explain environment variables
- Add troubleshooting guide
- Provide configuration examples
```

### Sequential Tasks (After Parallel Completion)

1. Integration of parallel work
2. Full test suite execution
3. Code quality checks (ruff, mypy)
4. Manual testing with real URL (request from user)
5. PR creation and CI monitoring

## Detailed Implementation Plan

### Phase 1: Core Implementation

#### 1.1 Add GitHub URL Patterns
Location: `src/mcp_server_troubleshoot/bundle.py` (near line 30)

```python
# GitHub URL patterns
GITHUB_ATTACHMENT_URL_PATTERN = re.compile(
    r"https://github\.com/user-attachments/files/\d+/.+"
)
GITHUB_RELEASE_URL_PATTERN = re.compile(
    r"https://github\.com/[^/]+/[^/]+/releases/download/.+"
)
GITHUB_RAW_URL_PATTERN = re.compile(
    r"https://raw\.githubusercontent\.com/.+"
)
```

#### 1.2 Modify Download Logic
Location: `src/mcp_server_troubleshoot/bundle.py` (method `_download_bundle`, around line 566)

- Detect GitHub URLs using patterns
- Route to new `_download_github_attachment()` method
- Maintain backward compatibility

#### 1.3 Implement GitHub Download Method
New method in `BundleManager` class:

```python
async def _download_github_attachment(self, url: str) -> Path:
    """Download bundle from GitHub with proper authentication."""
    # Token priority: GITHUB_TOKEN > GH_TOKEN > SBCTL_TOKEN
    github_token = (
        os.environ.get("GITHUB_TOKEN") or 
        os.environ.get("GH_TOKEN") or
        os.environ.get("SBCTL_TOKEN")
    )
    
    if not github_token:
        raise BundleDownloadError(
            "Cannot download from GitHub: No authentication token found. "
            "Set GITHUB_TOKEN, GH_TOKEN, or SBCTL_TOKEN environment variable."
        )
    
    # GitHub-specific headers
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "mcp-server-troubleshoot"
    }
    
    # Implement download with retry logic for rate limits
    # Handle 429 (rate limit) with exponential backoff
    # Handle redirects properly
```

### Phase 2: Testing

#### 2.1 Integration Tests
Create: `tests/integration/test_github_downloads.py`

```python
"""Integration tests for GitHub attachment downloads."""

class TestGitHubUrlPatterns:
    """Test GitHub URL pattern detection."""
    
    def test_attachment_url_pattern(self):
        """Test GitHub attachment URL matching."""
        # Test various URL formats
        
    def test_release_url_pattern(self):
        """Test GitHub release URL matching."""
        
    def test_raw_url_pattern(self):
        """Test raw GitHub content URL matching."""

class TestGitHubAuthentication:
    """Test GitHub authentication handling."""
    
    @pytest.mark.asyncio
    async def test_github_token_priority(self):
        """Test token selection priority."""
        
    @pytest.mark.asyncio
    async def test_missing_token_error(self):
        """Test error when no token available."""
        
    @pytest.mark.asyncio
    async def test_github_404_with_auth(self):
        """Test 404 error with valid auth."""

class TestGitHubRateLimiting:
    """Test GitHub rate limit handling."""
    
    @pytest.mark.asyncio
    async def test_rate_limit_retry(self):
        """Test retry logic for 429 responses."""
```

#### 2.2 Unit Tests
Update: `tests/unit/test_bundle.py`

- Add GitHub URL pattern matching tests
- Test token priority logic
- Test header generation

### Phase 3: Documentation

#### 3.1 Create Authentication Guide
File: `docs/AUTHENTICATION.md`

```markdown
# Authentication Guide

## GitHub Authentication

### Setting Up GitHub Token

1. Create a GitHub Personal Access Token:
   - Go to GitHub Settings > Developer Settings > Personal Access Tokens
   - Create token with `repo` scope for private repositories
   - For public repositories, no scope needed

2. Set Environment Variable:
   ```bash
   export GITHUB_TOKEN="ghp_your_token_here"
   # or
   export GH_TOKEN="ghp_your_token_here"
   ```

### Token Priority

The system checks tokens in this order:
1. `GITHUB_TOKEN` (recommended for GitHub URLs)
2. `GH_TOKEN` (GitHub CLI compatibility)
3. `SBCTL_TOKEN` (fallback)

### Troubleshooting

If you see "404 Not Found" for GitHub URLs:
1. Verify token is set: `echo $GITHUB_TOKEN`
2. Check token has required permissions
3. Ensure URL is correct and accessible
```

## Testing Checklist

### Local Testing Commands

```bash
# Setup environment (if not done)
./scripts/setup_env.sh

# Run ALL tests - MUST PASS
uv run pytest

# Run unit tests
uv run pytest -m unit

# Run integration tests
uv run pytest -m integration

# Run slow/container tests (REQUIRED locally)
uv run pytest -m slow -v

# Run specific GitHub tests
uv run pytest tests/integration/test_github_downloads.py -v

# Code quality checks - ALL MUST PASS
uv run ruff format .
uv run ruff check .
uv run mypy src
```

### Manual Testing Protocol

1. **Request Real URL from User**:
   ```
   "I'm ready to test with a real GitHub attachment URL. 
   Please provide one for testing. I will not commit or 
   reference this URL in any code or documentation."
   ```

2. **Test Authentication Scenarios**:
   - With GITHUB_TOKEN set
   - Without GITHUB_TOKEN (should fail gracefully)
   - With invalid token (should show clear error)

3. **Test Download Success**:
   - Verify bundle downloads correctly
   - Check extraction works
   - Confirm sbctl processes it

## Acceptance Criteria

### ✅ Functional Requirements
- [ ] GitHub attachment URLs correctly identified
- [ ] Downloads work with GITHUB_TOKEN
- [ ] Downloads work with GH_TOKEN
- [ ] Falls back to SBCTL_TOKEN
- [ ] Clear error messages for auth failures
- [ ] Rate limit handling with retry
- [ ] All existing functionality intact

### ✅ Code Quality (ALL MUST PASS)
- [ ] `uv run pytest` - ALL tests pass
- [ ] `uv run pytest -m slow -v` - Container tests pass locally
- [ ] `uv run ruff format .` - Code formatted
- [ ] `uv run ruff check .` - No linting errors
- [ ] `uv run mypy src` - Type checking passes

### ✅ CI/CD Requirements
- [ ] Push branch to origin
- [ ] Create PR with comprehensive description
- [ ] Monitor GitHub Actions CI
- [ ] Fix any CI failures immediately
- [ ] All CI checks must be green
- [ ] No tests skipped or disabled

## Implementation Notes

### Error Messages

Provide clear, actionable error messages:

```python
# Good
"Cannot download from GitHub: GITHUB_TOKEN not set. 
Create a token at https://github.com/settings/tokens and set GITHUB_TOKEN=<token>"

# Bad
"Authentication failed"
```

### Security Considerations

1. Never log tokens or include in error messages
2. Use secure token transmission (headers, not URL params)
3. Clear tokens from memory after use
4. Don't commit test tokens

### Retry Logic

Implement exponential backoff for rate limits:
- 429 response: Wait and retry (1s, 2s, 4s)
- 401/403: Fail immediately with auth error
- 404: Fail immediately with not found error
- Network errors: Retry with backoff

## Progress Tracking

### Development Steps

- [ ] Create git worktree for development
- [ ] Move task to active status
- [ ] Run parallel agents for implementation
- [ ] Integrate parallel work
- [ ] Run full test suite
- [ ] Fix any test failures
- [ ] Run code quality checks
- [ ] Request real URL for testing
- [ ] Perform manual testing
- [ ] Create PR
- [ ] Monitor CI until green
- [ ] Fix any CI failures
- [ ] Task complete when PR approved and CI passing

### Notes

**Implementation Complete (2025-08-06)**
- ✅ Implemented GitHub URL patterns for attachment, release, and raw URLs
- ✅ Added authentication with token priority: GITHUB_TOKEN > GH_TOKEN > SBCTL_TOKEN  
- ✅ Implemented retry logic with exponential backoff for rate limits (429)
- ✅ Added comprehensive error handling for 401, 404, 429, and other HTTP errors
- ✅ Created 8 new unit tests + 3 integration tests (all 52 total tests pass)
- ✅ Manual testing successful with real GitHub attachment URL
- ✅ Code quality: ruff format/lint + mypy all pass
- ✅ PR created: https://github.com/chris-sanders/troubleshoot-mcp-server/pull/53
- ✅ All existing functionality preserved (no regressions)

**Test Results**
- Successfully downloaded real GitHub attachment: support-bundle-2025-08-06T14_34_47.tar.gz (88KB)  
- Verified valid tar.gz format and proper routing through main download method
- Confirmed token priority behavior with GITHUB_TOKEN preference
- All automated tests pass including existing bundle functionality

---
*Remember: NEVER commit real URLs or private data. Always use generic examples in code.*