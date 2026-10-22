"""Loopback API with independent access token and explicit filesystem allowlist."""

import json
import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from .agent import AgentConfigurationError, AgentExecutionError, build_semantic_blueprint, rebuild_semantic_blueprint_scope
from .models import Graph, Manifest, Model
from .review import change_request, validate
from .scanner import directory_tree, digest, is_previewable, safe_file
from .service import Store


class Project(Model):
    path: str
    manifest: str | None = None


class Proposal(Model):
    graph: Graph
    revision: int = Field(default=0, ge=0)


class ScopedBlueprintRequest(Model):
    scope_node_id: str = Field(min_length=1, max_length=512)


def create_app(state: Path, allowed_roots: list[Path], token: str) -> FastAPI:
    store = Store(state, allowed_roots)

    def authorize(request: Request):
        if not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            raise HTTPException(401, "Enter the local BP-Reviewer access token")
        origin = request.headers.get("origin")
        if origin and origin not in {
            "http://127.0.0.1:5174",
            "http://localhost:5174",
            "http://127.0.0.1:8011",
            "http://localhost:8011",
        }:
            raise HTTPException(403, "Origin not allowed")

    app = FastAPI(title="BP-Reviewer", version="0.1.0", dependencies=[Depends(authorize)])
    app.state.store = store

    # No CORS wildcard; Vite uses a same-origin proxy. Reject DNS rebinding hosts.
    @app.middleware("http")
    async def boundary(request: Request, call_next):
        host = urlparse("http://" + request.headers.get("host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "Loopback Host required"}, status_code=403)
        if (
            request.headers.get("content-length", "0").isdigit()
            and int(request.headers.get("content-length", "0")) > 12_000_000
        ):
            return JSONResponse({"detail": "Request too large"}, status_code=413)
        try:
            return await call_next(request)
        except PermissionError as error:
            return JSONResponse({"detail": str(error)}, status_code=403)
        except FileNotFoundError:
            return JSONResponse({"detail": "Project or saved state unavailable"}, status_code=404)
        except ValueError as error:
            return JSONResponse({"detail": str(error)}, status_code=422)
        except RuntimeError as error:
            return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/projects")
    def projects():
        return {"allowed_roots": [str(p) for p in store.roots]}

    @app.get("/api/manifest-schema")
    def schema():
        return Manifest.model_json_schema()

    @app.post("/api/projects/scan")
    def inspect(project: Project):
        return store.inspect(project.path, project.manifest)

    @app.get("/api/snapshots/{key}")
    def snapshot(key: str):
        return store.snapshot(key)

    @app.get("/api/snapshots/{key}/draft")
    def draft(key: str):
        return store.draft(key)

    def project_root(key: str) -> Path:
        store.snapshot(key)
        source_path = store.path("sources", key)
        root = Path(json.loads(source_path.read_text(encoding="utf-8"))["root"]).resolve(strict=True)
        if not any(root.is_relative_to(allowed) for allowed in store.roots):
            raise HTTPException(403, "Project is no longer allowed")
        if not root.is_dir():
            raise HTTPException(404, "Project directory unavailable")
        return root

    @app.post("/api/snapshots/{key}/semantic-blueprint")
    def semantic_blueprint(key: str, force_refresh: bool = False):
        snapshot = store.snapshot(key)
        root = project_root(key)
        try:
            return build_semantic_blueprint(root, snapshot, store.state, force_refresh=force_refresh)
        except AgentConfigurationError as error:
            raise HTTPException(503, str(error)) from error
        except AgentExecutionError as error:
            raise HTTPException(502, str(error)) from error

    @app.post("/api/snapshots/{key}/semantic-blueprint/rebuild")
    def rebuild_semantic_scope(key: str, request: ScopedBlueprintRequest):
        snapshot = store.snapshot(key)
        root = project_root(key)
        try:
            return rebuild_semantic_blueprint_scope(
                root, snapshot, None, request.scope_node_id, store.state, force_refresh=True
            )
        except AgentConfigurationError as error:
            raise HTTPException(503, str(error)) from error
        except AgentExecutionError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/snapshots/{key}/tree")
    def tree(key: str):
        return directory_tree(project_root(key))

    @app.get("/api/snapshots/{key}/source")
    def source(key: str, path: str, line: int = 1):
        snapshot = store.snapshot(key)
        root = project_root(key)
        normalized_parts = path.replace("\\", "/").split("/")
        if path.startswith(("/", "\\")) or any(part == ".." for part in normalized_parts):
            raise HTTPException(403, "File path not allowed")
        file = safe_file(root, path)
        size = file.stat().st_size
        if not is_previewable(path, size):
            raise HTTPException(415, "File preview disabled for sensitive, binary, or oversized files")
        if size > 1_000_000:
            raise ValueError("Source exceeds size limit")
        raw = file.read_bytes()
        evidence = [p for n in snapshot.graph.nodes for p in n.evidence if p.path == path]
        first = max(1, line - 5)
        known_hashes = [p.sha256 for p in evidence if p.sha256]
        stale = bool(known_hashes) and not any(value == digest(raw) for value in known_hashes)
        return {
            "path": path,
            "first_line": first,
            "stale": stale,
            "indexed": bool(evidence),
            "content": "\n".join(raw.decode("utf-8", errors="replace").splitlines()[first - 1 : first + 79]),
        }

    @app.put("/api/snapshots/{key}/draft")
    def save(key: str, proposal: Proposal):
        return store.save_draft(key, proposal.graph, proposal.revision)

    @app.post("/api/snapshots/{key}/validate")
    def check(key: str, proposal: Proposal):
        return validate(proposal.graph, store.snapshot(key).require_dag)

    @app.post("/api/snapshots/{key}/change-request")
    def review(key: str, proposal: Proposal):
        return change_request(store.snapshot(key), proposal.graph)

    return app
