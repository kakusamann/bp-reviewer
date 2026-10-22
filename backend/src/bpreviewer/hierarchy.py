"""Hierarchical blueprint projection; separates functional areas from source evidence.

All paths are derived from the local static index, not invented by the LLM.
"""

from __future__ import annotations

import hashlib
from collections import Counter

from .models import Edge, Evidence, Node, Port
from .scanner import safe_file
from .structure import SOURCE_EXTENSIONS, domain_for_path


def _identifier(prefix: str, value: str, used: set[str]) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    result = f"agent:{prefix}:{digest}"
    if result in used:
        raise ValueError(f"Duplicate deterministic ID: {result}")
    used.add(result)
    return result


def hierarchical_projection(
    root, nodes: list[Node], edges: list[Edge], catalog: list[dict], labels: dict, tree_entries: list[dict]
) -> tuple[list[Node], list[Edge], dict]:
    """Construct root functional domains -> semantic tasks -> verified source files."""
    # Directory fallback nodes in the legacy generator are placeholders, not business functions.
    dropped = {node.id for node in nodes if node.kind == "logical_module" and node.label.startswith("未分析文件 · ")}
    retained = [node.model_copy(deep=True) for node in nodes if node.id not in dropped]
    used = {node.id for node in retained}
    support_id = _identifier("section", "engineering-support", used)
    retained.append(
        Node(
            id=support_id,
            label="工程与辅助资源",
            kind="logical_module",
            description="测试、脚本、配置与文档。只读证据，可按目录继续展开。",
            ports=[Port(id="in", io="input"), Port(id="out", io="output")],
            parameters={"structural_group": True, "confidence": 1.0},
        )
    )

    indexed_files = {
        node.evidence[0].path: node for node in retained if node.evidence and node.id == "file:" + node.evidence[0].path
    }
    domain_ids = {}
    for group in catalog:
        key = group["key"]
        domain_id = _identifier("domain", key, used)
        domain_ids[key] = domain_id
        area_label = labels.get(key, {})
        proofs = [indexed_files[path].evidence[0] for path in group["sample_paths"][:8] if path in indexed_files]
        retained.append(
            Node(
                id=domain_id,
                label=area_label.get("label") or group["label"],
                kind="logical_module",
                parent=support_id if group["is_support"] else "",
                ports=[Port(id="in", io="input", label="功能域输入"), Port(id="out", io="output", label="功能域输出")],
                parameters={
                    "structural_group": True,
                    "domain_key": key,
                    "file_count": group["count"],
                    "confidence": 0.75 if area_label else 0.6,
                },
                description=area_label.get("description")
                or "全项目结构识别的候选功能域；展开可查看具体任务与真实文件。",
                evidence=proofs,
            )
        )

    # Determine each semantic submodule's parent from actual member-file evidence.
    semantic_ids = {
        node.id for node in retained if node.kind == "logical_module" and node.id.startswith("agent:module:")
    }
    module_files: dict[str, list[str]] = {}
    for node in retained:
        if node.kind == "code_file" and node.parent in semantic_ids and node.evidence:
            module_files.setdefault(node.parent, []).append(node.evidence[0].path)
    module_parents = {}
    for module_id, paths in module_files.items():
        counts = Counter(domain_for_path(path) for path in paths)
        primary = sorted(counts, key=lambda key: (-counts[key], key))[0]
        module_parents[module_id] = domain_ids.get(primary, support_id)

    for i, node in enumerate(retained):
        if node.kind == "code_file" and node.evidence and node.id == "file:" + node.evidence[0].path:
            path = node.evidence[0].path
            if node.parent not in module_parents:
                retained[i] = node.model_copy(update={"parent": domain_ids.get(domain_for_path(path), support_id)})
        elif node.id in module_parents:
            retained[i] = node.model_copy(update={"parent": module_parents[node.id]})
        elif node.kind == "logical_module" and node.label == "数据与文件资产":
            retained[i] = node.model_copy(update={"parent": support_id})

    # Index other source languages as evidence, without pretending they have Python AST facts.
    extra_files = 0
    for entry in tree_entries:
        if entry["kind"] != "file" or entry["extension"] not in SOURCE_EXTENSIONS:
            continue
        path = entry["path"]
        if path in indexed_files or entry["sensitive"] or entry["extension"] == ".py":
            continue
        parent = domain_ids.get(domain_for_path(path))
        if not parent:
            continue
        try:
            file = safe_file(root, path)
            sha = hashlib.sha256(file.read_bytes()).hexdigest() if file.stat().st_size <= 1_000_000 else ""
        except (ValueError, OSError):
            sha = ""
        id_ = _identifier("source", path, used)
        retained.append(
            Node(
                id=id_,
                label=path,
                kind="code_file",
                parent=parent,
                description="本地目录中真实存在的源文件；未执行其代码。",
                evidence=[
                    Evidence(
                        path=path,
                        sha256=sha,
                        status="STATIC_INFERRED" if sha else "UNVERIFIED",
                        note="路径来自真实项目目录索引。",
                    )
                ],
            )
        )
        extra_files += 1

    present = {node.id for node in retained}
    projected_edges = [edge for edge in edges if edge.source in present and edge.target in present]
    # Give cross-domain semantic proposals visible endpoints in the root overview.
    known_edges = {edge.id for edge in projected_edges}
    domain_connections = 0
    for edge in list(projected_edges):
        if not edge.id.startswith("agent:proposal:") or edge.kind not in {"data", "control"}:
            continue
        source_domain = module_parents.get(edge.source)
        target_domain = module_parents.get(edge.target)
        if not source_domain or not target_domain or source_domain == target_domain:
            continue
        sig = f"{source_domain}|{target_domain}|{edge.kind}"
        derived_id = "agent:domain-flow:" + hashlib.sha256(sig.encode()).hexdigest()[:24]
        existing = next((candidate for candidate in projected_edges if candidate.id == derived_id), None)
        if existing:
            found = {p.model_dump_json() for p in existing.evidence}
            for proof in edge.evidence:
                if proof.model_dump_json() not in found:
                    existing.evidence.append(proof)
                    found.add(proof.model_dump_json())
            continue
        if derived_id in known_edges:
            raise ValueError(f"Colliding domain edge ID: {derived_id}")
        projected_edges.append(
            Edge(
                id=derived_id,
                source=source_domain,
                target=target_domain,
                source_port="out",
                target_port="in",
                kind=edge.kind,
                status="UNVERIFIED",
                evidence=list(edge.evidence),
            )
        )
        known_edges.add(derived_id)
        domain_connections += 1

    details = {
        "domain_count": sum(not group["is_support"] for group in catalog),
        "support_group_count": sum(group["is_support"] for group in catalog),
        "suppressed_directory_groups": len(dropped),
        "additional_source_files": extra_files,
        "domain_relations": domain_connections,
    }
    return retained, projected_edges, details
