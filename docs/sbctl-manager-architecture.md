# sbctl-manager Service Architecture (Phase 2)

## Overview

A long-running external service that manages sbctl subprocess lifecycles independently of MCP server or Temporal activity lifecycles. This is the correct architectural pattern for managing persistent infrastructure processes in a distributed system.

## Problem Statement

**Current Issue**: sbctl subprocesses are managed within Temporal activities, which are ephemeral. When activities complete, subprocess handles are lost, causing kubectl commands to fail because the API server is unavailable.

**Root Cause**: Activities are designed to be stateless and retriable. Managing long-lived subprocesses within them violates this design principle.

**Correct Pattern**: Externalize subprocess lifecycle management to a dedicated service. Workers become stateless clients that access sbctl via RPC.

## Architecture Principles

### 1. Separate Lifecycles
- Workflow/activity lifetimes ≠ infrastructure/process lifetimes
- sbctl processes should persist across activity invocations
- Service manages process lifecycle independently

### 2. Idempotency Everywhere
- `ensure(bundle_id, bundle_path)` is safe to call repeatedly
- Returns existing running instance or starts a new one
- No side effects if process already running

### 3. Persistent State for Recovery
Store per-bundle metadata on disk:
- `bundle_path` - location of bundle tarball
- `kubeconfig_path` - path to generated kubeconfig
- `port` - API server port (random, assigned by sbctl)
- `pid` / `pgid` - process and process group IDs
- `started_at` - timestamp
- `last_used` - timestamp for TTL/GC
- `status` - running/stopped/crashed

### 4. Health Before Restart
Always probe health before deciding to restart:
```python
# Quick health check
kubectl version --kubeconfig=<path> --request-timeout=1s
```

### 5. Bounded Resources
- Max concurrent sbctl processes (configurable limit)
- TTL/idle timeout for automatic cleanup
- Process group isolation (setsid)
- Optional: cgroup/ulimit caps per process

### 6. Concurrency Control
- Per-bundle file locks during ensure/start operations
- Prevents duplicate sbctl instances for same bundle
- Critical for fan-out scenarios (multiple workflows initializing same bundle)

## Service API

### Core Operations

#### ensure(bundle_id, bundle_path, ttl_seconds=3600) -> ConnectionInfo
Ensures sbctl is running for the given bundle.

**Behavior:**
- If already running and healthy: return connection info
- If not running or unhealthy: start sbctl, wait for readiness, return info
- Thread-safe: uses per-bundle file lock

**Returns:**
```python
{
    "bundle_id": str,
    "kubeconfig_path": str,
    "port": int,
    "pid": int,
    "pgid": int,
    "started_at": str (ISO 8601),
    "status": "running"
}
```

#### kubectl(bundle_id, args, json_output=False, timeout=30) -> Result
Execute kubectl command against the bundle's API server.

**Behavior:**
- Calls `ensure()` first to guarantee sbctl is running
- Executes kubectl with provided args
- Returns structured result

**Returns:**
```python
{
    "exit_code": int,
    "stdout": str,
    "stderr": str,
    "execution_time": float
}
```

**Why expose kubectl execution?**
- Simplifies client code (no need to manage kubectl subprocess)
- Keeps credentials local to manager (security)
- Centralizes logging and metrics
- Reduces client dependencies (kubectl only needed on manager host)

#### status(bundle_id) -> StatusInfo
Get current status of a bundle without starting it.

**Returns:**
```python
{
    "bundle_id": str,
    "status": "running" | "stopped" | "unknown",
    "kubeconfig_path": str | None,
    "port": int | None,
    "pid": int | None,
    "started_at": str | None,
    "last_used": str | None,
    "uptime_seconds": float | None
}
```

#### stop(bundle_id) -> None
Gracefully stop sbctl process for a bundle.

**Behavior:**
- Send SIGTERM to process group
- Wait up to 10 seconds for graceful shutdown
- Send SIGKILL if still running
- Clean up state files
- Remove from active processes

