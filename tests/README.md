# Tests for MCP Server Troubleshoot

This directory contains tests for the MCP server for Kubernetes support bundles.

## Test Structure

The tests are organized into the following directories:

- `unit/`: Unit tests for individual components (BundleManager, FileExplorer, KubectlExecutor, Server)
- `integration/`: Integration tests for multiple components working together, including real bundle tests
- `e2e/`: End-to-end tests for the full system, including container and Docker tests
- `fixtures/`: Test data and fixtures
- `util/`: Utility scripts for testing

## Setting Up the Test Environment

Before running tests, set up your development environment:

```bash
# Setup environment with our helper script (recommended)
./scripts/setup_env.sh

# Or manually set up the environment
uv venv -p python3.13 .venv
uv pip install -e ".[dev]"
```

## Running Tests

You can run tests using the provided script:

```bash
# Run all tests
./scripts/run_tests.sh

# Run specific test types
./scripts/run_tests.sh unit        # Run unit tests only
./scripts/run_tests.sh integration # Run integration tests
./scripts/run_tests.sh e2e         # Run end-to-end tests
./scripts/run_tests.sh quick       # Run quick verification tests
```

Alternatively, you can run tests directly with UV:

```bash
# Run all tests
uv run pytest

# Run specific test categories with markers
uv run pytest -m unit
uv run pytest -m integration
uv run pytest -m e2e

# Run specific test files
uv run pytest tests/unit/test_bundle.py

# Run with verbosity
uv run pytest -v

# Run with timeouts to prevent hanging tests
uv run pytest --timeout=30
```

## Test Categories

### Unit Tests

The unit tests test individual components in isolation:

- `test_bundle.py`: Tests for the BundleManager
- `test_files.py`: Tests for the FileExplorer
- `test_kubectl.py`: Tests for the KubectlExecutor
- `test_server.py`: Tests for the MCP server implementation
- `test_bundle_path_resolution.py`: Tests for bundle path resolution
- `test_components.py`: Tests for component interactions
- `test_lifecycle.py`: Tests for lifecycle management
- `test_grep_fix.py`: Tests for grep functionality

#### Parameterized Unit Tests

These tests use pytest parameterization to test key input combinations:

- `test_files_parametrized.py`: Focused tests for FileExplorer with essential scenarios
- `test_kubectl_parametrized.py`: Tests kubectl command execution with various inputs
- `test_server_parametrized.py`: Tests MCP server tools with different input combinations

### Integration Tests

The integration tests test multiple components working together:

- `test_real_bundle.py`: Tests using actual support bundles
- `test_mcp_client_config.py`: Tests for MCP client configuration
- `test_stdio_lifecycle.py`: Documentation on lifecycle tests (now in e2e tests)

### End-to-End Tests

The e2e tests test the full system:

#### Test Files

- `test_non_container.py`: Tests that verify basic e2e functionality without needing containers
  - Tests package imports and API functionality
  - Verifies CLI commands work correctly
  - Tests actual API components initialization and interaction

- `test_podman_container.py`: Podman container tests with efficient fixtures
  - Uses module-scoped fixtures to build container image only once
  - Provides isolated container instances for each test
  - Tests multiple container aspects (startup, tools, volume mounting)
  - Verifies required files exist (Containerfile, scripts)
  - Checks that tools are properly installed (sbctl, kubectl)

- `test_podman.py`: Additional Podman tests focused on container build and run processes
  - Tests file existence (Containerfile, .containerignore)
  - Tests script executability (build.sh, run.sh)
  - Tests container building, running, and tool installation

- `quick_check.py`: Basic checks for development and testing environment

## Test Implementation Patterns

The test suite uses several patterns to improve quality and maintainability:

### 1. Parameterized Tests

Parameterized tests provide several benefits:
- Targeted coverage of key scenarios with less code duplication
- Clear documentation of valid/invalid inputs
- Easier to add new test cases
- Improved test readability

### 2. Test Assertion Helpers

The `TestAssertions` class provides:
- Consistent assertion patterns across tests
- Improved failure messages
- Reduced boilerplate in test methods
- Specialized assertions for API responses

### 3. Test Object Factories

The `TestFactory` class generates test objects with sensible defaults:
- Reduces boilerplate for creating common test objects
- Ensures consistency in test objects across test files
- Simplifies test setup by focusing only on relevant properties
- Makes tests more maintainable when object structures change

