# Task: Implement Single Bundle Mode for Stateless Bundle Management

## Metadata
**Status**: backlog
**Created**: 2025-10-03
**Priority**: high
**Estimated effort**: medium

## Objective
Implement a "single bundle mode" that eliminates in-memory bundle state tracking by treating the presence of a bundle on disk as the source of truth. This enables stateless MCP server operation where each server restart automatically uses the bundle stored on disk without requiring manual reactivation.

## Context
The MCP server currently tracks bundle state using `self.active_bundle` (in-memory). This works for long-running server instances but fails in scenarios where the server is restarted frequently, such as:

- **Temporal/workflow orchestration**: Each activity (tool call) spawns a fresh MCP server process
- **Serverless/lambda deployments**: Short-lived function executions
- **Container-per-request architectures**: Each request gets a fresh container

Current behavior:
1. Activity 1: `initialize_bundle` → downloads bundle → sets `self.active_bundle` → MCP server exits
2. Activity 2: New MCP server starts → `self.active_bundle` is None → subsequent tools fail with "No bundle is active"

Even with `PRESERVE_BUNDLES=true`, the bundle files persist on disk but the new MCP server instance doesn't know which bundle to use.

## Problem Statement
**The "active bundle" concept is purely in-memory state that doesn't survive process restarts.**

When an MCP server restarts, it has no way to know:
- That a bundle exists on disk
- Which bundle should be active
- Whether to auto-load the bundle or wait for initialization

This makes the server **stateful** and incompatible with stateless/ephemeral execution patterns.

## Proposed Solution: Single Bundle Mode

Add a new environment variable: `MCP_SINGLE_BUNDLE_MODE=true`

When enabled, the server operates in "single bundle mode" with the following behavior:

### 1. On Server Startup (Any Activity)
- Check `MCP_BUNDLE_STORAGE` directory for existing bundle directories
- If exactly one bundle exists:
  - Automatically load it as the active bundle (auto-activation)
  - Log: "Single bundle mode: auto-activated bundle {bundle_id}"
- If multiple bundles exist:
  - Clean up all bundles (enforce single bundle constraint)
  - Wait for new `initialize_bundle` call
  - Log: "Single bundle mode: found multiple bundles, cleaning up"
- If no bundles exist:
  - Wait for `initialize_bundle` call
  - Log: "Single bundle mode: no bundle found, waiting for initialization"

### 2. On `initialize_bundle` Call
- **Always clean up existing bundles first** (enforce single bundle invariant)
- Download and extract the new bundle
- The bundle becomes "active" by virtue of being the only bundle on disk
- Log: "Single bundle mode: cleaned up existing bundles, initialized new bundle {bundle_id}"

### 3. On Tool Calls Requiring Bundle (list_files, kubectl, grep, etc.)
- If single bundle mode is enabled:
  - Look for bundle in `MCP_BUNDLE_STORAGE`
  - If bundle exists, use it (no "activation" needed - presence = usability)
  - If no bundle exists, return error: "No bundle found. Initialize a bundle first."
- If single bundle mode is disabled (default):
  - Use existing `self.active_bundle` tracking (current behavior)

### 4. On Server Shutdown
- `PRESERVE_BUNDLES=true` continues to work: bundle files remain on disk
- Single bundle mode and PRESERVE_BUNDLES work together seamlessly

## Success Criteria
- [ ] Environment variable `MCP_SINGLE_BUNDLE_MODE` added with default value `false` (off by default)
- [ ] When enabled, server auto-activates bundle on startup if exactly one exists
- [ ] When enabled, `initialize_bundle` cleans up existing bundles before initializing new one
- [ ] When enabled, tool calls automatically use the single bundle present on disk
- [ ] When enabled with multiple bundles present, server cleans up all bundles
- [ ] When disabled, existing behavior unchanged (use `self.active_bundle`)
- [ ] Integration tests verify stateless operation across server restarts
- [ ] Documentation updated with single bundle mode usage patterns

## Dependencies
- Existing `BundleManager` class in `src/troubleshoot_mcp_server/bundle.py`
- `PRESERVE_BUNDLES` environment variable (already implemented)
- `MCP_BUNDLE_STORAGE` environment variable (already used for bundle storage location)

## Implementation Plan

### 1. Add Single Bundle Mode Configuration
**File**: `src/troubleshoot_mcp_server/bundle.py`