#### gc() -> GarbageCollectionResult
Run garbage collection to clean up idle or dead processes.

**Behavior:**
- Scan all tracked bundles
- Stop processes idle longer than TTL
- Clean up processes that crashed (returncode != None)
- Remove stale state files

**Returns:**
```python
{
    "stopped_idle": int,      # count of idle processes stopped
    "cleaned_crashed": int,    # count of crashed processes cleaned
    "freed_bundles": [str],    # list of bundle_ids freed
}
```

## Process Management

### Starting sbctl

```python
async def _start_sbctl(bundle_id: str, bundle_path: Path) -> Process:
    # Create state directory
    state_dir = STATE_ROOT / "bundles" / bundle_id
    state_dir.mkdir(parents=True, exist_ok=True)

    # Change to state directory (sbctl writes kubeconfig to CWD)
    os.chdir(state_dir)

    # Start in new process group for isolation
    process = await asyncio.create_subprocess_exec(
        "sbctl", "serve",
        "--support-bundle-location", str(bundle_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True  # Creates new process group
    )

    # Write metadata
    metadata = {
        "bundle_id": bundle_id,
        "bundle_path": str(bundle_path),
        "pid": process.pid,
        "pgid": os.getpgid(process.pid),
        "started_at": datetime.now(UTC).isoformat(),
        "status": "starting"
    }

    # Wait for readiness
    kubeconfig_path = await _wait_for_readiness(state_dir, process)
    metadata["kubeconfig_path"] = str(kubeconfig_path)
    metadata["port"] = _extract_port_from_kubeconfig(kubeconfig_path)
    metadata["status"] = "running"

    # Persist metadata
    with open(state_dir / "metadata.json", "w") as f:
        json.dump(metadata, f)

    return process
```

### Readiness Detection

Wait for two conditions:
1. kubeconfig file exists
2. API server responds to health check

```python
async def _wait_for_readiness(state_dir: Path, process: Process, timeout: float = 30) -> Path:
    kubeconfig_path = state_dir / "kubeconfig"
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        # Check if process crashed
        if process.returncode is not None:
            raise ProcessCrashedError(f"sbctl exited with code {process.returncode}")

        # Check for kubeconfig
        if kubeconfig_path.exists():
            # Probe API health
            try:
                result = await asyncio.create_subprocess_exec(
                    "kubectl", "version",
                    "--kubeconfig", str(kubeconfig_path),
                    "--request-timeout", "1s",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                exit_code = await result.wait()
                if exit_code == 0:
                    return kubeconfig_path  # Ready!
            except Exception:
                pass  # Not ready yet

        await asyncio.sleep(0.5)

    raise TimeoutError(f"sbctl did not become ready within {timeout}s")
```

### Stopping sbctl

```python
async def _stop_sbctl(metadata: dict) -> None:
    pgid = metadata["pgid"]

    try:
        # Send SIGTERM to entire process group
        os.killpg(pgid, signal.SIGTERM)

        # Wait for graceful shutdown
        for _ in range(20):  # 10 seconds total
            try:
                os.killpg(pgid, 0)  # Check if process group exists
                await asyncio.sleep(0.5)
            except ProcessLookupError:
                # Process group gone - success
                return

        # Force kill if still running
        logger.warning(f"Bundle {metadata['bundle_id']} did not stop gracefully, sending SIGKILL")
        os.killpg(pgid, signal.SIGKILL)

    except ProcessLookupError:
        # Already gone
        pass
    finally:
        # Clean up state
        state_dir = STATE_ROOT / "bundles" / metadata["bundle_id"]
        shutil.rmtree(state_dir, ignore_errors=True)
```

## State Management

### Directory Structure

```
${STATE_ROOT}/bundles/
├── {bundle_id_1}/
│   ├── metadata.json         # Process metadata
│   ├── kubeconfig            # Generated by sbctl
│   ├── stdout.log            # Process stdout (rotated)
│   └── stderr.log            # Process stderr (rotated)
├── {bundle_id_2}/
│   └── ...
└── locks/
    ├── {bundle_id_1}.lock    # File lock for concurrent access
    └── {bundle_id_2}.lock
```

