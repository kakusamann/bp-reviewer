"""Read-only, bounded AST tools for investigating module inputs and outputs.

Every result points to a real source path and line. Never import or execute target code.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .scanner import safe_file

READ_NAMES = {"read_csv", "read_excel", "read_json", "read_parquet", "load", "loadtxt", "read_text", "read_bytes"}
WRITE_NAMES = {"to_csv", "to_excel", "to_json", "to_parquet", "dump", "save", "savetxt", "write_text", "write_bytes"}
EXTENSIONS = {
    ".csv",
    ".json",
    ".jsonl",
    ".xlsx",
    ".xls",
    ".parquet",
    ".pickle",
    ".pkl",
    ".txt",
    ".npy",
    ".npz",
    ".graphml",
    ".ttl",
    ".rdf",
    ".gexf",
}


def _parse(root: Path, path: str) -> ast.Module:
    if not isinstance(path, str) or Path(path).suffix.lower() != ".py":
        raise ValueError("Only indexed Python files can be inspected")
    file = safe_file(root, path)
    if file.stat().st_size > 200_000:
        raise ValueError("Source file exceeds safe AST analysis limit")
    return ast.parse(file.read_text(encoding="utf-8-sig", errors="replace"), filename=path)


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def inspect_file(root: Path, path: str) -> dict[str, Any]:
    """Return concrete static declarations and literal data I/O evidence."""
    tree = _parse(root, path)
    functions: list[dict[str, Any]] = []
    io: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    for item in ast.walk(tree):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in (item.args.posonlyargs + item.args.args + item.args.kwonlyargs)]
            functions.append(
                {
                    "name": item.name,
                    "line": item.lineno,
                    "parameters": args[:24],
                    "returns": ast.unparse(item.returns)[:100] if item.returns else "",
                }
            )
        if isinstance(item, ast.Call):
            name = _call_name(item.func)
            if name:
                calls.append({"name": name, "line": item.lineno})
            direction = "input" if name in READ_NAMES else "output" if name in WRITE_NAMES else None
            if name == "open":
                direction = "input"
                mode_node = (
                    item.args[1]
                    if len(item.args) > 1
                    else next((kw.value for kw in item.keywords if kw.arg == "mode"), None)
                )
                if isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str):
                    if any(c in mode_node.value for c in "wax+"):
                        direction = "output"
            if not direction:
                continue
            first = (
                item.args[0]
                if item.args
                else next(
                    (kw.value for kw in item.keywords if kw.arg in {"path", "filepath", "path_or_buf", "file"}), None
                )
            )
            if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
                continue
            literal = first.value.strip().replace("\\", "/")
            if not literal or len(literal) > 180 or "\x00" in literal or "://" in literal:
                continue
            if Path(literal).suffix.lower() not in EXTENSIONS:
                continue
            io.append(
                {
                    "direction": direction,
                    "name": literal,
                    "operation": name,
                    "path": path,
                    "line": item.lineno,
                    "data_type": Path(literal).suffix.lower().lstrip("."),
                }
            )
    return {"path": path, "functions": functions[:60], "io": io[:60], "calls": calls[:120]}


def tool_schema() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "inspect_function",
                "description": "查询实际 Python 函数签名、返回标注、定义行，不执行源码",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "name": {"type": "string"}},
                    "required": ["path", "name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "trace_data_io",
                "description": "检索真实 Python 文件的常见字面量文件读取写入及源码行号",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "trace_function_calls",
                "description": "检索 Python AST 函数调用名称与行号（候选而非运行时调用）",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_module_structure",
                "description": "枚举所选文件的真实函数与可验证文件 I/O 摘要",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
    ]


def execute_tool(root: Path, allowed_paths: list[str], name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Allowlisted tool dispatcher: model cannot use it to read outside the current scope."""
    allowed = {path for path in allowed_paths if Path(path).suffix.lower() == ".py"}
    if name == "get_module_structure":
        return {"files": [inspect_file(root, path) for path in sorted(allowed)[:12]]}
    path = args.get("path")
    if not isinstance(path, str) or path not in allowed:
        raise ValueError("Tool path is outside of the selected logical module")
    facts = inspect_file(root, path)
    if name == "inspect_function":
        return {"path": path, "functions": [f for f in facts["functions"] if f["name"] == args.get("name")]}
    if name == "trace_data_io":
        return {"path": path, "io": facts["io"]}
    if name == "trace_function_calls":
        return {"path": path, "calls": facts["calls"]}
    raise ValueError("Unrecognized investigation tool")


def collect_evidence(root: Path, paths: list[str], limit: int = 24) -> dict[str, Any]:
    """Deterministic analysis always runs before optional LLM tool investigation."""
    files = []
    for path in sorted(set(paths)):
        if Path(path).suffix.lower() != ".py":
            continue
        if len(files) >= limit:
            break
        try:
            files.append(inspect_file(root, path))
        except (OSError, ValueError, SyntaxError, UnicodeError):
            continue
    return {"files": files, "io": [record for file in files for record in file["io"]][:120]}