Add environment variable check in `BundleManager.__init__`:
```python
def __init__(self, temp_dir: Path):
    self.temp_dir = temp_dir
    self.active_bundle: Optional[BundleMetadata] = None
    # ... existing initialization ...

    # Single bundle mode: treat bundle presence as activation
    self.single_bundle_mode = os.environ.get("MCP_SINGLE_BUNDLE_MODE", "false").lower() == "true"

    if self.single_bundle_mode:
        logger.info("Single bundle mode enabled: bundle presence = activation")
```

### 2. Implement Bundle Auto-Discovery on Startup
**File**: `src/troubleshoot_mcp_server/bundle.py`

Add method to discover and auto-activate bundle:
```python
async def _auto_activate_bundle_if_exists(self) -> None:
    """
    Auto-activate bundle if single bundle mode is enabled and exactly one bundle exists.

    This should be called on server startup to restore bundle state from disk.
    Enforces single bundle invariant by cleaning up if multiple bundles found.
    """
    if not self.single_bundle_mode:
        return

    # Look for bundle directories in MCP_BUNDLE_STORAGE
    bundle_storage = Path(os.environ.get("MCP_BUNDLE_STORAGE", self.temp_dir))
    bundle_dirs = [d for d in bundle_storage.iterdir() if d.is_dir() and d.name.startswith("b_")]

    if len(bundle_dirs) == 0:
        logger.info("Single bundle mode: no bundle found, waiting for initialization")
        return

    if len(bundle_dirs) > 1:
        logger.warning(f"Single bundle mode: found {len(bundle_dirs)} bundles, cleaning up to enforce single bundle invariant")
        for bundle_dir in bundle_dirs:
            shutil.rmtree(bundle_dir, ignore_errors=True)
        return

    # Exactly one bundle - auto-activate it
    bundle_dir = bundle_dirs[0]
    bundle_id = bundle_dir.name
    logger.info(f"Single bundle mode: auto-activating bundle {bundle_id}")

    # Reconstruct BundleMetadata from directory
    self.active_bundle = BundleMetadata(
        id=bundle_id,
        path=bundle_dir,
        source=None,  # Unknown - bundle was persisted
        created_at=datetime.now()
    )
```

### 3. Call Auto-Activation on Server Startup
**File**: `src/troubleshoot_mcp_server/lifecycle.py` or `src/troubleshoot_mcp_server/server.py`

Call auto-activation during server initialization (exact location depends on server architecture):
```python
async def initialize_server():
    bundle_manager = BundleManager(temp_dir)
    await bundle_manager._auto_activate_bundle_if_exists()  # Auto-activate if single bundle mode
    # ... rest of initialization ...
```

### 4. Modify `initialize_bundle` to Clean Up in Single Bundle Mode
**File**: `src/troubleshoot_mcp_server/bundle.py`

Update `initialize_bundle` method:
```python
async def initialize_bundle(self, source: str | Path, force: bool = False) -> BundleMetadata:
    # Check if bundle is already initialized
    if self.active_bundle and not force:
        logger.info(f"Using already initialized bundle: {self.active_bundle.id}")
        return self.active_bundle

    # Single bundle mode: always clean up existing bundles first
    if self.single_bundle_mode:
        logger.info("Single bundle mode: cleaning up existing bundles before initialization")
        await self._cleanup_active_bundle()
        # Also clean up any orphaned bundle directories
        bundle_storage = Path(os.environ.get("MCP_BUNDLE_STORAGE", self.temp_dir))
        for bundle_dir in bundle_storage.glob("b_*"):
            if bundle_dir.is_dir():
                shutil.rmtree(bundle_dir, ignore_errors=True)
    else:
        # Normal mode: only clean up active bundle
        await self._cleanup_active_bundle()

    # ... rest of initialization logic ...
```

### 5. Update Tool Calls to Use Single Bundle Mode
**File**: `src/troubleshoot_mcp_server/bundle.py`

