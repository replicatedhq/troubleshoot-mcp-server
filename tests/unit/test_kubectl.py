"""
Tests for the Command Executor.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from mcp_server_troubleshoot.bundle import BundleManager, BundleMetadata
from mcp_server_troubleshoot.kubectl import (
    KubectlCommandArgs,
    KubectlError,
    KubectlExecutor,
    KubectlResult,
)

# Mark all tests in this file as unit tests
pytestmark = pytest.mark.unit


def test_kubectl_command_args_validation():
    """Test that KubectlCommandArgs validates commands correctly."""
    # Valid command
    args = KubectlCommandArgs(command="get pods")
    assert args.command == "get pods"
    assert args.timeout == 30  # Default value
    assert args.json_output is False  # Default value

    # Valid command with custom timeout and json_output
    args = KubectlCommandArgs(command="get pods", timeout=60, json_output=False)
    assert args.command == "get pods"
    assert args.timeout == 60
    assert args.json_output is False


def test_kubectl_command_args_validation_invalid():
    """Test that KubectlCommandArgs validates invalid commands correctly."""
    # Empty command
    with pytest.raises(ValidationError):
        KubectlCommandArgs(command="")

    # Dangerous operations
    dangerous_operations = [
        "delete",
        "edit",
        "exec",
        "cp",
        "patch",
        "port-forward",
        "attach",
        "replace",
        "apply",
    ]
    for op in dangerous_operations:
        with pytest.raises(ValidationError):
            KubectlCommandArgs(command=f"{op} something")


@pytest.mark.asyncio
async def test_kubectl_executor_initialization():
    """Test that the kubectl executor can be initialized."""
    bundle_manager = Mock(spec=BundleManager)
    executor = KubectlExecutor(bundle_manager)
    assert executor.bundle_manager == bundle_manager


@pytest.mark.asyncio
async def test_kubectl_executor_execute_no_bundle():
    """Test that the kubectl executor raises an error if no bundle is initialized."""
    bundle_manager = Mock(spec=BundleManager)
    bundle_manager.get_active_bundle.return_value = None
    executor = KubectlExecutor(bundle_manager)

    with pytest.raises(KubectlError) as excinfo:
        await executor.execute(command="get pods")

    assert "No bundle is initialized" in str(excinfo.value)
    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_kubectl_executor_execute_host_only_bundle():
    """Test that the kubectl executor raises an error for host-only bundles."""
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
        host_only_bundle=True,  # This is the key difference
    )
    bundle_manager.get_active_bundle.return_value = bundle
    executor = KubectlExecutor(bundle_manager)

    with pytest.raises(KubectlError) as excinfo:
        await executor.execute(command="get pods")

    assert "Host-only bundle detected" in str(excinfo.value)
    assert "no cluster resources" in str(excinfo.value)
    assert "file exploration tools" in str(excinfo.value)
    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_kubectl_executor_execute_success():
    """Test that the kubectl executor can execute a command successfully."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b'{"items": []}', b""))

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock the _run_kubectl_command method
    mock_result = KubectlResult(
        command="get pods",
        exit_code=0,
        stdout='{"items": []}',
        stderr="",
        output={"items": []},
        is_json=True,
        duration_ms=100,
    )
    executor._run_kubectl_command = AsyncMock(return_value=mock_result)

    # Execute a command
    result = await executor.execute(command="get pods")

    # Verify the result
    assert result == mock_result
    executor._run_kubectl_command.assert_awaited_once_with("get pods", bundle, 30, False)


@pytest.mark.asyncio
async def test_kubectl_executor_run_kubectl_command():
    """Test that the kubectl executor can run a kubectl command."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b'{"items": []}', b""))

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute a command
        result = await executor._run_kubectl_command(
            command="get pods", bundle=bundle, timeout=30, json_output=True
        )

        # Verify the result
        assert result.command == "get pods -o json"
        assert result.exit_code == 0
        assert result.stdout == '{"items": []}'
        assert result.stderr == ""
        assert result.output == {"items": []}
        assert result.is_json is True
        assert isinstance(result.duration_ms, int)

        # Verify that create_subprocess_exec was called with the right arguments
        mock_exec.assert_awaited_once()
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "kubectl"
        assert cmd_args[1] == "get"
        assert cmd_args[2] == "pods"
        assert cmd_args[3] == "-o"
        assert cmd_args[4] == "json"

        # Verify that communicate was called
        mock_process.communicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_kubectl_executor_run_kubectl_command_no_json():
    """Test that the kubectl executor can run a kubectl command without JSON output."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(
        return_value=(b"NAME    READY   STATUS\npod1    1/1     Running", b"")
    )

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute a command
        result = await executor._run_kubectl_command(
            command="get pods", bundle=bundle, timeout=30, json_output=False
        )

        # Verify the result
        assert result.command == "get pods"
        assert result.exit_code == 0
        assert result.stdout == "NAME    READY   STATUS\npod1    1/1     Running"
        assert result.stderr == ""
        assert result.output == "NAME    READY   STATUS\npod1    1/1     Running"
        assert result.is_json is False
        assert isinstance(result.duration_ms, int)

        # Verify that create_subprocess_exec was called with the right arguments
        mock_exec.assert_awaited_once()
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "kubectl"
        assert cmd_args[1] == "get"
        assert cmd_args[2] == "pods"

        # Should not have -o json
        assert "-o" not in cmd_args
        assert "json" not in cmd_args


