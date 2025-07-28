#!/usr/bin/env python3
"""
Mock sbctl implementation for testing.

This script provides a minimal implementation of sbctl that creates
a kubeconfig file and starts a minimal HTTP server to respond to
Kubernetes API requests.

Usage:
  python mock_sbctl.py serve --support-bundle-location PATH
"""

# Enable verbose debugging
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],  # Ensure stderr is used for logging
)
logger = logging.getLogger("mock_sbctl")
logger.setLevel(logging.DEBUG)
logger.debug("Mock sbctl starting")

import argparse
import http.server
import json
import os
import signal
import socketserver
import sys
import threading
import time
from pathlib import Path


class KubeAPIHandler(http.server.BaseHTTPRequestHandler):
    """Simple HTTP handler for mock Kubernetes API server."""

    def do_GET(self):
        """Handle GET requests."""
        # Respond to health checks
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
            return

        # Respond to version check
        if self.path == "/version" or self.path == "/api":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {
                "kind": "APIVersions",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [
                    {"clientCIDR": "0.0.0.0/0", "serverAddress": "localhost:8091"}
                ],
            }
            self.wfile.write(json.dumps(response).encode())
            return

        # API v1 resources
        if self.path == "/api/v1":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            response = {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": "v1",
                "resources": [
                    {
                        "name": "pods",
                        "singularName": "",
                        "namespaced": True,
                        "kind": "Pod",
                        "verbs": ["get", "list"],
                    },
                    {
                        "name": "nodes",
                        "singularName": "",
                        "namespaced": False,
                        "kind": "Node",
                        "verbs": ["get", "list"],
                    },
                    {
                        "name": "namespaces",
                        "singularName": "",
                        "namespaced": False,
                        "kind": "Namespace",
                        "verbs": ["get", "list"],
                    },
                ],
            }
            self.wfile.write(json.dumps(response).encode())
            return

        # Return empty list for most API requests
        if self.path.startswith("/api/") or self.path.startswith("/apis/"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()

            # Get specific resources
            if self.path == "/api/v1/namespaces":
                self.wfile.write(
                    json.dumps(
                        {
                            "kind": "NamespaceList",
                            "apiVersion": "v1",
                            "items": [
                                {
                                    "kind": "Namespace",
                                    "apiVersion": "v1",
                                    "metadata": {
                                        "name": "default",
                                        "uid": "00000000-0000-0000-0000-000000000000",
                                    },
                                    "status": {"phase": "Active"},
                                },
                                {
                                    "kind": "Namespace",
                                    "apiVersion": "v1",
                                    "metadata": {
                                        "name": "kube-system",
                                        "uid": "00000000-0000-0000-0000-000000000001",
                                    },
                                    "status": {"phase": "Active"},
                                },
                            ],
                        }
                    ).encode()
                )
                return
            elif self.path == "/api/v1/pods" or "/pods" in self.path:
                self.wfile.write(
                    json.dumps(
                        {
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
                    ).encode()
                )
                return
            elif self.path == "/api/v1/nodes" or "/nodes" in self.path:
                self.wfile.write(
                    json.dumps(
                        {
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
                                    "status": {
                                        "conditions": [
                                            {"type": "Ready", "status": "True"}
                                        ]
                                    },
                                }
                            ],
                        }
                    ).encode()
                )
                return
            else:
                # Generic empty list for other resources
                self.wfile.write(
                    json.dumps(
                        {"kind": "List", "apiVersion": "v1", "items": []}
                    ).encode()
                )
                return

        # Default response
        self.send_response(404)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "kind": "Status",
                    "apiVersion": "v1",
                    "metadata": {},
                    "status": "Failure",
                    "message": f"path not found: {self.path}",
                    "reason": "NotFound",
                    "code": 404,
                }
            ).encode()
        )

    def log_message(self, format, *args):
        """Override logging to be minimal."""
        print(f"Mock API server: {format % args}")


