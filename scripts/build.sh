#!/bin/bash
set -euo pipefail

# Configuration - use environment variables if set, otherwise use defaults
# This allows GitHub Actions to override these values
IMAGE_NAME=${IMAGE_NAME:-"troubleshoot-mcp-server"}
IMAGE_TAG=${IMAGE_TAG:-"latest"}

# Print commands before executing them
set -x

echo "Building with melange/apko..."

# Build melange package (single arch for local development, multi-arch for CI)
ARCH_FLAGS="--arch=amd64"
if [[ "${CI:-false}" == "true" ]]; then
    ARCH_FLAGS="--arch=amd64,arm64"
fi

echo "Building melange package..."

# Determine which signing key to use based on context
SIGNING_KEY=""
if [[ "${MELANGE_TEST_BUILD:-false}" == "true" ]]; then
    echo "Using test signing key for testing..."
    SIGNING_KEY="melange-test.rsa"
    
    # Generate test keys if they don't exist
    if [ ! -f "$SIGNING_KEY" ]; then
        echo "Generating test signing keys..."
        ./scripts/generate_test_keys.sh
    fi
elif [ -f melange.rsa ]; then
    echo "Using production signing key..."
    SIGNING_KEY="melange.rsa"
else
    echo "ERROR: No signing key available!"
    echo ""
    echo "For testing/development:"
    echo "  export MELANGE_TEST_BUILD=true"
    echo "  ./scripts/build.sh"
    echo ""
    echo "For production builds:"
    echo "  1. Copy your melange.rsa private key to the project root"
    echo "  2. The key should be ignored by git (already in .gitignore)"
    echo "  3. For CI/CD, this key is provided via MELANGE_RSA secret"
    exit 1
fi

echo "Using signing key: $SIGNING_KEY"
if ! podman run --rm --privileged --cap-add=SYS_ADMIN -v "$PWD":/work cgr.dev/chainguard/melange build .melange.yaml ${ARCH_FLAGS} --signing-key="$SIGNING_KEY"; then
    echo "Melange build failed!"
    exit 1
fi

echo "Building apko image..."
APKO_FLAGS="${ARCH_FLAGS}"
if [[ "${MELANGE_TEST_BUILD:-false}" == "true" ]]; then
    echo "Ignoring signatures for test build..."
    APKO_FLAGS="${APKO_FLAGS} --ignore-signatures"
fi

if ! podman run --rm --privileged --cap-add=SYS_ADMIN -v "$PWD":/work cgr.dev/chainguard/apko build apko.yaml "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}.tar" ${APKO_FLAGS}; then
    echo "Apko build failed!"
    exit 1
fi

echo "Loading image into podman..."
if ! podman load < "${IMAGE_NAME}.tar"; then
    echo "Failed to load apko image!"
    exit 1
fi

echo "✅ Melange/apko build completed successfully!"
echo "📦 Image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "🔧 Includes: sbctl v0.17.2, kubectl v1.33, Python MCP server"
echo ""
echo "To run the container:"
echo "  podman run -it --rm \\"
echo "    -v \$(pwd)/tests/fixtures:/data/bundles \\"
echo "    -e SBCTL_TOKEN=your_token_here \\"
echo "    ${IMAGE_NAME}:${IMAGE_TAG} [options]"