@pytest.mark.asyncio
async def test_kubectl_executor_run_kubectl_command_explicit_format():
    """Test that the kubectl executor respects explicit format in the command."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"name: pod1\nstatus: Running", b""))

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute a command with explicit format
        result = await executor._run_kubectl_command(
            command="get pods -o yaml", bundle=bundle, timeout=30, json_output=True
        )

        # Verify the result
        assert result.command == "get pods -o yaml"
        assert result.exit_code == 0
        assert result.stdout == "name: pod1\nstatus: Running"
        assert result.stderr == ""
        assert result.output == "name: pod1\nstatus: Running"
        assert result.is_json is False  # Not JSON even though json_output is True
        assert isinstance(result.duration_ms, int)

        # Verify that create_subprocess_exec was called with the right arguments
        mock_exec.assert_awaited_once()
        cmd_args = mock_exec.call_args[0]
        assert cmd_args[0] == "kubectl"
        assert cmd_args[1] == "get"
        assert cmd_args[2] == "pods"
        assert cmd_args[3] == "-o"
        assert cmd_args[4] == "yaml"


@pytest.mark.asyncio
async def test_kubectl_executor_run_kubectl_command_error():
    """Test that the kubectl executor handles command errors correctly."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.returncode = 1
    mock_process.communicate = AsyncMock(return_value=(b"", b'Error: resource "pods" not found'))

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        # Execute a command
        with pytest.raises(KubectlError) as excinfo:
            await executor._run_kubectl_command(
                command="get pods", bundle=bundle, timeout=30, json_output=True
            )

        # Verify the error
        assert "kubectl command failed" in str(excinfo.value)
        assert excinfo.value.exit_code == 1
        assert 'resource "pods" not found' in excinfo.value.stderr


@pytest.mark.asyncio
async def test_kubectl_executor_run_kubectl_command_timeout():
    """Test that the kubectl executor handles command timeouts correctly."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess
    mock_process = AsyncMock()
    mock_process.returncode = 0

    # Make communicate hang until timeout
    async def hang_until_timeout():
        await asyncio.sleep(10)  # This should exceed the timeout
        return (b"", b"")

    mock_process.communicate = AsyncMock(side_effect=hang_until_timeout)
    mock_process.kill = Mock()

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock subprocess_exec_with_cleanup to simulate timeout
    async def mock_subprocess_timeout(*args, **kwargs):
        raise asyncio.TimeoutError("Command timed out")

    with patch(
        "mcp_server_troubleshoot.subprocess_utils.subprocess_exec_with_cleanup",
        side_effect=mock_subprocess_timeout,
    ):
        # Execute a command with a short timeout
        with pytest.raises(KubectlError) as excinfo:
            await executor._run_kubectl_command(
                command="get pods", bundle=bundle, timeout=0.1, json_output=True
            )  # 0.1 second timeout

        # Verify the error
        assert "kubectl command timed out" in str(excinfo.value)
        assert excinfo.value.exit_code == 124

        # The subprocess_exec_with_cleanup utility handles process cleanup internally
        # so we don't need to verify kill was called directly - the timeout was handled


def test_process_output_json():
    """Test that the _process_output method handles JSON output correctly."""
    executor = KubectlExecutor(Mock(spec=BundleManager))

    output = '{"items": []}'
    processed, is_json = executor._process_output(output, True)

    assert processed == {"items": []}
    assert is_json is True


def test_process_output_text():
    """Test that the _process_output method handles text output correctly."""
    executor = KubectlExecutor(Mock(spec=BundleManager))

    output = "NAME    READY   STATUS\npod1    1/1     Running"
    processed, is_json = executor._process_output(output, True)

    assert processed == output
    assert is_json is False

    # If try_json is False, it should return the text directly
    processed, is_json = executor._process_output(output, False)
    assert processed == output
    assert is_json is False


@pytest.mark.asyncio
async def test_kubectl_default_cli_format():
    """Test that kubectl returns CLI format by default (not JSON)."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess to return CLI table format
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(
        return_value=(
            b"NAME    READY   STATUS    RESTARTS   AGE\npod1    1/1     Running   0          1m",
            b"",
        )
    )

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute with default json_output=False
        result = await executor._run_kubectl_command(
            command="get pods", bundle=bundle, timeout=30, json_output=False
        )

        # Verify CLI format is returned
        assert result.is_json is False
        assert "NAME" in result.stdout
        assert "READY" in result.stdout
        assert result.command == "get pods"  # No -o json added

        # Verify subprocess call doesn't include -o json
        cmd_args = mock_exec.call_args[0]
        assert "-o" not in cmd_args
        assert "json" not in cmd_args


