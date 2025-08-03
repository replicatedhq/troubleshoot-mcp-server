# GitHub Workflows

This directory contains GitHub Actions workflows for automating testing, linting, and publishing.

## PR Checks (`pr-checks.yaml`)

This workflow runs automatically when:
- A PR is opened or updated against the `main` branch
- Code is pushed to the `main` branch
- It's manually triggered through the GitHub UI

### Jobs

The workflow is split into several jobs that run in parallel after the initial tests pass:

1. **Test, Lint and Type Check**
   - Runs unit and integration tests
   - Performs code linting with Ruff
   - Checks code formatting with Black
   - Performs type checking with mypy
   - Generates test coverage reports

2. **Container Tests**
   - Tests Podman container build and run processes
   - Verifies the Containerfile and related scripts are correct
   - Builds and runs the container in a test environment

3. **Other E2E Tests**
   - Runs end-to-end tests that don't involve containers
   - Uses the "e2e" marker but excludes the "container" marker

### Local Reproduction

To reproduce these checks locally:

```bash
# Install dependencies
uv pip install -e ".[dev]"

# Run unit tests
uv run pytest -m unit -v

# Run integration tests
uv run pytest -m integration -v

# Run E2E tests (excluding container tests)
uv run pytest -m "e2e and not container" -v

# Run container tests
uv run pytest -m container -v

# Run linting
uv run ruff check .

# Run formatting check
uv run black --check .

# Run type checking
uv run mypy src
```

## Container Publishing (`publish-container.yaml`)

This workflow automatically builds and publishes the container image when:
- A SemVer tag is pushed (e.g., `1.0.0`, `2.1.3`)
- It's manually triggered for testing purposes

The container is published to the GitHub Container Registry (GHCR) with both the specific version tag and a `latest` tag (for non-test runs).

### Local Reproduction

To build the container locally:

```bash
# Build the container
./scripts/build.sh

# Or directly with Podman
podman build -t troubleshoot-mcp-server:latest -f Containerfile .
```

## Adding New Workflows

When adding new workflows:
1. Create a YAML file in this directory with the `.yaml` extension
2. Document the workflow in this README
3. Ensure the workflow uses the same standards (UV for Python commands, etc.)
4. Update existing workflows if dependencies change