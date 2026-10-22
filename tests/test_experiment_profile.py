"""Experiment-workflow acceptance tests using a non-executed synthetic project."""

import hashlib
import shutil
from pathlib import Path

from bpreviewer.experiment_profile import annotate_workflows
from bpreviewer.models import Evidence, Graph, Node
from bpreviewer.scanner import scan


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "research_workflow_sample"


def _copy_project(tmp_path):
    root = tmp_path / "sample"
    shutil.copytree(EXAMPLE, root)
    return root


def _hashes(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _graph(root):
    snap = scan(root)
    nodes = [
        Node(id="domain:kgc", label="知识图谱补全", kind="logical_module"),
        Node(id="domain:background", label="背景库匹配", kind="logical_module"),
        Node(id="domain:lcia", label="LCIA评价", kind="logical_module"),
        Node(id="kgc:workflow", label="KGC实验工作流", kind="logical_module", parent="domain:kgc"),
    ]
    for file in snap.graph.nodes:
        if not file.evidence or file.id != "file:" + file.evidence[0].path:
            continue
        path = file.evidence[0].path
        if "/kgc/" in path:
            parent = "kgc:workflow"
        elif "/background/" in path:
            parent = "domain:background"
        elif "/lcia/" in path:
            parent = "domain:lcia"
        else:
            continue
        nodes.append(file.model_copy(update={"kind": "code_file", "parent": parent}))
    return Graph(nodes=nodes)


def test_workflow_profile_distinguishes_available_models_config_and_recorded_runs(tmp_path):
    root = _copy_project(tmp_path)
    before = _hashes(root)
    enriched, info = annotate_workflows(root, _graph(root))
    assert info["workflow_profiles"] >= 3
    assert info["workflow_steps"] >= 3
    modules = {node.id: node for node in enriched.nodes}
    profile = modules["kgc:workflow"].parameters["analysis_profile"]
    assert {"TransE", "RotatE", "RGCN"}.issubset({model["name"] for model in profile["models"]})
    config = {param["name"]: param["value"] for param in profile["parameters"] if param["source"] == "配置文件"}
    assert config["model"] == "RotatE"
    assert config["training.embedding_dim"] == 200
    assert config["training.learning_rate"] == 0.001
    assert config["training.epochs"] == 15
    assert {step["key"] for step in profile["workflow"]}.issuperset({"input", "model", "execute", "evaluate", "output"})
    assert len(profile["runs"]) == 1
    assert profile["runs"][0]["name"] == "RotatE"
    assert profile["runs"][0]["run_id"] == "demo-run-001"
    metrics = {m["name"]: m["value"] for m in profile["metrics"]}
    assert metrics["MRR"] == 0.37
    assert metrics["Hits@10"] == 0.63
    assert all(m["path"] == "results/kgc_metrics.json" for m in profile["metrics"])
    assert _hashes(root) == before


def test_unrelated_modules_do_not_inherit_kgc_experiment_results(tmp_path):
    root = _copy_project(tmp_path)
    enriched, _ = annotate_workflows(root, _graph(root))
    modules = {node.id: node for node in enriched.nodes}
    for domain in ("domain:background", "domain:lcia"):
        profile = modules[domain].parameters["analysis_profile"]
        assert not profile["runs"]
        assert not profile["metrics"]
    all_ids = {node.id for node in enriched.nodes}
    assert len(all_ids) == len(enriched.nodes)
    assert all(not node.parent or node.parent in all_ids for node in enriched.nodes)


def test_single_file_workflow_can_expose_steps_without_executing_file(tmp_path):
    root = tmp_path / "single"
    root.mkdir()
    sample = root / "only.py"
    sample.write_text(
        'raise RuntimeError("must not run")\n'
        'def load_dataset():\n    return "input"\n'
        'def select_model():\n    return "model"\n'
        "def train_model(epochs=5):\n    return 1\n"
        "def evaluate_model(scores):\n    return len(scores)\n",
        encoding="utf-8",
    )
    snapshot = scan(root)
    file_node = next(node for node in snapshot.graph.nodes if node.id == "file:only.py")
    graph = Graph(
        nodes=[
            Node(id="logical:single", label="单文件实验", kind="logical_module"),
            file_node.model_copy(update={"kind": "code_file", "parent": "logical:single"}),
        ]
    )
    updated, info = annotate_workflows(root, graph)
    assert info["workflow_steps"] >= 2
    assert sum(1 for node in updated.nodes if node.parent == "logical:single" and node.kind == "logical_module") >= 2
    assert sample.read_text(encoding="utf-8").startswith('raise RuntimeError("must not run")')


def test_missing_record_is_not_fabricated(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    (root / "task.py").write_text("def train_model(): return 2\n", encoding="utf-8")
    graph = Graph(
        nodes=[
            Node(id="logic", label="模型训练", kind="logical_module"),
            Node(
                id="file:task.py",
                label="task.py",
                kind="code_file",
                parent="logic",
                evidence=[Evidence(path="task.py")],
            ),
        ]
    )
    updated, _ = annotate_workflows(root, graph)
    profile = next(node for node in updated.nodes if node.id == "logic").parameters["analysis_profile"]
    assert not profile["runs"]
    assert not profile["metrics"]
    assert profile["limitations"]
