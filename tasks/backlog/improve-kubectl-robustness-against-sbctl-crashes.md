# Improve kubectl Robustness Against sbctl Crashes

## Summary
Implement kubectl timeout reduction, automatic sbctl restart with crash diagnostics, and comprehensive error reporting to provide seamless recovery when sbctl crashes.

## Context
Invalid kubectl commands like `describe pod -n knime minio-` (with trailing dash) cause:
1. kubectl to hang for 30+ seconds making API calls sbctl doesn't properly handle
2. sbctl process to crash or become unresponsive  
3. All subsequent kubectl commands to fail until manual bundle reinitialization

## Tasks

### 1. Reduce kubectl Command Timeout

**Objective**: Prevent kubectl commands from hanging for 30 seconds.

**Implementation**:
- Change default timeout in `KubectlCommandArgs` from 30 to 5 seconds (`kubectl.py:46`)
- Most kubectl commands against a local API server should complete in 1-2 seconds
- Update error messages to reflect the shorter timeout
- Consider allowing override for specific commands if needed, but default to aggressive timeout

**Files to modify**:
- `src/troubleshoot_mcp_server/kubectl.py:46` - Change default timeout
- Update any tests that expect 30s timeout
- Update tool descriptions to mention 5s default timeout

### 2. Automatic sbctl Process Restart with Error Capture

**Objective**: Automatically restart sbctl when it crashes, capture crash diagnostics, and transparently continue kubectl operations.

**Implementation**:
- Add continuous stderr monitoring to capture crash information
- Modify `check_api_server_available()` to detect crashes and restart automatically  
- Include crash diagnostics in successful kubectl responses for tracing
- Use existing `self.active_bundle` to restart with same bundle

**Key changes**:
- Add `_monitor_sbctl_stderr()` method with rolling buffer for crash diagnosis
- Add `_restart_sbctl_process()` method to `BundleManager`
- Modify kubectl response to include crash recovery information
- Track last timeout command that may have triggered crash

**Recovery flow**:
1. kubectl command discovers crashed sbctl (exit code != None)
2. Capture crash info: exit code, stderr buffer, last timeout command  
3. Restart sbctl automatically with `self.active_bundle.path`
4. Execute original kubectl command successfully
5. Append crash diagnostics to successful response for LLM tracing

**Example response to LLM**:
```
NAME    READY   STATUS    RESTARTS   AGE
pod1    1/1     Running   0          2d
pod2    1/1     Running   0          1d

⚠️ SBCTL PROCESS RECOVERY:
The API server crashed (exit code -9) but was automatically restarted.
Last command before crash: describe pod -n knime minio-
Error output: Traceback (most recent call last)...
```

### 3. Comprehensive Error Capture and Reporting

**Objective**: Provide detailed crash information to LLM for debugging and tracing.

**Implementation**:
- Buffer sbctl stderr continuously during operation
- Track kubectl commands that timeout (potential crash triggers)
- Include crash diagnostics in recovery responses
- Log crash information for monitoring and pattern analysis

## PARALLEL DEVELOPMENT STRATEGY

**🚀 CRITICAL: Use parallel agents ONLY for complex, independent work. Primary agent handles simple tasks to maintain context.**

### Phase 1: Primary Agent Direct Implementation (15 minutes)

**Primary Agent Tasks:**
- ✅ Change timeout from 30s to 5s in `kubectl.py:46` (trivial change)
- ✅ Update existing timeout tests for new default  
- ✅ Update tool descriptions mentioning timeout

### Phase 2: PARALLEL Complex Implementation (Launch 2 agents simultaneously)

**Agent A: Error Capture & Restart Infrastructure** (60 minutes)
- Task: Complete stderr monitoring + restart logic
- Files: `src/troubleshoot_mcp_server/bundle.py`
- Focus: 
  - `_monitor_sbctl_stderr()` with rolling buffer
  - `_restart_sbctl_process()` method
  - Modify `check_api_server_available()` for crash detection
- **Complex, independent work** - justifies agent overhead

**Agent B: Integration Test Suite** (45 minutes)
- Task: Complete integration test framework and all test cases
- Files: `tests/integration/test_sbctl_crash_recovery.py`
- Focus:
  - Test infrastructure for crash scenarios
  - All test cases: restart, stderr capture, bundle preservation
- **Complex, independent work** - justifies agent overhead

### Phase 3: Primary Agent Integration (20 minutes)

**Primary Agent Tasks:**
- ✅ Integrate crash diagnostics into kubectl responses (`server.py`)
- ✅ Run complete test suite and verify integration
- ✅ Final validation and cleanup

## Testing Requirements

**Integration tests only** (no mocking - test real crash/recovery):

```python
# tests/integration/test_sbctl_crash_recovery.py (Agent C creates framework)

async def test_automatic_sbctl_restart_after_process_kill():
    # 1. Initialize bundle and verify kubectl works
    # 2. Kill sbctl process (simulates crash)  
    # 3. Run kubectl - should auto-restart and succeed
    # 4. Verify recovery message included in response
    
async def test_stderr_capture_during_crash():
    # Test that stderr buffer captures crash information (Agent B + C)
    
async def test_restart_preserves_bundle_state(): 
    # Verify restarted sbctl serves same bundle (Agent D + C)
```

## AGENT COORDINATION NOTES

**✅ Primary agent handles simple tasks:**
- Timeout change (single line modification)
- Test updates (straightforward search/replace)
- Response integration (simple append logic)
- Maintains context and momentum

**✅ Agents for complex, independent work:**
- Agent A: Complex async infrastructure (stderr monitoring, restart logic)  
- Agent B: Complete test suite development (infrastructure + test cases)
- Both work on different files with zero conflicts

**🎯 Optimal schedule:**
- **t=0**: Primary agent handles timeout changes (15 min)
- **t=15**: Launch Agents A & B simultaneously (60 min parallel work)
- **t=75**: Primary agent integrates and validates (20 min)

**Total time: ~95 minutes vs ~3+ hours sequential**
**Agent overhead: Minimized by keeping simple tasks with primary agent**

## Success Criteria

1. kubectl commands timeout after 5 seconds instead of 30 seconds
2. When sbctl crashes, it automatically restarts and kubectl commands resume working seamlessly
3. Crash diagnostics (exit code, stderr, triggering command) are captured and reported to LLM
4. Bundle reinitialization requests to LLM are eliminated for crash scenarios
5. LLM receives both successful kubectl output AND crash recovery information for tracing

## Files to Modify

- `src/troubleshoot_mcp_server/kubectl.py` - Timeout reduction and timeout command tracking
- `src/troubleshoot_mcp_server/bundle.py` - stderr monitoring, crash detection, automatic restart
- `src/troubleshoot_mcp_server/server.py` - Include crash recovery info in kubectl responses
- `tests/integration/test_sbctl_crash_recovery.py` - New integration tests
- Update existing timeout tests for 5s default