"""Project-neutral, read-only experimental and technical workflow analysis.

Produces compact, user-facing profiles grounded in AST/config/result evidence.
No inspected project code is imported or executed, and no project files are changed.
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from .models import Evidence, Graph, Node
from .scanner import directory_tree, safe_file

STAGE_WORDS = {
    "input": ("read", "load", "dataset", "schema", "contract", "parse", "ingest"),
    "prepare": ("prepare", "preprocess", "normalize", "transform", "tokenize", "sample", "split", "encode"),
    "model": ("model", "registry", "select", "construct", "instantiate", "architecture"),
    "execute": ("train", "fit", "run", "execute", "predict", "infer", "optimize", "calculate"),
    "evaluate": ("evaluat", "metric", "score", "benchmark", "validate", "accuracy"),
    "output": ("export", "save", "write", "report", "serialize", "persist"),
}
STAGE_TITLES = {
    "input": "数据输入与契约",
    "prepare": "数据准备与转换",
    "model": "算法与模型选择",
    "execute": "计算与执行",
    "evaluate": "评价与验证",
    "output": "结果输出",
}
STAGE_ORDER = tuple(STAGE_WORDS)
RELEVANT_CONFIG_SUFFIXES = {".json", ".toml"}
RESULT_TOKENS = ("result", "metric", "evaluation", "benchmark", "history", "experiment")


def _text(value: Any, limit: int = 160) -> str:
    return str(value)[:limit]


def _literal(node: ast.AST) -> Any:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, MemoryError, RecursionError):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 24 and all(
        isinstance(item, (str, int, float, bool)) for item in value
    ):
        return list(value)
    return None


def _source_facts(root: Path, path: str) -> dict[str, Any]:
    file = safe_file(root, path)
    if file.stat().st_size > 220_000:
        return {"functions": [], "models": [], "parameters": [], "artifacts": [], "stages": []}
    tree = ast.parse(file.read_text(encoding="utf-8-sig", errors="replace"), filename=path)
    facts: dict[str, Any] = {"functions": [], "models": [], "parameters": [], "artifacts": [], "stages": []}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            entry = {"name": node.name, "path": path, "line": node.lineno}
            facts["functions"].append(entry)
            lowered = node.name.lower()
            matched = next((stage for stage, words in STAGE_WORDS.items() if any(w in lowered for w in words)), None)
            if matched is not None:
                facts["stages"].append({**entry, "stage": matched})
            for argument, default in zip(reversed(node.args.args), reversed(node.args.defaults)):
                value = _literal(default)
                if value is not None and isinstance(value, (str, int, float, bool)):
                    if any(word in argument.arg.lower() for word in (
                        "model", "dim", "epoch", "batch", "rate", "learning", "seed",
                        "threshold", "optimizer", "margin", "dropout", "metric", "device"
                    )):
                        facts["parameters"].append({
                            "name": argument.arg, "value": value, "source": "函数默认值",
                            "path": path, "line": node.lineno,
                        })
        elif isinstance(node, ast.ClassDef):
            class_name = node.name
            if any(token in class_name.lower() for token in ("model", "network", "encoder", "classifier")):
                facts["models"].append({
                    "name": class_name, "source": "class", "path": path, "line": node.lineno,
                })
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) and node.targets else (
                node.target if isinstance(node, ast.AnnAssign) else None
            )
            value = node.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            identifier = target.id
            if identifier.lower() in {"model_registry", "models", "algorithms", "available_models"} and isinstance(value, ast.Dict):
                for key, impl in zip(value.keys, value.values):
                    name = _literal(key) if key is not None else None
                    if isinstance(name, str):
                        facts["models"].append({
                            "name": name[:80], "source": "模型注册表",
                            "implementation": ast.unparse(impl)[:100],
                            "path": path, "line": node.lineno,
                        })
            if identifier.isupper() or any(token in identifier.lower() for token in (
                "model", "learning_rate", "epochs", "batch_size", "embedding_dim"
            )):
                val = _literal(value)
                if isinstance(val, (str, int, float, bool)):
                    facts["parameters"].append({
                        "name": identifier, "value": val, "source": "代码常量",
                        "path": path, "line": node.lineno,
                    })
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.replace("\\", "/").strip()
            if (value.endswith((".json", ".toml", ".csv", ".parquet", ".tsv"))
                    and len(value) <= 200 and not value.startswith(("/", "~"))
                    and "://" not in value and ".." not in PurePosixPath(value).parts):
                facts["artifacts"].append({"path": value, "source_path": path, "line": node.lineno})
    return facts


def _read_small_document(root: Path, path: str) -> dict[str, Any] | None:
    try:
        file = safe_file(root, path)
        if file.stat().st_size > 100_000 or file.suffix.lower() not in RELEVANT_CONFIG_SUFFIXES:
            return None
        if file.suffix.lower() == ".json":
            data = json.loads(file.read_text(encoding="utf-8"))
        else:
            import tomllib
            data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _flatten_values(data: dict[str, Any], prefix: str = "", *, limit: int = 40) -> list[dict[str, Any]]:
    result = []
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.extend(_flatten_values(value, name, limit=limit))
        elif isinstance(value, (str, int, float, bool)):
            result.append({"name": name[:130], "value": value})
        if len(result) >= limit:
            break
    return result[:limit]


def _steps_for_module(root: Path, paths: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    profile: dict[str, Any] = {
        "summary": "基于实际源码、配置与结果文件建立的技术/实验工作流说明。",
        "inputs": [], "outputs": [], "workflow": [], "models": [],
        "parameters": [], "metrics": [], "runs": [], "source_files": paths[:30],
        "limitations": [],
    }
    stages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mentioned: dict[str, dict[str, Any]] = {}
    for path in paths[:50]:
        if Path(path).suffix.lower() != ".py":
            continue
        try:
            facts = _source_facts(root, path)
        except (SyntaxError, OSError, ValueError, UnicodeError):
            continue
        profile["models"].extend(facts["models"])
        profile["parameters"].extend(facts["parameters"])
        for fact in facts["stages"]:
            stages[fact["stage"]].append(fact)
        for artifact in facts["artifacts"]:
            mentioned.setdefault(artifact["path"], artifact)
    for artifact, site in sorted(mentioned.items()):
        item = {"name": artifact, "path": site["source_path"], "line": site["line"], "basis": "源码字面量"}
        if any(token in artifact.lower() for token in ("result", "output", "metric", "report")):
            profile["outputs"].append(item)
        else:
            profile["inputs"].append(item)
        if Path(artifact).suffix.lower() not in RELEVANT_CONFIG_SUFFIXES:
            continue
        document = _read_small_document(root, artifact)
        if document is None:
            continue
        if any(token in artifact.lower() for token in RESULT_TOKENS) and isinstance(document.get("metrics"), dict):
            name = _text(document.get("model", "未标注模型"))
            run_id = _text(document.get("run_id", "未标注运行编号"))
            profile["runs"].append({
                "name": name, "run_id": run_id, "file": artifact,
                "status": "发现结果记录；不代表已独立复现",
            })
            for metric in _flatten_values(document["metrics"], limit=20):
                profile["metrics"].append({
                    **metric, "path": artifact, "line": 1,
                    "model": name, "run_id": run_id,
                })
        elif not any(token in artifact.lower() for token in RESULT_TOKENS):
            for parameter in _flatten_values(document, limit=36):
                profile["parameters"].append({
                    **parameter, "path": artifact, "line": 1, "source": "配置文件",
                })
    for stage in STAGE_ORDER:
        facts = stages.get(stage, [])
        if facts:
            profile["workflow"].append({
                "key": stage, "title": STAGE_TITLES[stage],
                "summary": f"识别到 {len(facts)} 个相关函数；此分组不代表已确认执行顺序。",
                "functions": facts[:10], "path": facts[0]["path"], "line": facts[0]["line"],
                "basis": "静态函数职责线索",
            })
    for key in ("models", "parameters", "metrics", "inputs", "outputs"):
        unique = []
        seen = set()
        for item in profile[key]:
            signature = (item.get("name"), item.get("path"), item.get("line"), item.get("source"))
            if signature not in seen:
                unique.append(item)
                seen.add(signature)
        profile[key] = unique[:40]
    if not profile["runs"]:
        profile["limitations"].append("尚未发现可关联的实验结果记录，不能推断哪些模型已经运行。")
    if not profile["workflow"]:
        profile["limitations"].append("未找到足够的函数职责线索，请查看源码证据或进行局部 AI 细化。")
    return profile, profile["workflow"]


def annotate_workflows(root: Path, graph: Graph, *, generate_steps: bool = True) -> tuple[Graph, dict[str, int]]:
    """Attach human-facing profiles to each logical module, and subdivide single-file leaves.

    Generates only evidence-linked steps. No fictional control or data edges are created.
    """
    graph = graph.model_copy(deep=True)
    by_id = {node.id: node for node in graph.nodes}
    children: dict[str, list[str]] = defaultdict(list)
    for node in graph.nodes:
        if node.parent:
            children[node.parent].append(node.id)
    added: list[Node] = []
    count_profiles = 0
    files_seen: set[str] = set()
    for node in graph.nodes:
        if node.kind != "logical_module" or node.parameters.get("workflow_step"):
            continue
        pending = list(children.get(node.id, []))
        visited: set[str] = set()
        paths: list[str] = []
        semantic_children = 0
        while pending:
            child_id = pending.pop()
            if child_id in visited:
                continue
            visited.add(child_id)
            child = by_id.get(child_id)
            if child is None:
                continue
            if child.kind == "logical_module" and not child.parameters.get("workflow_step"):
                semantic_children += 1
            if child.evidence and child.id == "file:" + child.evidence[0].path:
                paths.append(child.evidence[0].path)
                files_seen.add(child.evidence[0].path)
            pending.extend(children.get(child_id, []))
        if not paths:
            continue
        unique_paths = sorted(set(paths))
        profile, steps = _steps_for_module(root, unique_paths)
        node.parameters["analysis_profile"] = profile
        count_profiles += 1
        if not generate_steps or semantic_children or len(steps) < 2:
            continue
        for step in steps[:6]:
            id_ = "agent:step:" + hashlib.sha256((node.id + "|" + step["key"]).encode()).hexdigest()[:20]
            if id_ in by_id:
                continue
            added.append(Node(
                id=id_, label=step["title"], kind="logical_module", parent=node.id,
                description=step["summary"],
                parameters={"workflow_step": True, "analysis_profile": {
                    "summary": step["summary"], "inputs": [], "outputs": [], "workflow": [step],
                    "models": profile["models"] if step["key"] == "model" else [],
                    "parameters": profile["parameters"] if step["key"] in {"model", "execute"} else [],
                    "metrics": profile["metrics"] if step["key"] == "evaluate" else [],
                    "runs": profile["runs"] if step["key"] == "evaluate" else [],
                    "source_files": [step["path"]],
                    "limitations": ["功能步骤按静态证据归类，不能据此认定运行时先后顺序。"],
                }},
                evidence=[Evidence(path=step["path"], line=step["line"],
                                   status="STATIC_INFERRED", note="函数名称与行号来自实际源码。")],
            ))
    if added:
        graph.nodes.extend(added)
    return Graph.model_validate(graph), {
        "workflow_profiles": count_profiles,
        "workflow_steps": len(added),
        "workflow_source_files": len(files_seen),
    }
