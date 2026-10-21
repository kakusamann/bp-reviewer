"""Connection checks and deterministic, review-only changes."""

import hashlib
import json

from .models import Graph, Snapshot


def validate(graph: Graph, require_dag: bool = True) -> dict:
    nodes = {n.id: n for n in graph.nodes}
    errors, warnings, incoming, adjacent, signatures = [], [], {}, {}, set()
    for edge in graph.edges:
        if edge.source not in nodes or edge.target not in nodes:
            errors.append(f"{edge.id}: missing endpoint")
            continue
        source = next((p for p in nodes[edge.source].ports if p.id == edge.source_port), None)
        target = next((p for p in nodes[edge.target].ports if p.id == edge.target_port), None)
        signature = (edge.source, edge.source_port, edge.target, edge.target_port, edge.kind)
        if signature in signatures:
            errors.append(f"{edge.id}: duplicate connection")
        signatures.add(signature)
        if not source or not target:
            errors.append(f"{edge.id}: missing port")
            continue
        if source.io != "output" or target.io != "input":
            errors.append(f"{edge.id}: output must connect to input")
        if edge.kind == "data":
            if not source.data_type or not target.data_type or not source.schema_ref or not target.schema_ref:
                warnings.append(f"{edge.id}: UNKNOWN_TYPE / 待确认；未证明兼容")
            if source.data_type and target.data_type and source.data_type != target.data_type:
                errors.append(f"{edge.id}: incompatible types")
            if source.schema_ref and target.schema_ref and source.schema_ref != target.schema_ref:
                errors.append(f"{edge.id}: incompatible schema")
            if any(source.semantics.get(k) != v for k, v in target.semantics.items()):
                errors.append(f"{edge.id}: incompatible semantics")
        port_key = (edge.target, edge.target_port)
        incoming[port_key] = incoming.get(port_key, 0) + 1
        if target.multiplicity == "one" and incoming[port_key] > 1:
            errors.append(f"{edge.id}: single input already connected")
        if edge.kind in {"data", "control"}:
            adjacent.setdefault(edge.source, set()).add(edge.target)
    for node in graph.nodes:
        for port in node.ports:
            if port.io == "input" and port.required and not incoming.get((node.id, port.id)):
                errors.append(f"{node.id}:{port.id}: required input missing")
    # Iterative topological check avoids recursion limits on larger projects.
    if require_dag:
        degrees = dict.fromkeys(nodes, 0)
        for targets in adjacent.values():
            for target in targets:
                degrees[target] += 1
        queue = [n for n, degree in degrees.items() if not degree]
        visited = 0
        while queue:
            current = queue.pop()
            visited += 1
            for target in adjacent.get(current, set()):
                degrees[target] -= 1
                if degrees[target] == 0:
                    queue.append(target)
        if visited != len(nodes):
            errors.append("DAG constraint: cycle detected")
    return {"valid": not errors, "errors": sorted(set(errors)), "warnings": sorted(set(warnings))}


def semantic_node(node):
    data = node.model_dump()
    data.pop("position")
    data.pop("evidence")
    return data


def signature(edge):
    return (edge.kind, edge.source, edge.source_port, edge.target, edge.target_port)


def change_request(snapshot: Snapshot, proposed: Graph) -> dict:
    before = {n.id: n for n in snapshot.graph.nodes}
    after = {n.id: n for n in proposed.nodes}
    operations = []
    for key in sorted(before.keys() - after.keys()):
        operations.append({"op": "remove_node", "node_id": key, "before": semantic_node(before[key])})
    for key in sorted(after.keys() - before.keys()):
        operations.append(
            {"op": "add_node", "node_id": key, "after": semantic_node(after[key]), "status": "UNVERIFIED"}
        )
    for key in sorted(before.keys() & after.keys()):
        a, b = semantic_node(before[key]), semantic_node(after[key])
        if a != b:
            operations.append({"op": "change_node", "node_id": key, "before": a, "after": b})
    old = {signature(e): e for e in snapshot.graph.edges}
    new = {signature(e): e for e in proposed.edges}
    for key in sorted(old.keys() - new.keys()):
        operations.append({"op": "remove_edge", "before": old[key].model_dump()})
    for key in sorted(new.keys() - old.keys()):
        data = new[key].model_dump()
        data.update(status="UNVERIFIED", evidence=[])
        operations.append({"op": "add_edge", "after": data})
    affected = set()
    for op in operations:
        if "node_id" in op:
            affected.add(op["node_id"])
        else:
            data = op.get("before") or op["after"]
            affected.update([data["source"], data["target"]])
    # Include directly downstream nodes so reviewers can inspect the impact boundary.
    direct = set(affected)
    for edge in [*snapshot.graph.edges, *proposed.edges]:
        if edge.source in direct:
            affected.add(edge.target)
    body = {
        "version": "1",
        "project": snapshot.project_name,
        "baseline_snapshot": snapshot.id,
        "baseline_git_head": snapshot.git_head,
        "operations": operations,
        "affected_nodes": sorted(affected),
        "baseline_evidence": {
            key: [p.model_dump() for p in before[key].evidence] for key in sorted(affected & before.keys())
        },
        "validation": validate(proposed, snapshot.require_dag),
        "assumptions": ["静态调用与导入不证明运行时执行或数据流。", "所有新增草稿关系为 UNVERIFIED。"],
        "manual_approval": ["确认端口、合同、Adapter 与下游影响", "由开发者审阅后另行实施代码修改"],
    }
    body["id"] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    lines = [
        "# BP-Reviewer Change Request",
        "",
        f"Project: {snapshot.project_name}",
        f"Baseline Git HEAD: {snapshot.git_head or 'UNAVAILABLE'}",
        f"Snapshot: {snapshot.id}",
        "",
        "草稿提案；目标源码未被修改。画布坐标不计入需求。",
        "",
    ]
    for op in operations:
        if op["op"].endswith("edge"):
            data = op.get("before") or op["after"]
            lines.append(
                f"- {op['op']}: `{data['source']}:{data['source_port']}` → `{data['target']}:{data['target_port']}` ({data['kind']}, {data['status']})"
            )
        else:
            lines.append(f"- {op['op']}: `{op['node_id']}`")
    lines += [
        "",
        "## Full review record",
        "",
        "```json",
        json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2),
        "```",
    ]
    return {"json": body, "markdown": "\n".join(lines) + "\n"}
