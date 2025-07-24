# Task: Parallel Pytest Execution for Performance Improvement

## Metadata
- **ID**: parallel-pytest-execution
- **Priority**: High
- **Complexity**: Medium
- **Estimated Time**: 3-4 hours
- **Status**: Backlog
- **Created**: 2025-01-24
- **Assigned**: -
- **Dependencies**: None

## Problem Statement

Current test execution times are too slow, particularly integration tests which take >90 seconds and are the bottleneck in our CI pipeline. We need to implement parallel pytest execution to achieve at least 50% reduction in the longest running tests (integration tests).

**Current Performance:**
- Unit tests: ~24 seconds (169 tests)
- Integration tests: >90 seconds (timing out, likely 60-120s normally)
- E2E direct tool tests: ~7 seconds (6 tests)
- Total test suite: ~120+ seconds

**Target Performance:**
- Integration tests: <45 seconds (50% reduction)
- Unit tests: <15 seconds (40%+ reduction)
- Total test suite: <75 seconds

## Acceptance Criteria

### Performance Requirements
- [ ] Integration tests complete in <45 seconds (50% reduction from baseline)
- [ ] Unit tests complete in <15 seconds (40%+ reduction from baseline)
- [ ] Total test suite completes in <75 seconds
- [ ] All parallel execution measurements documented with before/after comparisons

### Quality Requirements
- [ ] All existing tests continue to pass with parallel execution
- [ ] No flaky tests introduced due to resource conflicts or race conditions
- [ ] Test coverage metrics remain accurate and comprehensive
- [ ] CI pipeline successfully uses parallel execution

### Implementation Requirements
- [ ] pytest-xdist dependency added to pyproject.toml
- [ ] Parallel execution configured in pytest settings
- [ ] CI workflow updated to use parallel execution
- [ ] All documentation updated with parallel testing instructions and commands

## Technical Approach

### Dependencies to Add
```toml
# Add to pyproject.toml [project.optional-dependencies] dev section
"pytest-xdist",
```

### Configuration Changes
```toml
# Add to [tool.pytest.ini_options] in pyproject.toml
addopts = [
    "--strict-markers",
    "--strict-config", 
    "--dist=worksteal",  # Load balancing strategy
]
```

### CI Pipeline Updates
```yaml
# Update in .github/workflows/pr-checks.yaml
- name: Run unit tests with coverage
  run: uv run pytest tests/unit/ -n auto --cov=src --cov-report=xml:coverage-unit.xml --cov-report=term -v

- name: Run integration tests with coverage  
  run: uv run pytest tests/integration/ -n auto --cov=src --cov-report=xml:coverage-integration.xml --cov-report=term -v
```

## Implementation Plan

### Phase 1: Setup and Configuration (1 hour)
1. **Add pytest-xdist dependency**
   - Modify `pyproject.toml` to include pytest-xdist in dev dependencies
   - Run `uv pip install -e ".[dev]"` to install new dependency
   - Verify installation: `uv run pytest --help | grep -i dist`

2. **Configure parallel execution settings**
   - Add parallel-safe configurations to `[tool.pytest.ini_options]`
   - Set distribution strategy to `worksteal` for optimal load balancing

### Phase 2: Test Isolation Analysis (1-2 hours)
3. **Identify non-parallel-safe tests**
   - Review integration tests for resource conflicts:
     - `test_api_server_lifecycle.py` - potential port conflicts
     - `test_mcp_protocol_real.py` - potential async conflicts  
     - `test_real_bundle.py` - potential file system conflicts
   - Review unit tests for shared state issues

4. **Fix test isolation issues**
   - Implement unique temporary directories per worker
   - Fix any shared file system resources
   - Resolve port conflicts with dynamic allocation
   - Address async event loop conflicts

### Phase 3: CI Integration and Documentation (1-2 hours)
5. **Update CI pipeline**
   - Modify `.github/workflows/pr-checks.yaml` to use `-n auto`
   - Test CI pipeline with parallel execution
   - Monitor for any CI-specific issues

6. **Update all documentation**
   - Review and update `CLAUDE.md` with parallel testing commands
   - Update `docs/TESTING_STRATEGY.md` with comprehensive parallel testing section
   - Review and update any other `.md` files that reference testing commands
   - Document new commands, troubleshooting, and performance benchmarks

## Files to Modify

### Configuration Files
- **`pyproject.toml`** - Add pytest-xdist dependency and parallel settings
- **`.github/workflows/pr-checks.yaml`** - Add `-n auto` to pytest commands

