# Option 2: Migrate HTTP REST API to SSE MCP Transport

## Executive Summary

The current HTTP REST API bypasses MCP's built-in size limiting and response formatting. This document outlines how to migrate to the SSE MCP transport to get proper MCP protocol compliance with all its benefits.

## Current Architecture (HTTP REST API)

```
Temporal Activity → HTTP POST → http_server.py → Direct tool execution
                                                  ❌ No size limiting
                                                  ❌ No MCP formatters
                                                  ❌ No overflow messages
```

**Issues:**
- Size limiting was manually added as a quick fix (see commit XXX)
- Duplicates logic that already exists in MCP protocol handlers
- Misses out on MCP formatter features and improvements
- Not using standard MCP protocol

## Proposed Architecture (SSE MCP Transport)

```
Temporal Activity → MCP Client → SSE → server.py @mcp.tool() → Tool execution
                                                    ✅ Built-in size limiting
                                                    ✅ MCP formatters
                                                    ✅ Overflow messages
                                                    ✅ Standard protocol
```

**Benefits:**
- ✅ Automatic size limiting via `check_response_size()` (already implemented)
- ✅ Get MCP formatter's overflow messages (better agent feedback)
- ✅ No code duplication - reuse existing MCP protocol handlers
- ✅ Proper MCP protocol compliance
- ✅ Future MCP features automatically available
- ✅ Consistency with stdio/SSE clients

## Implementation Plan

### Phase 1: Add SSE MCP Client to HTTP Activities (2-3 hours)

Replace direct HTTP calls with MCP client:

```python
# Before (http_mcp_activities.py):
async with httpx.AsyncClient(timeout=120.0) as client:
    response = await client.post(
        f"{MCP_SERVER_URL}/bundles/{bundle_id}/kubectl",
        json={"args": args}
    )
    result = response.json()
    return result["output"]

# After:
from mcp import ClientSession
from mcp.client.sse import sse_client

async with sse_client(MCP_SERVER_URL) as (read, write):
    async with ClientSession(read, write) as session:
        # Initialize bundle using MCP protocol
        await session.initialize()
        
        # Call tool through MCP protocol
        result = await session.call_tool("kubectl", {
            "bundle_id": bundle_id,
            "args": args
        })
        
        # Result automatically has size limiting applied!
        return result.content[0].text
```

### Phase 2: Update MCP Server Tools for Bundle Isolation (1-2 hours)

Current MCP tools assume single active bundle. Need to support bundle_id parameter:

```python
# In server.py - update tool signatures:

@mcp.tool()
async def kubectl(
    bundle_id: str,  # Add bundle_id parameter
    command: str, 
    timeout: int = 5,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    """Execute kubectl command in specified bundle."""
    # Get bundle manager by ID
    manager = get_bundle_manager(bundle_id)
    
    # Rest of implementation stays the same
    # Size limiting via check_response_size() already works!
    ...
```

### Phase 3: Add Bundle Store to MCP Server (2-3 hours)

The MCP server needs to track multiple bundles like HTTP server does:

```python
# In server.py - add bundle store (similar to http_server.py):

class MCPBundleStore:
    """Thread-safe bundle storage for MCP server."""
    
    def __init__(self):
        self.bundles: dict[str, BundleManager] = {}
        self.lock = Lock()
    
    def add(self, bundle_id: str, manager: BundleManager):
        with self.lock:
            self.bundles[bundle_id] = manager
    
    def get(self, bundle_id: str) -> Optional[BundleManager]:
        with self.lock:
            return self.bundles.get(bundle_id)

# Global bundle store
bundle_store = MCPBundleStore()

@mcp.tool()
async def initialize_bundle(
    source: str,
    token: str,
    force: bool = False,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    """Initialize bundle and return bundle_id."""
    bundle_id = f"bundle-{uuid.uuid4()}"
    manager = BundleManager(bundle_dir=Path(f"/tmp/bundles/{bundle_id}"))
    
    await manager.initialize_bundle(source, force=force, token=token)
    bundle_store.add(bundle_id, manager)
    
    return [TextContent(type="text", text=f"Bundle initialized: {bundle_id}")]
```

### Phase 4: Testing (2 hours)

1. **Unit tests:**
   - Test bundle isolation with multiple bundles
   - Verify size limiting triggers correctly
   - Test overflow messages

2. **Integration tests:**
   - Test SSE client connection to MCP server
   - Test full workflow with Temporal
   - Verify no regressions in existing stdio/SSE clients

3. **Load tests:**
   - Multiple concurrent bundles
   - Large kubectl outputs (trigger size limiting)
   - Rapid bundle initialization/cleanup

### Phase 5: Deprecate HTTP REST API (1 hour)

Once SSE MCP is working:

1. Add deprecation warning to HTTP endpoints
2. Update documentation to recommend SSE transport
3. Keep HTTP API for backward compatibility (but frozen)
4. Plan eventual removal in future version

## Migration Checklist

- [ ] Add MCP SSE client to http_mcp_activities.py
- [ ] Update MCP tools to accept bundle_id parameter
- [ ] Add bundle store to MCP server
- [ ] Test with single bundle
- [ ] Test with multiple concurrent bundles
- [ ] Test size limiting triggers and overflow messages
- [ ] Update framework documentation
- [ ] Run full Temporal workflow end-to-end test
- [ ] Add integration tests
- [ ] Deprecate HTTP REST API endpoints
- [ ] Remove manual size limiting code from http_server.py

## Timeline

**Total estimated time:** 8-10 hours

- Phase 1: 2-3 hours
- Phase 2: 1-2 hours
- Phase 3: 2-3 hours
- Phase 4: 2 hours
- Phase 5: 1 hour

## Risks and Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| SSE connection stability with Temporal | High | Add connection retry logic, test thoroughly |
| Breaking existing stdio clients | High | Keep bundle store separate, don't change existing tool behavior |
| Performance overhead of MCP protocol | Medium | Benchmark SSE vs HTTP, acceptable if <10% slower |
| Bundle state confusion | Medium | Proper bundle isolation, clear bundle_id in all calls |

## Success Criteria

✅ All Temporal workflows work with SSE MCP transport
✅ Size limiting works automatically (no manual code)
✅ Agent gets proper overflow messages with suggestions
✅ No regressions in existing stdio/SSE clients
✅ Performance within 10% of HTTP REST API
✅ Multiple concurrent bundles work correctly

## References

- MCP Protocol Spec: https://modelcontextprotocol.io/docs
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- PydanticAI SSE Client: https://ai.pydantic.dev/
- Existing size_limiter.py: Already implements token estimation and overflow
- Existing server.py: Already has check_response_size() for MCP tools

## Notes

This is the "right" architectural fix. The HTTP REST API was a quick workaround
for Temporal integration, but bypassed MCP's built-in features. Migrating to
SSE MCP gives us proper protocol compliance and eliminates code duplication.

The manual size limiting added in the quick fix (Option 1) can be removed once
this migration is complete, as MCP's check_response_size() will handle it
automatically with better formatting.
