"""Cost-bounded semantic blueprint analysis orchestrated with LangGraph.

The target project is only read. The LLM receives a small, ranked set of code excerpts,
not the full repository or the contents of data artifacts.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI
from pydantic import ValidationError

from .models import Edge, Evidence, Graph, Node, Port, Snapshot
from .hierarchy import hierarchical_projection
from .scanner import directory_tree, safe_file
from .structure import domain_for_path, project_catalog


PROMPT_VERSION = "semantic-blueprint-v3-recursive-scoped"
CORE_TERMS = {
    "main",
    "app",
    "cli",
    "pipeline",
    "workflow",
    "build",
    "builder",
    "graph",
    "ontology",
    "mapping",
    "match",
    "lca",
    "calculator",
    "inventory",
    "model",
    "process",
    "service",
    "engine",
    "agent",
    "run",
    "train",
    "predict",
    "evaluate",
    "export",
    "api",
    "report",
    "construct",
    "convert",
}
DATA_SUFFIXES = {
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".parquet",
    ".feather",
    ".json",
    ".jsonl",
    ".npy",
    ".npz",
    ".pkl",
    ".pickle",
    ".pt",
    ".pth",
    ".ckpt",
    ".db",
    ".sqlite",
    ".rdf",
    ".ttl",
    ".graphml",
    ".xml",
}
READ_CALLS = {
    "open",
    "read_csv",
    "read_excel",
    "read_json",
    "read_parquet",
    "read_pickle",
    "load",
    "loadtxt",
    "genfromtxt",
    "load_workbook",
    "connect",
    "read_table",
}
WRITE_CALLS = {
    "to_csv",
    "to_excel",
    "to_json",
    "to_parquet",
    "to_pickle",
    "save",
    "savefig",
    "dump",
    "savetxt",
    "write_text",
    "write_bytes",
}


class BlueprintState(TypedDict, total=False):
    root: str
    snapshot: dict[str, Any]
    cache_path: str
    cache_key: str
    candidates: list[dict[str, Any]]
    catalog: list[dict[str, Any]]
    domain_labels: dict[str, dict[str, str]]
    scope_paths: list[str] | None
    summaries: list[dict[str, Any]]
    proposal: dict[str, Any]
    result: dict[str, Any]
    warnings: list[str]


class AgentConfigurationError(RuntimeError):
    """Raised when MiMo API settings are missing."""


class AgentExecutionError(RuntimeError):
    """Raised when the model output cannot be used safely."""


def _slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text[:48] or "module"


def _unique_id(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _json_from_response(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    fence = chr(96) * 3
    if cleaned.startswith(fence):
        cleaned = re.sub(r"^" + re.escape(fence) + r"(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*" + re.escape(fence) + r"$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AgentExecutionError("MiMo 返回内容不是有效 JSON")
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as error:
            raise AgentExecutionError("无法解析 MiMo 返回的 JSON") from error
    if not isinstance(value, dict):
        raise AgentExecutionError("MiMo 返回的 JSON 顶层必须是对象")
    return value


def _call_mimo(system_prompt: str, user_payload: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    api_key = os.environ.get("MIMO_API_KEY", "").strip()
    if not api_key:
        raise AgentConfigurationError(
            "尚未配置 MIMO_API_KEY。请在 BP-Reviewer 仓库根目录复制 .env.example 为 .env 并填写 API Key。"
        )
    base_url = os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip()
    model = os.environ.get("MIMO_MODEL", "mimo-v2.5-pro").strip()
    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            temperature=0,
            max_completion_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as error:
        raise AgentExecutionError(
            f"MiMo API 调用失败（{type(error).__name__}）。请检查 API Key、Base URL、模型名及套餐权限。"
        ) from error
    content = response.choices[0].message.content or ""
    if not content.strip():
        raise AgentExecutionError("MiMo 返回空内容，请检查模型配置后重试")
    return _json_from_response(content)


def _extract_static_facts(source: str) -> dict[str, Any]:
    imports: list[str] = []
    functions: list[str] = []
    classes: list[str] = []
    try:
        tree = ast.parse(source)
        for item in tree.body:
            if isinstance(item, ast.Import):
                imports.extend(alias.name for alias in item.names)
            elif isinstance(item, ast.ImportFrom):
                imports.append("." * item.level + (item.module or ""))
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(item.name)
            elif isinstance(item, ast.ClassDef):
                classes.append(item.name)
    except SyntaxError:
        pass
    io_hints = []
    keywords = (
        "read_csv",
        "read_excel",
        "read_json",
        "read_parquet",
        "to_csv",
        "to_excel",
        "to_json",
        "to_parquet",
        "json.load",
        "json.dump",
        "open(",
        ".save(",
        "load(",
        "write_text",
        "write_bytes",
        "pickle",
        "parquet",
        "sqlite",
    )
    for number, line in enumerate(source.splitlines(), start=1):
        if any(token in line for token in keywords):
            io_hints.append(f"{number}: {line.strip()[:180]}")
        if len(io_hints) >= 12:
            break
    return {
        "imports": imports[:20],
        "functions": functions[:30],
        "classes": classes[:20],
        "io_hints": io_hints,
        "line_count": len(source.splitlines()),
    }


def _index_project(state: BlueprintState) -> dict[str, Any]:
    """Whole-project structure without a model call or execution of target code."""
    root = Path(state["root"]).resolve(strict=True)
    snapshot = Snapshot.model_validate(state["snapshot"])
    tree = directory_tree(root, max_entries=10000, max_depth=16)
    scope_paths = state.get("scope_paths")
    entries = tree["entries"]
    if scope_paths is not None:
        allowed_paths = set(scope_paths)
        entries = [entry for entry in entries if entry["kind"] == "file" and entry["path"] in allowed_paths]
    catalog = project_catalog(snapshot, entries)
    warnings = []
    if tree["truncated"]:
        warnings.append("项目索引达到条目或深度上限；未索引的文件仍可在目录树中查看。")
    return {"catalog": catalog, "warnings": warnings}


def _label_domains(state: BlueprintState) -> dict[str, Any]:
    """One compact MiMo call labels structural areas without uploading source contents."""
    catalog = state["catalog"]
    # Keep a bounded overview; everything beyond this limit retains its deterministic label.
    payload = {
        "areas": [
            {
                "key": group["key"],
                "fallback_label": group["label"],
                "count": group["count"],
                "sample_paths": group["sample_paths"][:6],
                "sample_symbols": group["sample_symbols"][:8],
            }
            for group in catalog[:40]
        ]
    }
    if not payload["areas"]:
        return {"domain_labels": {}}
    result = _call_mimo(
        "你是项目架构分析器。基于目录结构、文件名和静态符号为现有功能域命名，"
        "不要臆造代码行为。不得新增、删改 key。优先使用明确的业务功能名称，"
        "不要简单重复文件路径，也不要把代码文件逐个作为模块。"
        '返回严格 JSON：{\\"areas\\":[{\\"key\\":string,\\"label\\":string,'
        '\\"description\\":string}]}。label 使用简短中文（不超过 16 字），'
        "description 不超过 60 汉字，不确定时沿用 fallback_label。",
        payload,
        max_tokens=2000,
    )
    raw = result.get("areas", [])
    valid = {area["key"] for area in payload["areas"]}
    labels = {}
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict) or item.get("key") not in valid:
                continue
            key = item["key"]
            label = str(item.get("label") or "").strip()[:40]
            if label:
                labels[key] = {
                    "label": label,
                    "description": str(item.get("description") or "")[:200],
                }
    return {"domain_labels": labels}


def _select_candidates(state: BlueprintState) -> dict[str, Any]:
    root = Path(state["root"]).resolve(strict=True)
    snapshot = Snapshot.model_validate(state["snapshot"])
    file_nodes = [node for node in snapshot.graph.nodes if node.id.startswith("file:") and node.evidence]
    if not file_nodes:
        raise AgentExecutionError("静态扫描中没有可供语义分析的 Python 文件节点")

    degrees: dict[str, int] = {}
    for edge in snapshot.graph.edges:
        degrees[edge.source] = degrees.get(edge.source, 0) + 1
        degrees[edge.target] = degrees.get(edge.target, 0) + 1
    scored = []
    for node in file_nodes:
        relative = node.evidence[0].path
        stem = Path(relative).stem.lower()
        score = min(degrees.get(node.id, 0), 10) * 1.4
        if stem in {"main", "app", "cli", "__main__"}:
            score += 6
        score += sum(2.0 for term in CORE_TERMS if term in stem)
        if Path(relative).name == "__init__.py":
            score -= 5
        child_functions = sum(1 for item in snapshot.graph.nodes if item.parent == node.id and item.kind == "function")
        score += min(child_functions, 8) * 0.35
        scored.append((score, relative, node))
    scored.sort(key=lambda item: (-item[0], item[1].casefold()))

    max_files = max(1, min(int(os.environ.get("MIMO_MAX_ANALYSIS_FILES", "8")), 12))
    max_file_chars = max(1000, min(int(os.environ.get("MIMO_MAX_FILE_CHARS", "3500")), 6000))
    total_budget = max(8000, min(int(os.environ.get("MIMO_MAX_TOTAL_CHARS", "22000")), 32000))
    candidates = []
    used_chars = 0
    # Stratify by the catalog to avoid high-degree API files monopolizing the budget.
    pools: dict[str, list[tuple[float, str, Node]]] = {}
    for scored_item in scored:
        key = domain_for_path(scored_item[1])
        pools.setdefault(key, []).append(scored_item)
    prioritized_keys = [group["key"] for group in state["catalog"] if not group["is_support"] and group["key"] in pools]
    prioritized_keys += [key for key in pools if key not in prioritized_keys]
    ordered = []
    while any(pools.values()):
        for key in prioritized_keys:
            if pools.get(key):
                ordered.append(pools[key].pop(0))
    for score, relative, node in ordered:
        if len(candidates) >= max_files or used_chars >= total_budget:
            break
        try:
            path = safe_file(root, relative)
            if path.stat().st_size > 1_000_000:
                continue
            raw = path.read_bytes()
            source = raw.decode("utf-8", errors="replace")
        except (ValueError, OSError):
            continue
        facts = _extract_static_facts(source)
        remaining = total_budget - used_chars
        excerpt_budget = min(max_file_chars, remaining)
        excerpt_lines = []
        excerpt_chars = 0
        for number, line in enumerate(source.splitlines(), start=1):
            line_text = f"{number}: {line}"
            if excerpt_chars + len(line_text) + 1 > excerpt_budget:
                excerpt_lines.append("[代码片段已截断]")
                break
            excerpt_lines.append(line_text)
            excerpt_chars += len(line_text) + 1
        excerpt = "\n".join(excerpt_lines)
        used_chars += len(excerpt)
        candidates.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "score": round(score, 2),
                "facts": facts,
                "excerpt": excerpt,
            }
        )
    if not candidates:
        raise AgentExecutionError("没有找到符合安全限制的候选 Python 文件")
    return {
        "candidates": candidates,
        "warnings": [
            f"仅向 MiMo 发送 {len(candidates)} 个高优先级 Python 文件的受限代码片段；未上传完整项目。",
            "静态分析与 LLM 推断均不代表程序已实际运行；新增关系需要人工确认。",
        ],
    }


def _summarize_candidates(state: BlueprintState) -> dict[str, Any]:
    payload = {
        "files": [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "static_facts": item["facts"],
                "source_excerpt_with_line_numbers": item["excerpt"],
            }
            for item in state["candidates"]
        ]
    }
    system_prompt = (
        "你是代码库架构分析器。仅依据输入的静态事实和代码片段分析，不运行代码，不臆造文件或数据流。"
        "每个 path 必须逐字对应输入中的 path，证据行号只能来自带行号的代码片段。"
        "请用简体中文输出简洁结果，不要输出 Markdown 或额外说明。"
        '返回严格 JSON：{\\"files\\":[{\\"path\\":string,\\"role\\":string,'
        '\\"summary\\":string,\\"inputs\\":[string],\\"outputs\\":[string],'
        '\\"operations\\":[string],\\"confidence\\":number,\\"evidence_lines\\":[integer]}]}。'
        "summary 不超过 70 个汉字；inputs/outputs/operations 每项不超过 8 个；"
        "无法确定的输入输出使用空数组；confidence 为 0 到 1 的保守估计。"
    )
    response = _call_mimo(system_prompt, payload, max_tokens=2600)
    raw_items = response.get("files", [])
    if not isinstance(raw_items, list):
        raise AgentExecutionError("MiMo 文件摘要结果缺少 files 数组")
    allowed = {item["path"]: item for item in state["candidates"]}
    summaries: dict[str, dict[str, Any]] = {}
    for item in raw_items:
        if not isinstance(item, dict) or item.get("path") not in allowed:
            continue
        path = item["path"]

        def text_list(key: str) -> list[str]:
            values = item.get(key, [])
            if not isinstance(values, list):
                return []
            return [str(value)[:100] for value in values[:8] if str(value).strip()]

        line_count = int(allowed[path]["facts"].get("line_count", 0))
        evidence_lines = item.get("evidence_lines", [])
        if not isinstance(evidence_lines, list):
            evidence_lines = []
        evidence_lines = sorted(
            {number for number in evidence_lines if isinstance(number, int) and 1 <= number <= line_count}
        )[:10]
        try:
            confidence = float(item.get("confidence", 0.25))
        except (TypeError, ValueError):
            confidence = 0.25
        summaries[path] = {
            "path": path,
            "role": str(item.get("role", "unknown"))[:80],
            "summary": str(item.get("summary", "功能待确认"))[:180],
            "inputs": text_list("inputs"),
            "outputs": text_list("outputs"),
            "operations": text_list("operations"),
            "confidence": max(0.0, min(1.0, confidence)),
            "evidence_lines": evidence_lines,
            "sha256": allowed[path]["sha256"],
        }
    for path, candidate in allowed.items():
        if path not in summaries:
            summaries[path] = {
                "path": path,
                "role": Path(path).stem,
                "summary": "未能生成可靠语义摘要，暂按静态结构归类",
                "inputs": [],
                "outputs": [],
                "operations": candidate["facts"]["functions"][:8],
                "confidence": 0.2,
                "evidence_lines": [],
                "sha256": candidate["sha256"],
            }
    return {"summaries": list(summaries.values())}


def _aggregate_modules(state: BlueprintState) -> dict[str, Any]:
    payload = {
        "project_areas": [
            {
                "key": area["key"],
                "name": state.get("domain_labels", {}).get(area["key"], {}).get("label", area["label"]),
                "sample_paths": area["sample_paths"][:4],
            }
            for area in state.get("catalog", [])[:40]
        ],
        "files": [
            {
                "path": item["path"],
                "role": item["role"],
                "summary": item["summary"],
                "inputs": item["inputs"],
                "outputs": item["outputs"],
                "operations": item["operations"],
                "confidence": item["confidence"],
                "evidence_lines": item["evidence_lines"],
            }
            for item in state["summaries"]
        ],
    }
    system_prompt = (
        "你是软件架构师。请将输入中的已分析文件聚合为少量、职责清晰的逻辑功能模块。"
        "不要按文件名机械分组；结合功能摘要、输入输出、项目功能域与操作判断。"
        "这些是功能域下的子功能，不是全项目的完整功能清单。"
        "每个输入 path 必须恰好归属一个模块；不得创造输入中不存在的 path。"
        "只在有明确依据时提出模块间关系，关系只允许 data 或 control；不确定时不要创建关系。"
        "模块关系是候选推断，不是运行时事实。请用简体中文输出严格 JSON，不要 Markdown。"
        '格式：{\\"modules\\":[{\\"id\\":string,\\"name\\":string,\\"description\\":string,'
        '\\"member_paths\\":[string],\\"inputs\\":[string],\\"outputs\\":[string],'
        '\\"confidence\\":number}],\\"edges\\":[{\\"source_module\\":string,'
        '\\"target_module\\":string,\\"kind\\":\\"data\\"|\\"control\\",'
        '\\"evidence_paths\\":[string],\\"reason\\":string}]}。'
        "模块 id 使用简短英文或 snake_case；name 使用简洁中文；每个模块应尽量包含至少一个文件。"
    )
    response = _call_mimo(system_prompt, payload, max_tokens=2400)
    modules = response.get("modules", [])
    edges = response.get("edges", [])
    if not isinstance(modules, list):
        raise AgentExecutionError("MiMo 模块聚合结果缺少 modules 数组")
    if not isinstance(edges, list):
        edges = []
    valid_paths = {item["path"] for item in state["summaries"]}
    seen_paths: set[str] = set()
    clean_modules = []
    used_module_keys: set[str] = set()
    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        raw_paths = module.get("member_paths", [])
        if not isinstance(raw_paths, list):
            continue
        member_paths = []
        for path in raw_paths:
            if path in valid_paths and path not in seen_paths:
                member_paths.append(path)
                seen_paths.add(path)
        if not member_paths:
            continue
        raw_key = str(module.get("id") or f"module_{index + 1}")[:80]
        # Model-generated module keys may repeat; internal keys must remain unique.
        key = _unique_id(raw_key, used_module_keys)
        name = str(module.get("name") or raw_key)[:100]
        try:
            confidence = float(module.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        def clean_labels(field: str) -> list[str]:
            values = module.get(field, [])
            if not isinstance(values, list):
                return []
            return [str(value)[:100] for value in values[:8] if str(value).strip()]

        clean_modules.append(
            {
                "key": key,
                "name": name,
                "description": str(module.get("description", ""))[:220],
                "member_paths": member_paths,
                "inputs": clean_labels("inputs"),
                "outputs": clean_labels("outputs"),
                "confidence": max(0.0, min(1.0, confidence)),
            }
        )
    # Unassigned files remain real evidence beneath their structural domain.
    # Never promote one unclassified file to a separate top-level business module.
    valid_keys = {module["key"] for module in clean_modules}
    clean_edges = []
    edge_by_signature: dict[tuple[str, str, str], dict[str, Any]] = {}
    for edge in edges[:40]:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source_module", ""))
        target = str(edge.get("target_module", ""))
        kind = edge.get("kind")
        evidence_paths = edge.get("evidence_paths", [])
        if source not in valid_keys or target not in valid_keys or source == target or kind not in {"data", "control"}:
            continue
        if not isinstance(evidence_paths, list):
            evidence_paths = []
        evidence_paths = [path for path in evidence_paths if isinstance(path, str) and path in valid_paths][:3]
        if not evidence_paths:
            continue
        signature = (source, target, kind)
        reason = str(edge.get("reason", ""))[:180]
        existing = edge_by_signature.get(signature)
        if existing is not None:
            # Merge duplicate semantic relationships but retain independent source evidence.
            existing["evidence_paths"] = list(dict.fromkeys([*existing["evidence_paths"], *evidence_paths]))[:8]
            if reason and reason not in existing["reason"]:
                existing["reason"] = (existing["reason"] + "；" + reason).strip("；")[:180]
            continue
        candidate = {
            "source_module": source,
            "target_module": target,
            "kind": kind,
            "evidence_paths": list(dict.fromkeys(evidence_paths)),
            "reason": reason,
        }
        edge_by_signature[signature] = candidate
        clean_edges.append(candidate)
    return {"proposal": {"modules": clean_modules, "edges": clean_edges}}


def _file_io_calls(source: str) -> list[tuple[str, str, int]]:
    """Find literal file paths in common read/write APIs without executing code."""
    found: list[tuple[str, str, int]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for call in (item for item in ast.walk(tree) if isinstance(item, ast.Call)):
        func = call.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if name not in READ_CALLS and name not in WRITE_CALLS:
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            continue
        literal = call.args[0].value.strip()
        if not literal or len(literal) > 240 or "\x00" in literal:
            continue
        if Path(literal).is_absolute() or "://" in literal:
            continue
        direction = "write" if name in WRITE_CALLS else "read"
        if name == "open" and len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            mode = str(call.args[1].value)
            direction = "write" if any(char in mode for char in "wax+") else "read"
        found.append((literal.replace("\\", "/"), direction, getattr(call, "lineno", 1)))
    return found


def _build_graph(
    root: Path,
    snapshot: Snapshot,
    summaries: list[dict[str, Any]],
    proposal: dict[str, Any],
    state_warnings: list[str],
    catalog: list[dict[str, Any]] | None = None,
    domain_labels: dict[str, dict[str, str]] | None = None,
    scope_paths: list[str] | None = None,
) -> tuple[Graph, int, dict[str, Any]]:
    used_ids = {node.id for node in snapshot.graph.nodes}
    warnings: list[str] = []
    file_nodes = {
        node.evidence[0].path: node for node in snapshot.graph.nodes if node.id.startswith("file:") and node.evidence
    }
    summary_by_path = {item["path"]: item for item in summaries}
    modules = list(proposal["modules"])
    assigned = {path for module in modules for path in module["member_paths"]}
    unassigned_by_dir: dict[str, list[str]] = {}
    for path in file_nodes:
        if path not in assigned:
            parent = Path(path).parent.as_posix()
            label = parent if parent != "." else "项目根目录"
            unassigned_by_dir.setdefault(label, []).append(path)
    used_module_keys = {module["key"] for module in modules}
    for directory, paths in sorted(unassigned_by_dir.items()):
        modules.append(
            {
                "key": _unique_id("unreviewed_" + _slug(directory), used_module_keys),
                "name": "未分析文件 · " + directory,
                "description": "这些文件未发送给 MiMo；按实际目录聚合，以降低 Token 消耗。双击模块可查看文件。",
                "member_paths": sorted(paths),
                "inputs": [],
                "outputs": [],
                "confidence": 0.0,
            }
        )

    module_ids: dict[str, str] = {}
    path_to_module: dict[str, str] = {}
    for module in modules:
        signature = module["name"] + "|" + "|".join(sorted(module["member_paths"]))
        suffix = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:8]
        module_id = _unique_id("agent:module:" + _slug(module["name"]) + ":" + suffix, used_ids)
        module_ids[module["key"]] = module_id
        for path in module["member_paths"]:
            path_to_module[path] = module_id

    new_nodes: list[Node] = []
    for node in snapshot.graph.nodes:
        if node.id.startswith("file:") and node.evidence:
            path = node.evidence[0].path
            new_nodes.append(
                node.model_copy(
                    update={
                        "parent": path_to_module.get(path, node.parent),
                        "kind": "code_file",
                        "description": node.description or ("项目文件：" + path),
                    }
                )
            )
        else:
            new_nodes.append(node)

    for module in modules:
        evidence = []
        for path in module["member_paths"][:20]:
            source_node = file_nodes.get(path)
            if source_node and source_node.evidence:
                summary = summary_by_path.get(path, {})
                line_candidates = summary.get("evidence_lines", [])
                evidence_line = line_candidates[0] if line_candidates else source_node.evidence[0].line
                proof = source_node.evidence[0].model_copy(
                    update={
                        "line": max(1, evidence_line),
                        "note": "逻辑模块成员文件；功能归属由静态证据与语义分析共同推断",
                    }
                )
                evidence.append(proof)
        ports = [
            Port(id="in", label="模块输入", io="input", description="逻辑模块输入汇总端口；具体合同需人工确认"),
            Port(id="out", label="模块输出", io="output", description="逻辑模块输出汇总端口；具体合同需人工确认"),
        ]
        for index, label in enumerate(module["inputs"][:8], start=1):
            ports.append(
                Port(
                    id=f"input_{index}",
                    label=label,
                    io="input",
                    description="MiMo 提出的输入候选；需对照源码确认",
                )
            )
        for index, label in enumerate(module["outputs"][:8], start=1):
            ports.append(
                Port(
                    id=f"output_{index}",
                    label=label,
                    io="output",
                    description="MiMo 提出的输出候选；需对照源码确认",
                )
            )
        new_nodes.append(
            Node(
                id=module_ids[module["key"]],
                label=module["name"],
                kind="logical_module",
                parent="",
                ports=ports,
                parameters={
                    "confidence": module["confidence"],
                    "analyzed_file_count": sum(1 for path in module["member_paths"] if path in summary_by_path),
                    "member_file_count": len(module["member_paths"]),
                },
                evidence=evidence,
                description=module["description"],
            )
        )

    new_edges = [edge.model_copy(deep=True) for edge in snapshot.graph.edges]
    edge_by_id = {edge.id: edge for edge in new_edges}
    duplicate_edges = 0
    hit_edge_limit = False

    def add_edge(edge: Edge) -> None:
        nonlocal duplicate_edges, hit_edge_limit
        existing = edge_by_id.get(edge.id)
        if existing is not None:

            def signature(value: Edge) -> tuple[str, ...]:
                return (value.source, value.source_port, value.target, value.target_port, value.kind, value.status)

            if signature(existing) != signature(edge):
                raise AgentExecutionError(
                    f"逻辑蓝图关系 ID 冲突：{edge.id}；"
                    f"已存在 {existing.source}:{existing.source_port} -> "
                    f"{existing.target}:{existing.target_port} ({existing.kind})，"
                    f"新关系 {edge.source}:{edge.source_port} -> "
                    f"{edge.target}:{edge.target_port} ({edge.kind})。"
                )
            seen_evidence = {proof.model_dump_json() for proof in existing.evidence}
            for proof in edge.evidence:
                encoded = proof.model_dump_json()
                if encoded not in seen_evidence:
                    existing.evidence.append(proof)
                    seen_evidence.add(encoded)
            duplicate_edges += 1
            return
        if len(new_edges) >= 19990:
            if not hit_edge_limit:
                warnings.append("逻辑蓝图关系数量达到安全上限，部分候选关系未展示。")
                hit_edge_limit = True
            return
        new_edges.append(edge)
        edge_by_id[edge.id] = edge

    for module in modules:
        module_id = module_ids[module["key"]]
        for path in module["member_paths"]:
            source_node = file_nodes.get(path)
            if not source_node:
                continue
            proof = source_node.evidence[0].model_copy(
                update={"note": "文件归属关系由逻辑聚合生成；不是 Python 导入关系"}
            )
            edge_id = hashlib.sha256(("contains|" + module_id + "|" + source_node.id).encode()).hexdigest()[:24]
            add_edge(
                Edge(
                    id="agent:contains:" + edge_id,
                    source=module_id,
                    source_port="out",
                    target=source_node.id,
                    target_port="in",
                    kind="contains",
                    status="STATIC_INFERRED",
                    evidence=[proof],
                )
            )

    # Aggregate static imports into module-level dependency edges.
    module_edge_signatures: set[tuple[str, str, str]] = set()
    node_by_id = {node.id: node for node in snapshot.graph.nodes}
    for edge in snapshot.graph.edges:
        if edge.kind != "import":
            continue
        source_node = node_by_id.get(edge.source)
        target_node = node_by_id.get(edge.target)
        if not source_node or not target_node or not source_node.evidence or not target_node.evidence:
            continue
        source_module = path_to_module.get(source_node.evidence[0].path)
        target_module = path_to_module.get(target_node.evidence[0].path)
        if not source_module or not target_module or source_module == target_module:
            continue
        signature = (source_module, target_module, "import")
        if signature in module_edge_signatures:
            continue
        module_edge_signatures.add(signature)
        edge_id = hashlib.sha256("|".join(signature).encode()).hexdigest()[:24]
        add_edge(
            Edge(
                id="agent:module-import:" + edge_id,
                source=source_module,
                source_port="out",
                target=target_module,
                target_port="in",
                kind="import",
                status="STATIC_INFERRED",
                evidence=edge.evidence,
            )
        )

    # LLM-proposed module relationships are unverified hypotheses with source evidence.
    for item in proposal["edges"]:
        source_module = module_ids.get(item["source_module"])
        target_module = module_ids.get(item["target_module"])
        if not source_module or not target_module:
            continue
        evidence = []
        for path in item["evidence_paths"]:
            source_node = file_nodes.get(path)
            if source_node and source_node.evidence:
                evidence.append(
                    source_node.evidence[0].model_copy(
                        update={
                            "status": "UNVERIFIED",
                            "note": "MiMo 提出的模块关系候选；需人工核验：" + item.get("reason", "")[:180],
                        }
                    )
                )
        if not evidence:
            continue
        signature = (source_module, target_module, item["kind"])
        edge_id = hashlib.sha256("|".join(signature).encode()).hexdigest()[:24]
        add_edge(
            Edge(
                id="agent:proposal:" + edge_id,
                source=source_module,
                source_port="out",
                target=target_module,
                target_port="in",
                kind=item["kind"],
                status="UNVERIFIED",
                evidence=evidence,
            )
        )

    # Index real structured-data artifacts locally. Their contents are never sent to MiMo.
    tree = directory_tree(root, max_entries=10000, max_depth=16)
    if scope_paths is not None:
        allowed_paths = set(scope_paths)
        tree["entries"] = [entry for entry in tree["entries"] if entry["path"] in allowed_paths]
    artifact_entries = [
        entry
        for entry in tree["entries"]
        if entry["kind"] == "file" and entry["extension"] in DATA_SUFFIXES and not entry["sensitive"]
    ]
    artifact_capacity = max(0, min(400, 5900 - len(new_nodes) - 1))
    if len(artifact_entries) > artifact_capacity:
        artifact_entries = artifact_entries[:artifact_capacity]
        warnings.append("数据文件节点达到安全上限，目录中其余数据文件仍可在左侧目录树查看。")
    artifact_module_id = _unique_id("agent:module:data-artifacts", used_ids)
    new_nodes.append(
        Node(
            id=artifact_module_id,
            label="数据与文件资产",
            kind="logical_module",
            ports=[
                Port(id="in", label="输入资产", io="input", description="文件用途由读写证据判断"),
                Port(id="out", label="输出资产", io="output", description="文件用途由读写证据判断"),
            ],
            description="从真实目录索引识别的结构化数据文件。仅读取文件元信息，不向 MiMo 上传文件内容。",
            parameters={"member_file_count": len(artifact_entries), "analyzed_file_count": 0, "confidence": 1.0},
            evidence=[],
        )
    )
    artifact_ids: dict[str, str] = {}
    for entry in artifact_entries:
        artifact_id = _unique_id(
            "agent:artifact:" + _slug(entry["path"]) + ":" + hashlib.sha256(entry["path"].encode()).hexdigest()[:8],
            used_ids,
        )
        artifact_ids[entry["path"]] = artifact_id
        sha = ""
        status = "UNVERIFIED"
        note = "目录中存在该文件；未读取文件内容或计算哈希"
        try:
            artifact_path = safe_file(root, entry["path"])
            if artifact_path.stat().st_size <= 1_000_000:
                sha = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                status = "STATIC_INFERRED"
                note = "目录中真实存在的文件；哈希仅用于证据校验"
        except (ValueError, OSError):
            continue
        new_nodes.append(
            Node(
                id=artifact_id,
                label=entry["name"],
                kind="data_artifact",
                parent=artifact_module_id,
                ports=[
                    Port(id="in", label="写入", io="input", description="是否为输出文件需结合读写证据确认"),
                    Port(id="out", label="读取", io="output", description="是否为输入文件需结合读写证据确认"),
                ],
                parameters={"path": entry["path"], "size_bytes": entry["size"], "extension": entry["extension"]},
                evidence=[Evidence(path=entry["path"], sha256=sha, status=status, note=note)],
                description="实际目录文件；路径：" + entry["path"],
            )
        )
        edge_id = hashlib.sha256(("artifact-contains|" + artifact_module_id + "|" + artifact_id).encode()).hexdigest()[
            :24
        ]
        add_edge(
            Edge(
                id="agent:artifact-contains:" + edge_id,
                source=artifact_module_id,
                source_port="out",
                target=artifact_id,
                target_port="in",
                kind="contains",
                status="STATIC_INFERRED",
                evidence=[Evidence(path=entry["path"], sha256=sha, status=status, note=note)],
            )
        )

    # Resolve literal read/write paths locally. No file contents are sent to the model.
    artifact_by_path = set(artifact_ids)
    path_to_artifact = {path.replace("\\", "/"): artifact_id for path, artifact_id in artifact_ids.items()}
    for code_path in file_nodes:
        try:
            code_file = safe_file(root, code_path)
            if code_file.stat().st_size > 1_000_000:
                continue
            raw = code_file.read_bytes()
            source_text = raw.decode("utf-8", errors="replace")
        except (ValueError, OSError):
            continue
        source_module = path_to_module.get(code_path)
        if not source_module:
            continue
        for literal, direction, line in _file_io_calls(source_text):
            literal_path = PurePosixPath(literal)
            possible_paths = [literal_path.as_posix(), (PurePosixPath(code_path).parent / literal_path).as_posix()]
            matched = next((path for path in possible_paths if path in artifact_by_path), None)
            if not matched:
                continue
            proof = Evidence(
                path=code_path,
                line=max(1, line),
                sha256=hashlib.sha256(raw).hexdigest(),
                status="STATIC_INFERRED",
                note="由代码中的字面路径与文件读写调用推断；不证明运行时执行",
            )
            if direction == "read":
                source_id, source_port, target_id, target_port = path_to_artifact[matched], "out", source_module, "in"
            else:
                source_id, source_port, target_id, target_port = source_module, "out", path_to_artifact[matched], "in"
            # One module-level file-flow edge, retaining every matching read/write site as evidence.
            edge_id = hashlib.sha256(f"{matched}|{source_module}|{direction}".encode()).hexdigest()[:24]
            add_edge(
                Edge(
                    id="agent:file-io:" + edge_id,
                    source=source_id,
                    source_port=source_port,
                    target=target_id,
                    target_port=target_port,
                    kind="data",
                    status="STATIC_INFERRED",
                    evidence=[proof],
                )
            )

    if tree["truncated"]:
        warnings.append("项目目录索引达到深度或条目上限；左侧目录树会提示该限制。")
    if len(new_nodes) > 6000:
        raise AgentExecutionError("逻辑蓝图节点数量超过安全上限，请先缩小项目扫描范围")
    seen_nodes: set[str] = set()
    for node in new_nodes:
        if node.id in seen_nodes:
            raise AgentExecutionError(f"逻辑蓝图节点 ID 冲突：{node.id}")
        seen_nodes.add(node.id)
    if duplicate_edges:
        warnings.append(f"已合并 {duplicate_edges} 条重复逻辑关系，并保留各自的源码证据。")
    try:
        full_catalog = catalog if catalog is not None else project_catalog(snapshot, tree["entries"])
        new_nodes, new_edges, hierarchy_info = hierarchical_projection(
            root,
            new_nodes,
            new_edges,
            full_catalog,
            domain_labels or {},
            tree["entries"],
        )
        graph = Graph(nodes=new_nodes, edges=new_edges)
    except (ValidationError, ValueError) as error:
        raise AgentExecutionError(f"逻辑蓝图结构校验失败：{error}") from error
    metadata = {
        "model": os.environ.get("MIMO_MODEL", "mimo-v2.5-pro"),
        "analyzed_files": len(summaries),
        "candidate_files": [item["path"] for item in summaries],
        "logical_modules": hierarchy_info["domain_count"] + len(proposal["modules"]),
        "project_domains": hierarchy_info["domain_count"],
        "hierarchy": hierarchy_info,
        "data_artifacts": len(artifact_entries),
        "unreviewed_files": len(file_nodes) - len(assigned),
        "cache_hit": False,
        "warnings": list(dict.fromkeys([*state_warnings, *warnings])),
    }
    return graph, len(artifact_entries), metadata


def _validate_and_build(state: BlueprintState) -> dict[str, Any]:
    root = Path(state["root"]).resolve(strict=True)
    snapshot = Snapshot.model_validate(state["snapshot"])
    graph, _, metadata = _build_graph(
        root,
        snapshot,
        state["summaries"],
        state["proposal"],
        state.get("warnings", []),
        catalog=state["catalog"],
        domain_labels=state.get("domain_labels", {}),
        scope_paths=state.get("scope_paths"),
    )
    return {"result": {"graph": graph.model_dump(), "metadata": metadata}}


def _persist_result(state: BlueprintState) -> dict[str, Any]:
    cache_path = Path(state["cache_path"])
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state["result"], ensure_ascii=False), encoding="utf-8")
    temporary.replace(cache_path)
    return {}


def _workflow():
    builder = StateGraph(BlueprintState)
    builder.add_node("index_project", _index_project)
    builder.add_node("label_domains", _label_domains)
    builder.add_node("select_candidates", _select_candidates)
    builder.add_node("summarize_candidates", _summarize_candidates)
    builder.add_node("aggregate_modules", _aggregate_modules)
    builder.add_node("validate_and_build", _validate_and_build)
    builder.add_node("persist_result", _persist_result)
    builder.add_edge(START, "index_project")
    builder.add_edge("index_project", "label_domains")
    builder.add_edge("label_domains", "select_candidates")
    builder.add_edge("select_candidates", "summarize_candidates")
    builder.add_edge("summarize_candidates", "aggregate_modules")
    builder.add_edge("aggregate_modules", "validate_and_build")
    builder.add_edge("validate_and_build", "persist_result")
    builder.add_edge("persist_result", END)
    return builder.compile()


def build_semantic_blueprint(
    root: Path,
    snapshot: Snapshot,
    state_dir: Path,
    force_refresh: bool = False,
    scope_paths: list[str] | None = None,
    recursive: bool = True,
) -> dict[str, Any]:
    """Build a cached logical blueprint, optionally restricted to a subtree's files."""
    model = os.environ.get("MIMO_MODEL", "mimo-v2.5-pro").strip()
    scope_digest = hashlib.sha256("\\n".join(sorted(set(scope_paths or []))).encode("utf-8")).hexdigest()[:16]
    cache_key = hashlib.sha256(f"{snapshot.id}|{model}|{PROMPT_VERSION}|{scope_digest}".encode("utf-8")).hexdigest()
    cache_path = state_dir / "semantic-blueprints" / f"{cache_key}.json"
    if cache_path.exists() and not force_refresh:
        try:
            result = json.loads(cache_path.read_text(encoding="utf-8"))
            result.setdefault("metadata", {})["cache_hit"] = True
            return result
        except (OSError, json.JSONDecodeError):
            pass
    if not os.environ.get("MIMO_API_KEY", "").strip():
        raise AgentConfigurationError(
            "尚未配置 MIMO_API_KEY。请在 BP-Reviewer 仓库根目录复制 .env.example 为 .env 并填写 API Key。"
        )
    initial: BlueprintState = {
        "root": str(root.resolve(strict=True)),
        "snapshot": snapshot.model_dump(),
        "cache_path": str(cache_path),
        "cache_key": cache_key,
        "scope_paths": sorted(set(scope_paths)) if scope_paths is not None else None,
        "warnings": [],
    }
    result = _workflow().invoke(initial)["result"]
    if recursive and scope_paths is None:
        graph = Graph.model_validate(result["graph"])
        domain_nodes = [
            node
            for node in graph.nodes
            if node.kind == "logical_module"
            and not node.parent
            and node.parameters.get("structural_group") is True
            and node.label != "工程与辅助资源"
        ]
        max_domains = max(0, min(int(os.environ.get("MIMO_MAX_RECURSIVE_DOMAINS", "12")), 40))
        eligible = []
        for node in domain_nodes:
            try:
                paths = _scope_paths_for_node(root, snapshot, graph, node.id)
            except AgentExecutionError:
                continue
            if sum(1 for path in paths if Path(path).suffix.lower() == ".py") >= 2:
                eligible.append(node.id)
        completed = 0
        recursion_warnings = []
        for scope_id in eligible[:max_domains]:
            try:
                refreshed = rebuild_semantic_blueprint_scope(
                    root, snapshot, graph, scope_id, state_dir, force_refresh=force_refresh
                )
                graph = Graph.model_validate(refreshed["graph"])
                completed += 1
            except (AgentExecutionError, AgentConfigurationError, ValueError) as error:
                recursion_warnings.append(f"未能递归整理功能域 {scope_id}：{str(error)[:180]}")
        metadata = result.setdefault("metadata", {})
        metadata["recursive_domains_built"] = completed
        metadata["recursive_domains_total"] = len(eligible)
        if len(eligible) > max_domains:
            recursion_warnings.append(f"递归功能域达到安全预算上限 {max_domains}；其余功能域仍可通过局部重建继续分析。")
        metadata["warnings"] = list(dict.fromkeys([*metadata.get("warnings", []), *recursion_warnings]))
        result["graph"] = graph.model_dump()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_path)
    return result


