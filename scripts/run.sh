#!/bin/bash
set -e

# Configuration
IMAGE_NAME="troubleshoot-mcp-server"
IMAGE_TAG="latest"
BUNDLE_DIR="$(pwd)/tests/fixtures"
INTERACTIVE="-i"  # Default is MCP mode (-i)
VERBOSE=""
DEBUG_MODE=false
MCP_MODE=true

# Parse command-line options
ARGS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --debug)
      # Interactive debug mode with terminal
      INTERACTIVE="-it"
      DEBUG_MODE=true
      MCP_MODE=false
      shift
      ;;
    --verbose)
      VERBOSE="--verbose"
      shift
      ;;
    --bundle-dir=*)
      BUNDLE_DIR="${1#*=}"
      shift
      ;;
    --bundle-dir)
      BUNDLE_DIR="$2"
      shift 2
      ;;
    --no-mcp)
      MCP_MODE=false
      shift
      ;;
    *)
      if [ -z "$ARGS" ]; then
        ARGS="$1"
      else
        ARGS="$ARGS $1"
      fi
      shift
      ;;
  esac
done

# Create bundle directory if it doesn't exist
mkdir -p "${BUNDLE_DIR}"

# Set log level based on debug mode
LOG_LEVEL="ERROR"
if [ "$DEBUG_MODE" = true ]; then
  LOG_LEVEL="DEBUG"
  # In debug mode, we can echo to stdout
  echo "Running in DEBUG mode with terminal access"
  echo "Using bundle directory: ${BUNDLE_DIR}"
  
  # Check if SBCTL_TOKEN is set
  if [ -z "${SBCTL_TOKEN}" ]; then
    echo "Warning: SBCTL_TOKEN is not set. Some operations may fail."
    echo "Set it with: export SBCTL_TOKEN=your_token_here"
  fi
else
  # In MCP mode, only print to stderr
  >&2 echo "Starting MCP server"
  >&2 echo "Using bundle directory: $BUNDLE_DIR"
fi

# Create a unique container name
CONTAINER_NAME="mcp-server-$(date +%s)-$RANDOM"

# MCP mode is detected automatically by the CLI
# No additional arguments needed

# Run the container with the new entrypoint
if [ "$DEBUG_MODE" = true ]; then
  # Run in interactive debug mode
  podman run ${INTERACTIVE} --rm \
    -v "${BUNDLE_DIR}:/data/bundles" \
    -e SBCTL_TOKEN="${SBCTL_TOKEN:-}" \
    -e MCP_BUNDLE_STORAGE="/data/bundles" \
    -e MCP_LOG_LEVEL="${LOG_LEVEL}" \
    --name "$CONTAINER_NAME" \
    "${IMAGE_NAME}:${IMAGE_TAG}" ${VERBOSE} ${ARGS}
else
  # Run in MCP mode (default)
  # Pipe stdin to the container and don't use terminal
  cat | podman run ${INTERACTIVE} \
    -v "${BUNDLE_DIR}:/data/bundles" \
    -e SBCTL_TOKEN="${SBCTL_TOKEN:-}" \
    --rm \
    --name "$CONTAINER_NAME" \
    "${IMAGE_NAME}:${IMAGE_TAG}" ${VERBOSE} ${ARGS}
fi
