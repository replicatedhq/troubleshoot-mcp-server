"""
Pytest configuration and fixtures for integration tests.
"""

import asyncio
from typing import Any, Dict, List, Optional
import pytest


# Import TestAssertions class and fixture from unit tests
class TestAssertions:
    """
    Collection of reusable test assertion helpers for common patterns in tests.

    These utilities make assertions more consistent, reduce duplication, and
    provide better error messages when tests fail.
    """

    @staticmethod
    def assert_attributes_exist(obj: Any, attributes: List[str]) -> None:
        """
        Assert that an object has all the specified attributes.

        Args:
            obj: The object to check
            attributes: List of attribute names to verify

        Raises:
            AssertionError: If any attribute is missing
        """
        for attr in attributes:
            assert hasattr(obj, attr), f"Object should have attribute '{attr}'"

    @staticmethod
    def assert_api_response_valid(
        response: List[Any], expected_type: str = "text", contains: Optional[List[str]] = None
    ) -> None:
        """
        Assert that an MCP API response is valid and contains expected content.

        Args:
            response: The API response to check
            expected_type: Expected response type (e.g., 'text')
            contains: List of strings that should be in the response text

        Raises:
            AssertionError: If response is invalid or missing expected content
        """
        assert isinstance(response, list), "Response should be a list"
        assert len(response) > 0, "Response should not be empty"
        assert hasattr(response[0], "type"), "Response item should have 'type' attribute"
        assert response[0].type == expected_type, f"Response type should be '{expected_type}'"

        if contains and hasattr(response[0], "text"):
            for text in contains:
                assert text in response[0].text, f"Response should contain '{text}'"

    @staticmethod
    def assert_object_matches_attrs(obj: Any, expected_attrs: Dict[str, Any]) -> None:
        """
        Assert that an object has attributes matching expected values.

        Args:
            obj: The object to check
            expected_attrs: Dictionary of attribute names and expected values

        Raises:
            AssertionError: If any attribute doesn't match the expected value
        """
        for attr, expected in expected_attrs.items():
            assert hasattr(obj, attr), f"Object should have attribute '{attr}'"
            actual = getattr(obj, attr)
            assert actual == expected, (
                f"Attribute '{attr}' value mismatch. Expected: {expected}, Got: {actual}"
            )

    @staticmethod
    async def assert_asyncio_timeout(coro, timeout: float = 0.1) -> None:
        """
        Assert that an async coroutine times out.

        Args:
            coro: Coroutine to execute
            timeout: Timeout in seconds

        Raises:
            AssertionError: If the coroutine doesn't time out
        """
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(coro, timeout=timeout)


@pytest.fixture
def test_assertions():
    """Fixture providing test assertion utilities."""
    return TestAssertions()
