"""Evidence-first functional dataflow extraction, with bounded optional MiMo Tool Calling.

Tool output is never treated as observed runtime behavior. Only literal file I/O
with a real source reference can create data ports and data-flow relations.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from openai import OpenAI

from .evidence_tools import collect_evidence, execute_tool, tool_schema
from .models import Edge, Evidence, Graph, Port


def _all_members(graph: Graph) -> dict[str, list[str]]:
    nodes = {node.id: node for node in graph.nodes}
    groups: dict[str, set[str]] = defaultdict(set)
    for node in graph.nodes:
        if not node.evidence or node.id != "file:" + node.evidence[0].path:
            continue
        ancestor = node.parent
        visited: set[str] = set()
        while ancestor and ancestor not in visited:
            visited.add(ancestor)
            parent = nodes.get(ancestor)
            if parent is None:
                break
            if parent.kind == "logical_module":
                groups[parent.id].add(node.evidence[0].path)
            ancestor = parent.parent
    return {key: sorted(paths) for key, paths in groups.items()}


def _probe_mimo(root: Path, paths: list[str]) -> int:
    """Allow MiMo to select read-only AST tools; never accept free-text output as proof."""
    if not os.environ.get("MIMO_API_KEY", "").strip() or not paths:
        return 0
    client = OpenAI(
        api_key=os.environ["MIMO_API_KEY"].strip(),
        base_url=os.environ.get("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1").strip(),
        timeout=30.0,
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": (
            "你是代码数据流核验助手。必须先调用工具查看选定模块的源码事实。"
            "仅判断输入输出；不执行代码，不猜测没有证据的运行时关系。"
            "输出简短 JSON 摘要；工具执行结果才是事实依据。"
        )},
        {"role": "user", "content": json.dumps({"files": paths[:5], "task": "通过静态工具检查输入输出"}, ensure_ascii=False)},
    ]
    calls = 0
    for _ in range(2):
        try:
            answer = client.chat.completions.create(
                model=os.environ.get("MIMO_MODEL", "mimo-v2.5-pro").strip(),
                messages=messages,
                tools=tool_schema(),
                tool_choice="auto",
                temperature=0,
                max_completion_tokens=650,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception:
            return calls  # unsupported tool-calling model remains on deterministic evidence
        msg = answer.choices[0].message
        if not msg.tool_calls:
            break
        messages.append(msg.model_dump(exclude_none=True))
        for call in msg.tool_calls[:3]:
            calls += 1
            try:
                arguments = json.loads(call.function.arguments or "{}")
                result = execute_tool(root, paths, call.function.name, arguments)
            except (OSError, ValueError, SyntaxError, TypeError, json.JSONDecodeError) as error:
                result = {"error": str(error)[:180]}
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False)[:5000],
            })
        if calls >= 5:
            break
    return calls


def enrich_dataflow(root: Path, graph: Graph, *, enable_mimo_tools: bool = True) -> tuple[Graph, dict[str, Any]]:
    """Resolve port labels and file-flow edges from actual AST literals and file evidence."""
    graph = graph.model_copy(deep=True)
    memberships = _all_members(graph)
    facts: dict[str, dict[str, Any]] = {}
    for module_id, paths in memberships.items():
        facts[module_id] = collect_evidence(root, paths, limit=18)
    inferred = 0
    io_records: dict[str, list[tuple[str, Port, Evidence]]] = defaultdict(list)
    for node in graph.nodes:
        if node.kind != "logical_module":
            continue
        evidence = facts.get(node.id, {})
        entries = evidence.get("io", [])
        existing_ports = {port.id for port in node.ports}
        for item in entries:
            sig = item["direction"] + "|" + item["name"]
            port_id = "io_" + hashlib.sha256(sig.encode()).hexdigest()[:12]
            proof = Evidence(path=item["path"], line=item["line"], status="STATIC_INFERRED",
                             note="AST 静态识别到文件读写；不等于运行时已执行。")
            port = next((p for p in node.ports if p.id == port_id), None)
            if port is None:
                if len(node.ports) >= 24:
                    continue
                port = Port(
                    id=port_id,
                    io=item["direction"],
                    label=("读取 " if item["direction"] == "input" else "写出 ") + Path(item["name"]).name,
                    data_type=item["data_type"],
                    description=f"源码 {item['path']}:{item['line']} 使用 {item['operation']} 操作",
                    semantics={"evidence_path": item["path"], "evidence_line": str(item["line"]),
                               "artifact_path": item["name"], "basis": "static_ast"},
                )
                node.ports.append(port)
                existing_ports.add(port_id)
                inferred += 1
            io_records[item["name"]].append((node.id, port, proof))
    existing = {edge.id for edge in graph.edges}
    added = 0
    for literal, records in io_records.items():
        writers = [(node_id, port, proof) for node_id, port, proof in records if port.io == "output"]
        readers = [(node_id, port, proof) for node_id, port, proof in records if port.io == "input"]
        for source_id, source_port, source_proof in writers:
            for target_id, target_port, target_proof in readers:
                if source_id == target_id or len(graph.edges) >= 19990:
                    continue
                # Nested modules must not create duplicate edges at every ancestor level.
                nodes = {node.id: node for node in graph.nodes}
                if nodes[source_id].parameters.get("structural_group") or nodes[target_id].parameters.get("structural_group"):
                    continue
                signature = "|".join([source_id, source_port.id, target_id, target_port.id, literal])
                edge_id = "agent:ast-data:" + hashlib.sha256(signature.encode()).hexdigest()[:24]
                if edge_id in existing:
                    continue
                graph.edges.append(Edge(
                    id=edge_id, source=source_id, source_port=source_port.id,
                    target=target_id, target_port=target_port.id,
                    kind="data", status="STATIC_INFERRED",
                    evidence=[source_proof, target_proof],
                ))
                existing.add(edge_id)
                added += 1

    tool_calls = 0
    if enable_mimo_tools and os.environ.get("MIMO_ENABLE_TOOL_CALLING", "true").lower() == "true":
        # Probe only one unclear module per build; deterministic AST evidence covers all modules.
        candidates = [(node_id, paths) for node_id, paths in memberships.items()
                      if len(paths) >= 2 and len(facts.get(node_id, {}).get("io", [])) == 0]
        candidates.sort(key=lambda pair: (-len(pair[1]), pair[0]))
        if candidates:
            tool_calls = _probe_mimo(root, candidates[0][1][:5])
    return Graph.model_validate(graph), {
        "ast_data_ports": inferred, "ast_data_relations": added,
        "agent_tool_calls": tool_calls,
        "evidence_file_count": len({path for paths in memberships.values() for path in paths}),
    }