Update methods that check `self.active_bundle` to support auto-discovery:
```python
def _ensure_bundle_active(self) -> BundleMetadata:
    """
    Ensure a bundle is active. In single bundle mode, auto-discover from disk.

    Raises:
        RuntimeError: If no bundle is active and none found on disk

    Returns:
        BundleMetadata: The active bundle
    """
    if self.active_bundle:
        return self.active_bundle

    if self.single_bundle_mode:
        # Try to auto-discover bundle from disk
        bundle_storage = Path(os.environ.get("MCP_BUNDLE_STORAGE", self.temp_dir))
        bundle_dirs = [d for d in bundle_storage.iterdir() if d.is_dir() and d.name.startswith("b_")]

        if len(bundle_dirs) == 1:
            bundle_dir = bundle_dirs[0]
            logger.info(f"Single bundle mode: auto-discovered bundle {bundle_dir.name}")
            self.active_bundle = BundleMetadata(
                id=bundle_dir.name,
                path=bundle_dir,
                source=None,
                created_at=datetime.now()
            )
            return self.active_bundle

    raise RuntimeError(
        "No bundle is active. Please initialize a bundle first using the "
        "initialize_bundle tool. Provide a bundle URL or path to the "
        "initialize_bundle tool."
    )
```

Use `_ensure_bundle_active()` in all methods that require a bundle.

### 6. Testing Strategy

#### Unit Tests (`tests/unit/test_single_bundle_mode.py`)
- Test auto-activation with single bundle on disk
- Test cleanup with multiple bundles on disk
- Test no-op with no bundles on disk
- Test `initialize_bundle` cleanup in single bundle mode
- Test `_ensure_bundle_active` auto-discovery

#### Integration Tests (`tests/integration/test_single_bundle_mode_stateless.py`)
- **Critical**: Simulate server restart between tool calls
  1. Server starts → `initialize_bundle` → server stops
  2. New server starts → auto-activates bundle → `list_files` succeeds
  3. New server starts → `kubectl` succeeds (without re-initialization)
- Test concurrent initialization (second init cleans up first bundle)
- Test `PRESERVE_BUNDLES=true` + single bundle mode together

### 7. Documentation Updates
**Files**: `README.md`, `docs/` (if exists)

Add section on single bundle mode:
```markdown
## Single Bundle Mode (Stateless Operation)

For stateless/ephemeral deployments (Temporal workflows, serverless, etc.),
enable single bundle mode:

```bash
export MCP_SINGLE_BUNDLE_MODE=true
export PRESERVE_BUNDLES=true
export MCP_BUNDLE_STORAGE=/persistent-storage/bundles
```

In this mode:
- The server auto-activates the bundle on disk (if exactly one exists)
- `initialize_bundle` cleans up existing bundles before creating new ones
- No need to track bundle state across server restarts
- Each server restart automatically uses the persisted bundle
```

## Acceptance Criteria
- Single bundle mode is off by default (backwards compatible)
- When enabled, server operates statelessly (survives restarts)
- Tool calls succeed after server restart without re-initialization
- `initialize_bundle` enforces single bundle invariant
- Comprehensive tests verify stateless behavior
- Documentation clearly explains use cases and configuration

## Example Usage Scenario

### Temporal Workflow (Current Problem)
```
Activity 1: initialize_bundle
  → Start MCP server
  → Download bundle → self.active_bundle = metadata
  → Exit MCP server (bundle files preserved with PRESERVE_BUNDLES=true)

Activity 2: list_files
  → Start NEW MCP server
  → self.active_bundle = None (in-memory state lost)
  → Error: "No bundle is active" ❌
```

### Temporal Workflow (With Single Bundle Mode)
```
Activity 1: initialize_bundle
  → Start MCP server (MCP_SINGLE_BUNDLE_MODE=true)
  → Download bundle → bundle exists in /persistent-storage/bundles/
  → Exit MCP server (PRESERVE_BUNDLES=true)

Activity 2: list_files
  → Start NEW MCP server (MCP_SINGLE_BUNDLE_MODE=true)
  → Auto-discover bundle from /persistent-storage/bundles/
  → self.active_bundle auto-set from disk
  → list_files succeeds ✅
```

## Notes
- This is a new feature, not a breaking change (off by default)
- Works seamlessly with existing `PRESERVE_BUNDLES` functionality
- Enables truly stateless operation for modern deployment patterns
- Single bundle constraint prevents confusion about which bundle is "active"
- Auto-cleanup when multiple bundles exist ensures consistent state

## Evidence of Completion
(To be filled by AI)
- [ ] Command output demonstrating server restart with auto-activation
- [ ] Integration test output showing stateless operation
- [ ] Path to created/modified files
- [ ] Summary of changes made

## Progress Updates
(To be filled by AI during implementation)