def _descendant_ids(graph: Graph, scope_node_id: str) -> set[str]:
    children: dict[str, list[str]] = {}
    for node in graph.nodes:
        if node.parent:
            children.setdefault(node.parent, []).append(node.id)
    found: set[str] = set()
    pending = list(children.get(scope_node_id, []))
    while pending:
        node_id = pending.pop()
        if node_id in found:
            continue
        found.add(node_id)
        pending.extend(children.get(node_id, []))
    return found


def _scope_paths_for_node(root: Path, snapshot: Snapshot, graph: Graph, scope_node_id: str) -> list[str]:
    """Return verified source/data paths contained in a logical module's current subtree."""
    nodes = {node.id: node for node in graph.nodes}
    scope = nodes.get(scope_node_id)
    if scope is None or scope.kind != "logical_module":
        raise AgentExecutionError("局部构建目标不存在，或不是逻辑模块")
    descendants = _descendant_ids(graph, scope_node_id)
    static_python_paths = {
        node.evidence[0].path
        for node in snapshot.graph.nodes
        if node.id.startswith("file:") and node.evidence and Path(node.evidence[0].path).suffix.lower() == ".py"
    }
    paths = {
        proof.path
        for node_id in descendants
        for node in [nodes[node_id]]
        if node.kind in {"code_file", "file"} or node.id.startswith("file:")
        for proof in node.evidence
        if proof.path in static_python_paths
    }
    # Include real data artifacts only when a visible data/control edge connects them
    # to a node inside the selected scope. Never read or upload their contents.
    linked_ids: set[str] = set()
    for edge in graph.edges:
        if edge.source in descendants or edge.target in descendants:
            linked_ids.add(edge.target if edge.source in descendants else edge.source)
    tree = directory_tree(root, max_entries=10000, max_depth=16)
    real_entries = {
        entry["path"]: entry for entry in tree["entries"] if entry["kind"] == "file" and not entry["sensitive"]
    }
    for node_id in linked_ids:
        node = nodes.get(node_id)
        if not node or node.kind != "data_artifact":
            continue
        path = str(node.parameters.get("path") or (node.evidence[0].path if node.evidence else ""))
        entry = real_entries.get(path)
        if entry and entry["extension"] in DATA_SUFFIXES:
            paths.add(path)
    return sorted(paths)


