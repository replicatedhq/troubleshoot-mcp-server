# Task: Standardize MCP Argument Schemas

**Status**: backlog  
**Priority**: high  
**Assignee**: TBD  
**Created**: 2025-07-29  
**Tags**: mcp, schema, compatibility, fastmcp

## Problem Statement

The troubleshoot MCP server currently uses a non-standard argument schema that wraps all tool parameters in an `args` object. This causes compatibility issues with multiple AI models and deviates from standard MCP conventions.

### Current Issue

FastMCP automatically wraps Pydantic model parameters, causing tools to advertise schemas like:

```json
{
  "properties": {
    "args": {
      "$ref": "#/$defs/KubectlCommandArgs"
    }
  },
  "required": ["args"],
  "title": "kubectlArguments",
  "type": "object"
}
```

This forces LLMs to use non-standard tool calls:
```json
{
  "name": "kubectl",
  "arguments": {
    "args": {
      "command": "get pods",
      "timeout": 30
    }
  }
}
```

### Target Standard Format

Standard MCP format with direct parameter passing:
```json
{
  "properties": {
    "command": {"type": "string", "description": "The kubectl command to execute"},
    "timeout": {"type": "integer", "default": 30, "description": "Timeout in seconds"},
    "json_output": {"type": "boolean", "default": false, "description": "Whether to format as JSON"},
    "verbosity": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": null}
  },
  "required": ["command"],
  "title": "kubectlArguments",
  "type": "object"
}
```

Target tool call format:
```json
{
  "name": "kubectl",
  "arguments": {
    "command": "get pods",
    "timeout": 30
  }
}
```

## Implementation Plan

### Phase 1: Setup and Investigation (Parallel)

#### Sub-task 1A: Development Environment Setup
- Create git worktree: `git worktree add trees/schema-fix -b task/standardize-mcp-schemas`
- Run environment setup: `./scripts/setup_env.sh`
- Verify current schema output with inspection script

#### Sub-task 1B: Schema Validation Framework (Parallel)
- Create `tests/unit/test_schema_validation.py` for schema format validation
- Implement helper functions to test MCP tool schemas
- Add test utilities for schema comparison

#### Sub-task 1C: Documentation Updates (Parallel)
- Review and update any `.md` files that reference `black` to use `ruff`
- Check `CLAUDE.md`, `tests/README.md`, and other documentation
- Update formatting command references from `black .` to `ruff format .`

### Phase 2: Core Implementation (Sequential with Parallel Testing)

#### Sub-task 2A: Update Tool Function Signatures
**Location**: `src/mcp_server_troubleshoot/server.py`

Transform each tool function from:
```python
@mcp.tool()
async def initialize_bundle(args: InitializeBundleArgs) -> List[TextContent]:
```

To:
```python
@mcp.tool()
async def initialize_bundle(
    source: str,
    force: bool = False,
    verbosity: Optional[str] = None
) -> List[TextContent]:
```

**Tools to update:**
1. `initialize_bundle(args: InitializeBundleArgs)` 
2. `list_available_bundles(args: ListAvailableBundlesArgs)`
3. `kubectl(args: KubectlCommandArgs)`
4. `list_files(args: ListFilesArgs)`
5. `read_file(args: ReadFileArgs)`
6. `grep_files(args: GrepFilesArgs)`

#### Sub-task 2B: Update Function Bodies (Parallel with 2A)
For each tool function, replace parameter access:
- `args.parameter` → `parameter`
- `args.verbosity` → `verbosity`
- Remove Pydantic model instantiation

#### Sub-task 2C: Update Import Statements (Parallel with 2A-2B)
- Remove imports of `*Args` classes from `server.py`
- Keep Pydantic models in their original files for internal use
- Add necessary type imports (`Optional`, etc.)

### Phase 3: Test Updates (Highly Parallel)

#### Sub-task 3A: Unit Test Updates (Parallel)
**Files to update:**
- `tests/unit/test_server.py` - Core server tool functionality
- `tests/unit/test_server_parametrized.py` - Parameter validation edge cases

**Requirement**: Update ALL existing tests to use direct parameters. Every test must pass - 100% pass rate required.

Transform test calls from:
```python
result = await initialize_bundle(InitializeBundleArgs(source="test.tar.gz"))
```

To:
```python
result = await initialize_bundle(source="test.tar.gz")
```

#### Sub-task 3B: Integration Test Updates (Parallel)
**Files to update:**
- `tests/integration/test_tool_functions.py` - Multi-component interactions
- `tests/integration/test_mcp_protocol_real.py` - Real MCP protocol flow

**Requirement**: Update ALL existing integration tests to work with new parameter format. Every test must pass - 100% pass rate required.

