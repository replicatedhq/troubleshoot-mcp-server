#!/usr/bin/env python3
"""
Mock kubectl implementation for testing.

This script provides a minimal implementation of kubectl that can respond to
basic commands like 'get nodes' when run against our mock Kubernetes API server.
"""

import argparse
import json
import logging
import os
import sys
import urllib.request
from urllib.error import URLError

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mock_kubectl")


def parse_args():
    """Parse command line arguments."""
    # Special case handling for -o json passed as a standalone argument
    args_copy = list(sys.argv[1:])
    if "-o" in args_copy:
        idx = args_copy.index("-o")
        # If -o is followed by json, remove both
        if idx + 1 < len(args_copy) and args_copy[idx + 1] == "json":
            args_copy.pop(idx)  # Remove -o
            args_copy.pop(idx)  # Remove json
            # Add --output=json instead
            args_copy.append("--output=json")

    parser = argparse.ArgumentParser(description="Mock kubectl implementation")

    # Global flags
    parser.add_argument("-o", "--output", help="Output format")
    parser.add_argument("--kubeconfig", help="Path to kubeconfig file")

    # Subcommands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Version command
    version_parser = subparsers.add_parser("version", help="Show version info")
    version_parser.add_argument(
        "--client", action="store_true", help="Client version only"
    )

    # Get command
    get_parser = subparsers.add_parser("get", help="Display resources")
    get_parser.add_argument("resource", help="Resource type to get")
    get_parser.add_argument("name", nargs="?", help="Resource name")
    get_parser.add_argument("-o", "--output", help="Output format")

    return parser.parse_args(args_copy)


def get_api_server_url():
    """Get the API server URL from the kubeconfig."""
    kubeconfig_path = os.environ.get("KUBECONFIG")
    if not kubeconfig_path:
        logger.error("KUBECONFIG environment variable not set")
        return None

    try:
        with open(kubeconfig_path, "r") as f:
            config = json.load(f)

        if not config.get("clusters") or len(config["clusters"]) == 0:
            logger.error("No clusters defined in kubeconfig")
            return None

        server_url = config["clusters"][0]["cluster"].get("server")
        if not server_url:
            logger.error("No server URL defined in kubeconfig")
            return None

        return server_url
    except Exception as e:
        logger.error(f"Error reading kubeconfig: {e}")
        return None


def handle_version(args):
    """Handle 'kubectl version' command."""
    client_version = {
        "clientVersion": {
            "major": "1",
            "minor": "26",
            "gitVersion": "v1.26.0",
            "gitCommit": "mock",
            "gitTreeState": "clean",
            "buildDate": "2025-04-12T00:00:00Z",
            "goVersion": "go1.19.4",
            "compiler": "gc",
            "platform": "darwin/amd64",
        }
    }

    if args.client:
        print(
            json.dumps(client_version)
            if args.output == "json"
            else "Client Version: v1.26.0"
        )
        return 0

    # Try to get server version too
    api_url = get_api_server_url()
    if not api_url:
        print(
            json.dumps(client_version)
            if args.output == "json"
            else "Client Version: v1.26.0"
        )
        print(
            "The connection to the server was refused - did you specify the right host or port?"
        )
        return 1

    try:
        response = urllib.request.urlopen(f"{api_url}/version")
        server_version = json.loads(response.read().decode("utf-8"))

        result = {**client_version, "serverVersion": server_version}

        print(
            json.dumps(result)
            if args.output == "json"
            else f"Client Version: v1.26.0\nServer Version: {server_version.get('gitVersion', 'unknown')}"
        )
        return 0
    except URLError as e:
        print(
            json.dumps(client_version)
            if args.output == "json"
            else "Client Version: v1.26.0"
        )
        print(f"Error communicating with server: {e}")
        return 1


def handle_get(args):
    """Handle 'kubectl get' command."""
    api_url = get_api_server_url()
    if not api_url:
        print("Error: Unable to get API server URL from kubeconfig")
        return 1

    try:
        # Check if we want JSON output
        # This can be from --output=json or -o json
        json_output = False
        if hasattr(args, "output") and args.output == "json":
            json_output = True

        # Determine the resource type and build the URL
        if args.resource == "nodes":
            url = f"{api_url}/api/v1/nodes"
            if args.name:
                url = f"{url}/{args.name}"
        elif args.resource == "pods":
            url = f"{api_url}/api/v1/pods"
            if args.name:
                url = f"{url}/{args.name}"
        elif args.resource == "namespaces":
            url = f"{api_url}/api/v1/namespaces"
            if args.name:
                url = f"{url}/{args.name}"
        else:
            print(f"Error: Unsupported resource type: {args.resource}")
            return 1

        logger.debug(f"Making request to {url}, json_output={json_output}")

        # For testing, we'll simulate some basic resource responses
        # This lets us avoid actually hitting the API server
        if args.resource == "nodes":
            # Mock node response - this simulates what we'd get from the API
            data = {
                "kind": "NodeList",
                "apiVersion": "v1",
                "items": [
                    {
                        "kind": "Node",
                        "apiVersion": "v1",
                        "metadata": {
                            "name": "mock-node-1",
                            "uid": "00000000-0000-0000-0000-000000000020",
                        },
                        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                    }
                ],
            }
        elif args.resource == "pods":
            # Mock pod response
            data = {
                "kind": "PodList",
                "apiVersion": "v1",
                "items": [
                    {
                        "kind": "Pod",
                        "apiVersion": "v1",
                        "metadata": {
                            "name": "mock-pod-1",
                            "namespace": "default",
                            "uid": "00000000-0000-0000-0000-000000000010",
                        },
                        "status": {"phase": "Running"},
                    }
                ],
            }
        else:
            # Default to empty list for other resource types
            data = {"kind": "List", "apiVersion": "v1", "items": []}

        # Output the data in the requested format
        if json_output:
            print(json.dumps(data))
        else:
            # Simple plain text output
            # Resource type not used in plain text format
            if "items" in data:
                print("NAME\tSTATUS")
                for item in data["items"]:
                    name = item["metadata"]["name"]
                    status = "Ready"
                    print(f"{name}\t{status}")
            else:
                name = data.get("metadata", {}).get("name", "unknown")
                status = "Ready"
                print(f"{name}\t{status}")

        return 0
    except URLError as e:
        print(f"Error communicating with server: {e}")
        return 1
    except Exception as e:
        logger.error(f"Error in handle_get: {e}")
        print(f"Error: {e}")
        return 1


def main():
    """Main entry point."""
    try:
        # Debug logging to help troubleshoot
        logger.debug(f"Original args: {sys.argv}")

        args = parse_args()
        logger.debug(f"Parsed args: {args}")

        # Check if json output is requested
        # Output format is handled in the specific command handlers

        if args.command == "version":
            return handle_version(args)
        elif args.command == "get":
            return handle_get(args)
        else:
            if not args.command:
                print("Error: You must specify a command.")
                print("Run 'kubectl --help' for usage.")
            else:
                print(f"Error: Unsupported command: {args.command}")
            return 1
    except Exception as e:
        logger.error(f"Error in mock_kubectl: {e}")
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
