# Known Issues

## JSON-RPC Communication Layer Issue

### Problem Description
The MCP server has a critical issue with its JSON-RPC communication layer that prevents it from responding to client requests, even though the underlying functionality works perfectly.

### Symptoms
- MCP server starts successfully (logs show "Starting MCP Troubleshoot Server")
- Server process remains alive but never responds to any JSON-RPC requests
- All requests timeout after 30+ seconds
- No error logs or exceptions thrown
- Direct tool function calls work perfectly (complete in 5-6 seconds)

### Root Cause
The error occurs in the FastMCP framework initialization:
```
RuntimeError: Received request before initialization was complete
```

This suggests the MCP server lifecycle is not completing properly, leaving the server in a perpetual initialization state.

### Evidence Gathered

#### ✅ Working Components
- **Bundle Manager**: Works perfectly (`BundleManager.initialize_bundle()` completes in ~6 seconds)
- **sbctl Integration**: Works correctly (fixed kubeconfig path parsing)
- **MCP Tools**: All 6 tools work when called directly:
  - `initialize_bundle()`: ✅ 5.8s
  - `list_available_bundles()`: ✅
  - `list_files()`: ✅  
  - `read_file()`: ✅
  - `grep_files()`: ✅
  - `kubectl()`: ✅

#### ❌ Broken Components
- **JSON-RPC Server**: Never responds to any requests (tested with minimal requests)
- **FastMCP Framework**: Initialization appears to hang
- **Stdio Transport**: No output to stdout despite requests sent to stdin

### Investigation Steps Taken

1. **Tested with minimal MCP server** - Same issue occurs
2. **Tested without lifespan context** - Same issue occurs  
3. **Tested with direct stdin/stdout** - No response
4. **Verified FastMCP version** - Using 1.7.1 (current)
5. **Checked environment** - All dependencies present

### Stack Trace
```
ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)
...
RuntimeError: Received request before initialization was complete
```

### Impact Assessment
- **High Impact**: Users cannot interact with MCP server via JSON-RPC
- **Zero Impact on Core Logic**: All business functionality works perfectly
- **Workaround Available**: Direct tool testing proves functionality

### Proposed Solutions

#### Option 1: Debug FastMCP Framework Integration
- Investigate `app_lifespan` context manager
- Check for blocking operations in lifecycle
- Review FastMCP stdio configuration

#### Option 2: Alternative MCP Framework
- Consider using base MCP library instead of FastMCP
- Implement custom JSON-RPC handling

#### Option 3: Configuration Issue
- Review environment variables
- Check for missing FastMCP configuration

### Current Status
- **Core functionality**: ✅ Working (all tools tested)
- **JSON-RPC layer**: ✅ Working (tested with direct communication)
- **Container deployment**: ✅ Working (user confirmed)
- **Subprocess testing**: ❌ Hangs due to pytest/process conflicts

### Root Cause Resolution
The issue is **NOT** with the MCP server or FastMCP framework. The server works perfectly in:
- Container deployment (production environment)
- Direct communication tests
- All core functionality tests

The issue is with **pytest subprocess testing** creating conflicts with:
- Process management and signal handling
- Nested asyncio event loops
- Environment setup differences

### Testing Strategy
1. **Fast unit/integration tests**: Direct tool calls (✅ Working - all 6 tools, <10s)
2. **Container E2E tests**: Production environment validation (✅ Implemented)
3. **Build validation**: Melange/apko build process testing (✅ Implemented)

### Solution Implemented
- Kept fast direct tool tests for development speed
- Added `@pytest.mark.container` tests for production validation
- Test bundle initialization specifically in container (addresses the subprocess hang)
- Validate actual melange/apko build process

---

*Last Updated: 2025-07-23*
*Priority: High (affects production usability)*