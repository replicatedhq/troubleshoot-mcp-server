# Podman Usage Instructions

This document provides instructions for building and running the MCP server for Kubernetes support bundles in a Podman container. The container includes all required dependencies, including Python, `kubectl`, and `sbctl` (from replicatedhq/sbctl).

## Building the Container

Build the Podman container with the standard Podman build command:

```bash
# Navigate to the project directory
cd troubleshoot-mcp-server

# Build the image (uses melange/apko instead of Containerfile)
./scripts/build.sh
```

This will create a Podman image named `troubleshoot-mcp-server-dev:latest` for local development.

**Note**: Local development builds use the `-dev` suffix to avoid conflicts with official production releases. The production image is named `troubleshoot-mcp-server:latest`.

## Running the Container

Run the container directly with Podman, mounting your bundle storage directory and setting required environment variables:

```bash
# Create a directory for bundles (if it doesn't exist)
mkdir -p ./bundles

# Set the SBCTL_TOKEN environment variable for bundle operations
export SBCTL_TOKEN="your_token_here"

# Run the container
podman run -i --rm \
  -v "$(pwd)/bundles:/data/bundles" \
  -e SBCTL_TOKEN="$SBCTL_TOKEN" \
  troubleshoot-mcp-server-dev:latest
```

### Command Parameters Explained

- `-i`: Run in interactive mode (required for MCP protocol communication)
- `--rm`: Automatically remove the container when it exits
- `-v "$(pwd)/bundles:/data/bundles"`: Mount local bundle directory to container path
- `-e SBCTL_TOKEN="$SBCTL_TOKEN"`: Pass authentication token from environment

### Optional Parameters

- `--verbose`: Enable verbose logging: `-e MCP_LOG_LEVEL=DEBUG`
- `--port 8080`: Map container port: `-p 8080:8080`


## Configuration

The container can be configured using the following:

### Volume Mounts

- `/data/bundles`: Mount a local directory to store and access support bundles.

### Environment Variables

- `SBCTL_TOKEN`: Authentication token for accessing protected bundles.
- `MCP_BUNDLE_STORAGE`: Directory to store and manage bundles (defaults to `/data/bundles`).
- `MCP_LOG_LEVEL`: Set the logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).

## Testing the Container

You can run the container tests using pytest:

```bash
# Run the container tests
pytest tests/e2e/test_container.py -v
```

For a more comprehensive test of the MCP protocol:

```bash
# Run the MCP protocol tests
./scripts/test_mcp.sh
```

These tests:
1. Verify that the container builds and runs correctly
2. Test the Python environment in the container
3. Verify the MCP server CLI functionality
4. Test JSON-RPC communication with the MCP server

## Configuration with MCP Clients

To use the Podman container with MCP clients (such as Claude or other AI models), add the server configuration to your client's settings.

### MCP Client Configuration

You can get the recommended configuration by running:

```bash
podman run --rm troubleshoot-mcp-server-dev:latest --show-config
```

The output will provide a ready-to-use configuration for MCP clients:

```json
{
  "mcpServers": {
    "troubleshoot": {
      "command": "podman",
      "args": [
        "run",
        "-i",
        "--rm",
        "-v", 
        "${HOME}/bundles:/data/bundles",
        "-e",
        "SBCTL_TOKEN=${SBCTL_TOKEN}",
        "troubleshoot-mcp-server-dev:latest"
      ]
    }
  }
}
```

This configuration assumes:

1. You have the `SBCTL_TOKEN` environment variable set in your environment
2. You want to store bundles in `${HOME}/bundles` on your host machine
3. You're using Podman as your container runtime

Replace `${HOME}/bundles` with the actual path to your bundles directory if needed.

## Using the MCP Inspector

For interactive testing and exploration of the MCP server, we recommend using the [MCP Inspector](https://github.com/modelcontextprotocol/inspector), which provides a graphical interface for interacting with MCP servers.

Run the MCP Inspector directly using npx:

```bash
npx @modelcontextprotocol/inspector
```

In the Inspector UI:
1. Click "Add Server"
2. Enter a name for your server (e.g., "Troubleshoot Server")
3. For the launch command, use:
   ```
   podman run -i --rm \
     -v "$(pwd)/bundles:/data/bundles" \
     -e SBCTL_TOKEN="$SBCTL_TOKEN" \
     troubleshoot-mcp-server-dev:latest
   ```
4. Click "Save"

Now you can interact with your MCP server through the Inspector:
- Initialize a bundle
- Execute kubectl commands
- Explore files
- View rich responses

The MCP Inspector provides a much better experience than using raw JSON-RPC calls and helps you explore the available tools and their parameters.

### Using with an MCP Client

Configure your MCP client to use the server as shown in the Configuration section, then you can interact with it via your AI model.

Example prompt to Claude:
```
I need help troubleshooting my Kubernetes cluster. I have a support bundle at `/path/to/bundles/bundle-2025-04-11.tar.gz`. 
Can you analyze it for common issues?
```

## Troubleshooting

### Container Fails to Start

Check if:
- Podman is installed and running
- You have permissions to run Podman commands
- The required ports are available

### Cannot Access Bundle Files

Check if:
- The volume mount is correctly specified
- The bundle directory exists locally
- The container has the necessary permissions

### Authentication Errors

Check if:
- The `SBCTL_TOKEN` environment variable is correctly set
- The token has the required permissions for the bundle source

### JSON-RPC Communication Errors

Check if:
- The correct MCP protocol format is being used
- JSON is properly formatted in requests
- The tool name specified exists in the available tools list