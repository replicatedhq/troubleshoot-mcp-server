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

### 3. Functional Tests (`tests/functional/`)
- **Purpose**: Test MCP protocol compatibility and server functionality through JSON-RPC
- **Coverage**: All 5 required tools validated through actual MCP protocol communication
- **Run with**: `uv run pytest tests/functional/` or `uv run pytest -m functional`
- **CI**: Run after lint passes, before slower tests
- **Key Features**:
  - Full protocol validation using MCPTestClient over stdio transport
  - Tool discovery and schema validation
  - Bundle lifecycle management through protocol
  - Error handling and parameter validation
  - Performance benchmarks (tool discovery <100ms, calls <5s)
  - Sequential execution adapted for stdio transport limitations

### 4. E2E Tests (`tests/e2e/`)
- **Purpose**: Test complete workflows end-to-end
- **Coverage**: Direct tool integration, container functionality
- **Run with**: `uv run pytest tests/e2e/`
- **CI**: Run `test_direct_tool_integration.py` on every PR

### 5. Container Tests
- **Purpose**: Test server running in container environment
- **Coverage**: Melange/Apko builds, container-specific functionality
- **Run with**: `uv run pytest -m container`
- **CI**: Run on every PR (slower tests)

## Testing Philosophy

### Multi-Layer Testing Approach
We use a comprehensive testing strategy that validates functionality at multiple levels:

#### Direct Tool Testing (Unit/Integration Tests)
We test MCP tool functionality by calling the tool functions directly for:

1. **Better Reliability**: No dependency on protocol implementation details
2. **Faster Execution**: Direct function calls are much faster
3. **Clearer Errors**: Direct exceptions rather than protocol error codes
4. **Easier Debugging**: Standard Python debugging tools work directly

#### MCP Protocol Testing (Functional Tests)
We also validate the complete MCP protocol layer to ensure compatibility:

1. **Protocol Compatibility**: Ensures server works correctly as an MCP server
2. **Schema Validation**: Verifies tool schemas follow MCP standards
3. **JSON-RPC Communication**: Tests actual protocol communication over stdio
4. **Error Boundary Testing**: Validates proper MCP error responses
5. **Performance Benchmarks**: Ensures protocol responses meet timing requirements

### Why Both Testing Layers?
This dual approach provides comprehensive coverage:

- **Direct Testing**: Fast, reliable validation of business logic and error handling
- **Protocol Testing**: Ensures MCP compatibility and catches protocol-breaking changes
- **Defense in Depth**: Changes that break either layer are caught before deployment
- **Future-Proof**: Protocol tests catch compatibility issues as MCP standard evolves

### Previous Protocol Test Issues (Historical)
We previously removed protocol tests due to maintenance issues, but have now implemented a robust approach using:

1. **MCPTestClient**: Reuses existing integration test infrastructure
2. **stdio Transport**: Simple, reliable communication method
3. **Sequential Execution**: Adapted for transport limitations
4. **Response Format Flexibility**: Handles server response evolution gracefully

## CI Optimization Strategy

### Coverage-First Approach
We run all tests with coverage enabled from the start to eliminate duplicate execution:

**Previous Approach (Inefficient):**
- Functional tests ran separately (5 minutes)
- All tests re-ran in coverage job (7.7 minutes)
- Total: ~13 minutes with 5 minutes wasted on duplicates

**Current Approach (Optimized):**
- All tests run once with coverage (7.7 minutes)
- Fast-fail enabled (exits on first failure)
- Total: ~8 minutes, 40% faster

**Why This Works:**
- pytest-cov overhead is minimal (<5%)
- Coverage data collected in single pass
- No artifact passing or merging complexity
- Simpler workflow maintenance

