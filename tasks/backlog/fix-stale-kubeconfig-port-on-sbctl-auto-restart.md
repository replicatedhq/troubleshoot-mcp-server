# Task: Fix Stale Kubeconfig Port on sbctl Auto-Restart

## Status: Backlog
- **Created**: 2025-10-13
- **Priority**: High
- **Complexity**: Medium
- **Type**: Bug Fix

## Problem Statement

When MCP_SINGLE_BUNDLE_MODE=true and a bundle is restored from disk, the auto-restart logic for sbctl fails due to stale kubeconfig port information. Specifically:

1. Agent calls `initialize_bundle`
2. MCP server checks `/tmp/mcp-bundles/` and finds existing bundle from earlier
3. Bundle is restored from disk with `source="<restored-from-disk>"`
4. Server detects sbctl is NOT running
5. Auto-restart logic kicks in (introduced in v1.15.0)
6. **BUG**: sbctl gets a NEW random port on restart
7. Old kubeconfig still has the OLD port (e.g., 56395 from hours ago)
8. `check_api_server_available()` reads stale kubeconfig and tries old port
9. Connection fails, returns `api_unavailable`

**Root Cause**: When sbctl restarts, it generates a fresh kubeconfig with a new random port. But if an old kubeconfig already exists in the bundle directory, sbctl may not overwrite it or the check happens before the new kubeconfig is written. The `check_api_server_available()` method reads this stale kubeconfig and attempts to connect to the wrong port.

## Testing Gap Analysis

**Why didn't integration tests catch this?**

The integration test `test_sbctl_auto_restart_real_bundle()` in `tests/integration/test_single_bundle_mode_stateless.py:310` uses a REAL sbctl process but has a race condition:

```python
# Line 355: Call check_api_server_available - should auto-restart sbctl if needed
await manager2.check_api_server_available()

# Line 358: KEY ASSERTION: sbctl should now be running
assert manager2.sbctl_process is not None, "sbctl should be running after check"
```

**The test passes because:**
1. The test only checks if `sbctl_process is not None` (process started)
2. It does NOT verify the kubeconfig port matches the new sbctl port
3. It does NOT attempt a kubectl command that would actually hit the API server
4. The test cleans up immediately, so it never discovers the port mismatch

**What should have been tested:**
- After auto-restart, verify kubeconfig port matches sbctl's actual port
- Run `kubectl get pods` to verify API server is actually reachable
- Check that the kubeconfig was freshly generated (timestamp, port range)

## Implementation Plan

### Step 1: Delete Stale Kubeconfig Before Auto-Restart
**File**: `src/troubleshoot_mcp_server/bundle.py`

In `_restart_sbctl_process()` (line 1589), add kubeconfig cleanup before restarting sbctl:

```python
async def _restart_sbctl_process(self) -> bool:
    """
    Restart the sbctl process after a crash.

    Returns:
        True if restart was successful, False otherwise
    """
    if not self.active_bundle:
        logger.error("Cannot restart sbctl: no active bundle")
        return False

    try:
        # ... existing crash info capture code ...

        logger.warning(f"Restarting sbctl after crash (exit code: {exit_code})")

        # Clean up current process
        await self._terminate_sbctl_process()

        # Clear stderr buffer for fresh start
        self._stderr_buffer.clear()

        # **NEW CODE**: Delete stale kubeconfig before restart
        # This ensures sbctl creates a fresh kubeconfig with the new port
        if self.active_bundle and self.active_bundle.kubeconfig_path.exists():
            try:
                logger.info(f"Deleting stale kubeconfig before sbctl restart: {self.active_bundle.kubeconfig_path}")
                self.active_bundle.kubeconfig_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete stale kubeconfig (continuing anyway): {e}")

        # Restart sbctl with the same bundle
        bundle_path = self.active_bundle.path
        await self._start_sbctl_process(bundle_path)

        # ... rest of existing code ...
```

**Rationale:**
- sbctl generates kubeconfig on startup with a random port
- By deleting the old kubeconfig, we force sbctl to create a fresh one
- This ensures `check_api_server_available()` reads the correct port
- If deletion fails, we log and continue (sbctl may still overwrite)

### Step 2: Also Delete Kubeconfig in `_initialize_with_sbctl` During Auto-Activate
**File**: `src/troubleshoot_mcp_server/bundle.py`

In `_auto_activate_bundle_if_exists()` (line 267), before attempting to restart sbctl:

```python
# Line 317-323: Attempt to restart sbctl process for the restored bundle
try:
    # **NEW CODE**: Delete stale kubeconfig before attempting restart
    if kubeconfig_path.exists():
        try:
            logger.info(f"Deleting stale kubeconfig before auto-activate restart: {kubeconfig_path}")
            kubeconfig_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete stale kubeconfig during auto-activate (continuing): {e}")

    await self._initialize_with_sbctl(bundle_dir / "bundle.tar.gz", bundle_dir)
except Exception as e:
    logger.warning(
        f"Could not restart sbctl for restored bundle (bundle may still be usable): {e}"
    )
```

