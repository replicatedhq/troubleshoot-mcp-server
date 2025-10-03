# Task: Add PRESERVE_BUNDLES Environment Variable

**Status:** active
**Started:** 2025-10-03
**Branch:** task/add-preserve-option

## Description

Add env var to skip cleanup:
  # In bundle.py cleanup()
  PRESERVE_BUNDLES = os.environ.get("PRESERVE_BUNDLES", "false").lower() == "true"

  if not PRESERVE_BUNDLES:
      await self._cleanup_active_bundle()

This should allow skipping the cleanup but default to off.

## Progress
- 2025-10-03: Started task, created worktree
- 2025-10-03: Implemented PRESERVE_BUNDLES environment variable in bundle.py cleanup()
- 2025-10-03: All quality checks pass (ruff format, ruff check, mypy)
- 2025-10-03: All unit tests pass (209 tests)
