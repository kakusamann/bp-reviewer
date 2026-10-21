import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bpreviewer.api import create_app
from bpreviewer.models import Edge, Graph, Manifest, Node, Port
from bpreviewer.review import change_request, validate
from bpreviewer.scanner import scan
from bpreviewer.service import Store

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/tiny_pipeline"


def fingerprints(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    }


def test_non_executing_ast_and_read_only(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "entry.py").write_text(
        "raise RuntimeError('NEVER RUN')\ndef source(x): return x\ndef sink(x): return source(x)\n"
    )
    before = fingerprints(project)
    snapshot = scan(project)
    assert len(snapshot.graph.nodes) == 3
    assert snapshot.graph.edges[0].status == "STATIC_INFERRED"
    assert snapshot.graph.edges[0].evidence[0].sha256 == before["entry.py"]
    assert fingerprints(project) == before


def test_rewire_exports_removal_addition_and_preserves_baseline(tmp_path):
    before = fingerprints(EXAMPLE)
    store = Store(tmp_path / "state", [EXAMPLE])
    snapshot = store.inspect(str(EXAMPLE))
    proposed = snapshot.graph.model_copy(deep=True)
    edge = next(e for e in proposed.edges if e.kind == "data")
    edge.target_port = "B"  # same edge ID; endpoint semantics determine the diff
    edge.status = "RUN_OBSERVED"  # client cannot promote a proposal to a fact
    result = change_request(snapshot, proposed)
    assert result == change_request(snapshot, proposed)
    operations = result["json"]["operations"]
    assert [o["op"] for o in operations] == ["remove_edge", "add_edge"]
    assert operations[0]["before"]["target_port"] == "A"
    assert operations[1]["after"]["target_port"] == "B"
    assert operations[1]["after"]["status"] == "UNVERIFIED"
    assert "logical:summary:B" in result["markdown"]
    assert result["json"]["baseline_evidence"]
    saved = store.save_draft(snapshot.id, proposed, 0)
    assert saved["revision"] == 1
    with pytest.raises(RuntimeError, match="conflict"):
        store.save_draft(snapshot.id, proposed, 0)
    assert next(e for e in store.snapshot(snapshot.id).graph.edges if e.kind == "data").target_port == "A"
    assert fingerprints(EXAMPLE) == before


def test_layout_does_not_change_semantics():
    snapshot = scan(EXAMPLE)
    proposed = snapshot.graph.model_copy(deep=True)
    proposed.nodes[0].position = {"x": 200, "y": 300}
    assert change_request(snapshot, proposed)["json"]["operations"] == []


def test_multiple_imports_and_redefined_functions_have_unique_ids(tmp_path):
    (tmp_path / "other.py").write_text("def a(): pass\ndef b(): pass\n")
    (tmp_path / "app.py").write_text("from other import a, b\ndef repeated(): pass\ndef repeated(): pass\n")
    snapshot = scan(tmp_path)
    assert len({n.id for n in snapshot.graph.nodes}) == len(snapshot.graph.nodes)
    assert len({e.id for e in snapshot.graph.edges}) == len(snapshot.graph.edges)


def typed_graph():
    return Graph(
        nodes=[
            Node(id="s", label="s", ports=[Port(id="out", io="output")]),
            Node(id="t", label="t", ports=[Port(id="A", io="input", multiplicity="one")]),
        ],
        edges=[Edge(id="e", source="s", source_port="out", target="t", target_port="A")],
    )


def test_unknown_not_claimed_compatible_and_explicit_contract_checks():
    graph = typed_graph()
    result = validate(graph)
    assert result["valid"] and "UNKNOWN_TYPE" in result["warnings"][0]
    graph.nodes[0].ports[0].data_type = "number"
    graph.nodes[1].ports[0].data_type = "text"
    assert not validate(graph)["valid"]
    graph.nodes[1].ports[0].data_type = "number"
    graph.nodes[0].ports[0].schema_ref = "v1"
    graph.nodes[1].ports[0].schema_ref = "v2"
    assert any("schema" in e for e in validate(graph)["errors"])
    graph.nodes[1].ports[0].schema_ref = "v1"
    graph.nodes[1].ports[0].semantics = {"unit": "kg"}
    assert any("semantics" in e for e in validate(graph)["errors"])