**Rationale:**
- When auto-activating a bundle from disk, the kubeconfig is from a previous sbctl instance
- That instance is long dead, and its port is stale
- By deleting before restart, sbctl creates a fresh kubeconfig with the correct port

### Step 3: Add Kubeconfig Freshness Check
**File**: `src/troubleshoot_mcp_server/bundle.py`

Add a helper method to verify kubeconfig matches running sbctl (optional, for robustness):

```python
def _is_kubeconfig_stale(self) -> bool:
    """
    Check if the kubeconfig might be stale.

    Returns:
        True if kubeconfig appears stale, False otherwise
    """
    if not self.active_bundle or not self.active_bundle.kubeconfig_path.exists():
        return False

    # If sbctl is not running, kubeconfig is definitely stale
    if not self.sbctl_process or self.sbctl_process.returncode is not None:
        return True

    # TODO: Could add additional checks:
    # - Compare kubeconfig mtime with sbctl process start time
    # - Parse kubeconfig port and verify sbctl is listening on that port

    return False
```

Use this check in `check_api_server_available()` to detect stale state early.

## Testing Strategy

### Unit Tests
**File**: `tests/unit/test_bundle.py`

Add test to verify kubeconfig deletion during restart:

```python
@pytest.mark.asyncio
async def test_restart_sbctl_deletes_stale_kubeconfig(bundle_manager, test_support_bundle):
    """Test that _restart_sbctl_process deletes stale kubeconfig."""
    # Initialize bundle
    bundle_manager._initialize_with_sbctl = AsyncMock()
    bundle_manager._start_sbctl_process = AsyncMock()

    bundle = await bundle_manager.initialize_bundle(str(test_support_bundle))
    kubeconfig_path = bundle.kubeconfig_path

    # Create a fake stale kubeconfig
    kubeconfig_path.parent.mkdir(parents=True, exist_ok=True)
    kubeconfig_path.write_text("apiVersion: v1\nclusters:\n- cluster:\n    server: http://127.0.0.1:99999\n  name: old-cluster\n")

    assert kubeconfig_path.exists()
    old_mtime = kubeconfig_path.stat().st_mtime

    # Simulate crash
    bundle_manager.sbctl_process = AsyncMock()
    bundle_manager.sbctl_process.returncode = 1

    # Restart should delete kubeconfig
    await bundle_manager._restart_sbctl_process()

    # Verify kubeconfig was deleted
    # (sbctl would create new one, but we've mocked it)
    # Check that old file is gone OR has newer mtime
    if kubeconfig_path.exists():
        new_mtime = kubeconfig_path.stat().st_mtime
        assert new_mtime > old_mtime, "Kubeconfig should be refreshed"
```

### Integration Tests
**File**: `tests/integration/test_single_bundle_mode_stateless.py`

**Enhance existing test** `test_sbctl_auto_restart_real_bundle()` (line 310):

```python
@pytest.mark.asyncio
async def test_sbctl_auto_restart_real_bundle(persistent_bundle_dir, test_support_bundle):
    """
    REAL integration test: Verify sbctl auto-restarts after server restart
    AND kubeconfig port is correct.
    """
    # === PHASE 1: Initialize bundle with REAL sbctl ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager1 = BundleManager(persistent_bundle_dir)
        bundle = await manager1.initialize_bundle(str(test_support_bundle))
        assert bundle.initialized is True

        # Capture original kubeconfig port
        import yaml
        with open(bundle.kubeconfig_path) as f:
            kubeconfig1 = yaml.safe_load(f)
        port1 = kubeconfig1['clusters'][0]['cluster']['server'].split(':')[-1]
        logger.info(f"Phase 1 kubeconfig port: {port1}")

        bundle_id = bundle.id
        await manager1._terminate_sbctl_process()

    # === PHASE 2: New server - simulate restart ===
    with patch.dict(os.environ, {"MCP_SINGLE_BUNDLE_MODE": "true", "PRESERVE_BUNDLES": "true"}):
        manager2 = BundleManager(persistent_bundle_dir)
        await manager2._auto_activate_bundle_if_exists()

        assert manager2.active_bundle is not None
        assert manager2.active_bundle.id == bundle_id

        # Call check_api_server_available - should auto-restart sbctl
        api_available = await manager2.check_api_server_available()

        # KEY ASSERTIONS
        assert manager2.sbctl_process is not None, "sbctl should be running after check"
        assert manager2.sbctl_process.returncode is None, "sbctl process should be alive"

        # **NEW ASSERTION**: Verify kubeconfig port is fresh
        with open(manager2.active_bundle.kubeconfig_path) as f:
            kubeconfig2 = yaml.safe_load(f)
        port2 = kubeconfig2['clusters'][0]['cluster']['server'].split(':')[-1]
        logger.info(f"Phase 2 kubeconfig port: {port2}")

        # Port should be different (sbctl assigns random port)
        # If they're the same, it's likely stale (very low probability of collision)
        # Actually, better check: verify API server is reachable
        assert api_available, "API server should be available after auto-restart"

        # **NEW ASSERTION**: Test actual kubectl connectivity
        import subprocess
        result = subprocess.run(
            ["kubectl", "get", "pods", "--v=0"],
            env={"KUBECONFIG": str(manager2.active_bundle.kubeconfig_path)},
            capture_output=True,
            timeout=5
        )
        assert result.returncode == 0, f"kubectl should work with auto-restarted sbctl: {result.stderr.decode()}"

        await manager2.cleanup()
```

