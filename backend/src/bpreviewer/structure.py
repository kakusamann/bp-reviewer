"""Bounded whole-project structure index for semantic blueprint planning.

The index is deterministic and never executes the inspected project. Files outside
the Python AST snapshot can be indexed by path and metadata, not source contents.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath

from .models import Snapshot

SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".sql", ".r", ".jl"}
SUPPORT_PARTS = {
    "tests",
    "test",
    "docs",
    "doc",
    "scripts",
    ".github",
    "deploy",
    "deployment",
    "config",
    "configs",
    "examples",
    "notebooks",
    "tools",
}
SUPPORT_LABELS = {
    "tests": "测试与质量验证",
    "test": "测试与质量验证",
    "docs": "文档与说明",
    "doc": "文档与说明",
    "scripts": "运行与维护脚本",
    ".github": "自动化与工程配置",
    "deploy": "部署与配置",
    "deployment": "部署与配置",
    "configs": "配置资源",
    "config": "配置资源",
    "examples": "案例与示例",
    "notebooks": "分析笔记",
    "tools": "通用工具",
}


def domain_for_path(path: str) -> str:
    parts = PurePosixPath(path).parts
    if not parts:
        return "source:root"
    lowered = [part.casefold() for part in parts]
    for part in lowered[:-1]:
        if part in SUPPORT_PARTS:
            return "support:" + part
    if "src" in lowered[:-1]:
        idx = lowered.index("src")
        rest = parts[idx + 1 : -1]
        # src / package / functional-area / file.py
        if len(rest) >= 2:
            return "source:" + "/".join(parts[: idx + 3])
        if rest:
            return "source:" + "/".join(parts[: idx + 2])
        return "source:" + "/".join(parts[: idx + 1])
    if len(parts) > 1:
        return "source:" + parts[0]
    return "source:root"


def fallback_label(key: str) -> str:
    if key.startswith("support:"):
        return SUPPORT_LABELS.get(key.partition(":")[2], "工程辅助")
    leaf = key.partition(":")[2].split("/")[-1]
    return "项目核心代码" if leaf == "root" else leaf.replace("_", " ").replace("-", " ")


def project_catalog(snapshot: Snapshot, entries: list[dict]) -> list[dict]:
    """Group all scan-visible source paths by functional area, not by individual file."""
    files = {
        node.evidence[0].path: node for node in snapshot.graph.nodes if node.evidence and node.id == "file:" + node.evidence[0].path
    }
    for entry in entries:
        if entry.get("kind") != "file" or entry.get("sensitive"):
            continue
        path = entry["path"]
        if PurePosixPath(path).suffix.lower() in SOURCE_EXTENSIONS:
            files.setdefault(path, None)
    groups: dict[str, list[str]] = defaultdict(list)
    for path in sorted(files):
        groups[domain_for_path(path)].append(path)
    degree: Counter[str] = Counter()
    for edge in snapshot.graph.edges:
        if edge.source.startswith("file:"):
            degree[edge.source[5:]] += 1
        if edge.target.startswith("file:"):
            degree[edge.target[5:]] += 1
    catalog = []
    for key, paths in groups.items():
        ranked = sorted(paths, key=lambda p: (-degree[p], p.casefold()))
        symbols = []
        for path in ranked[:12]:
            node = files[path]
            if node is None:
                continue
            symbols.extend(
                child.label for child in snapshot.graph.nodes if child.parent == node.id and child.kind == "function"
            )
            if len(symbols) >= 12:
                break
        catalog.append(
            {
                "key": key,
                "label": fallback_label(key),
                "is_support": key.startswith("support:"),
                "count": len(paths),
                "paths": paths,
                "sample_paths": ranked[:10],
                "sample_symbols": list(dict.fromkeys(symbols))[:12],
            }
        )
    return sorted(catalog, key=lambda group: (group["is_support"], -group["count"], group["key"]))
