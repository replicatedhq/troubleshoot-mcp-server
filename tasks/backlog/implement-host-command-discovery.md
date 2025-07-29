# Task: Implement Host Command Discovery Tool

## Metadata
**Status**: backlog
**Created**: 2025-07-29
**Priority**: high
**Estimated effort**: medium

## Objective
Implement a new tool that allows LLM agents to discover and access host-level commands from support bundles without needing to understand the internal bundle structure.

## Context
Currently, agents can use `kubectl` commands for cluster resources, but host-level commands from support bundles are not easily discoverable. These commands are stored in various locations within support bundles:
- `host-collectors/run-host/{collector}/` - Custom command outputs  
- `host-collectors/system/` - Standard system info
- `{collector-name}/` - Specialized collectors (e.g., mysql)

Agents need a unified way to discover what host commands were executed and access their outputs.

## Success Criteria
- [x] New `HostCommandDiscovery` class that can parse bundle structure for host commands
- [x] Two new MCP tools: `list_host_commands` and `get_host_command_output`
- [x] Support for standard, custom, and specialized collectors
- [x] Integration with existing error handling and size limiting infrastructure
- [x] Unit tests for core functionality
- [x] Integration tests with real bundle fixtures
- [x] Minimal token usage in responses (essential information only)

## Dependencies
- Existing BundleManager for bundle access
- Existing FileExplorer for file operations
- Existing ResponseFormatter for output formatting
- Existing SizeLimiter for response size management

## Parallel Development Strategy
This task is well-suited for parallel development with multiple sub-agents:
- **Core discovery logic** and **MCP integration** can be developed independently
- Both sub-agents work against the same well-defined data models
- Clear separation of concerns allows parallel work without conflicts

## Implementation Plan

### 1. Create Host Command Discovery Module
**File**: `src/mcp_server_troubleshoot/host_commands.py`

```python
class HostCommandInfo(BaseModel):
    collector_name: str
    command: str
    command_type: str  # "system", "custom", "specialized"

class HostCommandOutput(BaseModel):
    collector_name: str
    command: str
    output: str

class HostCommandDiscovery:
    def __init__(self, bundle_manager: BundleManager):
        self.bundle_manager = bundle_manager
    
    async def discover_host_commands(self) -> List[HostCommandInfo]:
        # Scan host-collectors/run-host/ for *-info.json and *.txt files
        # Scan host-collectors/system/ for standard system files  
        # Scan root level for specialized collectors
        
    async def get_command_output(self, collector_name: str) -> HostCommandOutput:
        # Read output file and parse metadata for command details
```

### 2. Add MCP Tools
**File**: `src/mcp_server_troubleshoot/server.py`

Add two new MCP tools:
- `list_host_commands` - Returns list of available host commands
- `get_host_command_output` - Returns output for specific command

### 3. Testing Strategy
Focused functional testing approach:

#### Integration Tests (`tests/integration/test_host_command_tools.py`)
- Test both MCP tools (`list_host_commands`, `get_host_command_output`) with real bundle fixture
- Test complete workflow: bundle initialization → host command discovery → output retrieval
- Test error cases (no bundle initialized, invalid collector name)
- Verify tools work as LLM agents would use them

This single integration test file covers the essential functionality without unnecessary test duplication.

### 4. Implementation Steps

**Phase 1: Core Implementation (Parallel Sub-agents)**
Sub-agents can work in parallel on these independent components:

**Sub-agent 1**: Core Discovery Logic
1. Create `HostCommandDiscovery` class with bundle structure parsing
2. Implement metadata extraction from *-info.json files  
3. Add output file reading with error handling

**Sub-agent 2**: MCP Integration  
4. Create MCP tool wrappers integrating with existing server architecture
5. Add size limiting using existing `SizeLimiter`
6. Add formatter support for verbosity levels

**Phase 2: Integration & Testing (Sequential)**
7. Integrate components and resolve any interface issues
8. Add focused integration tests with existing bundle fixtures

**Parallel Development Notes:**
- Sub-agents should coordinate on the interface between `HostCommandDiscovery` and MCP tools
- Both can work against the same data models (`HostCommandInfo`, `HostCommandOutput`)
- Integration phase may require minor adjustments to interfaces

### 5. File Structure
```
src/mcp_server_troubleshoot/
├── host_commands.py          # New: Host command discovery
├── server.py                 # Modified: Add new MCP tools
└── ...existing files...

tests/
├── integration/
│   └── test_host_command_tools.py  # New: Single focused test file
└── ...existing tests...
```

## Acceptance Criteria
- Agents can list all available host commands from a support bundle
- Agents can retrieve command output without understanding bundle structure
- Works with standard, custom, and specialized collectors
- Graceful error handling for missing files and corrupt data
- Integrates seamlessly with existing MCP server architecture
- Single focused integration test verifies tools work functionally for LLM agents
- Minimal token usage in responses

## Example Usage
```python
# List available host commands
commands = await list_host_commands()
# Returns: [
#   {"collector": "disk-usage", "command": "du -sh /var", "type": "custom"},
#   {"collector": "memory", "command": "free -h", "type": "system"}
# ]

# Get specific command output  
output = await get_host_command_output("disk-usage")
# Returns: {"collector": "disk-usage", "command": "du -sh /var", "output": "..."}
```

## Development Execution
When implementing this task, use sub-agents for parallel development:

1. **Launch Sub-agent 1** to handle core discovery logic (`host_commands.py`)
2. **Launch Sub-agent 2** simultaneously to handle MCP integration (`server.py` modifications)  
3. Both sub-agents should coordinate on data model interfaces
4. Final integration and testing can be done sequentially after both complete

## Notes
- Focus on essential information only to minimize token usage
- Reuse existing infrastructure (BundleManager, FileExplorer, ResponseFormatter, SizeLimiter)
- Follow existing patterns for error handling and logging
- No new dependencies required
- **Use parallel sub-agents to expedite development where components are independent**