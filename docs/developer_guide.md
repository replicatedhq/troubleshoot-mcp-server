# Developer Guide

This guide provides information for developers who want to understand, modify, or extend the MCP Server for Kubernetes Support Bundles.

## Table of Contents

- [Architecture](#architecture)
- [Components](#components)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Error Handling](#error-handling)
- [Adding New Features](#adding-new-features)
- [Contributing](#contributing)

## Architecture

The MCP Server is designed with a modular architecture following the MCP (Model Context Protocol) standard. The following diagram shows the high-level architecture:

```
┌────────────────────────────────────────────────────────────────────┐
│                           MCP Server                                │
│                                                                     │
│  ┌──────────────┐    ┌─────────────────┐    ┌─────────────────┐    │
│  │ Bundle       │    │ Command         │    │ File            │    │
│  │ Manager      │    │ Executor        │    │ Explorer        │    │
│  └──────────────┘    └─────────────────┘    └─────────────────┘    │
│         │                    │                      │               │
│         └────────────────────┼──────────────────────┘               │
│                              │                                      │
│                    ┌─────────────────┐                              │
│                    │ Support Bundle  │                              │
│                    └─────────────────┘                              │
└────────────────────────────────────────────────────────────────────┘
```

The server follows an asynchronous design pattern using Python's `asyncio` library, allowing efficient handling of multiple operations. Communication with AI models occurs through standard I/O using the MCP protocol's JSON format.

## Components

### MCP Server (`server.py`)

The core server component implements the MCP protocol, handling tool registration, listing, and execution. It:
- Registers tools from other components
- Handles incoming requests
- Routes requests to appropriate handlers
- Formats responses according to the MCP protocol

### Bundle Manager (`bundle.py`)

Manages support bundle operations:
- Downloading bundles
- Extracting bundle contents
- Initializing bundles for use with other components
- Providing bundle information

### Command Executor (`kubectl.py`)

Executes kubectl commands against the bundle's API server:
- Running kubectl commands with proper context
- Parsing command output
- Handling errors and formatting responses

### File Explorer (`files.py`)

Provides file system operations:
- Listing directories
- Reading file contents
- Searching for patterns in files
- Implementing security measures to prevent directory traversal

## Development Setup

1. **Clone the Repository**

```bash
git clone https://github.com/user/troubleshoot-mcp-server.git
cd troubleshoot-mcp-server
```

2. **Set Up Development Environment using UV**

```bash
# Use the setup script (recommended)
./scripts/setup_env.sh

# Or manually set up with UV
uv venv -p python3.13 .venv
uv pip install -e ".[dev]"
```

3. **Install System Dependencies**

Ensure you have `kubectl` and `sbctl` installed on your system.

## Running Tests

### Running All Tests

```bash
# Using UV directly
uv run pytest

# Using the helper script
./scripts/run_tests.sh
```

### Running Specific Tests

```bash
# Run a specific test file
uv run pytest tests/test_bundle.py

# Run a specific test function
uv run pytest tests/test_bundle.py::TestBundleManager::test_initialize_bundle

# Run with verbose output
uv run pytest -v
```

### Running Integration Tests

```bash
# Using UV directly
uv run pytest tests/test_integration.py

# Using the helper script with category
./scripts/run_tests.sh integration
```

### Code Formatting and Linting

```bash
# Format code with Ruff
uv run ruff format .

# Lint code with Ruff
uv run ruff check .
```

## Error Handling

The MCP server follows a hierarchical approach to error handling:

1. **Component-Specific Errors**: Each component defines its own exceptions, such as `BundleError`, `KubectlError`, and `FileOperationError`.

2. **MCP Protocol Errors**: The server converts component-specific errors to MCP protocol-compliant error responses.

3. **User-Facing Errors**: Errors are presented with clear messages and actionable information.

When adding features, follow these error handling principles:

- Use specific exception types for different error scenarios
- Include context in error messages
- Avoid exposing internal details in user-facing errors
- Handle expected errors gracefully
- Log unexpected errors for debugging

## Adding New Features

### Adding a New Tool

1. **Implement the Tool Functionality**

Add your tool functionality to an appropriate component (or create a new one):

```python
class MyComponent:
    async def my_new_tool(self, param1, param2):
        # Tool implementation
        return result
```

2. **Register the Tool with the MCP Server**

In `server.py`, add code to register your new tool:

```python
def register_my_component_tools(self, my_component):
    self.register_tool(
        "my_component__my_new_tool",
        my_component.my_new_tool,
        {
            "description": "Description of what the tool does",
            "parameters": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string", "description": "Description of param1"},
                    "param2": {"type": "number", "description": "Description of param2"}
                },
                "required": ["param1"]
            }
        }
    )
```

3. **Add Tests**

Create unit tests for your new functionality:

```python
async def test_my_new_tool():
    my_component = MyComponent()
    result = await my_component.my_new_tool("test", 42)
    assert result == expected_result
```

4. **Update Documentation**

Update relevant documentation files with information about your new tool.

### Adding a New Component

1. **Create a New Module**

Create a new Python file for your component:

```python
# my_component.py
class MyComponent:
    def __init__(self, bundle_manager):
        self.bundle_manager = bundle_manager
        
    async def some_function(self, params):
        # Implementation
        return result
```

2. **Update the MCP Server**

Modify `server.py` to include your new component:

```python
from troubleshoot_mcp_server.my_component import MyComponent

# In MCPServer.__init__:
self.my_component = MyComponent(self.bundle_manager)
self.register_my_component_tools(self.my_component)
```

3. **Add Tests and Documentation**

Create tests and update documentation as described above.

## Contributing

Contributions are welcome! Please follow these steps:

1. Create a branch for your changes: `git checkout -b feature/my-feature`
2. Make your changes and add tests
3. Ensure all tests pass and code is formatted correctly
4. Create a pull request with a detailed description of your changes

Before submitting a pull request, please:
- Follow the Python style guide (PEP 8)
- Include type annotations
- Write comprehensive tests
- Update relevant documentation