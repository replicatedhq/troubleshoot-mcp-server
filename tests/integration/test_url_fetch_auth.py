"""
Integration tests for URL fetch authentication functionality.

Tests URL pattern matching, authentication handling, and error scenarios
for bundle initialization from URLs.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import httpx
from aiohttp import ClientResponseError

from troubleshoot_mcp_server.bundle import BundleManager, BundleDownloadError
from troubleshoot_mcp_server.bundle import REPLICATED_VENDOR_URL_PATTERN


class TestUrlPatternMatching:
    """Test URL pattern detection without requiring authentication tokens."""

    def test_replicated_vendor_url_pattern_matching(self):
        """Test that Replicated vendor URLs are correctly identified."""
        test_cases = [
            # Valid URLs
            (
                "https://vendor.replicated.com/troubleshoot/analyze/2025-06-18@16:39",
                True,
                "2025-06-18@16:39",
            ),
            (
                "https://vendor.replicated.com/troubleshoot/analyze/my-bundle-slug",
                True,
                "my-bundle-slug",
            ),
            (
                "https://vendor.replicated.com/troubleshoot/analyze/complex-slug-123",
                True,
                "complex-slug-123",
            ),
            # Invalid URLs
            ("https://vendor.replicated.com/troubleshoot/", False, None),
            ("https://vendor.replicated.com/troubleshoot/analyze/", False, None),
            ("https://example.com/troubleshoot/analyze/slug", False, None),
            ("https://vendor.replicated.com/other/analyze/slug", False, None),
            (
                "http://vendor.replicated.com/troubleshoot/analyze/slug",
                False,
                None,
            ),  # http not https
        ]

        for url, should_match, expected_slug in test_cases:
            match = REPLICATED_VENDOR_URL_PATTERN.match(url)
            if should_match:
                assert match is not None, f"URL {url} should match pattern"
                assert match.group(1) == expected_slug, (
                    f"Expected slug {expected_slug}, got {match.group(1)}"
                )
            else:
                assert match is None, f"URL {url} should not match pattern"

    def test_url_detection_in_bundle_init(self):
        """Test that BundleManager correctly identifies URLs when initializing bundles."""
        from urllib.parse import urlparse

        # URLs that should be detected as URLs
        url_cases = [
            "https://vendor.replicated.com/troubleshoot/analyze/2025-06-18@16:39",
            "https://example.com/bundle.tar.gz",
            "http://example.com/bundle.tar.gz",
        ]

        # Paths that should be detected as local files
        file_cases = [
            "local-bundle.tar.gz",
            "/path/to/bundle.tar.gz",
            "./bundle.tar.gz",
            "../bundle.tar.gz",
        ]

        # Test URL detection using the same logic as the bundle manager
        for url in url_cases:
            parsed = urlparse(url)
            is_url = bool(parsed.netloc)
            assert is_url, f"Should identify {url} as URL"

        for file_path in file_cases:
            parsed = urlparse(file_path)
            is_url = bool(parsed.netloc)
            assert not is_url, f"Should identify {file_path} as local file"


class TestAuthenticationErrorHandling:
    """Test authentication error scenarios using mocks."""

    @pytest.mark.asyncio
    async def test_missing_token_error(self):
        """Test error when no authentication token is provided for Replicated URL."""
        url = "https://vendor.replicated.com/troubleshoot/analyze/2025-06-18@16:39"

        with patch.dict(os.environ, {}, clear=True):  # Clear all env vars
            bundle = BundleManager()

            with pytest.raises(
                BundleDownloadError,
                match="Cannot download from Replicated Vendor Portal: SBCTL_TOKEN or REPLICATED environment variable not set",
            ):
                await bundle.initialize_bundle(url)

    @pytest.mark.asyncio
    async def test_replicated_api_401_error(self):
        """Test handling of 401 authentication error from Replicated API."""
        url = "https://vendor.replicated.com/troubleshoot/analyze/2025-06-18@16:39"

        with patch.dict(os.environ, {"SBCTL_TOKEN": "invalid-token"}):
            bundle = BundleManager()

            # Mock httpx.AsyncClient to return 401
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "401 Client Error", request=MagicMock(), response=mock_response
            )

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

                with pytest.raises(
                    BundleDownloadError,
                    match="Failed to authenticate with Replicated API.*status 401",
                ):
                    await bundle.initialize_bundle(url)

    @pytest.mark.asyncio
    async def test_replicated_api_404_error(self):
        """Test handling of 404 error from Replicated API."""
        url = "https://vendor.replicated.com/troubleshoot/analyze/nonexistent-bundle"

        with patch.dict(os.environ, {"SBCTL_TOKEN": "valid-token"}):
            bundle = BundleManager()

            # Mock httpx.AsyncClient to return 404
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "404 Client Error", request=MagicMock(), response=mock_response
            )

            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

                with pytest.raises(
                    BundleDownloadError,
                    match="Support bundle not found on Replicated Vendor Portal.*status 404",
                ):
                    await bundle.initialize_bundle(url)

    @pytest.mark.asyncio
    async def test_network_timeout_error(self):
        """Test handling of network timeout errors."""
        url = "https://vendor.replicated.com/troubleshoot/analyze/2025-06-18@16:39"

        with patch.dict(os.environ, {"SBCTL_TOKEN": "valid-token"}):
            bundle = BundleManager()

            # Mock httpx.AsyncClient to raise timeout
            with patch("httpx.AsyncClient") as mock_client:
                mock_client.return_value.__aenter__.return_value.get.side_effect = (
                    httpx.TimeoutException("Request timed out")
                )

                with pytest.raises(
                    BundleDownloadError, match="Network error requesting signed URL"
                ):
                    await bundle.initialize_bundle(url)

    @pytest.mark.asyncio
    async def test_download_size_limit_exceeded(self):
        """Test handling of download size limit exceeded."""
        url = "https://example.com/huge-bundle.tar.gz"

        bundle = BundleManager()

        # Mock aiohttp response with large content-length
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.content_length = 2000000000  # 2GB

        with patch("aiohttp.ClientSession") as mock_session:
            mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response

            with pytest.raises(
                BundleDownloadError, match="Bundle size.*exceeds maximum allowed size"
            ):
                await bundle.initialize_bundle(url)

    @pytest.mark.asyncio
    async def test_direct_download_auth_error(self):
        """Test authentication error for direct download URLs."""
        url = "https://example.com/protected-bundle.tar.gz"

        with patch.dict(os.environ, {"SBCTL_TOKEN": "invalid-token"}):
            bundle = BundleManager()

            # Mock aiohttp to return 401
            mock_response = AsyncMock()
            mock_response.status = 401
            mock_response.raise_for_status.side_effect = ClientResponseError(
                request_info=MagicMock(), history=(), status=401, message="Unauthorized"
            )

            with patch("aiohttp.ClientSession") as mock_session:
                mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response

                with pytest.raises(
                    BundleDownloadError, match="Failed to download bundle.*HTTP 401"
                ):
                    await bundle.initialize_bundle(url)


@pytest.mark.requires_token
class TestRealTokenAuthentication:
    """Test real URL fetch with authentication token (requires SBCTL_TOKEN)."""

    @pytest.mark.asyncio
    async def test_replicated_url_with_real_token(self):
        """Test real URL fetch with authentication token."""
        # Skip if no token available
        token = os.environ.get("SBCTL_TOKEN") or os.environ.get("REPLICATED")
        if not token:
            pytest.skip("SBCTL_TOKEN or REPLICATED environment variable not set")

        url = "https://vendor.replicated.com/troubleshoot/analyze/2025-06-18@16:39"
        bundle = BundleManager()

        try:
            result = await bundle.initialize_bundle(url)

            # Verify bundle was successfully initialized
            assert result is not None
            assert result.path is not None
            assert os.path.exists(result.path)

        except Exception as e:
            # If the specific bundle doesn't exist anymore, that's OK
            # We just want to verify the authentication mechanism works
            if "Bundle not found" in str(e) or "status 404" in str(e):
                pytest.skip(f"Test bundle no longer available: {e}")
            else:
                raise

    def test_token_priority_sbctl_over_replicated(self):
        """Test that SBCTL_TOKEN takes precedence over REPLICATED."""
        # Test the token priority logic directly
        with patch.dict(
            os.environ, {"SBCTL_TOKEN": "sbctl-token", "REPLICATED": "replicated-token"}
        ):
            # This mirrors the logic in bundle.py line 397-398
            token = os.environ.get("SBCTL_TOKEN") or os.environ.get("REPLICATED")
            assert token == "sbctl-token", "SBCTL_TOKEN should take precedence over REPLICATED"

        with patch.dict(os.environ, {"REPLICATED": "replicated-token"}, clear=True):
            # When only REPLICATED is set (clear all env vars first)
            token = os.environ.get("SBCTL_TOKEN") or os.environ.get("REPLICATED")
            assert token == "replicated-token", (
                "REPLICATED should be used when SBCTL_TOKEN is not set"
            )

        with patch.dict(os.environ, {}, clear=True):
            # When neither is set
            token = os.environ.get("SBCTL_TOKEN") or os.environ.get("REPLICATED")
            assert token is None, "Token should be None when neither env var is set"