### Documentation Files (Review All)
- **`CLAUDE.md`** - Update testing commands to include parallel options
- **`docs/TESTING_STRATEGY.md`** - Add comprehensive parallel testing section
- **`tasks/*/*.md`** - Review task files for testing command references
- **`README.md`** - Update if it contains testing instructions
- **Any other `.md` files** - Review for testing command references

### Test Files (if needed)
- **`tests/integration/*.py`** - Fix any resource conflicts identified
- **`tests/unit/*.py`** - Fix any shared state issues identified

## Risk Assessment

### High Risk
- **Async event loop conflicts** - Mitigation: Proper async test isolation
- **File system race conditions** - Mitigation: Unique temp directories per worker
- **Port conflicts in integration tests** - Mitigation: Dynamic port allocation

### Medium Risk  
- **CI runner performance variability** - Mitigation: Monitor CI execution times
- **Test coverage accuracy** - Mitigation: Validate coverage reports with parallel execution

### Low Risk
- **Unit test parallelization** - Unit tests typically well-isolated

## Testing Strategy

### Validation Tests
1. **Performance benchmarking**
   ```bash
   # Before changes
   time uv run pytest tests/unit/ -q --tb=no
   time uv run pytest tests/integration/ -q --tb=no --timeout=120
   
   # After changes  
   time uv run pytest tests/unit/ -n auto -q --tb=no
   time uv run pytest tests/integration/ -n auto -q --tb=no
   ```

2. **Consistency testing**
   ```bash
   # Run multiple times to check for flaky tests
   for i in {1..5}; do uv run pytest tests/ -n auto -q; done
   ```

3. **Coverage validation**
   ```bash
   # Ensure coverage is accurate with parallel execution
   uv run pytest tests/unit/ -n auto --cov=src --cov-report=term
   ```

## Success Metrics

### Performance Metrics
- Integration test time: <45 seconds (target: 50% reduction)
- Unit test time: <15 seconds (target: 40% reduction)  
- Total test suite time: <75 seconds
- CI pipeline total time reduction: >30%

### Quality Metrics
- Test pass rate: 100% (no regressions)
- Test coverage: Maintained at current levels
- Flaky test count: 0 (no new flaky tests)

## Documentation Updates Required

### Commands to Document
```bash
# Parallel testing commands for different test categories
uv run pytest tests/unit/ -n auto -v                    # Parallel unit tests
uv run pytest tests/integration/ -n auto -v             # Parallel integration tests  
uv run pytest tests/unit/ tests/integration/ -n auto -v # Parallel combined tests

# With coverage
uv run pytest tests/unit/ -n auto --cov=src --cov-report=term -v
uv run pytest tests/integration/ -n auto --cov=src --cov-report=term -v

# Specific worker count (alternative to auto)
uv run pytest tests/unit/ -n 4 -v                       # Use 4 workers explicitly
```

### Performance Benchmarks to Document
- Before/after timing comparisons
- Optimal worker count recommendations
- CI vs local performance differences

## Troubleshooting Guide

### Common Issues
1. **Tests hang in parallel execution**
   - Check for async event loop conflicts
   - Verify proper test isolation

2. **Intermittent test failures**
   - Look for shared resources or race conditions
   - Check file system or network resource conflicts

3. **Coverage reports inaccurate**
   - Verify pytest-cov compatibility with pytest-xdist
   - Check coverage combining process

### Debug Commands
```bash
# Run with verbose logging
uv run pytest tests/integration/ -n auto -v -s --log-cli-level=DEBUG

# Run single test to isolate issues
uv run pytest tests/integration/test_specific.py -n 1 -v -s

# Check worker distribution
uv run pytest tests/ -n auto --dist=worksteal -v
```

## Follow-up Tasks

### Immediate
- Monitor CI performance after implementation
- Address any flaky tests that emerge
- Fine-tune worker count based on CI runner performance

### Future Enhancements  
- Implement test sharding for even larger test suites
- Add performance regression monitoring
- Consider parallel execution for E2E tests when suite grows

## Notes

- E2E tests (7 seconds, 6 tests) likely not worth parallelizing due to overhead
- Focus optimization efforts on integration tests (biggest bottleneck)
- pytest-xdist uses `worksteal` distribution for optimal load balancing
- GitHub Actions runners typically have 2-4 CPU cores available
- All documentation must be comprehensively updated to reflect parallel testing capabilities