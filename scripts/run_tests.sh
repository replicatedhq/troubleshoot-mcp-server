#!/bin/bash
# Script to run tests for the MCP server with proper markers using UV
# UV manages the environment, no manual activation needed
set -e

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Go to the project root
cd "$PROJECT_ROOT"

# Define usage
function usage() {
  echo "Usage: $0 [test_type] [options]"
  echo
  echo "Test Types:"
  echo "  unit         Run unit tests                          (uv run pytest -m unit)"
  echo "  integration  Run integration tests                   (uv run pytest -m integration)"
  echo "  e2e          Run end-to-end tests                    (uv run pytest -m e2e)"
  echo "  quick        Run quick verification tests            (uv run pytest -m quick)"
  echo "  docker       Run tests that need Docker              (uv run pytest -m docker)"
  echo "  all          Run all tests (default)                 (uv run pytest)"
  echo
  echo "Options:"
  echo "  -v, --verbose     Run with verbose output            (uv run pytest -v)"
  echo "  --no-timeout      Disable test timeouts              (uv run pytest --timeout 0)"
 
  echo "  --                Pass remaining options to pytest   (uv run pytest ...)"
  echo
  echo "Examples:"
  echo "  $0                   # Run all tests"
  echo "  $0 unit              # Run only unit tests"
  echo "  $0 e2e -v            # Run e2e tests with verbose output"
  echo "  $0 quick --mock-sbctl # Run quick tests with mock sbctl"
  echo "  $0 docker -- -k \"container\"  # Run Docker tests matching 'container'"
  exit 1
}

# Default options
VERBOSE=""
TIMEOUT=""
TEST_TYPE=${1:-all}
shift_index=0

# Parse command-line arguments that we handle
for arg in "$@"; do
  shift_index=$((shift_index + 1))
  
  case "$arg" in
    -v|--verbose)
      VERBOSE="-v"
      ;;
    --no-timeout)
      TIMEOUT="--timeout 0"
      ;;
    --)
      # Stop parsing our args
      break
      ;;
    --help)
      usage
      ;;
  esac
done

# Remove the arguments we've processed
shift $shift_index

# Determine pytest marker based on test type
case "$TEST_TYPE" in
  unit)
    MARKER="-m unit"
    ;;
  integration)
    MARKER="-m integration"
    ;;
  e2e)
    MARKER="-m e2e"
    ;;
  quick)
    MARKER="-m quick"
    ;;
  docker)
    MARKER="-m docker"
    ;;
  all)
    MARKER=""
    ;;
  --help)
    usage
    ;;
  *)
    echo "Unknown test type: $TEST_TYPE"
    usage
    ;;
esac

# Run the tests using UV directly
echo "Running tests: $TEST_TYPE"
uv run pytest $MARKER $VERBOSE $TIMEOUT "$@"