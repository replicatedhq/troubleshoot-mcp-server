"""
Unit tests for the SizeLimiter class.

Focused test coverage for the SizeLimiter component with essential tests only.
Covers core functionality without excessive redundancy.
"""

import os
import pytest
from troubleshoot_mcp_server.size_limiter import SizeLimiter

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


@pytest.fixture
def mock_environment():
    """Provide a clean environment for testing environment variable configurations."""
    original_env = os.environ.copy()
    # Clear relevant environment variables
    env_vars = ["MCP_TOKEN_LIMIT", "MCP_SIZE_CHECK_ENABLED"]
    for var in env_vars:
        if var in os.environ:
            del os.environ[var]

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


# Core token estimation tests
@pytest.mark.parametrize(
    "text,expected_tokens",
    [
        ("", 0),  # Empty string
        ("test", 1),  # 4 chars = 1 token
        ("abcde", 1),  # 5 chars rounds down to 1 token
        ("a" * 100, 25),  # 100 chars = 25 tokens
        ("a" * 1000, 250),  # 1000 chars = 250 tokens
    ],
)
def test_token_estimation_accuracy(text, expected_tokens):
    """
    Test token estimation accuracy using ~4 characters per token approximation.
    """
    size_limiter = SizeLimiter()
    estimated_tokens = size_limiter.estimate_tokens(text)
    assert estimated_tokens == expected_tokens


# Size limit threshold testing
@pytest.mark.parametrize(
    "token_count,limit,should_pass",
    [
        (1000, 25000, True),  # Well under limit
        (25000, 25000, True),  # Exactly at limit
        (25001, 25000, False),  # Just over limit
        (50000, 25000, False),  # Well over limit
    ],
)
def test_size_limit_thresholds(token_count, limit, should_pass):
    """
    Test size limit threshold detection for boundary conditions.
    """
    size_limiter = SizeLimiter(token_limit=limit)
    # Create text with approximately the target token count
    text = "x" * (token_count * 4)
    result = size_limiter.check_size(text)
    assert result == should_pass


# Environment variable configuration tests
@pytest.mark.parametrize(
    "env_value,expected_limit",
    [
        ("10000", 10000),  # Custom limit
        (None, 25000),  # Default when not set
    ],
)
def test_mcp_token_limit_environment_variable(mock_environment, env_value, expected_limit):
    """
    Test MCP_TOKEN_LIMIT environment variable configuration.
    """
    if env_value is not None:
        os.environ["MCP_TOKEN_LIMIT"] = str(env_value)

    size_limiter = SizeLimiter()
    assert size_limiter.token_limit == expected_limit


@pytest.mark.parametrize(
    "env_value,expected_enabled",
    [
        ("true", True),  # Enabled
        ("false", False),  # Disabled
        (None, True),  # Default when not set
    ],
)
def test_mcp_size_check_enabled_environment_variable(mock_environment, env_value, expected_enabled):
    """
    Test MCP_SIZE_CHECK_ENABLED environment variable configuration.
    """
    if env_value is not None:
        os.environ["MCP_SIZE_CHECK_ENABLED"] = str(env_value)

    size_limiter = SizeLimiter()
    assert size_limiter.enabled == expected_enabled


# Edge case tests
@pytest.mark.parametrize(
    "text,description",
    [
        ("", "empty string"),
        ("Hello 世界 🌍 Émoji", "mixed Unicode"),
        ("A" * 10000, "large text"),
        ("Line 1\nLine 2\nLine 3", "multiline text"),
    ],
)
def test_edge_cases(text, description):
    """
    Test SizeLimiter with edge cases and unusual input.
    """
    size_limiter = SizeLimiter()

    # Should not raise any exceptions
    tokens = size_limiter.estimate_tokens(text)
    result = size_limiter.check_size(text)

    # Basic sanity checks
    assert isinstance(tokens, int), f"Token count should be integer for {description}"
    assert tokens >= 0, f"Token count should be non-negative for {description}"
    assert isinstance(result, bool), f"Check size should be boolean for {description}"


# Integration test for complete functionality
def test_size_limiter_complete_workflow():
    """
    Test complete SizeLimiter workflow from initialization to size checking.
    """
    size_limiter = SizeLimiter()

    # Test basic functionality
    small_text = "test"
    large_text = "X" * 100004  # Large text that exceeds default limit (25001 tokens)

    # Small text should pass
    assert size_limiter.check_size(small_text)
    assert size_limiter.estimate_tokens(small_text) == 1

    # Large text should fail
    assert not size_limiter.check_size(large_text)
    assert size_limiter.estimate_tokens(large_text) == 25001


def test_size_limiter_with_disabled_checking(mock_environment):
    """
    Test SizeLimiter behavior when size checking is disabled.
    """
    os.environ["MCP_SIZE_CHECK_ENABLED"] = "false"

    size_limiter = SizeLimiter()
    assert not size_limiter.enabled

    # Even very large content should pass when disabled
    large_text = "X" * 100000
    assert size_limiter.check_size(large_text)


def test_size_limiter_overflow_summary():
    """
    Test overflow summary generation for large content.
    """
    size_limiter = SizeLimiter()
    large_text = "A" * 1000

    summary = size_limiter.get_overflow_summary(large_text, max_preview_chars=100)

    assert "Content size limit exceeded" in summary
    assert "250" in summary  # Token count
    assert "25000" in summary  # Token limit
    assert "1000" in summary  # Character count


def test_size_limiter_initialization_with_custom_limit():
    """
    Test SizeLimiter initialization with custom token limits.
    """
    # Test with custom limit
    custom_limiter = SizeLimiter(token_limit=5000)
    assert custom_limiter.token_limit == 5000

    # Test with default limit
    default_limiter = SizeLimiter()
    assert default_limiter.token_limit == 25000


def test_size_limiter_factory_function():
    """
    Test the get_size_limiter factory function.
    """
    from troubleshoot_mcp_server.size_limiter import get_size_limiter

    # Test with custom limit
    limiter = get_size_limiter(token_limit=10000)
    assert limiter.token_limit == 10000

    # Test with default
    default_limiter = get_size_limiter()
    assert default_limiter.token_limit == 25000