def find_free_port():
    """Find a free port to use for the API server."""
    import socket

    # Get a socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("", 0))  # Bind to a free port provided by the OS
    port = s.getsockname()[1]  # Get the port number
    s.close()

    logger.debug(f"Found free port: {port}")
    return port


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP Server with socket reuse capabilities."""

    allow_reuse_address = True  # Allow quick reuse of sockets
    daemon_threads = (
        True  # Daemon threads terminate automatically when main thread exits
    )


def start_mock_api_server(port=None):
    """Start a mock Kubernetes API server."""
    handler = KubeAPIHandler

    # If no port is specified, find a free one
    if port is None:
        port = find_free_port()

    # Try to bind to the port
    try:
        logger.debug(f"Attempting to create TCP server on port {port}")
        httpd = ThreadedTCPServer(("", port), handler)
        logger.info(f"Successfully started mock Kubernetes API server on port {port}")

        # Store the actual port used in an environment variable so the test can find it
        os.environ["MOCK_K8S_API_PORT"] = str(port)

        # Start in a separate thread so we can keep the main thread running
        server_thread = threading.Thread(target=httpd.serve_forever)
        server_thread.daemon = True
        server_thread.start()
        logger.debug(f"API server thread started on port {port}")

        return httpd
    except OSError as e:
        logger.error(f"Failed to bind to port {port}: {e}")

        # Try once more with a different port
        new_port = find_free_port()
        logger.debug(f"Retrying with port {new_port}")

        try:
            httpd = ThreadedTCPServer(("", new_port), handler)
            logger.info(
                f"Successfully started mock Kubernetes API server on port {new_port}"
            )

            # Store the actual port used in an environment variable
            os.environ["MOCK_K8S_API_PORT"] = str(new_port)

            # Start in a separate thread
            server_thread = threading.Thread(target=httpd.serve_forever)
            server_thread.daemon = True
            server_thread.start()
            logger.debug(f"API server thread started on port {new_port}")

            return httpd
        except OSError as e:
            logger.error(f"Failed to bind to alternate port {new_port}: {e}")
            raise RuntimeError(f"Failed to start API server on any port: {e}")


def create_kubeconfig(directory):
    """Create a mock kubeconfig file."""
    logger.debug(f"Creating kubeconfig in directory: {directory}")
    kubeconfig_path = Path(directory) / "kubeconfig"

    # Get the port from the environment variable, or use a default
    api_port = int(os.environ.get("MOCK_K8S_API_PORT", "8091"))
    logger.debug(f"Using API port {api_port} for kubeconfig")

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": "mock-cluster",
                "cluster": {"server": f"http://localhost:{api_port}"},
            }
        ],
        "contexts": [
            {
                "name": "mock-context",
                "context": {"cluster": "mock-cluster", "user": "mock-user"},
            }
        ],
        "current-context": "mock-context",
        "users": [{"name": "mock-user", "user": {}}],
    }

    with open(kubeconfig_path, "w") as f:
        json.dump(kubeconfig, f, indent=2)

    logger.info(
        f"Created kubeconfig at {kubeconfig_path} with API server at http://localhost:{api_port}"
    )
    return kubeconfig_path


def serve_bundle(bundle_path):
    """Serve a Kubernetes bundle."""
    logger.info(f"Starting mock sbctl server with bundle: {bundle_path}")

    # Start the mock API server first
    logger.debug("Starting API server before creating kubeconfig")
    api_server = start_mock_api_server()

    # Wait a moment for the server to be fully up
    time.sleep(0.5)

    # Create a kubeconfig in the current directory
    logger.debug(f"Current directory: {os.getcwd()}")
    # Generate kubeconfig but don't need to store the path
    create_kubeconfig(os.getcwd())

    # Create and write PID file to help manage process cleanup
    pid_file = Path(os.getcwd()) / "mock_sbctl.pid"
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    logger.info(f"Created PID file at {pid_file} with PID {os.getpid()}")

    # Set up signal handling for clean shutdown
    def handle_signal(sig, frame):
        logger.info(f"Received signal {sig}, shutting down mock API server...")
        try:
            api_server.shutdown()
            api_server.server_close()  # Ensure socket is properly closed
            if pid_file.exists():
                pid_file.unlink()  # Remove PID file on clean shutdown
            logger.info("API server shutdown complete")
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Keep running until interrupted
    try:
        logger.info("Mock sbctl server running - waiting for interruption")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down mock API server...")
        api_server.shutdown()
        api_server.server_close()  # Ensure socket is properly closed
        if pid_file.exists():
            pid_file.unlink()  # Remove PID file on clean shutdown


def main():
    """Parse arguments and run mock sbctl."""
    logger.debug("Starting mock sbctl command parser")
    parser = argparse.ArgumentParser(
        description="Mock sbctl implementation for testing"
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-command to execute")

    # Version command - creates 'version' subcommand
    subparsers.add_parser("version", help="Show version information")

    # Serve command
    serve_parser = subparsers.add_parser("serve", help="Start a mock API server")
    serve_parser.add_argument(
        "--support-bundle-location", required=True, help="Path to the support bundle"
    )

    args = parser.parse_args()
    logger.debug(f"Parsed arguments: {args}")

    if args.command == "version" or args.command is None:
        logger.info("Running version command")
        print("sbctl version 0.17.1-mock")
        return 0

    if args.command == "serve":
        logger.info("Running serve command")
        bundle_path = args.support_bundle_location

        # Verify the bundle exists
        if not os.path.isfile(bundle_path):
            logger.error(f"Support bundle file not found at {bundle_path}")
            print(f"Error: Support bundle file not found at {bundle_path}")
            return 1

        logger.debug(f"Bundle file exists at {bundle_path}")
        serve_bundle(bundle_path)
        return 0

    logger.error(f"Unknown command: {args.command}")
    print(f"Unknown command: {args.command}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