def test_cardinality_direction_required_and_cycles():
    graph = typed_graph()
    graph.edges.append(graph.edges[0].model_copy(update={"id": "duplicate"}))
    assert any("single input" in e for e in validate(graph)["errors"])
    graph.edges = []
    graph.nodes[1].ports[0].required = True
    assert any("required" in e for e in validate(graph)["errors"])
    graph = typed_graph()
    graph.nodes[0].ports.append(Port(id="in", io="input"))
    graph.nodes[1].ports.append(Port(id="out", io="output"))
    graph.edges.append(Edge(id="back", source="t", source_port="out", target="s", target_port="in"))
    assert any("cycle" in e for e in validate(graph)["errors"])
    graph.edges[0].source_port = "in"
    assert any("output" in e for e in validate(graph)["errors"])


def test_manifest_schema_limits_and_no_fake_runtime(tmp_path):
    manifest = {"version": "1", "graph": {"nodes": [], "edges": []}}
    (tmp_path / "bpreviewer.manifest.json").write_text(json.dumps(manifest))
    assert scan(tmp_path).require_dag
    assert Manifest.model_json_schema()["properties"]["version"]
    with pytest.raises(ValueError):
        scan(tmp_path, "../escape.json")
    with pytest.raises(ValueError):
        Graph(nodes=[Node(id="n", label="a"), Node(id="n", label="b")])


def test_file_depth_limits_and_hidden_data_exclusion(tmp_path):
    (tmp_path / "one.py").write_text("def one(): pass")
    (tmp_path / "two.py").write_text("def two(): pass")
    (tmp_path / "workplace").mkdir()
    (tmp_path / "workplace/secret.py").write_text("raise Exception()")
    snapshot = scan(tmp_path, max_files=1)
    assert any("limit" in w for w in snapshot.warnings)
    assert not any("secret" in n.id for n in snapshot.graph.nodes)


def test_symlink_escape(tmp_path):
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1")
    project = tmp_path / "project"
    project.mkdir()
    try:
        (project / "linked.py").symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink permission unavailable")
    assert not scan(project).graph.nodes


def test_project_isolation_and_state_boundary(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "same.py").write_text("def f(): pass")
    (b / "same.py").write_text("def f(): pass")
    store = Store(tmp_path / "state", [a, b])
    assert store.inspect(str(a)).id != store.inspect(str(b)).id
    with pytest.raises(PermissionError):
        store.inspect(str(tmp_path))
    with pytest.raises(ValueError):
        Store(a / "state", [a]).inspect(str(a))


def test_api_security_drafts_export_source_and_hashes(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def app(x): return x\n")
    app = create_app(tmp_path / "state", [project], "local-test-token")
    client = TestClient(app)
    assert client.get("/api/projects").status_code == 401
    client.headers["Authorization"] = "Bearer local-test-token"
    assert client.get("/api/projects", headers={"Origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/api/projects", headers={"Host": "untrusted.example"}).status_code == 403
    assert client.post("/api/projects/scan", json={"path": str(tmp_path)}).status_code == 403
    response = client.post("/api/projects/scan", json={"path": str(project)})
    assert response.status_code == 200, response.text
    snapshot = response.json()
    key = snapshot["id"]
    url = f"/api/snapshots/{key}"
    assert client.get(url + "/source", params={"path": "../secret"}).status_code == 403
    source = client.get(url + "/source", params={"path": "app.py"}).json()
    assert not source["stale"] and "def app" in source["content"]
    before = fingerprints(project)
    assert client.put(url + "/draft", json={"graph": snapshot["graph"], "revision": 0}).status_code == 200
    assert client.put(url + "/draft", json={"graph": snapshot["graph"], "revision": 0}).status_code == 409
    assert client.post(url + "/change-request", json={"graph": snapshot["graph"]}).json()["json"]["operations"] == []
    assert fingerprints(project) == before
    (project / "app.py").write_text("def app(x): return x+1\n")
    assert client.get(url + "/source", params={"path": "app.py"}).json()["stale"]


def test_environment_is_independent():
    assert importlib.util.find_spec("astralca") is None
    for package in ("torch", "torch_geometric", "bw2data", "neo4j"):
        assert importlib.util.find_spec(package) is None
