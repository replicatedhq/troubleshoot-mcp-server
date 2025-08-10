# Task: Improve Tool Descriptions to Address LLM Failure Modes

## Objective
Fix three identified failure modes where LLMs misuse the troubleshoot MCP server tools:
1. Unnecessary bundle re-initialization due to confusing "initialize first" language
2. kubectl commands with shell operations (pipes, redirects) that timeout after 30 seconds
3. Excessive list_tools calls due to unclear tool stability

## Context
Analysis of LLM usage patterns revealed:
- LLMs interpret "requires bundle to be initialized first" as "initialize before every call"
- Shell operations like `kubectl get pods | grep nginx` fail with confusing 30s timeouts
- LLMs repeatedly call list_tools thinking the tool set might change during sessions
- Current kubectl validation in `src/troubleshoot_mcp_server/kubectl.py:validate_command()` only blocks dangerous operations
- kubectl commands are executed via `subprocess_exec_with_cleanup()` which doesn't invoke shell

## Success Criteria
- [ ] kubectl commands with shell operations (`|`, `>`, `&&`, etc.) return clear error messages in <1 second
- [ ] Tool descriptions clearly explain bundle state persistence vs. when re-initialization is needed
- [ ] All tool descriptions include note about static tool set to reduce redundant list_tools calls
- [ ] All existing valid kubectl commands continue to work unchanged
- [ ] Test coverage >95% for new validation logic

## Dependencies
- Existing FastMCP framework and tool registration in `server.py`
- Current validation patterns in `kubectl.py`
- Existing test fixtures and mocks

## Implementation Plan

### Phase 1: kubectl Shell Operation Prevention
1. **Update kubectl validation** (`src/troubleshoot_mcp_server/kubectl.py:53-87`):
   - Add shell operator detection in `validate_command()` method
   - Check for: `|`, `&&`, `||`, `;`, `>`, `>>`, `<`, `<<`, `$(`, `` ` ``, `&`
   - Return clear error message instead of allowing timeout

2. **Update kubectl tool description** (`src/troubleshoot_mcp_server/server.py:253-274`):
   - Add explicit statement: "Accepts kubectl arguments only, not shell commands"
   - Add examples of valid vs invalid usage
   - Clarify no pipe/redirect support

### Phase 2: Bundle State Description Improvements
3. **Update initialize_bundle description** to explain bundle persistence:
   - Add explanation that initialized bundles remain active until a different bundle is initialized
   - Clarify when to use force parameter vs. normal initialization

4. **Update bundle-dependent tool descriptions**:
   - `list_files` (server.py:378-379)
   - `read_file` (server.py:426-427) 
   - `grep_files` (server.py:483-484)
   
5. **Replace confusing language for bundle-dependent tools (kubectl, list_files, read_file, grep_files)**:
   - **CURRENT**: "IMPORTANT: This tool requires a bundle to be initialized first using the `initialize_bundle` tool. If no bundle is initialized, use the `list_available_bundles` tool to find available bundles."
   
   - **NEW**: "BUNDLE REQUIREMENT: This tool requires an active bundle. If no bundle is currently active, use `initialize_bundle` to load a bundle first."

6. **Ensure consistent bundle requirement messaging**:
   - All 4 bundle-dependent tools (kubectl, list_files, read_file, grep_files) should have the exact same "BUNDLE REQUIREMENT" statement
   - Primary recommendation should always be `initialize_bundle`, not `list_available_bundles`
   - `list_available_bundles` mentioned only in initialize_bundle context for discovering stored bundles

### Phase 3: Tool Clarity Improvements  
7. **Clarify list_available_bundles purpose**:
   - **CURRENT**: "Scan the bundle storage directory to find available compressed bundle files and list them. This tool helps discover which bundles are available for initialization."
   - **NEW**: "List previously downloaded/initialized support bundles stored locally. This tool shows bundles that have been downloaded or initialized before and are available in local storage for quick re-initialization."

### Phase 4: Testing
8. **Create focused functional tests**:
   - `tests/unit/test_kubectl_shell_validation.py` - Test shell operator rejection with actual error messages
   - Add shell operation test cases to existing kubectl tests
   - Integration test: Verify kubectl shell operations fail fast (<1s) with clear messages
   - Schema validation: Ensure tool descriptions follow consistent format

## Validation Plan
- **Functional Tests**: Shell operation detection returns proper errors without mocking kubectl execution
- **Integration Tests**: Real kubectl commands with pipes fail fast (<1s) with clear messages  
- **Regression Tests**: Existing valid kubectl commands continue working unchanged
- **Performance Validation**: Shell operation rejection in <1 second vs previous 30s timeout

## Evidence of Completion
(To be filled by AI)
- [ ] Command output showing shell operations rejected quickly with clear messages
- [ ] Path to modified files with improved descriptions
- [ ] Test results showing >95% coverage for new validation
- [ ] Before/after comparison of tool description clarity

## Notes

### Current Shell Operation Behavior
- Commands split with `command.split()` in `kubectl.py:204`
- Uses `subprocess_exec_with_cleanup()` not shell execution
- Pipe `|` becomes literal argument to kubectl, causing parse errors and timeouts

### Specific Shell Operators to Detect
- Pipes: `|`
- Redirects: `>`, `>>`, `<`, `<<`
- Command chaining: `&&`, `||`, `;`
- Command substitution: `$(`, `` ` ``
- Background: `&`

Note: `*` and `?` are NOT included as they can be valid literal characters in kubectl resource names.

### Example Error Message
```
Shell operations are not supported in kubectl commands. 
Use kubectl arguments only, not shell commands.
❌ Invalid: 'get pods | grep nginx'
✅ Valid: 'get pods -l app=nginx'
```

## Progress Updates
(To be filled by AI during implementation)