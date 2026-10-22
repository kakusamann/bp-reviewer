"""Regression tests for AI-generated duplicate IDs; no MiMo requests are made."""

from pathlib import Path

import pytest

from bpreviewer.agent import AgentExecutionError, _aggregate_modules, _build_graph
from bpreviewer.models import Edge
from bpreviewer.scanner import scan


def summary(path: str) -> dict:
    return {
        "path": path,
        "role": "test",
        "summary": "Synthetic example",
        "inputs": [],
        "outputs": [],
        "operations": [],
        "confidence": 0.7,
        "evidence_lines": [],
    }


def proposal() -> dict:
    return {
        "modules": [
            {
                "key": "upstream",
                "name": "Source",
                "description": "Read source",
                "member_paths": ["first.py"],
                "inputs": [],
                "outputs": [],
                "confidence": 0.8,
            },
            {
                "key": "downstream",
                "name": "Sink",
                "description": "Use source",
                "member_paths": ["second.py"],
                "inputs": [],
                "outputs": [],
                "confidence": 0.8,
            },
        ],
        "edges": [
            {
                "source_module": "upstream",
                "target_module": "downstream",
                "kind": "data",
                "evidence_paths": ["first.py"],
                "reason": "produces records",
            },
            {
                "source_module": "upstream",
                "target_module": "downstream",
                "kind": "data",
                "evidence_paths": ["second.py"],
                "reason": "consumes records",
            },
        ],
    }


def example_project(tmp_path: Path):
    (tmp_path / "first.py").write_text("from second import answer\n", encoding="utf-8")
    (tmp_path / "second.py").write_text("def answer(): return 42\n", encoding="utf-8")
    return scan(tmp_path)


def test_aggregate_merges_model_duplicate_edges_without_dropping_evidence(monkeypatch):
    def fake_mimo(_system_prompt, _payload, max_tokens):
        assert max_tokens == 2400
        return {
            "modules": [
                {"id": "upstream", "name": "Source", "member_paths": ["first.py"]},
                {"id": "downstream", "name": "Sink", "member_paths": ["second.py"]},
            ],
            "edges": [
                {
                    "source_module": "upstream", "target_module": "downstream", "kind": "data",
                    "evidence_paths": ["first.py"], "reason": "produces",
                },
                {
                    "source_module": "upstream", "target_module": "downstream", "kind": "data",
                    "evidence_paths": ["second.py"], "reason": "consumes",
                },
                {
                    "source_module": "upstream", "target_module": "downstream", "kind": "control",
                    "evidence_paths": ["first.py"], "reason": "invokes",
                },
            ],
        }

    monkeypatch.setattr("bpreviewer.agent._call_mimo", fake_mimo)
    result = _aggregate_modules({"summaries": [summary("first.py"), summary("second.py")]})
    edges = result["proposal"]["edges"]
    assert len(edges) == 2
    data = next(edge for edge in edges if edge["kind"] == "data")
    assert data["evidence_paths"] == ["first.py", "second.py"]
    assert "produces" in data["reason"] and "consumes" in data["reason"]


def test_aggregate_normalizes_duplicate_model_module_keys(monkeypatch):
    def fake_mimo(_system_prompt, _payload, max_tokens):
        return {
            "modules": [
                {"id": "repeated", "name": "First", "member_paths": ["first.py"]},
                {"id": "repeated", "name": "Second", "member_paths": ["second.py"]},
            ],
            "edges": [],
        }

    monkeypatch.setattr("bpreviewer.agent._call_mimo", fake_mimo)
    result = _aggregate_modules({"summaries": [summary("first.py"), summary("second.py")]})
    keys = [module["key"] for module in result["proposal"]["modules"]]
    assert keys == ["repeated", "repeated-2"]


def test_build_graph_deduplicates_edges_preserving_source_evidence(tmp_path):
    snapshot = example_project(tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in tmp_path.iterdir() if path.is_file()
    }
    graph, _, meta = _build_graph(
        tmp_path, snapshot, [summary("first.py"), summary("second.py")], proposal(), []
    )
    assert len({node.id for node in graph.nodes}) == len(graph.nodes)
    assert len({edge.id for edge in graph.edges}) == len(graph.edges)
    proposed = [edge for edge in graph.edges if edge.id.startswith("agent:proposal:")]
    assert len(proposed) == 1
    assert proposed[0].status == "UNVERIFIED"
    assert {proof.path for proof in proposed[0].evidence} == {"first.py", "second.py"}
    assert any("重复逻辑关系" in warning for warning in meta["warnings"])
    assert snapshot.graph.nodes[0].kind == "module"
    assert before == {
        path.name: path.read_bytes()
        for path in tmp_path.iterdir() if path.is_file()
    }


def test_build_graph_reports_true_id_collision_with_both_endpoints(tmp_path):
    snapshot = example_project(tmp_path)
    graph, _, _ = _build_graph(
        tmp_path, snapshot, [summary("first.py"), summary("second.py")], proposal(), []
    )
    generated = next(edge for edge in graph.edges if edge.id.startswith("agent:proposal:"))
    snapshot.graph.edges.append(
        Edge(
            id=generated.id,
            source="file:first.py",
            source_port="out",
            target="file:second.py",
            target_port="in",
            kind="import",
            status="STATIC_INFERRED",
        )
    )
    with pytest.raises(AgentExecutionError, match="关系 ID 冲突") as error:
        _build_graph(
            tmp_path, snapshot, [summary("first.py"), summary("second.py")], proposal(), []
        )
    assert "file:first.py" in str(error.value)
    assert "agent:module:" in str(error.value)
