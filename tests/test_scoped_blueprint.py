"""Scoped semantic blueprint rebuilds preserve all out-of-scope branches."""

from bpreviewer.agent import AgentExecutionError, rebuild_semantic_blueprint_scope
from bpreviewer.models import Edge, Graph, Node, Port
from bpreviewer.scanner import scan


def _write_project(root):
    files = {
        "src/kg/ontology.py": "def build_ontology():\n    return {}\n",
        "src/kg/instance.py": "def build_instances():\n    return []\n",
        "src/matching/retriever.py": "def retrieve():\n    return []\n",
    }
    for path, source in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return files


def _port_list():
    return [Port(id="in", io="input"), Port(id="out", io="output")]


def test_scoped_rebuild_replaces_only_selected_subtree(tmp_path, monkeypatch):
    _write_project(tmp_path)
    snapshot = scan(tmp_path)
    static_files = {
        node.evidence[0].path: node for node in snapshot.graph.nodes if node.id.startswith("file:") and node.evidence
    }
    kg_paths = ["src/kg/ontology.py", "src/kg/instance.py"]
    other_path = "src/matching/retriever.py"
    old_module = Node(
        id="agent:module:old-kg",
        label="旧图谱子模块",
        kind="logical_module",
        parent="domain:kg",
        ports=_port_list(),
        evidence=[],
        description="old",
    )
    other_module = Node(
        id="agent:module:other",
        label="背景匹配",
        kind="logical_module",
        parent="domain:matching",
        ports=_port_list(),
        evidence=[],
        description="untouched",
    )
    original_nodes = [
        Node(
            id="domain:kg",
            label="知识图谱",
            kind="logical_module",
            parent="",
            ports=_port_list(),
            parameters={"structural_group": True},
        ),
        Node(
            id="domain:matching",
            label="背景匹配",
            kind="logical_module",
            parent="",
            ports=_port_list(),
            parameters={"structural_group": True},
        ),
        old_module,
        other_module,
        *[
            static_files[path].model_copy(update={"kind": "code_file", "parent": "agent:module:old-kg"})
            for path in kg_paths
        ],
        static_files[other_path].model_copy(update={"kind": "code_file", "parent": "agent:module:other"}),
    ]
    original_edges = [
        Edge(
            id="old:contains:1",
            source=old_module.id,
            source_port="out",
            target="file:" + kg_paths[0],
            target_port="in",
            kind="contains",
        ),
        Edge(
            id="old:contains:2",
            source=old_module.id,
            source_port="out",
            target="file:" + kg_paths[1],
            target_port="in",
            kind="contains",
        ),
        Edge(
            id="other:contains",
            source=other_module.id,
            source_port="out",
            target="file:" + other_path,
            target_port="in",
            kind="contains",
        ),
    ]
    original = Graph(nodes=original_nodes, edges=original_edges)
    generated_domain = Node(
        id="agent:domain:temporary",
        label="临时功能域",
        kind="logical_module",
        parent="",
        ports=_port_list(),
        parameters={"structural_group": True},
    )
    new_module = Node(
        id="agent:module:new-kg",
        label="图谱实例化",
        kind="logical_module",
        parent=generated_domain.id,
        ports=_port_list(),
        description="new summary",
    )
    generated_nodes = [
        generated_domain,
        new_module,
        *[static_files[path].model_copy(update={"kind": "code_file", "parent": new_module.id}) for path in kg_paths],
    ]
    generated_edges = [
        Edge(
            id="temporary:domain-module",
            source=generated_domain.id,
            source_port="out",
            target=new_module.id,
            target_port="in",
            kind="contains",
        ),
        *[
            Edge(
                id=f"new:contains:{i}",
                source=new_module.id,
                source_port="out",
                target="file:" + path,
                target_port="in",
                kind="contains",
            )
            for i, path in enumerate(kg_paths)
        ],
    ]

    def fake_build(*args, **kwargs):
        assert set(kwargs["scope_paths"]) == set(kg_paths)
        return {
            "graph": Graph(nodes=generated_nodes, edges=generated_edges).model_dump(),
            "metadata": {"analyzed_files": 2, "warnings": [], "cache_hit": False},
        }

    monkeypatch.setattr("bpreviewer.agent.build_semantic_blueprint", fake_build)
    result = rebuild_semantic_blueprint_scope(
        tmp_path, snapshot, original, "domain:kg", tmp_path / ".bp-state", force_refresh=True
    )
    merged = Graph.model_validate(result["graph"])
    nodes = {node.id: node for node in merged.nodes}
    assert "domain:kg" in nodes
    assert "agent:module:old-kg" not in nodes
    assert nodes["agent:module:new-kg"].parent == "domain:kg"
    assert "file:src/kg/ontology.py" in nodes, [
        (node.id, node.kind, [proof.path for proof in node.evidence]) for node in merged.nodes
    ]
    assert nodes["file:src/kg/ontology.py"].parent == "agent:module:new-kg"
    assert nodes["agent:module:other"].description == "untouched"
    assert nodes["file:src/matching/retriever.py"].parent == "agent:module:other"
    assert all(not node.parent or node.parent in nodes for node in merged.nodes)
    assert all(edge.source in nodes and edge.target in nodes for edge in merged.edges)
    assert result["metadata"]["local_rebuild"] is True
    assert result["metadata"]["scope_label"] == "知识图谱"


def test_scoped_rebuild_rejects_missing_scope(tmp_path):
    _write_project(tmp_path)
    snapshot = scan(tmp_path)
    try:
        rebuild_semantic_blueprint_scope(tmp_path, snapshot, snapshot.graph, "missing-node", tmp_path / ".bp-state")
    except AgentExecutionError as error:
        assert "不存在" in str(error) or "不是逻辑模块" in str(error)
    else:
        raise AssertionError("Missing scope must be rejected")