def _merge_scoped_graph(
    original: Graph, generated: Graph, scope_node_id: str, analyzed_files: int, scope_paths: list[str]
) -> Graph:
    """Replace only descendants of a scope; keep ancestors and sibling branches intact."""
    original_nodes = {node.id: node for node in original.nodes}
    scope = original_nodes.get(scope_node_id)
    if scope is None or scope.kind != "logical_module":
        raise AgentExecutionError("局部构建目标在当前图谱中不存在")
    descendants = _descendant_ids(original, scope_node_id)
    protected_assets = {
        node.id
        for node in original.nodes
        if node.id in descendants and (node.kind == "data_artifact" or node.label == "数据与文件资产")
    }
    remove_ids = descendants - protected_assets

    retained: dict[str, Node] = {}
    for node in original.nodes:
        if node.id in remove_ids:
            continue
        updated = node
        if node.id == scope_node_id:
            parameters = dict(node.parameters)
            parameters["last_local_rebuild"] = True
            parameters["last_rebuild_analyzed_files"] = analyzed_files
            updated = node.model_copy(update={"parameters": parameters})
        elif node.parent in remove_ids:
            # Data artifacts may be retained while their old asset container is rebuilt.
            updated = node.model_copy(update={"parent": scope_node_id})
        retained[updated.id] = updated

    structural_ids = {
        node.id
        for node in generated.nodes
        if node.kind == "logical_module" and node.parameters.get("structural_group") is True
    }
    generated_ids = {
        node.id
        for node in generated.nodes
        if (node.kind == "logical_module" and node.id.startswith("agent:module:"))
        or (
            node.kind in {"code_file", "function", "class", "file"}
            and any(proof.path in set(scope_paths) for proof in node.evidence)
        )
        or (node.id.startswith("file:") and node.id[5:] in set(scope_paths))
    }
    for node in generated.nodes:
        if node.id == scope_node_id or node.id in structural_ids:
            continue
        if node.kind == "data_artifact" or node.label == "数据与文件资产":
            continue
        if node.id not in generated_ids:
            continue
        parent = node.parent
        if parent in structural_ids or (parent and parent not in {item.id for item in generated.nodes}):
            parent = scope_node_id
        if parent == node.id:
            raise AgentExecutionError(f"局部构建生成了自引用父节点：{node.id}")
        retained[node.id] = node.model_copy(update={"parent": parent})

    valid_ids = set(retained)
    kept_edges = [
        edge.model_copy(deep=True)
        for edge in original.edges
        if edge.source not in remove_ids
        and edge.target not in remove_ids
        and edge.source in valid_ids
        and edge.target in valid_ids
    ]
    edge_by_id = {edge.id: edge for edge in kept_edges}
    for edge in generated.edges:
        if edge.source in structural_ids or edge.target in structural_ids:
            continue
        if edge.source not in valid_ids or edge.target not in valid_ids:
            continue
        existing = edge_by_id.get(edge.id)
        if existing:
            signature = (existing.source, existing.target, existing.source_port, existing.target_port, existing.kind)
            candidate = (edge.source, edge.target, edge.source_port, edge.target_port, edge.kind)
            if signature != candidate:
                raise AgentExecutionError(f"局部构建产生冲突关系 ID：{edge.id}")
            continue
        copied = edge.model_copy(deep=True)
        kept_edges.append(copied)
        edge_by_id[copied.id] = copied

    for node in retained.values():
        if node.parent and node.parent not in valid_ids:
            raise AgentExecutionError(f"局部构建产生无效父节点：{node.label} -> {node.parent}")
    for edge in kept_edges:
        if edge.source not in valid_ids or edge.target not in valid_ids:
            raise AgentExecutionError(f"局部构建产生无效关系端点：{edge.id}")
    return Graph(nodes=list(retained.values()), edges=kept_edges)


