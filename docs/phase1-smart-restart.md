# Phase 1: Smart Restart Implementation

## Goal
Unblock Temporal testing by implementing smart restart with health probing, eliminating unnecessary sbctl restarts on every kubectl call.

## Changes Required

### 1. Add Health Probe Function

```python
async def _health_probe_kubectl(self, kubeconfig_path: Path, timeout: float = 2.0) -> bool:
    """
    Test if kubectl API server is responding via quick version check.

    Args:
        kubeconfig_path: Path to kubeconfig file
        timeout: Maximum time to wait for response

    Returns:
        True if API server responds successfully, False otherwise
    """
    if not kubeconfig_path.exists():
        return False

    try:
        process = await asyncio.create_subprocess_exec(
            "kubectl", "version", "--client=false",
            "--kubeconfig", str(kubeconfig_path),
            "--request-timeout", f"{int(timeout)}s",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )

        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=timeout + 1.0)
            return exit_code == 0
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except:
                pass
            return False
    except Exception as e:
        logger.debug(f"Health probe failed: {e}")
        return False
```

### 2. Persist Metadata to Disk

Add metadata persistence after successful sbctl start:

```python
async def _persist_sbctl_metadata(self, bundle_id: str):
    """
    Persist sbctl process metadata to disk for recovery across activity invocations.

    Args:
        bundle_id: Bundle ID to persist metadata for
    """
    if bundle_id not in self.bundle_states:
        return

    state = self.bundle_states[bundle_id]
    if not state.metadata:
        return

    process = self.sbctl_processes.get(bundle_id)
    if not process:
        return

    # Create metadata file in bundle directory
    metadata_file = state.metadata.path / "sbctl_metadata.json"

    metadata = {
        "bundle_id": bundle_id,
        "bundle_path": str(state.metadata.path / "bundle.tar.gz"),
        "kubeconfig_path": str(state.metadata.kubeconfig_path),
        "pid": process.pid,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running"
    }

    try:
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)
        logger.debug(f"Persisted sbctl metadata for {bundle_id} to {metadata_file}")
    except Exception as e:
        logger.warning(f"Failed to persist metadata for {bundle_id}: {e}")

async def _load_sbctl_metadata(self, bundle_id: str) -> Optional[dict]:
    """
    Load persisted sbctl metadata from disk.

    Args:
        bundle_id: Bundle ID to load metadata for

    Returns:
        Metadata dict if found, None otherwise
    """
    if bundle_id not in self.bundle_states:
        return None

    state = self.bundle_states[bundle_id]
    if not state.metadata:
        return None

    metadata_file = state.metadata.path / "sbctl_metadata.json"

    if not metadata_file.exists():
        return None

    try:
        with open(metadata_file) as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load metadata for {bundle_id}: {e}")
        return None
```

### 3. Add File Locking

Use filelock to prevent concurrent sbctl starts:

```python
from filelock import FileLock, Timeout as FileLockTimeout

async def _ensure_sbctl_with_lock(self, bundle_id: str) -> bool:
    """
    Ensure sbctl is running with file-based locking to prevent concurrent starts.

    Args:
        bundle_id: Bundle ID to ensure sbctl for

    Returns:
        True if sbctl is running (or was started), False otherwise
    """
    if bundle_id not in self.bundle_states:
        return False

    state = self.bundle_states[bundle_id]
    if not state.metadata:
        return False

    # Create lock file
    lock_file = state.metadata.path / "sbctl.lock"
    lock = FileLock(lock_file, timeout=30)

    try:
        with lock:
            # Inside lock - check if already running
            if await self._is_sbctl_healthy(bundle_id):
                logger.debug(f"sbctl already running and healthy for {bundle_id}")
                return True

            # Not healthy - need to start/restart
            logger.info(f"Starting sbctl for {bundle_id} (under lock)")
            success = await self._restart_sbctl_process_for_bundle(bundle_id)

            if success:
                await self._persist_sbctl_metadata(bundle_id)

            return success
    except FileLockTimeout:
        logger.error(f"Timeout waiting for lock on {lock_file}")
        return False
    except Exception as e:
        logger.error(f"Error ensuring sbctl for {bundle_id}: {e}")
        return False

async def _is_sbctl_healthy(self, bundle_id: str) -> bool:
    """
    Check if sbctl is running and healthy for a bundle.

    Checks:
    1. Process handle exists in memory
    2. Process hasn't exited (returncode is None)
    3. Kubeconfig file exists
    4. API server responds to health probe

    Args:
        bundle_id: Bundle ID to check

    Returns:
        True if healthy, False otherwise
    """
    # Check in-memory process handle
    process = self.sbctl_processes.get(bundle_id)
    if process and process.returncode is None:
        # Process handle exists and running - verify with health probe
        state = self.bundle_states.get(bundle_id)
        if state and state.metadata and state.metadata.kubeconfig_path:
            if await self._health_probe_kubectl(state.metadata.kubeconfig_path):
                logger.debug(f"sbctl healthy (in-memory process + probe) for {bundle_id}")
                return True

    # No in-memory handle or probe failed - check persisted metadata
    metadata = await self._load_sbctl_metadata(bundle_id)
    if not metadata:
        return False

    # Check if persisted process still exists
    try:
        pid = metadata.get("pid")
        if pid:
            os.kill(pid, 0)  # Signal 0 = check if process exists

            # Process exists - health probe
            kubeconfig_path = Path(metadata["kubeconfig_path"])
            if await self._health_probe_kubectl(kubeconfig_path):
                logger.info(f"sbctl healthy (recovered from disk) for {bundle_id}")
                # TODO: Could try to reattach process handle here, but not critical for Phase 1
                return True
    except ProcessLookupError:
        # Process doesn't exist
        pass
    except Exception as e:
        logger.debug(f"Error checking persisted process for {bundle_id}: {e}")

    return False
```

### 4. Update check_api_server_available()

Replace current implementation with smart restart logic:

```python
async def check_api_server_available(self, bundle_id: Optional[str] = None) -> bool:
    """
    Check if the Kubernetes API server is available for a specific bundle.

    Phase 1: Implements smart restart with health probing and file locking.
    - Only restarts sbctl if health probe fails
    - Uses file locks to prevent concurrent starts
    - Persists metadata for recovery across activity invocations

    Args:
        bundle_id: Bundle ID to check (if None, uses active_bundle for legacy compatibility)

    Returns:
        True if the API server is responding, False otherwise
    """
    # Legacy compatibility
    if not bundle_id:
        if self.active_bundle:
            bundle_id = self.active_bundle.id
        else:
            logger.warning("No bundle_id provided and no active_bundle")
            return False

    # Ensure bundle state exists
    if bundle_id not in self.bundle_states:
        logger.warning(f"Bundle {bundle_id} not found in bundle_states")
        return False

    # Use smart restart with locking
    return await self._ensure_sbctl_with_lock(bundle_id)
```

### 5. Update _start_sbctl_process to Persist Metadata

Add metadata persistence call after successful start:

```python
async def _start_sbctl_process(
    self, bundle_path: Path, working_dir: Optional[Path] = None, bundle_id: Optional[str] = None
) -> None:
    """
    Start the sbctl process with the given bundle.

    Args:
        bundle_path: Path to the bundle to serve
        working_dir: Directory to run sbctl in (defaults to bundle path parent)
        bundle_id: Bundle ID to associate with this process (required for concurrent support)
    """
    # ... existing implementation ...

    # NEW: Persist metadata after successful start
    if bundle_id:
        await self._persist_sbctl_metadata(bundle_id)
```

## Testing Strategy

### Unit Tests