@pytest.mark.asyncio
async def test_kubectl_explicit_json_request():
    """Test that explicit JSON request works with compact format."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess to return JSON format
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(
        return_value=(b'{"items": [{"metadata": {"name": "pod1"}}]}', b"")
    )

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute with explicit json_output=True
        result = await executor._run_kubectl_command(
            command="get pods", bundle=bundle, timeout=30, json_output=True
        )

        # Verify JSON format is returned
        assert result.is_json is True
        assert result.command == "get pods -o json"  # -o json was added
        assert isinstance(result.output, dict)
        assert "items" in result.output

        # Verify subprocess call includes -o json
        cmd_args = mock_exec.call_args[0]
        assert "-o" in cmd_args
        assert "json" in cmd_args


@pytest.mark.asyncio
async def test_kubectl_user_format_preserved():
    """Test that user-specified format is preserved."""
    # Mock bundle manager
    bundle_manager = Mock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Mock subprocess to return YAML format
    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(
        return_value=(b"apiVersion: v1\nkind: Pod\nmetadata:\n  name: pod1", b"")
    )

    # Create the executor
    executor = KubectlExecutor(bundle_manager)

    # Mock create_subprocess_exec
    with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
        # Execute with user-specified YAML format
        result = await executor._run_kubectl_command(
            command="get pods -o yaml", bundle=bundle, timeout=30, json_output=False
        )

        # Verify user format is preserved
        assert result.command == "get pods -o yaml"  # No modification
        assert result.is_json is False
        assert "apiVersion" in result.stdout

        # Verify subprocess call preserves user format
        cmd_args = mock_exec.call_args[0]
        assert "yaml" in cmd_args


@pytest.mark.asyncio
async def test_kubectl_executor_defaults_to_table_format():
    """Test that kubectl executor defaults to table format, not JSON."""
    # Mock bundle manager and bundle
    bundle_manager = AsyncMock(spec=BundleManager)
    bundle = BundleMetadata(
        id="test",
        source="test",
        path=Path("/test"),
        kubeconfig_path=Path("/test/kubeconfig"),
        initialized=True,
        host_only_bundle=False,
    )
    bundle_manager.get_active_bundle.return_value = bundle

    # Create executor
    executor = KubectlExecutor(bundle_manager)

    # Mock the run command to return table format output
    mock_result = KubectlResult(
        command="get pods",
        exit_code=0,
        stdout="NAME              READY   STATUS    RESTARTS   AGE\\nmy-pod            1/1     Running   0          1d",
        stderr="",
        output="NAME              READY   STATUS    RESTARTS   AGE\\nmy-pod            1/1     Running   0          1d",
        is_json=False,  # This is the key assertion - it should NOT be JSON by default
        duration_ms=100,
    )
    executor._run_kubectl_command = AsyncMock(return_value=mock_result)

    # Execute a command WITHOUT specifying json_output (should default to False)
    result = await executor.execute(command="get pods")

    # Verify the result is NOT JSON
    assert result.is_json is False, "Default kubectl execution should NOT return JSON format"
    assert result == mock_result

    # Verify _run_kubectl_command was called with json_output=False (the new default)
    executor._run_kubectl_command.assert_awaited_once_with("get pods", bundle, 30, False)


def test_compact_json_formatting():
    """Test that JSON formatting is compact (no indentation)."""
    import json
    from mcp_server_troubleshoot.formatters import ResponseFormatter
    from mcp_server_troubleshoot.kubectl import KubectlResult

    # Create a sample JSON result
    result = KubectlResult(
        command="get pods -o json",
        exit_code=0,
        stdout='{"items": [{"metadata": {"name": "pod1"}}]}',
        stderr="",
        duration_ms=100,
        output={"items": [{"metadata": {"name": "pod1"}}]},
        is_json=True,
    )

    # Create formatter with verbose verbosity to trigger JSON formatting
    formatter = ResponseFormatter(verbosity="verbose")
    formatted = formatter.format_kubectl_result(result)

    # Extract the JSON from the formatted response
    json_start = formatted.find("```json\n") + 8
    json_end = formatted.find("\n```", json_start)
    json_str = formatted[json_start:json_end]

    # Verify JSON is compact (no indentation)
    assert "\n  " not in json_str  # No indented lines

    # Verify it's valid JSON and compact
    parsed = json.loads(json_str)
    compact_json = json.dumps(parsed, separators=(",", ":"))
    assert json_str == compact_json or json_str == json.dumps(parsed)


@pytest.mark.asyncio
async def test_kubectl_executor_host_only_bundle():
    """Test that KubectlExecutor properly handles host-only bundles."""
    # Create a mock bundle manager
    bundle_manager = Mock(spec=BundleManager)

    # Create a host-only bundle metadata
    host_only_bundle = BundleMetadata(
        id="host-only-bundle",
        source="/path/to/host-only-bundle.tar.gz",
        path=Path("/tmp/host-only-bundle"),
        kubeconfig_path=Path("/tmp/host-only-bundle/kubeconfig"),
        initialized=True,
        host_only_bundle=True,  # This is the key field
    )

    # Mock the bundle manager to return the host-only bundle
    bundle_manager.get_active_bundle.return_value = host_only_bundle

    # Create executor
    executor = KubectlExecutor(bundle_manager)

    # Test that executing any kubectl command raises appropriate error
    with pytest.raises(KubectlError) as exc_info:
        await executor.execute(command="get pods")

    # Verify the error message is appropriate for host-only bundles
    error = exc_info.value
    assert error.exit_code == 1
    error_message = str(error).lower()
    assert "host resources" in error_message
    assert "no cluster resources" in error_message
    assert "file exploration tools" in error_message


@pytest.mark.asyncio
async def test_kubectl_executor_regular_bundle_not_affected():
    """Test that regular bundles (non-host-only) work normally."""
    # Create a mock bundle manager
    bundle_manager = Mock(spec=BundleManager)

    # Create a regular bundle metadata (host_only_bundle=False)
    regular_bundle = BundleMetadata(
        id="regular-bundle",
        source="/path/to/regular-bundle.tar.gz",
        path=Path("/tmp/regular-bundle"),
        kubeconfig_path=Path("/tmp/regular-bundle/kubeconfig"),
        initialized=True,
        host_only_bundle=False,  # Regular bundle
    )

    # Mock the bundle manager to return the regular bundle
    bundle_manager.get_active_bundle.return_value = regular_bundle

    # Create executor
    executor = KubectlExecutor(bundle_manager)

    # Mock a successful kubectl process
    mock_process = AsyncMock()
    mock_process.communicate.return_value = (b'{"items": []}', b"")
    mock_process.returncode = 0

    # Mock file existence check for kubeconfig
    with patch("pathlib.Path.exists", return_value=True):
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            # This should work normally (no host-only bundle error)
            result = await executor.execute(command="get pods", json_output=False)

            # Verify it returns a normal result
            assert result.exit_code == 0
            assert result.command == "get pods"


@pytest.mark.asyncio
async def test_kubectl_executor_no_bundle_still_works():
    """Test that the no-bundle error takes precedence over host-only checks."""
    # Create a mock bundle manager with no active bundle
    bundle_manager = Mock(spec=BundleManager)
    bundle_manager.get_active_bundle.return_value = None

    # Create executor
    executor = KubectlExecutor(bundle_manager)

    # Test that it raises the normal "no bundle" error, not host-only error
    with pytest.raises(KubectlError) as exc_info:
        await executor.execute(command="get pods")

    # Verify this is the standard "no bundle" error
    error = exc_info.value
    assert error.exit_code == 1
    error_message = str(error).lower()
    assert (
        "no active bundle" in error_message
        or "bundle not initialized" in error_message
        or "no bundle is initialized" in error_message
    )
    # Should NOT mention host-only bundle
    assert "host resources" not in error_message
