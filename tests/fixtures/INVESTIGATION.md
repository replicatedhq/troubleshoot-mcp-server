
---

## Session 3: sbctl Kubeconfig Path Issue - 2025-11-06

**Status:** ✅ ROOT CAUSE IDENTIFIED - sbctl creates kubeconfig in /tmp, not cwd

### Problem Discovery

While fixing tests, noticed bundles with cluster resources reporting `"status": "api_unavailable"`.

**Test bundle verification:**
```bash
$ tar -tzf tests/fixtures/support-bundle-2025-04-11T14_05_31.tar.gz | head -5
support-bundle-2025-04-11T14_05_31/cluster-resources/cronjobs/kube-node-lease.json
support-bundle-2025-04-11T14_05_31/cluster-resources/configmaps/wg-easy.json
support-bundle-2025-04-11T14_05_31/cluster-resources/roles/kube-system.json
```
✅ Bundle DOES contain cluster resources

**sbctl availability:**
```bash
$ which sbctl
/Users/chris/go/bin/sbctl
```
✅ sbctl IS available on this machine

### Manual Test - The Smoking Gun

```bash
$ cd tests/fixtures
$ sbctl serve --support-bundle-location support-bundle-2025-04-11T14_05_31.tar.gz
time="2025-11-06T15:38:32-06:00" level=info msg="called getAPIV1"
time="2025-11-06T15:38:32-06:00" level=info msg="Reading /var/folders/.../sbctl-932668330/support-bundle-2025-04-11T14_05_31/cluster-resources/resources.json file"
127.0.0.1 - - [06/Nov/2025:15:38:32 -0600] "GET /api/v1 HTTP/1.1" 200 6000
Server is running

export KUBECONFIG=/var/folders/lj/0fpgqrkd7fzgmhlvq189_7h80000gp/T/local-kubeconfig-1162345481
```

**KEY FINDING**: sbctl outputs `export KUBECONFIG=/tmp/local-kubeconfig-<random>`

### ROOT CAUSE

**Our code assumes** (bundle.py:1460-1461):
```python
os.chdir(output_dir)
kubeconfig_path = output_dir / "kubeconfig"
```

**Actual sbctl behavior**:
- Creates kubeconfig at `/tmp/local-kubeconfig-<random-number>` (temp directory)
- Prints the path to stdout: `export KUBECONFIG=<path>`
- Does NOT create `./kubeconfig` in current working directory

**Why this breaks everything**:
1. Code expects kubeconfig at `{bundle_dir}/{bundle_id}/kubeconfig`
2. sbctl actually creates it at `/tmp/local-kubeconfig-<random>`
3. `check_api_server_available()` reads wrong path → file doesn't exist → "api_unavailable"
4. kubectl commands use wrong path → fail
5. Tests fail because API is actually running but we can't find it!

### The Fix

**Parse sbctl's stdout** to extract the real kubeconfig path:

```python
# In _initialize_with_sbctl(), after starting process:
# 1. Wait for "Server is running" message
# 2. Parse stdout for "export KUBECONFIG=<path>" or "KUBECONFIG=<path>"
# 3. Extract the path
# 4. Use that path instead of hardcoded output_dir / "kubeconfig"
```

**Implementation location**: bundle.py `_wait_for_initialization()` method

### Why This Wasn't Caught Earlier

1. The code at bundle.py:1790-1825 has "alternative kubeconfig" logic that searches for kubeconfig files
2. This fallback might have worked sometimes, masking the issue
3. But it's unreliable - depends on file system state
4. The CORRECT fix is to parse sbctl's output, not rely on fallback search

### Files to Modify

1. **bundle.py:~1644-1800**: `_wait_for_initialization()` - parse stdout for KUBECONFIG path
2. **bundle.py:1461**: Remove assumption that kubeconfig is in output_dir

### Expected Test Results After Fix

- ✅ Bundles with cluster resources → `"status": "ready"`
- ✅ `check_api_server_available()` finds correct kubeconfig
- ✅ kubectl commands work
- ✅ ALL functional tests pass

### Investigation Continued

**Found existing fallback code** at bundle.py:1726-1750:
- DOES parse stdout for "export KUBECONFIG=" pattern
- DOES extract path and copy to expected location
- BUT: Only reads first 1024 bytes with 1-second timeout

**Hypothesis**: The "Server is running" message might come later in stdout, after the initial read.

**Timeline issue**:
```
T0: Start sbctl process
T1: Read first 1024 bytes of stdout (timeout=1.0s)
T2: sbctl still initializing...
T3: sbctl finishes, prints "Server is running\nexport KUBECONFIG=..."
T4: Code already moved past stdout reading, missed the output
```

**Testing hypothesis**: Need to read stdout continuously or wait longer.