```python
async def test_health_probe_success():
    """Test health probe returns True for healthy API server."""
    manager = BundleManager()
    kubeconfig = create_test_kubeconfig_with_mock_api()
    assert await manager._health_probe_kubectl(kubeconfig) is True

async def test_health_probe_failure():
    """Test health probe returns False for unreachable API server."""
    manager = BundleManager()
    kubeconfig = create_test_kubeconfig_with_dead_api()
    assert await manager._health_probe_kubectl(kubeconfig) is False

async def test_file_locking_prevents_concurrent_starts():
    """Test that file lock prevents multiple concurrent sbctl starts."""
    manager = BundleManager()
    bundle_id = "test-bundle"

    # Start two concurrent ensure operations
    results = await asyncio.gather(
        manager._ensure_sbctl_with_lock(bundle_id),
        manager._ensure_sbctl_with_lock(bundle_id),
    )

    # Both should succeed, but only one should actually start sbctl
    assert all(results)
    assert count_sbctl_starts() == 1  # Only one actual start

async def test_metadata_persistence():
    """Test that metadata is persisted and loaded correctly."""
    manager = BundleManager()
    bundle_id = "test-bundle"

    # Start sbctl
    await manager._ensure_sbctl_with_lock(bundle_id)

    # Verify metadata file exists
    metadata_file = manager.bundle_dir / bundle_id / "sbctl_metadata.json"
    assert metadata_file.exists()

    # Load metadata
    loaded = await manager._load_sbctl_metadata(bundle_id)
    assert loaded is not None
    assert loaded["bundle_id"] == bundle_id
    assert "pid" in loaded

async def test_health_check_with_persisted_metadata():
    """Test that health check works with persisted metadata after process restart."""
    manager1 = BundleManager()
    bundle_id = "test-bundle"

    # Start sbctl
    await manager1._ensure_sbctl_with_lock(bundle_id)

    # Simulate activity completion (lose in-memory process handle)
    manager1.sbctl_processes.clear()

    # Create new manager (simulating new activity invocation)
    manager2 = BundleManager()

    # Should detect running sbctl from persisted metadata
    is_healthy = await manager2._is_sbctl_healthy(bundle_id)
    assert is_healthy is True
```

### Integration Tests

```python
async def test_temporal_kubectl_flow():
    """Test full Temporal flow: initialize → lose process handle → kubectl still works."""

    # Activity 1: initialize_bundle
    manager1 = BundleManager()
    await manager1.initialize_bundle(
        source="https://example.com/bundle.tar.gz",
        bundle_id="workflow-123"
    )

    # Simulate activity completion
    del manager1

    # Activity 2: kubectl (new manager instance)
    manager2 = BundleManager()

    # Should detect existing sbctl and use it (no restart)
    api_available = await manager2.check_api_server_available("workflow-123")
    assert api_available is True

    # kubectl should work
    result = await manager2.execute_kubectl("workflow-123", ["get", "nodes"])
    assert result.exit_code == 0
```

## Rollout Plan

1. **Add new helper functions** (health probe, metadata persistence, locking)
2. **Update check_api_server_available()** to use smart restart
3. **Add unit tests** for new functions
4. **Test in SSE mode** (should behave identically to before)
5. **Test in Temporal mode** (should eliminate unnecessary restarts)
6. **Monitor metrics** (restart count, health probe success rate)

## Expected Improvements

### Before Phase 1
- Every kubectl call in Temporal mode: restart sbctl (~5-10 seconds delay)
- Unnecessary restarts when sbctl is still running
- Race conditions on concurrent kubectl calls

### After Phase 1
- kubectl calls use existing sbctl if healthy (no delay)
- Only restart when health probe actually fails
- File locks prevent concurrent restart attempts
- Persistent metadata enables recovery across activity invocations

## Metrics to Track

```python
# Add counters
sbctl_health_probes_total{result="success|failure"}
sbctl_restarts_total{reason="process_missing|unhealthy|crashed"}
sbctl_starts_prevented_by_health_probe_total
file_lock_acquisitions_total{result="success|timeout"}
```

## Known Limitations (Phase 1)

1. **Can't truly reattach** to process - just verify it's running
2. **No TTL/GC** - processes run indefinitely once started
3. **No resource limits** - unlimited concurrent bundles
4. **No centralized observability** - logs scattered across activities

These limitations will be addressed in Phase 2 (sbctl-manager service).

## Migration to Phase 2

Phase 1 code is designed to be easily replaceable:
- All sbctl management logic encapsulated in helper methods
- Could swap implementation with RPC client to manager service
- Metadata format compatible with Phase 2 state directory

```python
# Phase 1 (current)
await self._ensure_sbctl_with_lock(bundle_id)

# Phase 2 (future)
await self.sbctl_manager.ensure(bundle_id, bundle_path)
```