**Rationale for test changes:**
- Capture original kubeconfig port before restart
- After auto-restart, verify kubeconfig port (may be different)
- **Critical**: Run `kubectl get pods` to verify API server is actually reachable
- This catches the bug: if kubeconfig is stale, kubectl will fail with connection refused

### Manual Testing Steps
1. Start MCP server with `MCP_SINGLE_BUNDLE_MODE=true PRESERVE_BUNDLES=true`
2. Initialize bundle via `initialize_bundle` tool
3. Note kubeconfig port: `grep 'server:' /tmp/mcp-bundles/*/kubeconfig`
4. Stop MCP server (simulate restart)
5. Start MCP server again (bundle auto-activates)
6. Call any kubectl tool (e.g., `kubectl_get_pods`)
7. **VERIFY**: Tool succeeds (not `api_unavailable`)
8. Check kubeconfig port again - may be different from step 3
9. Verify sbctl is running: `ps aux | grep sbctl`

## Files to Modify

1. **src/troubleshoot_mcp_server/bundle.py**
   - `_restart_sbctl_process()` - Add kubeconfig deletion before restart (line ~1620)
   - `_auto_activate_bundle_if_exists()` - Add kubeconfig deletion before auto-activate restart (line ~318)

2. **tests/integration/test_single_bundle_mode_stateless.py**
   - Enhance `test_sbctl_auto_restart_real_bundle()` to verify kubeconfig freshness and actual API connectivity (line 310)

3. **tests/unit/test_bundle.py** (optional)
   - Add `test_restart_sbctl_deletes_stale_kubeconfig()` unit test

## Acceptance Criteria

- [ ] `_restart_sbctl_process()` deletes stale kubeconfig before restarting sbctl
- [ ] `_auto_activate_bundle_if_exists()` deletes stale kubeconfig before attempting sbctl restart
- [ ] Integration test verifies kubeconfig freshness after auto-restart
- [ ] Integration test runs `kubectl get pods` to verify actual API connectivity
- [ ] Manual testing confirms kubectl tools work after MCP server restart
- [ ] All existing tests pass (no regressions)
- [ ] Code quality checks pass: `uv run ruff format . && uv run ruff check . && uv run mypy src`

## Success Metrics

- **Before Fix**: `api_unavailable` error after server restart with restored bundle
- **After Fix**: kubectl tools succeed immediately after auto-restart
- **Test Coverage**: Integration test catches stale kubeconfig bug

## Dependencies

- Existing auto-restart logic in `_restart_sbctl_process()` (v1.15.0)
- Single bundle mode infrastructure (`MCP_SINGLE_BUNDLE_MODE`)
- Bundle persistence (`PRESERVE_BUNDLES`)

## Rollout Notes

- **Risk**: Low - only affects error recovery path (auto-restart)
- **Rollback**: If issues occur, remove kubeconfig deletion code
- **Monitoring**: Watch for increased auto-restart failures (shouldn't happen)

## Related Issues/Tasks

- v1.15.0: Auto-restart sbctl feature (PR #65)
- Single bundle mode implementation (task file: `tasks/active/implement-single-bundle-mode.md`)

## Notes for Implementation Agent

**Key Points:**
1. The bug is subtle: old kubeconfig with stale port survives across restarts
2. Solution is simple: delete kubeconfig before sbctl restart
3. Testing is crucial: must verify ACTUAL API connectivity, not just process state
4. Two code paths need the fix: `_restart_sbctl_process()` AND `_auto_activate_bundle_if_exists()`

**Testing Priority:**
- Integration test enhancement is MANDATORY (this is what caught the gap)
- Unit test is optional but recommended for coverage
- Manual testing should verify end-to-end flow

**Code Quality:**
- Use existing logging patterns (logger.info, logger.warning)
- Handle file deletion failures gracefully (log and continue)
- No exceptions should propagate from kubeconfig deletion

**Edge Cases:**
- Kubeconfig already deleted (file not found) - OK, continue
- Kubeconfig locked by another process - log warning, continue
- sbctl fails to create new kubeconfig - existing error handling catches this

**Expected Test Results After Fix:**
- `uv run pytest -m integration tests/integration/test_single_bundle_mode_stateless.py::test_sbctl_auto_restart_real_bundle -v` - PASS
- kubectl commands work immediately after server restart
- No `api_unavailable` errors in logs
