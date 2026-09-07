"""Bounded Python symbol metadata used by resolution and embedding recipes."""

from __future__ import annotations

import ast


def python_metadata(content: bytes) -> tuple[dict[str, object], dict[tuple[str, int], dict[str, object]]]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return {}, {}
    imports: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    imports[alias.asname or alias.name] = {"module": "." * node.level + (node.module or ""), "symbol": alias.name}
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split(".")[0]] = {"module": alias.name, "symbol": ""}
    result = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        extra: dict[str, object] = {
            "docstring": " ".join((ast.get_docstring(node) or "").split())[:1500],
            "decorators": [ast.unparse(d)[:200] for d in node.decorator_list[:10]],
        }
        receivers: dict[str, str] = {}
        functions = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] if isinstance(node, ast.ClassDef) else [node]
        for function in functions:
            annotations = {a.arg: ast.unparse(a.annotation) for a in (*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs) if a.annotation}
            if function is node:
                extra["params"] = ast.unparse(function.args)[:1000]
                extra["return_type"] = ast.unparse(function.returns)[:300] if function.returns else ""
                extra["signature"] = f"def {function.name}({extra['params']})" + (f" -> {extra['return_type']}" if extra["return_type"] else "")
                receivers.update(annotations)
            # Only direct statements: nested scopes must not leak receiver types.
            for statement in function.body:
                if isinstance(statement, ast.Assign):
                    value = statement.value
                    inferred = ast.unparse(value.func) if isinstance(value, ast.Call) else annotations.get(value.id, "") if isinstance(value, ast.Name) else ""
                    if inferred:
                        for target in statement.targets:
                            receivers[ast.unparse(target)] = inferred
                elif isinstance(statement, ast.AnnAssign) and statement.annotation:
                    receivers[ast.unparse(statement.target)] = ast.unparse(statement.annotation)
        extra["receivers"] = dict(list(receivers.items())[:30])
        result[(node.name, node.lineno)] = extra
    return {"imports": dict(list(imports.items())[:50])}, result