### Fast-Fail Behavior
Tests use `-x` flag to exit immediately on first failure:
- Saves time during development (don't wait for full suite if something breaks)
- Provides faster feedback on broken builds
- Reduces GitHub Actions minutes usage

## Running Tests

### Local Development
```bash
# Run all tests
uv run pytest

# Run specific test categories
uv run pytest tests/unit/
uv run pytest tests/integration/
uv run pytest tests/functional/  # MCP protocol validation
uv run pytest tests/e2e/

# Run with coverage
uv run pytest --cov=src --cov-report=term
```

### CI/CD Pipeline
The GitHub Actions workflow runs tests in this order:

1. **Fast Feedback** (parallel):
   - Linting and type checking (~24s)
   - Direct tool E2E tests (~21s)

2. **Comprehensive Testing** (after lint passes, parallel):
   - All tests with coverage (~7.7m)
     - Runs: unit + integration + functional tests
     - Coverage-enabled from start (no duplicate runs)
     - Fast-fail enabled (exits on first failure)
   - Container tests (~2.5m)

3. **Coverage Reporting** (~13s):
   - Generates coverage summary
   - Posts PR comment with details
   - Enforces 60% threshold

**Total CI Time:** ~8 minutes (optimized from previous ~13 minutes)

## Test Requirements

### Environment Setup
- Python 3.13+
- UV for dependency management
- sbctl binary (for bundle operations)
- Test fixtures in `tests/fixtures/`

### Writing New Tests
1. Use appropriate test category based on scope:
   - **Unit**: Testing isolated components/functions
   - **Integration**: Testing multiple components together
   - **Functional**: Testing through MCP protocol (for new tools or protocol changes)
   - **E2E**: Testing complete workflows
   - **Container**: Testing container-specific functionality

2. Follow existing patterns in test files
3. Use fixtures for common setup
4. Ensure tests are independent and can run in any order
5. Add appropriate pytest markers (@pytest.mark.unit, @pytest.mark.functional, etc.)

### When to Add Functional Tests
Add functional tests when:
- **Adding new MCP tools**: Ensure they work through the protocol layer
- **Changing tool schemas**: Validate schema compatibility is maintained
- **Modifying response formats**: Ensure protocol responses remain valid
- **Protocol-level changes**: Any changes that could affect MCP communication
- **Performance requirements**: When tools need specific response time guarantees

### Functional Test Patterns

#### Single Client Testing (Most Common)
```python
@pytest.mark.functional
@pytest.mark.asyncio
async def test_new_tool_via_protocol(mcp_protocol_client: MCPTestClient) -> None:
    \"\"\"Test new tool through MCP protocol.\"\"\"
    # Setup - initialize bundle if needed
    await mcp_protocol_client.call_tool("initialize_bundle", {...})

    # Execute - call tool through protocol
    result = await mcp_protocol_client.call_tool("new_tool", {...})

    # Verify - check response format and content
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert "expected content" in result[0]["text"]
```

#### Parallel Testing (For Performance-Critical Operations)
```python
@pytest.mark.functional
@pytest.mark.asyncio
async def test_parallel_operations() -> None:
    \"\"\"Test operations in parallel using multiple client instances.\"\"\"
    clients = []
    try:
        # Create multiple clients (each with own server subprocess)
        for i in range(3):
            client = MCPTestClient()
            await client.start_server()
            await client.initialize_mcp({\"name\": f\"client-{i}\", \"version\": \"1.0.0\"})
            await client.send_notification(\"notifications/initialized\")
            clients.append(client)

        # Run operations in true parallel (2.94x speedup demonstrated)
        tasks = [client.call_tool(\"tool_name\", {...}) for client in clients]
        results = await asyncio.gather(*tasks)

        # Verify all results...

    finally:
        # Clean up all clients
        await asyncio.gather(*[client.cleanup() for client in clients])
```

**Performance Benefits**: Parallel testing provides 2.94x speedup for I/O-bound operations like bundle initialization.

## Coverage Goals

We aim for:
- 55%+ combined code coverage (across unit, integration, and functional tests)
- 60%+ unit test coverage
- 45%+ integration test coverage
- 100% functional test coverage of all MCP tools (protocol validation)
- 90%+ coverage for critical paths (bundle loading, tool execution)

Coverage is tracked natively in GitHub Actions on each PR:
- Coverage percentages are displayed in GitHub Actions summaries
- Coverage reports are posted as PR comments with file-by-file breakdowns
- Coverage thresholds are enforced - PRs fail if coverage drops below minimums
- No external services required - uses built-in GitHub features