### 4. Fixtures for Common Scenarios

Several fixtures provide standardized test environments:

- `test_file_setup`: Creates a consistent file environment for testing
- `mock_bundle_manager`: Provides a pre-configured mock bundle manager
- `mock_command_environment`: Sets up isolated command testing environment
- `error_setup`: Provides standard error scenarios for testing

## Test Suite Improvements

The test suite has been optimized with a focus on Podman for container testing:

### 1. Podman-Focused Container Testing

- **Podman-Only**: Tests now use Podman exclusively for container operations
- **Module-Scoped Fixtures**: Container images are built only once per test module
- **Concurrent Test Execution**: Tests are designed to run in parallel where possible
- **Reduced Redundancy**: Eliminated duplicate code across container test files

### 2. Maintainability Improvements

- **Focused Test Files**: Each test file has a clear, specific purpose
- **Better Documentation**: Improved docstrings and README documentation
- **Consistent Patterns**: Used consistent fixture and test patterns throughout
- **Simplified Structure**: Clear separation between container and non-container tests

### 3. Smart Functional Testing

- **Value-Based Testing**: Tests focus on actual behavior rather than implementation details
- **Strategic Test Coverage**: Tests cover real functionality and critical edge cases
- **API-Driven Tests**: Tests verify API contracts and component interactions
- **Real-World Scenarios**: Tests simulate actual usage patterns
- **Quality Over Quantity**: Focus on meaningful tests that prevent regressions

### 4. Container Testing Optimization

- **Single Build Process**: Podman container is built only once during test suite execution
- **Isolated Test Instances**: Each test gets a fresh container instance without rebuilding
- **Proper Resource Cleanup**: All containers and images are properly cleaned up
- **Clear Container Lifecycle**: Tests clearly separate build, run, and cleanup phases

### 5. CI Workflow Improvements

- **Targeted Test Selection**: CI workflow runs tests based on their category
- **Better Failure Reporting**: Test failures are more clearly reported
- **Faster Feedback Loop**: Developers get faster feedback on their changes
- **Simplified CI Configuration**: Workflow steps clearly match test categories

## Best Practices

Follow these guidelines when writing tests:

### 1. Focus on Valuable Functional Testing

- Test what the function *does*, not how it *does it*
- Define clear functional contracts for components
- Focus on business-critical paths and edge cases
- Test the public API rather than internal methods
- Prioritize tests that catch real bugs over exhaustive coverage

### 2. Use Proper Test Isolation

- Each test should be independent
- Use fixtures for common setup
- Properly clean up resources
- Avoid test interdependence

### 3. Mock at the Right Level

- Mock external dependencies, not internal implementations
- When testing async code, use `AsyncMock` appropriately
- Create proper test doubles with the right interfaces
- Use patch with side_effect rather than monkeypatching

### 4. Asyncio Testing Best Practices

- Always use `@pytest.mark.asyncio` for async tests
- Use proper fixtures for event loop management
- Ensure all resources are cleaned up
- Handle asyncio-specific cleanup issues properly

## Test Data and Fixtures

The test fixtures directory contains:

- Sample data for testing, including a small support bundle
- Mock implementations for testing (mock_kubectl.py, mock_sbctl.py)

## Adding New Tests

When adding new tests:

1. Place them in the appropriate directory based on test scope:
   - Unit tests for individual components in `unit/`
   - Integration tests for component interactions in `integration/`
   - End-to-end tests in `e2e/`
2. Follow the naming convention of `test_*.py` for test files
3. Use pytest fixtures for test setup and teardown
4. Add documentation in docstrings for each test
5. Add appropriate timeout marks to prevent tests from hanging
6. Clean up resources in Docker and container tests
7. Consider using parameterized tests for key scenario coverage
8. Focus on testing behavior rather than implementation details

## Warning Handling

The test suite handles warnings in a targeted way:

1. **Asyncio-related warnings**: These are handled with specific filters in pytest.ini
   and conftest.py.

2. **Unix Pipe Transport Warning**: Filtered due to a Python standard library issue
   with `_UnixReadPipeTransport.__del__`.

3. **Event Loop Closed Warning**: Filtered because it occurs during normal asyncio cleanup
   when the event loop is closing.

When adding new warning filters:
- Never use blanket suppressions
- Document each suppressed warning with reasons
- Try to fix root causes rather than suppressing