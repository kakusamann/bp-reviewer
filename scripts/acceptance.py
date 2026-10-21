"""Read-only end-to-end acceptance against a synthetic and optional external project."""

import argparse
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from bpreviewer.api import create_app

parser = argparse.ArgumentParser()
parser.add_argument("--project", type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
example = root / "examples/tiny_pipeline"
state = root / "state/acceptance"
targets = [example] + ([args.project.resolve()] if args.project else [])
app = create_app(state, targets, "synthetic-acceptance-token")
client = TestClient(app, headers={"Authorization": "Bearer synthetic-acceptance-token"})
summary = []
for target in targets:
    files = [
        p
        for p in target.rglob("*.py")
        if not any(x in p.parts for x in ("workplace", "__pycache__", "node_modules", ".git"))
    ]
    before = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    response = client.post("/api/projects/scan", json={"path": str(target)})
    assert response.is_success, response.text
    response.raise_for_status()
    snapshot = response.json()
    graph = snapshot["graph"]
    if target == example:
        edge = next(e for e in graph["edges"] if e["kind"] == "data")
        edge["target_port"] = "B"
        result = client.post(f"/api/snapshots/{snapshot['id']}/change-request", json={"graph": graph})
        result.raise_for_status()
        review = result.json()
        assert [op["op"] for op in review["json"]["operations"]] == ["remove_edge", "add_edge"]
        (state / "rewire.json").write_text(json.dumps(review["json"], ensure_ascii=False, indent=2), encoding="utf-8")
        (state / "rewire.md").write_text(review["markdown"], encoding="utf-8")
    after = {str(p.relative_to(target)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    assert before == after, "Source was modified"
    summary.append(
        {
            "project": target.name,
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
            "git_head": snapshot["git_head"],
            "source_hashes_unchanged": True,
            "statuses": sorted({e["status"] for e in graph["edges"]}),
            "warnings": snapshot["warnings"],
        }
    )
(state / "result.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(summary, ensure_ascii=True, indent=2))
