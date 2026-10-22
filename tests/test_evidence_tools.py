"""Regression tests for read-only evidence tools and source-verified port inference."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bpreviewer.evidence_tools import collect_evidence, execute_tool, inspect_file
from bpreviewer.models import Evidence, Graph, Node, Port
from bpreviewer.relation_agent import _probe_mimo, enrich_dataflow


def test_ast_tools_do_not_execute_target_code_and_guard_scope(tmp_path: Path):
    producer = tmp_path / "producer.py"
    consumer = tmp_path / "consumer.py"
    producer.write_text(
        'raise RuntimeError("must never be run")\n'
        "def export(destination: str):\n"
        '    with open("shared.csv", "w") as handle:\n'
        '        handle.write("x")\n',
        encoding="utf-8",
    )
    consumer.write_text(
        'raise RuntimeError("must never be run")\n' "def load_data():\n" '    return read_csv("shared.csv")\n',
        encoding="utf-8",
    )
    initial = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in [producer, consumer]}
    report = inspect_file(tmp_path, "producer.py")
    assert report["functions"][0]["parameters"] == ["destination"]
    assert any(e["name"] == "shared.csv" and e["direction"] == "output" for e in report["io"])
    inputs = execute_tool(tmp_path, ["consumer.py"], "trace_data_io", {"path": "consumer.py"})
    assert inputs["io"][0]["direction"] == "input"
    with pytest.raises(ValueError, match="outside"):
        execute_tool(tmp_path, ["producer.py"], "trace_data_io", {"path": "consumer.py"})
    with pytest.raises(ValueError):
        execute_tool(tmp_path, ["producer.py"], "unlisted_tool", {"path": "producer.py"})
    collected = collect_evidence(tmp_path, ["producer.py", "consumer.py"])
    assert len(collected["files"]) == 2
    assert {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in [producer, consumer]} == initial


def test_static_input_output_ports_include_evidence_and_link(tmp_path: Path, monkeypatch):
    (tmp_path / "producer.py").write_text(
        'def make():\n    with open("shared.csv", "w") as handle:\n        handle.write("x")\n',
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        'def use():\n    return read_csv("shared.csv")\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MIMO_ENABLE_TOOL_CALLING", "false")
    port_in = Port(id="in", io="input", label="汇总输入")
    port_out = Port(id="out", io="output", label="汇总输出")
    graph = Graph(
        nodes=[
            Node(id="m1", label="原始数据", kind="logical_module", ports=[port_in, port_out]),
            Node(id="m2", label="消费数据", kind="logical_module", ports=[port_in, port_out]),
            Node(
                id="file:producer.py",
                label="producer.py",
                kind="code_file",
                parent="m1",
                evidence=[Evidence(path="producer.py")],
            ),
            Node(
                id="file:consumer.py",
                label="consumer.py",
                kind="code_file",
                parent="m2",
                evidence=[Evidence(path="consumer.py")],
            ),
        ]
    )
    enriched, info = enrich_dataflow(tmp_path, graph)
    assert info["ast_data_ports"] == 2
    assert info["ast_data_relations"] == 1
    assert info["agent_tool_calls"] == 0
    source = next(n for n in enriched.nodes if n.id == "m1")
    target = next(n for n in enriched.nodes if n.id == "m2")
    out = next(p for p in source.ports if p.semantics.get("artifact_path") == "shared.csv")
    inn = next(p for p in target.ports if p.semantics.get("artifact_path") == "shared.csv")
    assert out.io == "output" and inn.io == "input"
    assert out.data_type == "csv" and inn.data_type == "csv"
    assert out.semantics["evidence_path"] == "producer.py"
    edge = next(edge for edge in enriched.edges if edge.id.startswith("agent:ast-data:"))
    assert (edge.source, edge.source_port, edge.target, edge.target_port) == ("m1", out.id, "m2", inn.id)
    assert edge.status == "STATIC_INFERRED"
    assert {proof.path for proof in edge.evidence} == {"producer.py", "consumer.py"}


def test_ast_tools_reject_non_python_and_path_traversal(tmp_path: Path):
    (tmp_path / "README.md").write_text("ignored", encoding="utf-8")
    with pytest.raises(ValueError, match="Python"):
        inspect_file(tmp_path, "README.md")
    with pytest.raises((ValueError, FileNotFoundError, PermissionError)):
        inspect_file(tmp_path, "../escape.py")


def test_mimo_tool_call_is_bounded_and_investigates_real_files(tmp_path: Path, monkeypatch):
    (tmp_path / "example.py").write_text(
        'def load():\\n    return read_csv("inventory.csv")\\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MIMO_API_KEY", "fake-only-for-unit-test")
    observed = []

    def create(**kwargs):
        observed.append(kwargs)
        if len(observed) == 1:
            call = SimpleNamespace(
                id="tool-1",
                function=SimpleNamespace(name="trace_data_io", arguments='{"path":"example.py"}'),
            )
            message = SimpleNamespace(
                tool_calls=[call],
                model_dump=lambda **_kwargs: {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.function.name, "arguments": call.function.arguments},
                        }
                    ],
                },
            )
        else:
            message = SimpleNamespace(tool_calls=[], content='{"summary": "evidence collected"}')
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(
        "bpreviewer.relation_agent.OpenAI",
        lambda **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))),
    )
    assert _probe_mimo(tmp_path, ["example.py"]) == 1
    assert len(observed) == 2
    result_messages = observed[1]["messages"]
    tool_reply = next(m for m in result_messages if m["role"] == "tool")
    assert "inventory.csv" in tool_reply["content"]
    assert "example.py" in tool_reply["content"]