### Metadata Schema

```json
{
    "bundle_id": "workflow-run-id-12345",
    "bundle_path": "/path/to/mcp_bundles/workflow-run-id-12345/bundle.tar.gz",
    "kubeconfig_path": "/path/to/state/bundles/workflow-run-id-12345/kubeconfig",
    "port": 38947,
    "pid": 12345,
    "pgid": 12345,
    "started_at": "2025-11-07T10:30:00Z",
    "last_used": "2025-11-07T10:45:00Z",
    "status": "running",
    "uptime_seconds": 900
}
```

### Recovery on Service Restart

When the sbctl-manager restarts:

```python
async def recover_existing_processes():
    """Scan state directory and verify existing processes."""
    for bundle_dir in (STATE_ROOT / "bundles").iterdir():
        if not bundle_dir.is_dir():
            continue

        metadata_file = bundle_dir / "metadata.json"
        if not metadata_file.exists():
            logger.warning(f"No metadata for {bundle_dir.name}, cleaning up")
            shutil.rmtree(bundle_dir)
            continue

        with open(metadata_file) as f:
            metadata = json.load(f)

        # Check if process still exists
        try:
            os.kill(metadata["pid"], 0)  # Signal 0 = check existence

            # Verify it's actually healthy
            if await _health_check(metadata["kubeconfig_path"]):
                logger.info(f"Recovered bundle {metadata['bundle_id']} (PID {metadata['pid']})")
                # Add to active processes
                self.processes[metadata["bundle_id"]] = metadata
            else:
                logger.warning(f"Bundle {metadata['bundle_id']} process exists but unhealthy, will restart on demand")
                metadata["status"] = "unhealthy"
                self.processes[metadata["bundle_id"]] = metadata

        except ProcessLookupError:
            # Process dead, mark for cleanup
            logger.warning(f"Bundle {metadata['bundle_id']} process dead, marked for cleanup")
            metadata["status"] = "crashed"
            # Will be cleaned by GC or restarted on next ensure()
```

## Resource Management

### Global Limits

```python
class ResourceLimits:
    max_concurrent_processes: int = 10
    max_memory_per_process_mb: int = 512
    max_cpu_percent: float = 50.0
    default_ttl_seconds: int = 3600  # 1 hour
    gc_interval_seconds: int = 300   # 5 minutes
```

### TTL-based Cleanup

```python
async def gc_loop():
    """Background task that runs garbage collection periodically."""
    while True:
        await asyncio.sleep(RESOURCE_LIMITS.gc_interval_seconds)

        try:
            result = await gc()
            if result["stopped_idle"] > 0 or result["cleaned_crashed"] > 0:
                logger.info(f"GC: stopped {result['stopped_idle']} idle, "
                           f"cleaned {result['cleaned_crashed']} crashed")
        except Exception as e:
            logger.error(f"GC failed: {e}")
```

### Eviction Policy

When at max_concurrent_processes and new ensure() arrives:

1. Try to GC crashed/idle processes first
2. If still at limit, evict least-recently-used (LRU) process
3. Return error if no evictable processes (all recently used)

```python
async def _maybe_evict_for_new_bundle():
    if len(self.processes) < RESOURCE_LIMITS.max_concurrent_processes:
        return  # Room available

    # Run GC to free crashed/idle
    await gc()

    if len(self.processes) < RESOURCE_LIMITS.max_concurrent_processes:
        return  # GC freed space

    # Find LRU
    lru_bundle = min(
        self.processes.items(),
        key=lambda x: x[1]["last_used"]
    )

    logger.info(f"Evicting LRU bundle {lru_bundle[0]} to make room")
    await stop(lru_bundle[0])
```

## Integration Examples

### SSE Mode (Long-running MCP Server)

Replace in-memory subprocess management with manager calls:

