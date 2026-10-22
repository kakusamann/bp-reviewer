"""Real progress API tests (no model calls or target-code execution)."""

import time

from fastapi.testclient import TestClient

from bpreviewer.api import create_app
from bpreviewer.models import Graph


def test_blueprint_job_reports_real_stage_and_returns_result(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "module.py").write_text("def transform(value): return value\n", encoding="utf-8")
    app = create_app(tmp_path / "state", [root], "test-token")
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer test-token"
    scanned = client.post("/api/projects/scan", json={"path": str(root)})
    assert scanned.status_code == 200
    snapshot = scanned.json()
    assert client.post(f"/api/snapshots/{snapshot['id']}/semantic-blueprint/jobs", json={},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401

    def fake_build(root, snapshot, state_dir, force_refresh=False, progress_callback=None):
        assert force_refresh
        assert progress_callback is not None
        progress_callback("index_project", "全项目索引完成", 1, 5)
        progress_callback("validate_and_build", "证据校验完成", 4, 5)
        return {"graph": snapshot.graph.model_dump(), "metadata": {"analyzed_files": 0}}

    monkeypatch.setattr("bpreviewer.api.build_semantic_blueprint", fake_build)
    key = snapshot["id"]
    created = client.post(f"/api/snapshots/{key}/semantic-blueprint/jobs", json={"force_refresh": True})
    assert created.status_code == 200, created.text
    job_id = created.json()["job_id"]
    assert client.get(f"/api/snapshots/{key}/semantic-blueprint/jobs/nonexistent").status_code == 404
    status = {}
    for _ in range(100):
        response = client.get(f"/api/snapshots/{key}/semantic-blueprint/jobs/{job_id}")
        assert response.status_code == 200
        status = response.json()
        if status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    assert status["status"] == "completed", status
    assert Graph.model_validate(status["result"]["graph"]).nodes
    assert status["stage"] == "complete"
