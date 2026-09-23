"""Bounded Python symbol metadata used by resolution and source navigation."""

from __future__ import annotations

import ast
import re


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
        if isinstance(node, ast.ClassDef):
            extra["bases"] = [ast.unparse(base) for base in node.bases[:10]]
            extra["fields"] = {n.target.id: ast.unparse(n.annotation) for n in node.body
                               if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
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
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            extra["parameters"] = [a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
            extra["assignments"] = [
                {"target": ast.unparse(target)[:200], "value": ast.unparse(statement.value)[:1000]}
                for statement in node.body if isinstance(statement, (ast.Assign, ast.AnnAssign)) and statement.value
                for target in (statement.targets if isinstance(statement, ast.Assign) else [statement.target])
            ][:30]
            extra["returns"] = [ast.unparse(n.value)[:1000] for n in node.body
                                if isinstance(n, ast.Return) and n.value][:10]
            extra["bindings"] = [a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
            local_imports = {}
            for statement in node.body:
                if isinstance(statement, ast.ImportFrom):
                    for alias in statement.names:
                        local_imports[alias.asname or alias.name] = {
                            "module": "." * statement.level + (statement.module or ""), "symbol": alias.name,
                        }
                elif isinstance(statement, ast.Assign):
                    extra["bindings"].extend(t.id for t in statement.targets if isinstance(t, ast.Name))
            extra["imports"] = local_imports
        result[(node.name, node.lineno)] = extra
    blueprints = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        if isinstance(call.func, ast.Name) and call.func.id in imports and imports[call.func.id] == {"module": "flask", "symbol": "Blueprint"}:
            prefix = next((k.value.value for k in call.keywords if k.arg == "url_prefix"
                           and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)), "")
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    blueprints[target.id] = prefix
    return {"imports": dict(list(imports.items())[:50]), "blueprints": blueprints}, result


def tree_metadata(root, content: bytes, language: str) -> dict[str, object]:
    """Import aliases, re-exports and typed receivers from syntax nodes.

    Uses parsed declarations, not comments/string regexes over whole source.
    All paths are interpreted against the committed graph inventory by resolution.
    """
    imports: dict[str, dict[str, str]] = {}
    exports: dict[str, str] = {}
    receivers: dict[str, str] = {}

    def text(node) -> str:
        return content[node.start_byte:node.end_byte].decode("utf-8", errors="replace") if node else ""

    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        value = text(node)
        if language in {"javascript", "typescript", "tsx"}:
            if node.type in {"import_statement", "export_statement"}:
                source = node.child_by_field_name("source")
                module = text(source).strip("'\"")
                if module:
                    clause = value.split("from", 1)[0]
                    names = re.search(r"\{([^}]+)\}", clause)
                    if names:
                        for part in names[1].split(","):
                            pair = re.split(r"\s+as\s+", part.strip().removeprefix("type "))
                            if pair[0]:
                                imports[pair[-1]] = {"module": module, "symbol": pair[0]}
                                if node.type == "export_statement":
                                    exports[pair[-1]] = pair[-1]
                    namespace = re.search(r"\*\s+as\s+(\w+)", clause)
                    default = re.match(r"import\s+(\w+)(?:\s*,|\s*$)", clause)
                    if namespace:
                        imports[namespace[1]] = {"module": module, "symbol": ""}
                    elif default:
                        imports[default[1]] = {"module": module, "symbol": "default"}
                if node.type == "export_statement" and re.match(r"export\s+default\b", value):
                    match = re.match(r"export\s+default\s+(?:(?:async\s+)?(?:function|class)\s+)?(\w+)", value)
                    if match:
                        exports["default"] = match[1]
            elif node.type == "variable_declarator":
                name = text(node.child_by_field_name("name"))
                rhs = text(node.child_by_field_name("value"))
                required = re.fullmatch(r"require\(['\"]([^'\"]+)['\"]\)", rhs)
                if required:
                    for part in name.strip("{} ").split(","):
                        pair = [v.strip() for v in part.split(":")]
                        imports[pair[-1]] = {"module": required[1], "symbol": pair[0] if name.startswith("{") else ""}
                constructed = re.match(r"new\s+([\w.]+)", rhs)
                if constructed and (root.type not in {"program", "module"} or node.parent.parent == root):
                    receivers[name] = constructed[1]
            elif node.type == "assignment_expression":
                left = text(node.child_by_field_name("left"))
                right = text(node.child_by_field_name("right"))
                if left.startswith("exports.") or left.startswith("module.exports."):
                    exports[left.rsplit(".", 1)[-1]] = right
                elif left == "module.exports" and re.fullmatch(r"\w+", right):
                    exports["default"] = right
            elif node.type in {"required_parameter", "optional_parameter"}:
                name = text(node.child_by_field_name("pattern"))
                annotation = text(node.child_by_field_name("type")).lstrip(": ")
                if name and annotation and root.type not in {"program", "module"}:
                    receivers[name] = annotation
        elif language in {"java", "kotlin", "scala", "csharp"}:
            if node.type in {"import_declaration", "using_directive"}:
                module = re.sub(r"^(?:import|using)\s+(?:static\s+)?", "", value).strip("; \n")
                parts = module.rsplit(".", 1)
                if len(parts) == 2:
                    imports[parts[-1]] = {"module": parts[0], "symbol": parts[-1]}
            elif node.type in {"formal_parameter", "parameter", "field_declaration", "local_variable_declaration"}:
                annotation = text(node.child_by_field_name("type"))
                declarators = [node, *node.named_children]
                for declaration in declarators:
                    name = text(declaration.child_by_field_name("name"))
                    if name and annotation and root.type not in {"program", "module"}:
                        receivers[name] = annotation
        elif language == "go" and node.type == "import_spec":
            module = text(node.child_by_field_name("path")).strip('"')
            alias = text(node.child_by_field_name("name")) or module.rsplit("/", 1)[-1]
            imports[alias] = {"module": module, "symbol": ""}
        elif language == "go" and node.type == "parameter_declaration" and root.type != "source_file":
            name = text(node.child_by_field_name("name"))
            annotation = text(node.child_by_field_name("type")).lstrip("*")
            if name and annotation:
                receivers[name] = annotation
        elif language == "rust":
            if node.type == "use_declaration":
                match = re.fullmatch(r"use\s+([\w:]+)(?:\s+as\s+(\w+))?;", value)
                if match and "::" in match[1]:
                    module, name = match[1].rsplit("::", 1)
                    imports[match[2] or name] = {"module": module, "symbol": name}
            elif node.type == "let_declaration" and root.type != "source_file":
                name = text(node.child_by_field_name("pattern"))
                annotation = text(node.child_by_field_name("type"))
                rhs = text(node.child_by_field_name("value"))
                constructor = re.match(r"([\w:]+)::(?:new|default)\(", rhs)
                if annotation or constructor:
                    receivers[name] = annotation or constructor[1]
    return {"imports": dict(list(imports.items())[:50]),
            "exports": dict(list(exports.items())[:50]),
            "receivers": dict(list(receivers.items())[:50])}
