"""
Functional tests for bundle lifecycle management through MCP protocol.

Tests validate complete bundle workflows including initialization, state management,
and reinitialization scenarios.
"""

import time
from pathlib import Path
from typing import Dict

import pytest

from tests.integration.mcp_test_utils import MCPTestClient


@pytest.mark.functional
@pytest.mark.asyncio
async def test_bundle_initialization_success(
    mcp_protocol_client: MCPTestClient,
    test_bundle_source: Path,
    performance_threshold: Dict[str, int],
) -> None:
    """Test successful bundle initialization through MCP protocol."""
    start_time = time.time()

    # Initialize bundle through MCP protocol
    result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "verbose"},
    )

    end_time = time.time()
    duration_ms = (end_time - start_time) * 1000

    # Verify performance
    max_duration = performance_threshold["bundle_init_max_ms"]
    assert duration_ms < max_duration, (
        f"Bundle initialization took {duration_ms:.1f}ms, expected under {max_duration}ms"
    )

    # Verify successful response
    assert len(result) == 1, "Expected single response content item"
    content = result[0]
    assert content["type"] == "text", "Response should be text content"

    response_text = content["text"]
    # Check for either new JSON format or old text format
    assert "Bundle initialized successfully" in response_text or (
        '"bundle_id":' in response_text and '"status": "ready"' in response_text
    ), f"Expected success message or JSON status, got: {response_text}"

    # Verify bundle information is included (may be in JSON format)
    assert (
        "Bundle ID:" in response_text
        or "bundle:" in response_text.lower()
        or '"id":' in response_text
    ), f"Expected bundle ID information: {response_text}"


@pytest.mark.functional
@pytest.mark.asyncio
async def test_bundle_initialization_force_reinit(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test bundle reinitialization with force flag."""
    # First initialization
    result1 = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )

    assert len(result1) == 1
    result1_text = result1[0]["text"]
    assert "Bundle initialized successfully" in result1_text or (
        '"bundle_id":' in result1_text and '"status": "ready"' in result1_text
    )

    # Second initialization without force (should succeed but may skip work)
    result2 = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )

    assert len(result2) == 1
    response2_text = result2[0]["text"]
    # Should either succeed with initialization or indicate already initialized
    assert (
        "Bundle initialized successfully" in response2_text
        or "already" in response2_text.lower()
        or ('"bundle_id":' in response2_text and '"status": "ready"' in response2_text)
    )

    # Third initialization with force (should definitely reinitialize)
    result3 = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": True, "verbosity": "minimal"},
    )

    assert len(result3) == 1
    result3_text = result3[0]["text"]
    assert "Bundle initialized successfully" in result3_text or (
        '"bundle_id":' in result3_text and '"status": "ready"' in result3_text
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_bundle_state_persistence(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test that bundle state persists across tool calls."""
    # Initialize bundle
    init_result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": False, "verbosity": "minimal"},
    )

    init_text = init_result[0]["text"]
    assert "Bundle initialized successfully" in init_text or (
        '"bundle_id":' in init_text and '"status": "ready"' in init_text
    )

    # Try to use kubectl (should work with initialized bundle)
    kubectl_result = await mcp_protocol_client.call_tool(
        "kubectl",
        {
            "command": "version --client",
            "timeout": 10,
            "json_output": False,
            "verbosity": "minimal",
        },
    )

    # Should not get "no bundle initialized" error
    kubectl_text = kubectl_result[0]["text"]
    assert "no bundle" not in kubectl_text.lower(), (
        f"kubectl should work with initialized bundle, got: {kubectl_text}"
    )

    # Try to use file operations (should work with initialized bundle)
    list_result = await mcp_protocol_client.call_tool(
        "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
    )

    list_text = list_result[0]["text"]
    assert "no bundle" not in list_text.lower(), (
        f"list_files should work with initialized bundle, got: {list_text}"
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_bundle_initialization_verbosity_levels(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test different verbosity levels in bundle initialization."""
    # Test minimal verbosity
    result_minimal = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": True, "verbosity": "minimal"},
    )

    minimal_text = result_minimal[0]["text"]
    minimal_lines = len(minimal_text.split("\n"))

    # Test verbose verbosity
    result_verbose = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": str(test_bundle_source), "force": True, "verbosity": "verbose"},
    )

    verbose_text = result_verbose[0]["text"]
    verbose_lines = len(verbose_text.split("\n"))

    # Verbose should provide more detail
    assert verbose_lines > minimal_lines, (
        f"Verbose output ({verbose_lines} lines) should be longer than "
        f"minimal output ({minimal_lines} lines)"
    )

    # Both should indicate success
    assert "Bundle initialized successfully" in minimal_text or (
        '"bundle_id":' in minimal_text and '"status": "ready"' in minimal_text
    )
    assert "Bundle initialized successfully" in verbose_text or (
        '"bundle_id":' in verbose_text and '"status": "ready"' in verbose_text
    )

    # Verbose should include more diagnostic information (JSON metadata counts as diagnostic info)
    assert (
        "API server" in verbose_text.lower()
        or "diagnostic" in verbose_text.lower()
        or "kubeconfig_path" in verbose_text
        or "initialized" in verbose_text
    )


@pytest.mark.functional
@pytest.mark.asyncio
async def test_bundle_initialization_invalid_source(mcp_protocol_client: MCPTestClient) -> None:
    """Test bundle initialization with invalid source path."""
    # Try to initialize with non-existent bundle
    result = await mcp_protocol_client.call_tool(
        "initialize_bundle",
        {"source": "/non/existent/bundle.tar.gz", "force": False, "verbosity": "minimal"},
    )

    assert len(result) == 1
    response_text = result[0]["text"]

    # Should get an error message
    assert (
        "error" in response_text.lower()
        or "not found" in response_text.lower()
        or "failed" in response_text.lower()
    ), f"Expected error message for invalid source, got: {response_text}"

    # Should not indicate success
    assert "Bundle initialized successfully" not in response_text


@pytest.mark.functional
@pytest.mark.asyncio
async def test_multiple_bundle_lifecycle_cycles(
    mcp_protocol_client: MCPTestClient, test_bundle_source: Path
) -> None:
    """Test multiple complete lifecycle cycles."""
    for cycle in range(3):
        # Initialize bundle
        init_result = await mcp_protocol_client.call_tool(
            "initialize_bundle",
            {
                "source": str(test_bundle_source),
                "force": True,  # Force reinitialization each cycle
                "verbosity": "minimal",
            },
        )

        init_text = init_result[0]["text"]
        assert "Bundle initialized successfully" in init_text or (
            '"bundle_id":' in init_text and '"status": "ready"' in init_text
        ), f"Cycle {cycle}: Bundle initialization failed: {init_text}"

        # Use the bundle (kubectl test)
        kubectl_result = await mcp_protocol_client.call_tool(
            "kubectl",
            {
                "command": "version --client",
                "timeout": 10,
                "json_output": False,
                "verbosity": "minimal",
            },
        )

        kubectl_text = kubectl_result[0]["text"]
        assert "no bundle" not in kubectl_text.lower(), (
            f"Cycle {cycle}: kubectl failed with initialized bundle"
        )

        # Use the bundle (file operations test)
        list_result = await mcp_protocol_client.call_tool(
            "list_files", {"path": ".", "recursive": False, "verbosity": "minimal"}
        )

        list_text = list_result[0]["text"]
        assert "no bundle" not in list_text.lower(), (
            f"Cycle {cycle}: list_files failed with initialized bundle"
        )
