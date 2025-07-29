# MCP Server Testing Strategy

## Overview

This document outlines the testing strategy for the MCP Troubleshoot Server, explaining our approach to ensuring quality and reliability.

## Test Categories

### 1. Unit Tests (`tests/unit/`)
- **Purpose**: Test individual components in isolation
- **Coverage**: Bundle management, file operations, kubectl execution
- **Run with**: `uv run pytest tests/unit/`
- **CI**: Always run on every PR

### 2. Integration Tests (`tests/integration/`)
- **Purpose**: Test multiple components working together
- **Coverage**: Tool function integration, error handling scenarios
- **Run with**: `uv run pytest tests/integration/`
- **CI**: Always run on every PR

### 3. E2E Tests (`tests/e2e/`)
- **Purpose**: Test complete workflows end-to-end
- **Coverage**: Direct tool integration, container functionality
- **Run with**: `uv run pytest tests/e2e/`
- **CI**: Run `test_direct_tool_integration.py` on every PR

### 4. Container Tests
- **Purpose**: Test server running in container environment
- **Coverage**: Melange/Apko builds, container-specific functionality
- **Run with**: `uv run pytest -m container`
- **CI**: Run on every PR (slower tests)

## Testing Philosophy

### Direct Tool Testing
We test MCP tool functionality by calling the tool functions directly rather than through the MCP protocol layer. This approach provides:

1. **Better Reliability**: No dependency on protocol implementation details
2. **Faster Execution**: Direct function calls are much faster
3. **Clearer Errors**: Direct exceptions rather than protocol error codes
4. **Easier Debugging**: Standard Python debugging tools work directly

### Protocol Layer Testing
The MCP protocol layer is handled by the FastMCP framework, which is well-tested by its maintainers. We focus our testing efforts on:

1. **Business Logic**: The actual tool implementations
2. **Error Handling**: How tools handle various error conditions
3. **Integration**: How tools work together in workflows
4. **Performance**: Ensuring tools respond quickly

### Why No MCP Protocol Tests?
We previously had MCP protocol tests that communicated via JSON-RPC, but removed them because:

1. **Limited Value**: They tested FastMCP's protocol handling, not our code
2. **Maintenance Burden**: Required maintaining a custom test client
3. **Redundant Coverage**: All functionality was already tested directly
4. **False Failures**: Protocol changes caused test failures unrelated to functionality

## Running Tests

### Local Development
```bash
# Run all tests
uv run pytest

# Run specific test categories
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/e2e/

# Run with coverage
uv run pytest --cov=src --cov-report=term
```

### CI/CD Pipeline
The GitHub Actions workflow runs tests in this order:

1. **Fast Feedback** (parallel):
   - Linting and type checking
   - Unit tests
   - Direct tool E2E tests

2. **Comprehensive Testing** (after fast tests pass):
   - Integration tests
   - Container tests

## Test Requirements

### Environment Setup
- Python 3.13+
- UV for dependency management
- sbctl binary (for bundle operations)
- Test fixtures in `tests/fixtures/`

### Writing New Tests
1. Use appropriate test category based on scope
2. Follow existing patterns in test files
3. Use fixtures for common setup
4. Ensure tests are independent and can run in any order
5. Add appropriate pytest markers (@pytest.mark.unit, etc.)

## Coverage Goals

We aim for:
- 80%+ overall code coverage
- 90%+ coverage for critical paths (bundle loading, tool execution)
- 100% coverage for error handling code

Current coverage is tracked via Codecov on each PR.