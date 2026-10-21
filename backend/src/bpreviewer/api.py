"""Loopback API with independent access token and explicit filesystem allowlist."""

import secrets
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from .models import Graph, Manifest, Model
from .review import change_request, validate
from .scanner import safe_file
from .scanner import digest
from .service import Store


class Project(Model):
    path: str
    manifest: str | None = None


class Proposal(Model):
    graph: Graph
    revision: int = Field(default=0, ge=0)


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

    @app.get("/api/snapshots/{key}/source")
    def source(key: str, path: str, line: int = 1):
        import json

        snapshot = store.snapshot(key)
        evidence = [p for n in snapshot.graph.nodes for p in n.evidence if p.path == path]
        if not evidence:
            raise HTTPException(403, "Only indexed evidence can be viewed")
        root = Path(json.loads(store.path("sources", key).read_text(encoding="utf-8"))["root"]).resolve()
        if not any(root.is_relative_to(allowed) for allowed in store.roots):
            raise HTTPException(403, "Project is no longer allowed")
        file = safe_file(root, path)
        if file.stat().st_size > 1_000_000:
            raise ValueError("Source exceeds size limit")
        raw = file.read_bytes()
        first = max(1, line - 5)
        return {
            "path": path,
            "first_line": first,
            "stale": digest(raw) != evidence[0].sha256,
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
