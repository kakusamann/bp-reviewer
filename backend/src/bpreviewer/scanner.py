"""Bounded AST inspection. Never import, compile, exec or run target scripts."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path

from .models import Edge, Evidence, Graph, Manifest, Node, Port, Snapshot

EXCLUDED = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    "workplace",
    "state",
    ".tox",
    ".pytest_cache",
}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("Only relative paths within the project are permitted")
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Symlinks/junctions are not inspected")
    if not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError("Source file unavailable")
    return path


TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".xml", ".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".sql",
    ".sh", ".ps1", ".ipynb", ".r", ".jl",
}
SENSITIVE_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "id_rsa", "id_ed25519", "known_hosts",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".crt", ".cer", ".der"}


def is_sensitive_path(relative: str) -> bool:
    name = Path(relative).name.lower()
    return (
        name in SENSITIVE_NAMES
        or (name.startswith(".env") and name != ".env.example")
        or Path(name).suffix.lower() in SENSITIVE_SUFFIXES
    )


def is_previewable(relative: str, size: int | None = None) -> bool:
    if is_sensitive_path(relative):
        return False
    if size is not None and size > 1_000_000:
        return False
    return Path(relative).suffix.lower() in TEXT_SUFFIXES or Path(relative).name.lower() == ".env.example"


def directory_tree(root: Path, max_entries: int = 10000, max_depth: int = 16) -> dict:
    """Return a bounded, read-only directory index; never read file contents."""
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project must be a directory")
    entries = []
    truncated = False
    for base, dirs, names in os.walk(
        root, followlinks=False, onerror=lambda error: None
    ):
        directory = Path(base)
        depth = len(directory.relative_to(root).parts)
        dirs[:] = sorted(
            d for d in dirs
            if d not in EXCLUDED
            and not (directory / d).is_symlink()
            and not (hasattr(directory / d, "is_junction") and (directory / d).is_junction())
            and (directory / d).resolve().is_relative_to(root)
        )
        depth_limited = depth >= max_depth
        if depth_limited:
            if dirs:
                truncated = True
            dirs[:] = []
        for name in dirs:
            if len(entries) >= max_entries:
                truncated = True
                break
            rel = (directory / name).relative_to(root).as_posix()
            entries.append({
                "path": rel, "name": name, "parent": directory.relative_to(root).as_posix() if depth else "",
                "kind": "directory", "depth": depth, "size": None, "extension": "",
                "modified_ns": (directory / name).stat().st_mtime_ns,
                "previewable": False, "sensitive": False,
            })
        if len(entries) >= max_entries:
            truncated = True
            break
        for name in sorted(names, key=str.casefold):
            if len(entries) >= max_entries:
                truncated = True
                break
            rel_path = directory / name
            if rel_path.is_symlink() or (hasattr(rel_path, "is_junction") and rel_path.is_junction()):
                continue
            try:
                if not rel_path.is_file() or not rel_path.resolve().is_relative_to(root):
                    continue
                size = rel_path.stat().st_size
            except OSError:
                continue
            rel = rel_path.relative_to(root).as_posix()
            entries.append({
                "path": rel, "name": name, "parent": directory.relative_to(root).as_posix() if depth else "",
                "kind": "file", "depth": depth, "size": size, "extension": rel_path.suffix.lower(),
                "modified_ns": rel_path.stat().st_mtime_ns,
                "previewable": is_previewable(rel, size), "sensitive": is_sensitive_path(rel),
            })
        if len(entries) >= max_entries:
            truncated = True
            break
    entries.sort(key=lambda item: (
        item["parent"].casefold(),
        0 if item["kind"] == "directory" else 1,
        item["name"].casefold(),
    ))
    return {"entries": entries, "entry_count": len(entries), "truncated": truncated}


def module_name(relative: str) -> str:
    parts = list(Path(relative).with_suffix("").parts)
    if "src" in parts:
        parts = parts[parts.index("src") + 1 :]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def scan(
    root: Path, manifest_path: str | None = None, max_files: int = 1000, max_nodes: int = 5000, max_depth: int = 12
) -> Snapshot:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project must be a directory")
    nodes, edges, warnings, records, modules = [], [], [], {}, {}
    total = 0
    for base, dirs, names in os.walk(
        root, followlinks=False, onerror=lambda error: warnings.append(str(type(error).__name__))
    ):
        directory = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in EXCLUDED
            and not d.startswith(".")
            and not (directory / d).is_symlink()
            and not (hasattr(directory / d, "is_junction") and (directory / d).is_junction())
            and (directory / d).resolve().is_relative_to(root)
        )
        if len(directory.relative_to(root).parts) >= max_depth:
            if dirs:
                warnings.append("Depth limit reached; project view is partial")
            dirs[:] = []
        for name in sorted(names):
            if not name.endswith(".py") or name.startswith("."):
                continue
            total += 1
            if total > max_files or len(nodes) >= max_nodes:
                warnings.append("File/node limit reached; project view is partial")
                break
            rel = (directory / name).relative_to(root).as_posix()
            try:
                path = safe_file(root, rel)
                if path.stat().st_size > 1_000_000:
                    warnings.append(f"Skipped large Python file: {rel}")
                    continue
                raw = path.read_bytes()
                tree = ast.parse(raw, filename=rel)
            except (ValueError, OSError, SyntaxError) as error:
                warnings.append(f"Skipped {rel}: {type(error).__name__}")
                continue
            proof = Evidence(path=rel, sha256=digest(raw))
            key = "file:" + rel
            ports = [Port(id="in", io="input"), Port(id="out", io="output")]
            nodes.append(Node(id=key, label=rel, ports=ports, evidence=[proof]))
            mod = module_name(rel)
            modules.setdefault(mod, []).append(key)
            functions = {}
            ambiguous = set()
            for item in tree.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if len(nodes) >= max_nodes:
                        warnings.append("Node limit reached; symbols omitted")
                        break
                    fid = key + ":" + item.name + ":" + str(item.lineno)
                    args = [*item.args.posonlyargs, *item.args.args, *item.args.kwonlyargs]
                    fp = [
                        Port(
                            id=a.arg,
                            label=a.arg,
                            io="input",
                            data_type=ast.unparse(a.annotation) if a.annotation else None,
                        )
                        for a in args
                    ]
                    fp += [
                        Port(id="$return", io="output", data_type=ast.unparse(item.returns) if item.returns else None),
                        Port(id="$call", io="input"),
                        Port(id="$calls", io="output"),
                    ]
                    nodes.append(
                        Node(
                            id=fid,
                            label=item.name,
                            kind="function",
                            parent=key,
                            ports=fp,
                            evidence=[proof.model_copy(update={"line": item.lineno})],
                            description=ast.get_docstring(item) or "",
                        )
                    )
                    if item.name in functions:
                        ambiguous.add(item.name)
                    functions[item.name] = fid
            for duplicate in ambiguous:
                functions.pop(duplicate, None)
            records[key] = (tree, functions, proof, mod, name == "__init__.py")
        if total > max_files or len(nodes) >= max_nodes:
            break
    for key, (tree, functions, proof, mod, is_package) in records.items():
        bindings = {}
        for item in tree.body:
            candidates = []
            if isinstance(item, ast.Import):
                candidates = [(a.name, a.asname or a.name.split(".")[0], "") for a in item.names]
            elif isinstance(item, ast.ImportFrom):
                base = mod.split(".") if is_package else mod.split(".")[:-1]
                prefix = base[: len(base) - item.level + 1] if item.level else []
                module = ".".join([*prefix, *([item.module] if item.module else [])])
                candidates = [(module, a.asname or a.name, a.name) for a in item.names]
            for candidate_index, (module, alias, symbol) in enumerate(candidates):
                targets = modules.get(module, [])
                if len(targets) == 1:
                    target = targets[0]
                    ep = proof.model_copy(update={"line": item.lineno})
                    edges.append(
                        Edge(
                            id=digest(f"{key}|{target}|{item.lineno}|{candidate_index}".encode())[:24],
                            source=key,
                            source_port="out",
                            target=target,
                            target_port="in",
                            kind="import",
                            status="STATIC_INFERRED",
                            evidence=[ep],
                        )
                    )
                    bindings[alias] = (target, symbol)
        for item in tree.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) or item.name not in functions:
                continue
            # Assignments/arguments can shadow imports or local function names: abstain.
            shadowed = {n.id for n in ast.walk(item) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
            shadowed.update(a.arg for a in [*item.args.args, *item.args.posonlyargs, *item.args.kwonlyargs])
            for call in ast.walk(item):
                if not isinstance(call, ast.Call):
                    continue
                target = None
                if isinstance(call.func, ast.Name) and call.func.id not in shadowed:
                    target = functions.get(call.func.id)
                    if not target and call.func.id in bindings:
                        file_id, symbol = bindings[call.func.id]
                        target = records[file_id][1].get(symbol)
                elif (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id not in shadowed
                ):
                    binding = bindings.get(call.func.value.id)
                    if binding and not binding[1]:
                        target = records[binding[0]][1].get(call.func.attr)
                if target:
                    edges.append(
                        Edge(
                            id=digest(f"{functions[item.name]}|{target}|{call.lineno}|{call.col_offset}".encode())[:24],
                            source=functions[item.name],
                            source_port="$calls",
                            target=target,
                            target_port="$call",
                            kind="control",
                            status="STATIC_INFERRED",
                            evidence=[
                                proof.model_copy(
                                    update={"line": call.lineno, "note": "静态调用候选；不证明执行或数据传输"}
                                )
                            ],
                        )
                    )
    require_dag = False
    manifest_name = manifest_path or "bpreviewer.manifest.json"
    if manifest_path or (root / manifest_name).exists():
        path = safe_file(root, manifest_name)
        if path.stat().st_size > 1_000_000:
            raise ValueError("Manifest exceeds 1 MB")
        raw = path.read_bytes()
        manifest = Manifest.model_validate_json(raw)
        existing = {n.id for n in nodes}
        for node in manifest.graph.nodes:
            if node.id in existing:
                raise ValueError("Manifest node IDs must not collide with scanned IDs")
            node.evidence = [Evidence(path=manifest_name, sha256=digest(raw), status="DECLARED")]
            nodes.append(node)
        for edge in manifest.graph.edges:
            edge.status = "DECLARED"
            edge.evidence = [Evidence(path=manifest_name, sha256=digest(raw), status="DECLARED")]
            edges.append(edge)
        require_dag = manifest.require_dag
    graph = Graph(nodes=nodes, edges=edges)
    if len(edges) > 20000:
        raise ValueError("Edge limit exceeded")
    # git is used solely for rev-parse, with optional locks disabled; no target command executes.
    head = None
    if (root / ".git").exists():
        try:
            head = subprocess.check_output(
                ["git", "--no-optional-locks", "-C", str(root), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            warnings.append("Git HEAD unavailable")
    warnings.append("静态扫描不恢复完整运行数据流；类方法、动态分派、外部库和隐式数据流需补充 manifest。")
    snapshot_id = digest(
        json.dumps({"root": str(root), "head": head, "graph": graph.model_dump()}, sort_keys=True).encode()
    )
    return Snapshot(
        id=snapshot_id,
        project_name=root.name,
        git_head=head,
        graph=graph,
        warnings=sorted(set(warnings)),
        require_dag=require_dag,
    )