#### Sub-task 3C: E2E Test Updates (Parallel)
**Files to update:**
- `tests/e2e/test_mcp_protocol_integration.py` - Full MCP protocol with new schemas
- `tests/e2e/test_direct_tool_integration.py` - Direct tool calls with new format
- `tests/e2e/test_non_container.py` - Non-container e2e tests

**Requirement**: Update ALL existing e2e tests to work with new parameter format. Every test must pass - 100% pass rate required.

#### Sub-task 3D: Minimal Schema Regression Test (Parallel)
**New file:** `tests/unit/test_schema_validation.py`

**Focus**: Add ONE focused test to prevent regression - ensure schemas never revert to `args` wrapper format. Test actual JSON schema output structure only.

### Phase 4: Validation and Quality Assurance (Sequential)

#### Sub-task 4A: Schema Verification
- Run schema inspection script to verify new format
- Validate all 6 tools generate standard MCP schemas
- Test with actual MCP client (if available)

#### Sub-task 4B: Test Suite Execution
- Run: `uv run pytest -m unit` (unit tests)
- Run: `uv run pytest -m integration` (integration tests) 
- Run: `uv run pytest -m e2e` (end-to-end tests)
- All tests must pass

#### Sub-task 4C: Code Quality Checks
- Run: `uv run ruff format .` (code formatting)
- Run: `uv run ruff check .` (linting)
- Run: `uv run mypy src` (type checking)
- All quality checks must pass

#### Sub-task 4D: Container Testing
- Run: `uv run pytest -m container` (container tests)
- Verify containerized server works with new schemas
- Test deployment scenarios

### Phase 5: Documentation and Completion

#### Sub-task 5A: Update Task Documentation
- Update task status to "completed"
- Add implementation notes and lessons learned
- Document any edge cases or gotchas discovered

#### Sub-task 5B: Create Pull Request
- Push branch: `git push -u origin task/standardize-mcp-schemas`
- Create PR: `gh pr create --title "Standardize MCP Argument Schemas" --body "..."`
- Include before/after schema examples in PR description

#### Sub-task 5C: Task File Management
- Move task file to `tasks/completed/`
- Add completion metadata (date, PR link, etc.)

## Detailed Tool Function Transformations

### 1. initialize_bundle

**Before:**
```python
@mcp.tool()
async def initialize_bundle(args: InitializeBundleArgs) -> List[TextContent]:
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(args.verbosity)
    result = await bundle_manager.initialize_bundle(args.source, args.force)
```

**After:**
```python
@mcp.tool()
async def initialize_bundle(
    source: str,
    force: bool = False,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    bundle_manager = get_bundle_manager()
    formatter = get_formatter(verbosity)
    result = await bundle_manager.initialize_bundle(source, force)
```

### 2. kubectl

**Before:**
```python
@mcp.tool()
async def kubectl(args: KubectlCommandArgs) -> List[TextContent]:
    result = await get_kubectl_executor().execute(args.command, args.timeout, args.json_output)
```

**After:**
```python
@mcp.tool()
async def kubectl(
    command: str,
    timeout: int = 30,
    json_output: bool = False,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    result = await get_kubectl_executor().execute(command, timeout, json_output)
```

### 3. list_files

**Before:**
```python
@mcp.tool()
async def list_files(args: ListFilesArgs) -> List[TextContent]:
    result = await get_file_explorer().list_files(args.path, args.recursive)
```

**After:**
```python
@mcp.tool()
async def list_files(
    path: str,
    recursive: bool = False,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    result = await get_file_explorer().list_files(path, recursive)
```

### 4. read_file

**Before:**
```python
@mcp.tool()
async def read_file(args: ReadFileArgs) -> List[TextContent]:
    result = await get_file_explorer().read_file(args.path, args.start_line, args.end_line)
```

**After:**
```python
@mcp.tool()
async def read_file(
    path: str,
    start_line: int = 0,
    end_line: Optional[int] = None,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    result = await get_file_explorer().read_file(path, start_line, end_line)
```

### 5. grep_files

**Before:**
```python
@mcp.tool()
async def grep_files(args: GrepFilesArgs) -> List[TextContent]:
    result = await get_file_explorer().grep_files(
        args.pattern, args.path, args.recursive, args.glob_pattern,
        args.case_sensitive, args.max_results
    )
```

**After:**
```python
@mcp.tool()
async def grep_files(
    pattern: str,
    path: str,
    recursive: bool = True,
    glob_pattern: Optional[str] = None,
    case_sensitive: bool = False,
    max_results: int = 1000,
    max_results_per_file: int = 5,
    max_files: int = 10,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    result = await get_file_explorer().grep_files(
        pattern, path, recursive, glob_pattern,
        case_sensitive, max_results, max_results_per_file, max_files
    )
```

