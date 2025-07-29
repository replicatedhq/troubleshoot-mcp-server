# Implement Free PR Coverage Reporting

**Status:** Ready  
**Priority:** Medium  
**Estimate:** 1-2 hours  
**Created:** 2025-07-29  

## Problem

The current Codecov setup requires an external paid service and authentication token. Coverage reports are generated in CI but not visible in PR comments or summaries, making it difficult to track coverage changes during code review.

## Goal

Implement free, built-in coverage reporting that displays coverage information directly in GitHub PRs without requiring external services.

## Requirements

### Must Have
1. Coverage percentage displayed in GitHub Actions summary
2. Coverage threshold checking with pass/fail status
3. Remove dependency on Codecov service
4. Maintain existing coverage generation (XML reports for artifacts)

### Should Have
1. PR comments with coverage details and file-by-file breakdown
2. Coverage comparison between PR and main branch
3. Coverage badge generation for README

### Could Have
1. HTML coverage report uploaded as GitHub Pages artifact
2. Coverage trend tracking over time

## Technical Approach

### Phase 1: Basic Coverage Status
- Add coverage percentage extraction to unit test job
- Display coverage in GitHub Actions summary
- Add coverage threshold check (e.g., 75% minimum)
- Set job status based on coverage threshold

### Phase 2: PR Comments
- Use GitHub CLI to post coverage report as PR comment
- Include file-by-file coverage breakdown
- Show coverage diff when possible

### Phase 3: Clean Up
- Remove Codecov action from workflow
- Update documentation
- Add coverage badge using shields.io with GitHub Actions

## Implementation Details

### Coverage Extraction
```bash
coverage_pct=$(uv run coverage report --precision=0 | grep TOTAL | awk '{print $4}' | sed 's/%//')
```

### GitHub Actions Summary
```bash
echo "## Coverage Report" >> $GITHUB_STEP_SUMMARY
echo "Coverage: ${coverage_pct}%" >> $GITHUB_STEP_SUMMARY
```

### PR Comments via GitHub CLI
```bash
gh pr comment --body "Coverage: ${coverage_pct}%"
```

## Acceptance Criteria

- [ ] Coverage percentage appears in GitHub Actions summary
- [ ] PRs show coverage information without external service
- [ ] Coverage threshold enforced (job fails below threshold)
- [ ] Codecov dependency removed
- [ ] Documentation updated

## Dependencies

- Existing pytest-cov setup (already configured)
- GitHub CLI (already available in GitHub Actions)
- GitHub Actions summary feature (built-in)

## Risks

- **Low Risk:** GitHub CLI permissions - should work with default GITHUB_TOKEN
- **Low Risk:** Coverage parsing - format is stable
- **Medium Risk:** PR comment frequency - need to avoid spam

## Testing Plan

1. Test coverage extraction locally
2. Verify GitHub Actions summary display
3. Test PR comment functionality
4. Confirm threshold enforcement works
5. Validate removal of Codecov doesn't break workflow

## Notes

- This replaces paid external service with free GitHub native features
- Maintains all existing coverage generation and CI structure
- Can be implemented incrementally without breaking current workflow