```python
# In bundle.py
class BundleManager:
    def __init__(self):
        self.sbctl_manager = SbctlManagerClient()  # RPC client or in-proc

    async def check_api_server_available(self, bundle_id: str) -> bool:
        """Check if sbctl is running, start if needed."""
        try:
            # Ensure sbctl is up (idempotent)
            conn_info = await self.sbctl_manager.ensure(
                bundle_id=bundle_id,
                bundle_path=self._get_bundle_path(bundle_id)
            )
            return True
        except Exception as e:
            logger.error(f"Failed to ensure sbctl for {bundle_id}: {e}")
            return False

    async def execute_kubectl(self, bundle_id: str, args: list) -> dict:
        """Execute kubectl command via manager."""
        return await self.sbctl_manager.kubectl(
            bundle_id=bundle_id,
            args=args,
            timeout=30
        )
```

### Temporal Mode (Activities)

Activities become stateless clients:

```python
@activity.defn
async def initialize_bundle(source: str, bundle_id: str):
    """Download bundle and ensure sbctl is running."""
    # Download/extract bundle
    bundle_path = await download_and_extract(source, bundle_id)

    # Ensure sbctl is running (calls external manager)
    sbctl_manager = get_sbctl_manager_client()
    conn_info = await sbctl_manager.ensure(
        bundle_id=bundle_id,
        bundle_path=bundle_path
    )

    # Persist metadata for later tool calls
    metadata = BundleMetadata(
        id=bundle_id,
        path=bundle_path.parent,
        kubeconfig_path=Path(conn_info["kubeconfig_path"]),
        initialized=True
    )
    await save_metadata_to_disk(metadata)

    return {"bundle_id": bundle_id, "status": "ready"}

@activity.defn
async def kubectl(bundle_id: str, command: str):
    """Execute kubectl command."""
    # Load metadata
    metadata = await load_metadata_from_disk(bundle_id)

    # Execute via manager (ensures sbctl is still up)
    sbctl_manager = get_sbctl_manager_client()
    result = await sbctl_manager.kubectl(
        bundle_id=bundle_id,
        args=command.split(),
        timeout=30
    )

    if result["exit_code"] != 0:
        raise KubectlError(f"kubectl failed: {result['stderr']}")

    return result["stdout"]
```

## Deployment Options

### Option A: Separate Daemon Process (Recommended)

Run sbctl-manager as a systemd service:

```ini
[Unit]
Description=sbctl Process Manager
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/sbctl-manager serve --port 8080
Restart=always
RestartSec=5
User=sbctl-manager
WorkingDirectory=/var/lib/sbctl-manager

[Install]
WantedBy=multi-user.target
```

**Pros:**
- Clean separation of concerns
- Can be managed independently (restart without affecting MCP server)
- Centralizes all sbctl management
- Easier to implement resource limits (cgroups, systemd slice)

**Cons:**
- Additional deployment step
- Need RPC communication (HTTP/gRPC)
- More moving parts

### Option B: In-Process Manager (Development/Simple Deployments)

Embed manager in MCP server process:

```python
class InProcessSbctlManager:
    """Same API as remote manager, but runs in-process."""
    # ... same implementation ...

# In MCP server startup
sbctl_manager = InProcessSbctlManager(state_root="/var/lib/mcp-server/sbctl")
```

**Pros:**
- No additional process to manage
- No RPC overhead
- Simpler deployment

**Cons:**
- sbctl processes tied to MCP server lifecycle
- Harder to implement strict resource limits
- Less isolation

## API Protocol (Option A)

### HTTP/JSON API