### 6. list_available_bundles

**Before:**
```python
@mcp.tool()
async def list_available_bundles(args: ListAvailableBundlesArgs) -> List[TextContent]:
    bundles = await bundle_manager.list_available_bundles(args.include_invalid)
```

**After:**
```python
@mcp.tool()
async def list_available_bundles(
    include_invalid: bool = False,
    verbosity: Optional[str] = None
) -> List[TextContent]:
    bundles = await bundle_manager.list_available_bundles(include_invalid)
```

## Test Update Examples

### Unit Test Updates

**Before:**
```python
@pytest.mark.asyncio
async def test_kubectl_tool(tmp_path: Path) -> None:
    args = KubectlCommandArgs(command="get pods", timeout=30)
    result = await kubectl(args)
```

**After:**
```python
@pytest.mark.asyncio
async def test_kubectl_tool(tmp_path: Path) -> None:
    result = await kubectl(command="get pods", timeout=30)
```

### Integration Test Updates

**Before:**
```python
args = InitializeBundleArgs(source=str(bundle_path), force=True)
result = await server.initialize_bundle(args)
```

**After:**
```python
result = await server.initialize_bundle(source=str(bundle_path), force=True)
```

## Quality Assurance Checklist

### Pre-Implementation
- [ ] Development environment set up with git worktree
- [ ] Current schema output documented and verified
- [ ] Minimal test framework for schema validation created

### Implementation Phase
- [ ] All 6 tool functions updated with new signatures
- [ ] Function bodies updated to use direct parameters
- [ ] Import statements cleaned up

### Testing Phase  
- [ ] All existing unit tests updated and passing (100% pass rate required)
- [ ] All existing integration tests updated and passing (100% pass rate required)
- [ ] All existing e2e tests updated and passing (100% pass rate required)
- [ ] Only add schema validation test to prevent regression (focused new testing)

### Quality Checks
- [ ] `uv run ruff format .` - Code formatting check (MANDATORY)
- [ ] `uv run ruff check .` - Linting check (MANDATORY)
- [ ] `uv run mypy src` - Type checking (MANDATORY)
- [ ] `uv run pytest` - Full test suite (100% pass rate MANDATORY)

### Validation
- [ ] Schema inspection confirms standard MCP format (no `args` wrapper)
- [ ] All tools callable with direct parameters (core behavior verified)
- [ ] MCP client compatibility verified (if PydanticAI available)

### Documentation
- [ ] All `.md` files updated to reference `ruff` instead of `black` (completed)
- [ ] Task file updated with completion status
- [ ] Pull request created with focused description

## Risk Mitigation

### Rollback Plan
- Use git worktree for easy branch switching
- Keep original implementation accessible in main branch
- Document any breaking changes discovered

### Testing Strategy
- Update ALL existing tests to work with new parameter format (100% pass rate required)
- Add minimal schema validation test to prevent regression (test actual JSON output structure)
- No unnecessary new tests - focus on making existing tests work

### Compatibility Concerns
- Maintain internal API compatibility for existing components
- Ensure Pydantic models still work for internal validation
- Verify no breaking changes to bundle/kubectl/file APIs

## Success Criteria

1. **Schema Compliance**: All tools generate standard MCP schemas without `args` wrapper
2. **AI Compatibility**: Tools work correctly with PydanticAI and other MCP clients
3. **Functionality Preserved**: All existing functionality works identically
4. **All Tests Pass**: 100% test pass rate across all categories (unit, integration, e2e)
5. **Code Quality**: All quality checks pass (ruff format, ruff check, mypy)
6. **Documentation**: All references to `black` updated to `ruff`

## Dependencies

### Internal Dependencies
- FastMCP framework behavior and schema generation
- Existing Pydantic model definitions in bundle.py, kubectl.py, files.py
- Current test suite structure and fixtures

### External Dependencies
- MCP protocol specification compliance
- PydanticAI and other MCP client compatibility
- UV package manager for all Python operations

## Estimated Effort

- **Setup and Investigation**: 2-3 hours
- **Core Implementation**: 4-6 hours
- **Test Updates**: 6-8 hours
- **Quality Assurance**: 2-3 hours
- **Documentation**: 1-2 hours

**Total Estimated Time**: 15-22 hours

## Notes

- This task directly addresses the compatibility issue with PydanticAI and other MCP clients
- The parallel sub-task structure allows for efficient work distribution
- Focus on `ruff` for all formatting going forward (no more `black`)
- Maintain backward compatibility for internal APIs
- FastMCP's automatic schema generation will handle the standard format once we use direct parameters