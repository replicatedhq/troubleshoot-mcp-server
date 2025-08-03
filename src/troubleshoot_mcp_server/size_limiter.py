"""
Size limiting utilities for MCP response optimization.

This module implements the SizeLimiter class that provides fast token estimation
and content size validation to prevent oversized responses that could impact
MCP client performance.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class SizeLimiter:
    """
    Provides token estimation and size limiting for MCP responses.

    This class implements fast character-based token approximation and
    configurable limits to optimize response sizes while maintaining
    functionality for normal-sized content.
    """

    def __init__(self, token_limit: Optional[int] = None):
        """
        Initialize the size limiter with configurable token limit.

        Args:
            token_limit: Maximum token limit, or None to use environment default
        """
        if token_limit is None:
            # Default to 25000 tokens, configurable via environment
            token_limit = int(os.environ.get("MCP_TOKEN_LIMIT", "25000"))

        self.token_limit = token_limit

        # Check if size checking is enabled (can be disabled for testing)
        self.enabled = os.environ.get("MCP_SIZE_CHECK_ENABLED", "true").lower() not in (
            "false",
            "0",
            "no",
        )

        if not self.enabled:
            logger.debug("Size limiting disabled via MCP_SIZE_CHECK_ENABLED")

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count using fast character-based approximation.

        Uses a simple heuristic of ~4 characters per token, which provides
        a reasonable approximation for most text content while being
        extremely fast for normal-sized responses.

        Args:
            text: The text content to estimate tokens for

        Returns:
            Estimated token count
        """
        if not text:
            return 0

        # Fast approximation: ~4 characters per token
        return len(text) // 4

    def check_size(self, content: str) -> bool:
        """
        Check if content size is within acceptable limits.

        Returns True if the content is within limits or size checking
        is disabled, False if it exceeds the configured token limit.

        Args:
            content: The content to check

        Returns:
            True if content is within limits, False if it exceeds limits
        """
        if not self.enabled:
            return True

        estimated_tokens = self.estimate_tokens(content)

        if estimated_tokens > self.token_limit:
            logger.warning(
                f"Content size ({estimated_tokens} estimated tokens) exceeds limit ({self.token_limit})"
            )
            return False

        return True

    def get_overflow_summary(self, content: str, max_preview_chars: int = 500) -> str:
        """
        Generate a summary for content that exceeds size limits.

        Provides a truncated preview with overflow information when
        content is too large to return in full.

        Args:
            content: The original content that exceeded limits
            max_preview_chars: Maximum characters to include in preview

        Returns:
            A summary string with truncated content and overflow info
        """
        estimated_tokens = self.estimate_tokens(content)
        content_length = len(content)

        # Create truncated preview
        if len(content) > max_preview_chars:
            preview = content[:max_preview_chars] + "..."
        else:
            preview = content

        summary = f"""Content size limit exceeded:
- Estimated tokens: {estimated_tokens}
- Token limit: {self.token_limit}
- Content length: {content_length} characters

Preview (first {min(max_preview_chars, content_length)} chars):
{preview}

--- Content truncated due to size limits ---
Consider using more specific queries or filters to reduce response size."""

        return summary


def get_size_limiter(token_limit: Optional[int] = None) -> SizeLimiter:
    """
    Get a SizeLimiter instance with the specified token limit.

    Args:
        token_limit: The token limit, or None to use environment defaults

    Returns:
        A configured SizeLimiter instance
    """
    return SizeLimiter(token_limit)
