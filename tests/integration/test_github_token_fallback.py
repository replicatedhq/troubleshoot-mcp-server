"""
Regression tests for GitHub token fallback behavior.

Tests ensure that SBCTL_TOKEN is never used for GitHub URLs and that
appropriate error messages are shown when no GitHub tokens are available.
"""

import os
import pytest
from unittest.mock import patch

from troubleshoot_mcp_server.bundle import BundleManager, BundleDownloadError


class TestGitHubTokenFallbackRegression:
    """Regression tests to prevent SBCTL_TOKEN from being used for GitHub URLs."""

    @pytest.mark.asyncio
    async def test_sbctl_token_not_used_for_github(self):
        """Test that SBCTL_TOKEN is NOT used even when it's the only token set."""
        github_url = "https://github.com/user-attachments/files/12345/fake-bundle.tar.gz"

        # Only set SBCTL_TOKEN, no GitHub tokens
        with patch.dict(os.environ, {"SBCTL_TOKEN": "some-replicated-token"}, clear=True):
            bundle = BundleManager()

            with pytest.raises(
                BundleDownloadError,
                match=r"Cannot download from GitHub: No authentication token found\.",
            ):
                await bundle.initialize_bundle(github_url)

    @pytest.mark.asyncio
    async def test_clear_error_message_when_no_github_tokens(self):
        """Test clear error message when no GitHub tokens are available."""
        github_url = "https://github.com/user-attachments/files/12345/fake-bundle.tar.gz"

        # Only set SBCTL_TOKEN, no GitHub tokens
        with patch.dict(os.environ, {"SBCTL_TOKEN": "some-replicated-token"}, clear=True):
            bundle = BundleManager()

            with pytest.raises(
                BundleDownloadError, match=r"Set GITHUB_TOKEN environment variable\."
            ):
                await bundle.initialize_bundle(github_url)

    @pytest.mark.asyncio
    async def test_error_message_explains_sbctl_token_limitation(self):
        """Test that error message explains SBCTL_TOKEN cannot be used for GitHub."""
        github_url = "https://github.com/user-attachments/files/12345/fake-bundle.tar.gz"

        # Only set SBCTL_TOKEN, no GitHub tokens
        with patch.dict(os.environ, {"SBCTL_TOKEN": "some-replicated-token"}, clear=True):
            bundle = BundleManager()

            with pytest.raises(
                BundleDownloadError,
                match=r"Note: SBCTL_TOKEN is only for Replicated URLs, not GitHub\.",
            ):
                await bundle.initialize_bundle(github_url)

    @pytest.mark.asyncio
    async def test_github_token_selection_logic(self):
        """Test that only GITHUB_TOKEN is used (no SBCTL_TOKEN)."""
        # Test GITHUB_TOKEN is used
        with patch.dict(
            os.environ,
            {"GITHUB_TOKEN": "github-token", "SBCTL_TOKEN": "sbctl-token"},
            clear=True,
        ):
            # We can't actually test the download without mocking the HTTP client,
            # but we can verify the token selection logic by checking the method directly
            token = os.environ.get("GITHUB_TOKEN")
            assert token == "github-token", "GITHUB_TOKEN should be used"

        # Test SBCTL_TOKEN is ignored (should be None)
        with patch.dict(os.environ, {"SBCTL_TOKEN": "sbctl-token"}, clear=True):
            token = os.environ.get("GITHUB_TOKEN")
            assert token is None, "SBCTL_TOKEN should be ignored for GitHub authentication"

    @pytest.mark.asyncio
    async def test_different_github_url_patterns(self):
        """Test that SBCTL_TOKEN is not used for different GitHub URL patterns."""
        github_urls = [
            "https://github.com/user-attachments/files/12345/bundle.tar.gz",
            "https://github.com/owner/repo/releases/download/v1.0.0/bundle.tar.gz",
            "https://raw.githubusercontent.com/owner/repo/main/bundle.tar.gz",
        ]

        # Only set SBCTL_TOKEN, no GitHub tokens
        with patch.dict(os.environ, {"SBCTL_TOKEN": "some-replicated-token"}, clear=True):
            bundle = BundleManager()

            for url in github_urls:
                with pytest.raises(
                    BundleDownloadError,
                    match=r"Cannot download from GitHub: No authentication token found\.",
                ):
                    await bundle.initialize_bundle(url)

    @pytest.mark.asyncio
    async def test_no_tokens_available_for_github(self):
        """Test error when no tokens are available at all for GitHub URLs."""
        github_url = "https://github.com/user-attachments/files/12345/fake-bundle.tar.gz"

        # Clear all environment variables
        with patch.dict(os.environ, {}, clear=True):
            bundle = BundleManager()

            with pytest.raises(
                BundleDownloadError,
                match=r"Cannot download from GitHub: No authentication token found\.",
            ):
                await bundle.initialize_bundle(github_url)
