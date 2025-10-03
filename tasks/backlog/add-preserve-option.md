This should allow skipping the cleanup but default to off, something like:

Add env var to skip cleanup:
  # In bundle.py cleanup()
  PRESERVE_BUNDLES = os.environ.get("PRESERVE_BUNDLES", "false").lower() == "true"

  if not PRESERVE_BUNDLES:
      await self._cleanup_active_bundle()

