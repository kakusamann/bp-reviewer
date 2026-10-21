"""Immutable snapshots and revisioned private drafts outside inspected trees."""

import json
import os
import re
import secrets
import threading
from pathlib import Path

from .models import Graph, Snapshot
from .scanner import scan


class Store:
    def __init__(self, state: Path, allowed_roots: list[Path]):
        self.state = state.resolve()
        self.roots = [p.resolve(strict=True) for p in allowed_roots]
        self.state.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def path(self, kind: str, key: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", key):
            raise ValueError("Invalid state identifier")
        directory = self.state / kind
        directory.mkdir(exist_ok=True)
        return directory / (key + ".json")

    def write(self, path: Path, value: dict):
        temp = path.with_suffix("." + secrets.token_hex(6) + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(temp, path)

    def inspect(self, source: str, manifest: str | None = None) -> Snapshot:
        requested = Path(source).expanduser()
        root = requested.resolve(strict=True)
        if not any(root.is_relative_to(allowed) for allowed in self.roots):
            raise PermissionError("Project is outside configured --allow-root directories")
        if self.state.is_relative_to(root) or root.is_relative_to(self.state):
            raise ValueError("State and inspected project must be separate")
        snapshot = scan(root, manifest)
        with self.lock:
            self.write(self.path("snapshots", snapshot.id), snapshot.model_dump())
            self.write(self.path("sources", snapshot.id), {"root": str(root)})
        return snapshot

    def snapshot(self, key: str) -> Snapshot:
        return Snapshot.model_validate_json(self.path("snapshots", key).read_text(encoding="utf-8"))

    def draft(self, key: str) -> dict:
        path = self.path("drafts", key)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {"snapshot_id": key, "revision": 0, "graph": self.snapshot(key).graph.model_dump()}

    def save_draft(self, key: str, graph: Graph, revision: int) -> dict:
        with self.lock:
            current = self.draft(key)
            if current["revision"] != revision:
                raise RuntimeError("Draft revision conflict; reload before saving")
            # Draft evidence is never promoted to verified baseline evidence.
            baseline = self.snapshot(key)
            old = {e.model_dump_json() for e in baseline.graph.edges}
            for edge in graph.edges:
                if edge.model_dump_json() not in old:
                    edge.status = "UNVERIFIED"
                    edge.evidence = []
            result = {"snapshot_id": key, "revision": revision + 1, "graph": graph.model_dump()}
            self.write(self.path("drafts", key), result)
            return result
