"""
HTTP REST API server for troubleshoot-mcp-server.

This provides a simple HTTP interface for Temporal workflows to interact with
the MCP server functionality without the complexity of MCP protocol or stdio/SSE transports.

Each workflow gets a unique bundle ID for complete isolation.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from .bundle import BundleManager
from .kubectl import KubectlExecutor, KubectlError
from .files import FileExplorer, FileSystemError
from .size_limiter import SizeLimiter
from .formatters import ResponseFormatter

logger = logging.getLogger(__name__)

# Server start time for uptime tracking
start_time = time.time()

# Maximum response size for Temporal compatibility (1.5 MB safe buffer below 2 MB limit)
MAX_RESPONSE_SIZE = 1_500_000


class BundleStore:
    """Thread-safe storage for bundle managers."""

    def __init__(self, base_dir: Path):
        self.bundles: dict[str, BundleManager] = {}
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.lock = Lock()
        logger.info(f"BundleStore initialized with base_dir: {base_dir}")

    async def create(self, url: str, token: str) -> tuple[str, BundleManager]:
        """Create new bundle and return ID."""
        bundle_id = f"bundle-{uuid.uuid4()}"
        bundle_dir = self.base_dir / bundle_id

        logger.info(f"Creating bundle {bundle_id} from {url}")

        # Create bundle manager
        manager = BundleManager(bundle_dir=bundle_dir)

        # Store before initialization
        with self.lock:
            self.bundles[bundle_id] = manager

        try:
            # Initialize bundle (slow operation, outside lock)
            # Pass token as parameter instead of manipulating environment (thread-safe)
            await manager.initialize_bundle(url, force=False, token=token)
            logger.info(f"Bundle {bundle_id} initialized successfully")
            return bundle_id, manager

        except Exception as e:
            # Remove on failure
            with self.lock:
                self.bundles.pop(bundle_id, None)
            logger.error(f"Failed to initialize bundle {bundle_id}: {e}")
            raise

    def get(self, bundle_id: str) -> Optional[BundleManager]:
        """Get bundle manager by ID."""
        with self.lock:
            return self.bundles.get(bundle_id)

    async def remove(self, bundle_id: str) -> Optional[BundleManager]:
        """Remove and cleanup bundle manager."""
        manager = None
        with self.lock:
            manager = self.bundles.pop(bundle_id, None)

        if manager:
            try:
                # Cleanup bundle resources
                await manager.cleanup_bundle(bundle_id)
                logger.info(f"Bundle {bundle_id} cleaned up")
            except Exception as e:
                logger.error(f"Error cleaning up bundle {bundle_id}: {e}")

        return manager

    def count(self) -> int:
        """Count active bundles."""
        with self.lock:
            return len(self.bundles)

    def list_ids(self) -> list[str]:
        """List all active bundle IDs."""
        with self.lock:
            return list(self.bundles.keys())


# Request/Response Models

class InitializeRequest(BaseModel):
    url: str
    token: str


class InitializeResponse(BaseModel):
    bundle_id: str
    status: str
    extracted_path: str


class KubectlRequest(BaseModel):
    args: list[str]


class KubectlResponse(BaseModel):
    output: str
    exit_code: int = 0


class FilesResponse(BaseModel):
    files: list[str]
    path: str


class FileContentResponse(BaseModel):
    content: str
    path: str
    size: int


class CleanupResponse(BaseModel):
    status: str
    bundle_id: str


class HealthResponse(BaseModel):
    status: str
    active_bundles: int
    bundle_ids: list[str]
    uptime_seconds: float


# Create FastAPI app

def create_app(bundle_dir: Path) -> FastAPI:
    """Create FastAPI application with bundle store."""
    app = FastAPI(
        title="Troubleshoot MCP HTTP Server",
        description="HTTP REST API for support bundle troubleshooting",
        version="1.0.0"
    )

    # Global bundle store
    bundle_store = BundleStore(base_dir=bundle_dir)

    @app.get("/health", response_model=HealthResponse)
    async def health_check():
        """Health check endpoint."""
        return HealthResponse(
            status="healthy",
            active_bundles=bundle_store.count(),
            bundle_ids=bundle_store.list_ids(),
            uptime_seconds=time.time() - start_time,
        )

    @app.post("/bundles/initialize", response_model=InitializeResponse, status_code=201)
    async def initialize_bundle(req: InitializeRequest):
        """
        Initialize a new support bundle from URL.

        Returns a unique bundle_id that should be used for all subsequent operations.
        """
        try:
            bundle_id, manager = await bundle_store.create(req.url, req.token)
            extracted_path = str(manager.bundle_dir / "extracted")

            return InitializeResponse(
                bundle_id=bundle_id,
                status="initialized",
                extracted_path=extracted_path,
            )
        except Exception as e:
            logger.exception(f"Failed to initialize bundle: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to initialize bundle: {str(e)}"
            )

    @app.post("/bundles/{bundle_id}/kubectl", response_model=KubectlResponse)
    async def kubectl_execute(bundle_id: str, req: KubectlRequest):
        """
        Execute kubectl command in the specified bundle.

        Args:
            bundle_id: Bundle identifier from initialize_bundle
            req: kubectl arguments to execute
        """
        manager = bundle_store.get(bundle_id)
        if not manager:
            raise HTTPException(
                status_code=404,
                detail=f"Bundle {bundle_id} not found"
            )

        try:
            kubectl = KubectlExecutor(bundle_manager=manager)
            # Join args into command string (kubectl.execute expects string, not list)
            command = " ".join(req.args)
            result = await kubectl.execute(command, timeout=30, json_output=False)

            # Use MINIMAL formatter for compact output (saves tokens!)
            formatter = ResponseFormatter("minimal")
            output = formatter.format_kubectl_result(result)

            # Apply token limiting (same as MCP protocol does via check_response_size)
            size_limiter = SizeLimiter()  # Default: 25K tokens
            if not size_limiter.check_size(output):
                # Output exceeds token limit - return summary instead
                output = size_limiter.get_overflow_summary(output, max_preview_chars=1000)

            # Apply Temporal size limiting (belt and suspenders)
            if len(output) > MAX_RESPONSE_SIZE:
                logger.warning(
                    f"kubectl output ({len(output)} bytes) exceeds limit ({MAX_RESPONSE_SIZE} bytes). "
                    f"Truncating response. Command: {command}"
                )

                # Truncate and add helpful message for the agent
                truncated = output[:MAX_RESPONSE_SIZE]
                output = (
                    f"{truncated}\n\n"
                    f"... [OUTPUT TRUNCATED]\n"
                    f"Response exceeded {MAX_RESPONSE_SIZE/1024/1024:.1f} MB limit (Temporal constraint).\n"
                    f"Original size: {len(result.output)/1024/1024:.2f} MB\n"
                    f"\n"
                    f"Suggestions to reduce output size:\n"
                    f"  - Use smaller --tail value (e.g., --tail=50 instead of --tail=200)\n"
                    f"  - Filter by specific container: --container=<name>\n"
                    f"  - Use grep to filter logs: get pods <name> -o yaml | grep <pattern>\n"
                    f"  - Query specific fields: -o jsonpath='{{...}}'\n"
                )

            return KubectlResponse(
                output=output,
                exit_code=result.exit_code,
            )
        except KubectlError as e:
            logger.error(f"kubectl error in bundle {bundle_id}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"kubectl execution failed: {str(e)}"
            )
        except Exception as e:
            logger.exception(f"Unexpected error executing kubectl in bundle {bundle_id}")
            raise HTTPException(
                status_code=500,
                detail=f"kubectl execution failed: {str(e)}"
            )

    @app.get("/bundles/{bundle_id}/files", response_model=FilesResponse)
    async def list_files(
        bundle_id: str,
        path: str = Query(default="", description="Path within bundle (defaults to bundle root)")
    ):
        """
        List files in the specified bundle path.

        Args:
            bundle_id: Bundle identifier from initialize_bundle
            path: Path within bundle (FileExplorer handles extraction path resolution)
        """
        manager = bundle_store.get(bundle_id)
        if not manager:
            raise HTTPException(
                status_code=404,
                detail=f"Bundle {bundle_id} not found"
            )

        try:
            # Use path as-is - FileExplorer's _get_bundle_path() handles /extracted resolution
            # and automatically finds support-bundle-xxx subdirectory
            file_explorer = FileExplorer(bundle_manager=manager)
            result = await file_explorer.list_files(path if path else "/", recursive=False)

            # Use MINIMAL formatter for compact output (saves tokens!)
            formatter = ResponseFormatter("minimal")
            formatted_output = formatter.format_file_list(result)

            # Parse the formatted JSON to get file names
            import json
            file_names = json.loads(formatted_output)

            return FilesResponse(
                files=file_names,
                path=path,  # Return original path so agent doesn't see /extracted prefix
            )
        except FileSystemError as e:
            logger.error(f"Filesystem error in bundle {bundle_id}: {e}")
            raise HTTPException(
                status_code=404 if "not found" in str(e).lower() else 500,
                detail=str(e)
            )
        except Exception as e:
            logger.exception(f"Unexpected error listing files in bundle {bundle_id}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to list files: {str(e)}"
            )

    @app.get("/bundles/{bundle_id}/files/content", response_model=FileContentResponse)
    async def read_file(
        bundle_id: str,
        path: str = Query(..., description="Path to file within bundle")
    ):
        """
        Read file content from the specified bundle.

        Args:
            bundle_id: Bundle identifier from initialize_bundle
            path: Path to file within bundle (FileExplorer handles extraction path resolution)
        """
        manager = bundle_store.get(bundle_id)
        if not manager:
            raise HTTPException(
                status_code=404,
                detail=f"Bundle {bundle_id} not found"
            )

        try:
            # Use path as-is - FileExplorer's _get_bundle_path() and _normalize_path() handle resolution
            file_explorer = FileExplorer(bundle_manager=manager)
            result = await file_explorer.read_file(path, start_line=0, end_line=None)

            # Block binary files - reading them returns hex dumps that fill context
            if result.binary:
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot read binary file: {path}. Binary files are not supported. Use file exploration tools for text files only."
                )

            # Use MINIMAL formatter for compact output (saves tokens!)
            formatter = ResponseFormatter("minimal")
            content = formatter.format_file_content(result)

            # Apply token limiting (same as MCP protocol does via check_response_size)
            size_limiter = SizeLimiter()  # Default: 25K tokens
            if not size_limiter.check_size(content):
                # Output exceeds token limit - return summary instead
                content = size_limiter.get_overflow_summary(content, max_preview_chars=1000)

            # Apply Temporal size limiting (belt and suspenders)
            original_size = len(content)
            if original_size > MAX_RESPONSE_SIZE:
                logger.warning(
                    f"File content ({original_size} bytes) exceeds limit ({MAX_RESPONSE_SIZE} bytes). "
                    f"Truncating response. Path: {path}"
                )

                # Truncate and add helpful message
                truncated = content[:MAX_RESPONSE_SIZE]
                content = (
                    f"{truncated}\n\n"
                    f"... [FILE TRUNCATED]\n"
                    f"File size ({original_size/1024/1024:.2f} MB) exceeded {MAX_RESPONSE_SIZE/1024/1024:.1f} MB limit.\n"
                    f"\n"
                    f"Suggestions:\n"
                    f"  - Use grep_files to search for specific content\n"
                    f"  - Read file in smaller chunks if the tool supports it\n"
                )

            return FileContentResponse(
                content=content,
                path=path,
                size=original_size,  # Return original size so caller knows it was truncated
            )
        except FileSystemError as e:
            logger.error(f"Filesystem error reading file in bundle {bundle_id}: {e}")
            raise HTTPException(
                status_code=404 if "not found" in str(e).lower() else 500,
                detail=str(e)
            )
        except Exception as e:
            logger.exception(f"Unexpected error reading file in bundle {bundle_id}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read file: {str(e)}"
            )

    @app.delete("/bundles/{bundle_id}", response_model=CleanupResponse)
    async def cleanup_bundle(bundle_id: str):
        """
        Cleanup bundle resources and remove bundle.

        Args:
            bundle_id: Bundle identifier to cleanup
        """
        manager = await bundle_store.remove(bundle_id)
        if not manager:
            raise HTTPException(
                status_code=404,
                detail=f"Bundle {bundle_id} not found"
            )

        return CleanupResponse(
            status="deleted",
            bundle_id=bundle_id,
        )

    return app


async def run_http_server(host: str = "0.0.0.0", port: int = 9000, bundle_dir: Path = Path("/tmp/bundles")):
    """
    Run the HTTP server.

    Args:
        host: Host to bind to
        port: Port to listen on
        bundle_dir: Base directory for bundle storage
    """
    import uvicorn

    app = create_app(bundle_dir)

    logger.info(f"Starting HTTP server on {host}:{port}")
    logger.info(f"Bundle storage: {bundle_dir}")

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
    )

    server = uvicorn.Server(config)
    await server.serve()