def rebuild_semantic_blueprint_scope(
    root: Path,
    snapshot: Snapshot,
    existing_graph: Graph | None,
    scope_node_id: str,
    state_dir: Path,
    force_refresh: bool = True,
) -> dict[str, Any]:
    """Re-analyze one logical module and atomically replace only its descendants."""
    root = root.resolve(strict=True)
    model = os.environ.get("MIMO_MODEL", "mimo-v2.5-pro").strip()
    global_scope_digest = hashlib.sha256("".encode("utf-8")).hexdigest()[:16]
    global_cache_key = hashlib.sha256(
        f"{snapshot.id}|{model}|{PROMPT_VERSION}|{global_scope_digest}".encode("utf-8")
    ).hexdigest()
    global_cache_path = state_dir / "semantic-blueprints" / f"{global_cache_key}.json"
    global_cache = {}
    if existing_graph is None:
        if global_cache_path.exists():
            try:
                global_cache = json.loads(global_cache_path.read_text(encoding="utf-8"))
                existing_graph = Graph.model_validate(global_cache["graph"])
            except (OSError, json.JSONDecodeError, KeyError, ValidationError):
                existing_graph = None
        if existing_graph is None:
            global_cache = build_semantic_blueprint(root, snapshot, state_dir)
            existing_graph = Graph.model_validate(global_cache["graph"])
    else:
        existing_graph = Graph.model_validate(existing_graph)
        if global_cache_path.exists():
            try:
                global_cache = json.loads(global_cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                global_cache = {}
    all_paths = _scope_paths_for_node(root, snapshot, existing_graph, scope_node_id)
    python_paths = {
        path
        for path in all_paths
        if Path(path).suffix.lower() == ".py" and any(node.id == "file:" + path for node in snapshot.graph.nodes)
    }
    if len(python_paths) < 2:
        raise AgentExecutionError("当前模块范围内不足 2 个可分析 Python 文件，无法进一步细分；可直接查看其源码证据。")

    selected_ids = set()
    static_nodes = {node.id: node for node in snapshot.graph.nodes}
    for node in snapshot.graph.nodes:
        if node.id.startswith("file:") and node.id[5:] in python_paths:
            selected_ids.add(node.id)
        elif any(proof.path in python_paths for proof in node.evidence):
            selected_ids.add(node.id)
    # Preserve any static ancestors required by the selected file/function nodes.
    pending = list(selected_ids)
    while pending:
        node = static_nodes.get(pending.pop())
        if node and node.parent and node.parent in static_nodes and node.parent not in selected_ids:
            selected_ids.add(node.parent)
            pending.append(node.parent)
    scoped_snapshot = snapshot.model_copy(
        update={
            "graph": Graph(
                nodes=[node.model_copy(deep=True) for node in snapshot.graph.nodes if node.id in selected_ids],
                edges=[
                    edge.model_copy(deep=True)
                    for edge in snapshot.graph.edges
                    if edge.source in selected_ids and edge.target in selected_ids
                ],
            )
        }
    )
    generated_result = build_semantic_blueprint(
        root,
        scoped_snapshot,
        state_dir,
        force_refresh=force_refresh,
        scope_paths=all_paths,
        recursive=False,
    )
    generated_graph = Graph.model_validate(generated_result["graph"])
    merged = _merge_scoped_graph(
        existing_graph,
        generated_graph,
        scope_node_id,
        int(generated_result.get("metadata", {}).get("analyzed_files", 0)),
        all_paths,
    )
    metadata = dict(generated_result.get("metadata", {}))
    metadata.update(
        {
            "local_rebuild": True,
            "scope_node_id": scope_node_id,
            "scope_label": next(node.label for node in existing_graph.nodes if node.id == scope_node_id),
            "scope_files": len(python_paths),
            "cache_hit": False,
            "warnings": list(dict.fromkeys(metadata.get("warnings", []))),
        }
    )
    # Persist the merged graph so subsequent local rebuilds operate on the latest hierarchy.
    global_cache["graph"] = merged.model_dump()
    global_metadata = global_cache.setdefault("metadata", {})
    global_metadata["last_local_rebuild"] = {
        "scope_node_id": scope_node_id,
        "scope_label": metadata["scope_label"],
        "scope_files": len(python_paths),
    }
    global_cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = global_cache_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(global_cache, ensure_ascii=False), encoding="utf-8")
    temporary.replace(global_cache_path)
    return {"graph": merged.model_dump(), "metadata": metadata}