```
POST /api/v1/ensure
{
    "bundle_id": "workflow-123",
    "bundle_path": "/path/to/bundle.tar.gz",
    "ttl_seconds": 3600
}
→ 200 OK
{
    "bundle_id": "workflow-123",
    "kubeconfig_path": "/var/lib/sbctl-manager/bundles/workflow-123/kubeconfig",
    "port": 38947,
    "pid": 12345,
    "started_at": "2025-11-07T10:30:00Z",
    "status": "running"
}

POST /api/v1/kubectl
{
    "bundle_id": "workflow-123",
    "args": ["get", "nodes", "-o", "json"],
    "timeout": 30
}
→ 200 OK
{
    "exit_code": 0,
    "stdout": "{...}",
    "stderr": "",
    "execution_time": 0.234
}

GET /api/v1/status/{bundle_id}
→ 200 OK
{
    "bundle_id": "workflow-123",
    "status": "running",
    ...
}

DELETE /api/v1/bundles/{bundle_id}
→ 204 No Content

POST /api/v1/gc
→ 200 OK
{
    "stopped_idle": 2,
    "cleaned_crashed": 1,
    "freed_bundles": ["workflow-456", "workflow-789"]
}
```

### gRPC (Alternative)

```protobuf
service SbctlManager {
    rpc Ensure(EnsureRequest) returns (ConnectionInfo);
    rpc Kubectl(KubectlRequest) returns (KubectlResult);
    rpc Status(StatusRequest) returns (StatusInfo);
    rpc Stop(StopRequest) returns (google.protobuf.Empty);
    rpc GC(GCRequest) returns (GCResult);
}
```

## Observability

### Metrics

```python
# Counters
sbctl_starts_total
sbctl_stops_total
sbctl_crashes_total
kubectl_requests_total
kubectl_errors_total

# Gauges
sbctl_processes_running
sbctl_processes_starting

# Histograms
sbctl_startup_duration_seconds
kubectl_execution_duration_seconds
gc_duration_seconds

# Summary
kubectl_requests_per_bundle
```

### Logging

Structured logging per operation:

```json
{
    "timestamp": "2025-11-07T10:30:00Z",
    "level": "info",
    "operation": "ensure",
    "bundle_id": "workflow-123",
    "action": "started_new_process",
    "pid": 12345,
    "startup_time_ms": 234
}
```

## Security Considerations

1. **Credential Isolation**: Keep kubeconfigs local to manager, avoid distributing them
2. **Process Isolation**: Use process groups, consider containers for stronger isolation
3. **API Access**: Use Unix domain socket + filesystem ACLs, or mTLS for remote access
4. **Resource Limits**: Enforce per-process limits to prevent DoS
5. **Audit Logging**: Log all kubectl executions with bundle_id and user context

## Testing Strategy

### Unit Tests
- Process lifecycle (start, stop, restart)
- Readiness detection
- Health probing
- Metadata persistence/recovery
- Concurrent access (locking)

### Integration Tests
- Full ensure → kubectl → stop flow
- Recovery after manager restart
- GC behavior (TTL, crashed processes)
- Resource limits (max processes, eviction)

### Load Tests
- Many concurrent ensure() calls
- Fan-out (many bundles simultaneously)
- Sustained kubectl load per bundle
- GC under load

## Migration Path

1. **Phase 1 (Current)**: Implement smart restart in existing BundleManager
2. **Phase 1.5**: Extract manager logic into separate class with same interface
3. **Phase 2**: Run manager as separate process, MCP server calls via RPC
4. **Phase 3**: Containerize sbctl processes for stronger isolation

## Open Questions

1. Should manager run kubectl or just return kubeconfig?
   - **Recommendation**: Manager runs kubectl (Option A) for better security and simpler clients

2. Single manager instance or per-worker?
   - **Recommendation**: Single shared manager (simplifies resource limits and GC)

3. HTTP/gRPC/Unix socket for RPC?
   - **Recommendation**: Unix domain socket for local, gRPC for distributed

4. Should manager support "batch kubectl" (multiple commands in one RPC)?
   - **Recommendation**: Yes, reduces RPC overhead for sequential queries

5. How to handle manager crashes?
   - **Recommendation**: Systemd auto-restart + recovery on startup scans state dir

## References

- [Temporal Best Practices: Activities](https://docs.temporal.io/activities)
- [Kubernetes API Server Architecture](https://kubernetes.io/docs/concepts/overview/components/)
- [Process Group Management in Unix](https://man7.org/linux/man-pages/man2/setsid.2.html)
- [File-based Locking in Python](https://pypi.org/project/filelock/)
