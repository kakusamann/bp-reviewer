"""Hierarchical-blueprint integration checks with synthetic read-only projects."""

import hashlib

from bpreviewer.agent import _build_graph, _index_project, _label_domains
from bpreviewer.scanner import directory_tree, scan
from bpreviewer.structure import domain_for_path, project_catalog


def _project(root):
    folders = [
        root / "backend" / "src" / "demo" / "ontology",
        root / "backend" / "src" / "demo" / "matching",
        root / "backend" / "src" / "demo" / "api",
        root / "frontend" / "src",
        root / "tests",
    ]
    for folder in folders:
        folder.mkdir(parents=True)
    files = {
        "backend/src/demo/ontology/builder.py": "def build_graph(): return 1\n",
        "backend/src/demo/matching/retriever.py": "def retrieve(): return 2\n",
        "backend/src/demo/api/router.py": "from demo.ontology.builder import build_graph\n",
        "frontend/src/Panel.tsx": "export const Panel = () => null;\n",
        "tests/test_example.py": "def test_example(): pass\n",
    }
    for relative, content in files.items():
        (root / relative).write_text(content, encoding="utf-8")
    return files


def test_whole_project_index_and_domain_labels_are_stable(tmp_path, monkeypatch):
    files = _project(tmp_path)
    snapshot = scan(tmp_path)
    before = {path: hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() for path in files}
    tree = directory_tree(tmp_path)
    catalog = project_catalog(snapshot, tree["entries"])
    keys = {item["key"] for item in catalog}
    assert domain_for_path("backend/src/demo/ontology/builder.py") == "source:backend/src/demo/ontology"
    assert "source:backend/src/demo/matching" in keys
    assert "support:tests" in keys
    assert "source:frontend/src" in keys
    state = _index_project({"root": str(tmp_path), "snapshot": snapshot.model_dump()})
    assert state["catalog"]

    def fake_mimo(_prompt, payload, max_tokens):
        assert max_tokens == 2000
        assert all("sample_paths" in group and "key" in group for group in payload["areas"])
        return {"areas": [{"key": "source:backend/src/demo/ontology",
                           "label": "本体构建", "description": "管理图谱本体"}]}

    monkeypatch.setattr("bpreviewer.agent._call_mimo", fake_mimo)
    names = _label_domains(state)["domain_labels"]
    assert names["source:backend/src/demo/ontology"]["label"] == "本体构建"
    assert before == {path: hashlib.sha256((tmp_path / path).read_bytes()).hexdigest() for path in files}


def test_unanalyzed_files_are_evidence_not_root_logic_nodes(tmp_path):
    _project(tmp_path)
    snapshot = scan(tmp_path)
    path = "backend/src/demo/ontology/builder.py"
    summaries = [{"path": path, "role": "builder", "summary": "Generate graph",
                  "inputs": [], "outputs": [], "operations": [], "confidence": 0.8,
                  "evidence_lines": [1]}]
    proposal = {"modules": [{"key": "builder", "name": "实例构建", "description": "Graph building",
                             "member_paths": [path], "inputs": [], "outputs": [],
                             "confidence": 0.8}], "edges": []}
    graph, _, meta = _build_graph(
        tmp_path, snapshot, summaries, proposal, [],
        domain_labels={"source:backend/src/demo/ontology": {
            "label": "本体与图谱", "description": "Graph model structures",
        }},
    )
    root_nodes = [node for node in graph.nodes if not node.parent]
    assert all("未分析文件" not in node.label for node in root_nodes)
    assert all(node.kind == "logical_module" for node in root_nodes)
    area = next(node for node in graph.nodes if node.label == "本体与图谱")
    semantic = next(node for node in graph.nodes if node.label == "实例构建")
    assert semantic.parent == area.id
    assert next(node for node in graph.nodes if node.id == f"file:{path}").parent == semantic.id
    other = next(node for node in graph.nodes
                 if node.id == "file:backend/src/demo/matching/retriever.py")
    assert other.parent.startswith("agent:domain:")
    assert any(node.label == "frontend/src/Panel.tsx" and node.parent.startswith("agent:domain:")
               for node in graph.nodes)
    assert meta["hierarchy"]["suppressed_directory_groups"] > 0
    assert meta["project_domains"] >= 3
    assert len({node.id for node in graph.nodes}) == len(graph.nodes)
    assert len({edge.id for edge in graph.edges}) == len(graph.edges)
