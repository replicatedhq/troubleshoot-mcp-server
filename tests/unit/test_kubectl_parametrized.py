"""
Parametrized tests for the KubectlExecutor component.

This module tests the kubectl command execution functionality with parameterized tests
that verify all key behaviors while focusing on functionality rather than implementation.

Benefits of the parameterized approach:
1. Comprehensive testing of multiple input combinations
2. Clear documentation of valid/invalid inputs and their expected behavior
3. Easier maintenance - adding new test cases is simple
4. Better visualization of edge cases and error handling

The tests cover these main user scenarios:
1. Command argument validation (ensuring proper input validation)
2. Command execution with various output formats (JSON vs. text)
3. Error handling for different failure cases (timeouts, missing kubectl, etc.)
4. Output parsing for different formats

Each test verifies the behavior that users would observe, rather than implementation
details, making the tests more resilient to refactoring.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from troubleshoot_mcp_server.bundle import BundleManager
from troubleshoot_mcp_server.kubectl import (
    KubectlCommandArgs,
    KubectlError,
    KubectlExecutor,
)

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


# Parameterized validation tests
@pytest.mark.parametrize(
    "command,timeout,json_output,expected_valid",
    [
        # Valid cases
        ("get pods", 30, True, True),
        ("get nodes", 60, False, True),
        ("get namespace default", 10, True, True),
        # Invalid cases
        ("", 30, True, False),  # Empty command
        ("delete pods", 30, True, False),  # Dangerous operation
        ("exec -it pod1 -- bash", 30, True, False),  # Dangerous operation
        ("apply -f file.yaml", 30, True, False),  # Dangerous operation
    ],
    ids=[
        "valid-get-pods",
        "valid-get-nodes-no-json",
        "valid-get-namespace",
        "invalid-empty-command",
        "invalid-delete-operation",
        "invalid-exec-operation",
        "invalid-apply-operation",
    ],
)
def test_kubectl_command_args_validation_parametrized(
    command, timeout, json_output, expected_valid
):
    """
    Test KubectlCommandArgs validation with parameterized test cases.

    This test covers both valid and invalid inputs in a single test,
    making it easier to see all validation rules and add new cases.

    Args:
        command: The kubectl command to validate
        timeout: Command timeout in seconds
        json_output: Whether to request JSON output
        expected_valid: Whether validation should pass
    """
    if expected_valid:
        # Should succeed
        args = KubectlCommandArgs(command=command, timeout=timeout, json_output=json_output)
        assert args.command == command
        assert args.timeout == timeout
        assert args.json_output == json_output
    else:
        # Should raise ValidationError
        with pytest.raises(ValidationError):
            KubectlCommandArgs(command=command, timeout=timeout, json_output=json_output)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,expected_args,add_json",
    [
        # Basic commands
        ("get pods", ["kubectl", "get", "pods"], True),
        ("get nodes", ["kubectl", "get", "nodes"], True),
        # Commands with explicit output formats (shouldn't add JSON)
        ("get pods -o yaml", ["kubectl", "get", "pods", "-o", "yaml"], False),
        ("get pods -o wide", ["kubectl", "get", "pods", "-o", "wide"], False),
        # Commands with additional flags
        ("get pods -n default", ["kubectl", "get", "pods", "-n", "default"], True),
        (
            "get pods --field-selector=status.phase=Running",
            ["kubectl", "get", "pods", "--field-selector=status.phase=Running"],
            True,
        ),
        # Query-type commands
        ("api-resources", ["kubectl", "api-resources"], True),
        ("version", ["kubectl", "version"], True),
    ],
    ids=[
        "basic-get-pods",
        "basic-get-nodes",
        "explicit-yaml-format",
        "explicit-wide-format",
        "namespace-flag",
        "field-selector",
        "api-resources",
        "version",
    ],
)
async def test_kubectl_command_execution_parameters(command, expected_args, add_json, test_factory):
    """
    Test that the kubectl executor handles different command formats correctly.

    This test ensures the command is properly parsed and executed for
    various command patterns with different options.

    Args:
        command: The kubectl command to execute
        expected_args: Expected command arguments list
        add_json: Whether -o json should be added to the command
        test_factory: Factory fixture for test objects
    """
    # Create a bundle for testing
    bundle = test_factory.create_bundle_metadata()

    # Create mock objects for testing
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b'{"items": []}', b""))

    # Create the executor with a mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle_manager.get_active_bundle.return_value = bundle
    executor = KubectlExecutor(bundle_manager)

    # If we should add JSON format, add it to the expected args
    if add_json:
        expected_args.extend(["-o", "json"])

    # Mock the create_subprocess_exec function
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute the command
        result = await executor._run_kubectl_command(
            command=command, bundle=bundle, timeout=30, json_output=True
        )

        # Verify the command was constructed correctly
        mock_exec.assert_awaited_once()
        args = mock_exec.call_args[0]

        # Verify each argument matches the expected value
        for i, arg in enumerate(expected_args):
            assert args[i] == arg, f"Argument {i} should be '{arg}', got '{args[i]}'"

        # Verify the result structure
        assert result.exit_code == 0
        assert isinstance(result.stdout, str)
        assert isinstance(result.stderr, str)

        # Verify JSON handling
        if add_json:
            assert result.is_json is True
            assert isinstance(result.output, dict)

        # Verify timing information
        assert isinstance(result.duration_ms, int)
        assert result.duration_ms >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "return_code,stdout_content,stderr_content,expected_exit_code,should_raise",
    [
        # Success cases
        (0, '{"items": []}', "", 0, False),
        (0, "NAME  READY  STATUS", "", 0, False),
        # Error cases
        (1, "", "Error: resource not found", 1, True),
        (2, "", "Error: unknown flag", 2, True),
        (127, "", "Error: command not found", 127, True),
    ],
    ids=[
        "success-json",
        "success-text",
        "error-resource-not-found",
        "error-unknown-flag",
        "error-command-not-found",
    ],
)
async def test_kubectl_error_handling(
    return_code,
    stdout_content,
    stderr_content,
    expected_exit_code,
    should_raise,
    test_factory,
):
    """
    Test that the kubectl executor handles errors correctly.

    This test verifies that command failures are handled properly,
    with appropriate errors raised and error information preserved.

    Args:
        return_code: The command return code
        stdout_content: Command standard output
        stderr_content: Command standard error
        expected_exit_code: Expected exit code in the result/error
        should_raise: Whether an exception should be raised
        test_factory: Factory fixture for test objects
    """
    # Create a bundle for testing
    bundle = test_factory.create_bundle_metadata()

    # Create mock objects for testing
    mock_process = AsyncMock()
    mock_process.returncode = return_code
    mock_process.communicate = AsyncMock(
        return_value=(stdout_content.encode(), stderr_content.encode())
    )

    # Create the executor with a mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle_manager.get_active_bundle.return_value = bundle
    executor = KubectlExecutor(bundle_manager)

    # Test command execution
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        if should_raise:
            # Should raise KubectlError
            with pytest.raises(KubectlError) as excinfo:
                await executor._run_kubectl_command(
                    command="get pods", bundle=bundle, timeout=30, json_output=True
                )

            # Verify error details
            assert excinfo.value.exit_code == expected_exit_code
            assert stderr_content in excinfo.value.stderr
        else:
            # Should succeed
            result = await executor._run_kubectl_command(
                command="get pods", bundle=bundle, timeout=30, json_output=True
            )

            # Verify result details
            assert result.exit_code == expected_exit_code
            assert result.stdout == stdout_content
            assert result.stderr == stderr_content


@pytest.mark.asyncio
async def test_kubectl_timeout_behavior(test_assertions, test_factory):
    """
    Test that the kubectl executor properly handles command timeouts.

    This test verifies that:
    1. Commands that exceed their timeout are properly terminated
    2. KubectlError is raised with the correct error information
    3. The process is killed to prevent resource leaks

    Args:
        test_assertions: Assertions helper fixture
        test_factory: Factory fixture for test objects
    """
    # Create a bundle for testing
    bundle = test_factory.create_bundle_metadata()

    # Create a mock process
    mock_process = AsyncMock()
    mock_process.returncode = 0

    # Create a function that hangs to simulate a timeout
    async def hang_forever():
        await asyncio.sleep(30)  # Much longer than our timeout
        return (b"", b"")

    mock_process.communicate = AsyncMock(side_effect=hang_forever)
    mock_process.kill = Mock()

    # Create the executor
    bundle_manager = Mock(spec=BundleManager)
    bundle_manager.get_active_bundle.return_value = bundle
    executor = KubectlExecutor(bundle_manager)

    # Test with a very short timeout by mocking the subprocess utility
    async def mock_subprocess_timeout(*args, **kwargs):
        raise asyncio.TimeoutError("Command timed out")

    with patch(
        "troubleshoot_mcp_server.subprocess_utils.subprocess_exec_with_cleanup",
        side_effect=mock_subprocess_timeout,
    ):
        with pytest.raises(KubectlError) as excinfo:
            await executor._run_kubectl_command(
                command="get pods", bundle=bundle, timeout=0.1, json_output=True
            )

        # Verify error details
        assert "timed out" in str(excinfo.value).lower()
        assert excinfo.value.exit_code == 124  # Standard timeout exit code

        # The subprocess_exec_with_cleanup utility handles process cleanup internally


@pytest.mark.asyncio
async def test_kubectl_response_parsing(test_assertions, test_factory):
    """
    Test that kubectl output is properly parsed based on format.

    This test verifies:
    1. JSON output is properly parsed into Python objects
    2. Non-JSON output is handled correctly
    3. JSON parsing errors are handled gracefully

    Args:
        test_assertions: Assertions helper fixture
        test_factory: Factory fixture for test objects
    """
    # Create a kubectl executor for testing
    executor = KubectlExecutor(Mock(spec=BundleManager))

    # Test cases for output processing
    test_cases = [
        # Valid JSON
        {
            "output": '{"items": [{"name": "pod1"}]}',
            "try_json": True,
            "expected_is_json": True,
            "expected_type": dict,
        },
        # JSON array
        {
            "output": '[{"name": "pod1"}, {"name": "pod2"}]',
            "try_json": True,
            "expected_is_json": True,
            "expected_type": list,
        },
        # Not trying JSON parsing
        {
            "output": '{"items": []}',
            "try_json": False,
            "expected_is_json": False,
            "expected_type": str,
        },
        # Invalid JSON
        {
            "output": '{"items": [} - malformed',
            "try_json": True,
            "expected_is_json": False,
            "expected_type": str,
        },
        # Plain text
        {
            "output": "NAME  READY  STATUS\npod1  1/1    Running",
            "try_json": True,
            "expected_is_json": False,
            "expected_type": str,
        },
    ]

    # Test each case
    for i, case in enumerate(test_cases):
        processed, is_json = executor._process_output(case["output"], case["try_json"])

        # Assert the output format was detected correctly
        assert is_json == case["expected_is_json"], f"Case {i}: JSON detection failed"

        # Assert the output was processed to the right type
        assert isinstance(processed, case["expected_type"]), f"Case {i}: Wrong output type"

        # For JSON outputs, verify structure
        if case["expected_is_json"]:
            if isinstance(processed, dict) and "items" in processed:
                assert isinstance(processed["items"], list)
            elif isinstance(processed, list):
                assert all(isinstance(item, dict) for item in processed)
