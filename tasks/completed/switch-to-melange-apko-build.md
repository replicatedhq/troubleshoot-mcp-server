# Task: Switch to Melange/Apko Based Image and Tooling

## Metadata
- **Status**: active
- **Started**: 2025-06-20
- **Dependencies**: none

## Context

Switch from traditional Podman Containerfile builds to melange/apko based builds using Wolfi base images for enhanced security and smaller footprint.

Current state uses multi-stage Containerfile with Python 3.13-slim base images. Target state uses melange for package building and apko for container image creation with Wolfi/Chainguard base.

**Also standardize naming**: The current project uses "mcp-server-troubleshoot" in some places but the repo is "troubleshoot-mcp-server". This task will standardize everything to use "troubleshoot-mcp-server".

## Implementation Steps

### Phase 1: Create Melange Package Configuration

1. **Create `.melange.yaml`**:
```yaml
package:
  name: troubleshoot-mcp-server
  version: ${{package.version}}
  description: MCP Server for Kubernetes Support Bundles
  copyright:
    - license: MIT
  dependencies:
    runtime:
      - python3
      - kubectl
      - sbctl

environment:
  contents:
    keyring:
      - https://packages.wolfi.dev/os/wolfi-signing.rsa.pub
    repositories:
      - https://packages.wolfi.dev/os
    packages:
      - ca-certificates-bundle
      - busybox
      - python3
      - python3-dev
      - py3-pip
      - build-base

pipeline:
  - name: Install package with dependencies
    runs: |
      python3 -m pip install .
```

2. **Test Phase 1**:
```bash
# Build package using melange container (multi-arch)
podman run --rm -v "$PWD":/work cgr.dev/chainguard/melange build .melange.yaml --arch=amd64,arm64
# Verify: ls packages/
```

### Phase 2: Create Apko Image Configuration

1. **Create `apko.yaml`**:
```yaml
contents:
  keyring:
    - https://packages.wolfi.dev/os/wolfi-signing.rsa.pub
  repositories:
    - https://packages.wolfi.dev/os
    - "@local ./packages"
  packages:
    - ca-certificates-bundle
    - wolfi-baselayout
    - troubleshoot-mcp-server@local

accounts:
  groups:
    - groupname: mcp
      gid: 1000
  users:
    - username: mcp-user
      uid: 1000
      gid: 1000
      shell: /bin/sh
      home: /home/mcp-user

work-dir: /home/mcp-user

entrypoint:
  command: /usr/bin/troubleshoot-mcp-server

environment:
  PATH: /usr/sbin:/sbin:/usr/bin:/bin
```

2. **Test Phase 2**:
```bash
# Build image using apko container (multi-arch)
podman run --rm -v "$PWD":/work cgr.dev/chainguard/apko build apko.yaml troubleshoot-mcp-server:latest troubleshoot-mcp-server.tar --arch=amd64,arm64
# Load and test
podman load < troubleshoot-mcp-server.tar
podman run --rm troubleshoot-mcp-server:latest --version
```

### Phase 3: Update Build Scripts

1. **Modify `scripts/build.sh`**:
```bash
#!/bin/bash
set -euo pipefail

IMAGE_NAME="${IMAGE_NAME:-troubleshoot-mcp-server}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

# Build melange package (multi-arch)
podman run --rm -v "$PWD":/work cgr.dev/chainguard/melange build .melange.yaml --arch=amd64,arm64

# Build apko image (multi-arch)
podman run --rm -v "$PWD":/work cgr.dev/chainguard/apko build apko.yaml "${IMAGE_NAME}:${IMAGE_TAG}" "${IMAGE_NAME}.tar" --arch=amd64,arm64

# Load into podman
podman load < "${IMAGE_NAME}.tar"

echo "Built ${IMAGE_NAME}:${IMAGE_TAG}"
```

2. **Test Phase 3**:
```bash
# Test build script
./scripts/build.sh
# Test run script
./scripts/run.sh
# Verify MCP server responds
echo '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}' | podman run -i --rm troubleshoot-mcp-server:latest
```

### Phase 4: Update and Run Container Tests

**Update existing container test infrastructure instead of manual testing:**

1. **Update container build fixture in `tests/conftest.py`**:
   - Modify `docker_image` fixture to use melange/apko build process instead of `podman build`

2. **Update build verification tests in `tests/e2e/test_podman.py`**:
   - Update tests to check for `.melange.yaml` and `apko.yaml` instead of `Containerfile`

3. **Run existing container test suite**:
```bash
# Run all container tests
uv run pytest -m container -v

# Run specific container functionality tests
uv run pytest tests/e2e/test_podman_container.py -v

# Run container build tests
uv run pytest tests/e2e/test_podman.py -v
```

**The existing tests already verify:**
- Container build process
- MCP server startup and protocol response
- kubectl and sbctl tool availability
- User permissions and environment
- Bundle processing functionality
- Volume mounting and file operations

### Phase 5: Update CI/CD Pipeline

1. **Modify `.github/workflows/publish-container.yaml`** - Update to use melange/apko while keeping existing version tagging and workflow structure intact. The current workflow uses SemVer tags without 'v' prefix and publishes as `ghcr.io/repo/troubleshoot-mcp-server`.

2. **Update `.github/workflows/pr-checks.yaml`** - Update container build section to use melange/apko and run existing container test suite

## Files to Create/Modify

### New Files:
- `.melange.yaml` - Package definition  
- `apko.yaml` - Image configuration

### Modified Files:
- `scripts/build.sh` - Replace Containerfile build with melange/apko
- `.github/workflows/publish-container.yaml` - Use melange/apko tools and standardize to troubleshoot-mcp-server naming
- `.github/workflows/pr-checks.yaml` - Update container tests
- `tests/conftest.py` - Update container build fixture for melange/apko
- `tests/e2e/test_podman.py` - Update build file checks

## Testing Strategy

**Phase 1-3**: Manual verification commands provided for each phase
**Phase 4**: Use existing comprehensive container test suite (`uv run pytest -m container`) instead of manual tests
**Phase 5**: CI/CD pipeline integration and testing

## Acceptance Criteria

- [ ] Melange builds Python package successfully
- [ ] Apko creates functional container image  
- [ ] Container serves MCP protocol correctly
- [ ] kubectl and sbctl binaries available and functional
- [ ] Non-root user (mcp-user) properly configured
- [ ] CI/CD pipeline builds and publishes successfully
- [ ] All naming standardized to "troubleshoot-mcp-server"
- [ ] Container test suite passes with new build process

## Progress Log

- **2025-06-20**: Started task implementation, created worktree and moved to active status
- **2025-06-20**: Completed Phase 1 - Created .melange.yaml package configuration
- **2025-06-20**: Completed Phase 2 - Created apko.yaml image configuration
- **2025-06-20**: Completed Phase 3 - Updated scripts/build.sh for melange/apko workflow
- **2025-06-20**: Completed Phase 4 - Updated container test infrastructure for melange/apko
- **2025-06-20**: Completed Phase 5 - Updated CI/CD pipeline for melange/apko and standardized naming to troubleshoot-mcp-server