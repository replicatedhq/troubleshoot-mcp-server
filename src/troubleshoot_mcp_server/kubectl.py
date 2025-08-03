"""
Command Executor for Kubernetes support bundles.

This module implements the Command Executor component, which is responsible for
running kubectl commands against the initialized support bundle's API server and
processing the results for consumption by AI models.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from .bundle import BundleManager, BundleMetadata

logger = logging.getLogger(__name__)


class KubectlError(Exception):
    """Exception raised when a kubectl command fails."""

    def __init__(self, message: str, exit_code: int | None, stderr: str) -> None:
        """
        Initialize a KubectlError exception.

        Args:
            message: The error message
            exit_code: The command exit code (may be None in some error cases)
            stderr: The standard error output
        """
        self.exit_code = exit_code if exit_code is not None else 1
        self.stderr = stderr
        super().__init__(f"{message} (exit code {self.exit_code}): {stderr}")


class KubectlCommandArgs(BaseModel):
    """
    Arguments for executing a kubectl command.
    """

    command: str = Field(description="The kubectl command to execute")
    timeout: int = Field(30, description="Timeout in seconds for the command")
    json_output: bool = Field(False, description="Whether to format the output as JSON")
    verbosity: Optional[str] = Field(
        None,
        description="Verbosity level for response formatting (minimal|standard|verbose|debug)",
    )

    @field_validator("command")
    def validate_command(cls, v: str) -> str:
        """
        Validate the kubectl command.

        Args:
            v: The command string to validate

        Returns:
            The validated command string

        Raises:
            ValueError: If the command is invalid
        """
        # Basic validation to ensure the command isn't empty
        if not v or not v.strip():
            raise ValueError("kubectl command cannot be empty")

        # Check for potentially dangerous operations
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
            if re.search(rf"^\s*{op}\b", v):
                raise ValueError(f"Kubectl command '{op}' is not allowed for safety reasons")

        return v


class KubectlResult(BaseModel):
    """
    Result of a kubectl command execution.
    """

    command: str = Field(description="The kubectl command that was executed")
    exit_code: int | None = Field(description="The exit code of the command")
    stdout: str = Field(description="The standard output of the command")
    stderr: str = Field(description="The standard error output of the command")
    output: Any = Field(description="The parsed output, if applicable")
    is_json: bool = Field(description="Whether the output is JSON")
    duration_ms: int = Field(description="The duration of the command execution in milliseconds")

    @field_validator("exit_code")
    @classmethod
    def validate_exit_code(cls, v: int | None) -> int:
        """Handle None values for exit_code by defaulting to 1."""
        return 1 if v is None else v


class KubectlExecutor:
    """
    Executes kubectl commands against a Kubernetes API server.

    This class is responsible for running kubectl commands against the API server
    emulated from a support bundle and processing the results.
    """

    def __init__(self, bundle_manager: BundleManager) -> None:
        """
        Initialize the kubectl executor.

        Args:
            bundle_manager: The bundle manager that provides the kubeconfig
        """
        self.bundle_manager = bundle_manager

    async def execute(
        self, command: str, timeout: int = 30, json_output: bool = False
    ) -> KubectlResult:
        """
        Execute a kubectl command.

        Args:
            command: The kubectl command to execute
            timeout: Timeout in seconds for the command
            json_output: Whether to format the output as JSON

        Returns:
            The result of the command execution

        Raises:
            KubectlError: If the command fails or if no bundle is initialized
            BundleManagerError: If there's an issue with the bundle
        """
        # Check if a bundle is initialized
        active_bundle = self.bundle_manager.get_active_bundle()
        if active_bundle is None or not active_bundle.initialized:
            raise KubectlError(
                "No bundle is initialized",
                1,
                "Please initialize a bundle before executing kubectl commands",
            )

        # Check if this is a host-only bundle
        if active_bundle.host_only_bundle:
            raise KubectlError(
                "Host-only bundle detected",
                1,
                "This support bundle contains only host resources and no cluster resources. "
                "kubectl commands are not available for host-only bundles. "
                "Use file exploration tools instead.",
            )

        # Construct the command
        return await self._run_kubectl_command(command, active_bundle, timeout, json_output)

    async def _run_kubectl_command(
        self, command: str, bundle: BundleMetadata, timeout: int, json_output: bool
    ) -> KubectlResult:
        """
        Run a kubectl command with the given bundle's kubeconfig.

        Args:
            command: The kubectl command to execute
            bundle: The bundle metadata containing the kubeconfig
            timeout: Timeout in seconds for the command
            json_output: Whether to format the output as JSON

        Returns:
            The result of the command execution

        Raises:
            KubectlError: If the command fails
        """

        # Normal execution for non-test mode
        # Format the command
        if json_output and not re.search(r"\s+-o\s+(\S+)", command):
            command = f"{command} -o json"

        kubeconfig_path = bundle.kubeconfig_path

        # Start timer
        start_time = asyncio.get_event_loop().time()

        try:
            # Create environment with KUBECONFIG set
            env = os.environ.copy()
            env["KUBECONFIG"] = str(kubeconfig_path)

            logger.info(f"Executing kubectl command: {command}")

            # Split the command into parts for security
            cmd = ["kubectl"] + command.split()

            # Run the command with proper cleanup
            from .subprocess_utils import subprocess_exec_with_cleanup

            try:
                returncode, stdout, stderr = await subprocess_exec_with_cleanup(
                    *cmd, timeout=timeout, env=env
                )
            except asyncio.TimeoutError:
                raise KubectlError(
                    f"kubectl command timed out after {timeout} seconds",
                    124,
                    "Command execution took too long and was terminated",
                )

            # End timer
            end_time = asyncio.get_event_loop().time()
            duration_ms = int((end_time - start_time) * 1000)

            # Convert bytes to string
            stdout_str = stdout.decode("utf-8")
            stderr_str = stderr.decode("utf-8")

            # Process the output
            output, is_json = self._process_output(stdout_str, returncode == 0 and json_output)

            # Create the result
            result = KubectlResult(
                command=command,
                exit_code=returncode,
                stdout=stdout_str,
                stderr=stderr_str,
                output=output,
                is_json=is_json,
                duration_ms=duration_ms,
            )

            # Log the result
            if returncode == 0:
                logger.info(f"kubectl command completed successfully in {duration_ms}ms")
            else:
                logger.error(f"kubectl command failed with exit code {returncode}: {stderr_str}")
                raise KubectlError("kubectl command failed", returncode, stderr_str)

            return result

        except (OSError, FileNotFoundError) as e:
            logger.exception(f"Error executing kubectl command: {str(e)}")
            raise KubectlError("Failed to execute kubectl command", 1, f"Error: {str(e)}")

    def _process_output(self, output: str, try_json: bool) -> Tuple[Any, bool]:
        """
        Process the command output.

        Args:
            output: The command output
            try_json: Whether to try parsing the output as JSON

        Returns:
            A tuple of (processed_output, is_json)
        """
        if not try_json:
            return output, False

        try:
            parsed = json.loads(output)
            return parsed, True
        except (json.JSONDecodeError, ValueError):
            return output, False
