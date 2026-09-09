"""Bounded static propagation through Python constructors and local factories.

Only parsed expressions are inspected: indexed code is never imported/executed.
Unions are retained to avoid resolving conflicting dependency-injection sites.
"""

from __future__ import annotations

import ast
import json
from collections import defaultdict
from collections.abc import Callable


def infer_composition(nodes: dict[str, dict], bind: Callable[[str, dict], str | None]) -> Callable[[str, str], str | None]:
    metadata = {qn: json.loads(n.get("extra_json", "{}")) for qn, n in nodes.items()
                if n.get("language") == "python" and not n.get("is_test")}
    variables: dict[tuple[str, str], set[str]] = defaultdict(set)
    fields: dict[tuple[str, str], set[str]] = defaultdict(set)
    returns: dict[str, set[str]] = defaultdict(set)
    registry: dict[str, set[str]] = defaultdict(set)
    methods = {(n.get("parent_qualified"), n["name"]): qn for qn, n in nodes.items()}
    unknown = "<unknown-composition>"
    sealed = False

    def parse(value: str) -> ast.expr | None:
        try:
            return ast.parse(value, mode="eval").body
        except (SyntaxError, ValueError, RecursionError):
            return None

    def registry_key(expr: ast.expr | None, scope: str) -> str | None:
        if not (isinstance(expr, ast.Subscript) and isinstance(expr.value, ast.Attribute)
                and expr.value.attr == "extensions" and isinstance(expr.slice, ast.Constant)
                and isinstance(expr.slice.value, str)):
            return None
        # An app-local write or imported Flask current_app read, not arbitrary subscripts.
        receiver = expr.value.value
        if isinstance(receiver, ast.Name):
            imported = metadata.get(nodes[scope]["file_path"], {}).get("imports", {}).get(receiver.id)
            if imported == {"module": "flask", "symbol": "current_app"}:
                return expr.slice.value
            source = metadata.get(scope, {})
            annotation = source.get("receivers", {}).get(receiver.id)
            flask_import = metadata.get(nodes[scope]["file_path"], {}).get("imports", {}).get(annotation)
            if flask_import == {"module": "flask", "symbol": "Flask"}:
                return expr.slice.value
        return None

    def types(expr: ast.expr | None, scope: str, depth: int = 0) -> set[str]:
        if expr is None or depth > 8:
            return set()
        if key := registry_key(expr, scope):
            return set(registry[key])
        if isinstance(expr, ast.Name):
            if expr.id in {"self", "cls"}:
                owner = nodes[scope].get("parent_qualified")
                return {owner} if owner in nodes and nodes[owner]["kind"] == "Class" else set()
            found = variables[(scope, expr.id)]
            if found:
                return set(found)
            target = bind(expr.id, nodes[scope])
            return {target} if target in nodes and nodes[target]["kind"] == "Class" else set()
        if isinstance(expr, ast.Attribute):
            return set().union(*(fields[(owner, expr.attr)] for owner in types(expr.value, scope, depth + 1)))
        if isinstance(expr, ast.Call):
            target = bind(ast.unparse(expr.func), nodes[scope])
            if target in nodes and nodes[target]["kind"] == "Class":
                constructor = methods.get((target, "__init__"))
                if constructor:
                    parameters = [p for p in metadata.get(constructor, {}).get("parameters", []) if p not in {"self", "cls"}]
                    for param, arg in zip(parameters, expr.args):
                        inferred = types(arg, scope, depth + 1)
                        variables[(constructor, param)].update(inferred or ({unknown} if sealed else set()))
                    for arg in expr.keywords:
                        if arg.arg in parameters:
                            inferred = types(arg.value, scope, depth + 1)
                            variables[(constructor, arg.arg)].update(inferred or ({unknown} if sealed else set()))
                # Dataclass/named container fields have explicit annotations.
                return {target}
            return set(returns.get(target, ()))
        return set()

    expressions = {}
    for qn, extra in metadata.items():
        for name, annotation in extra.get("receivers", {}).items():
            target = bind(annotation, nodes[qn])
            if target in nodes and nodes[target]["kind"] == "Class":
                variables[(qn, name)].add(target)
        for name, annotation in extra.get("fields", {}).items():
            target = bind(annotation, nodes[qn])
            if target in nodes:
                fields[(qn, name)].add(target)
        returned = bind(extra.get("return_type", ""), nodes[qn]) if extra.get("return_type") else None
        if returned in nodes and nodes[returned]["kind"] == "Class":
            returns[qn].add(returned)
        expressions[qn] = ([(parse(a["target"]), parse(a["value"])) for a in extra.get("assignments", [])],
                           [parse(value) for value in extra.get("returns", [])])

    # Bounded monotone union: never let a later site overwrite ambiguity.
    for iteration in range(24):
        if iteration == 12:
            sealed = True
        before = sum(map(len, (*variables.values(), *fields.values(), *returns.values(), *registry.values())))
        for qn, (assignments, returned) in expressions.items():
            for target, value in assignments:
                inferred = types(value, qn)
                if isinstance(target, ast.Name):
                    variables[(qn, target.id)].update(inferred)
                elif isinstance(target, ast.Attribute):
                    for owner in types(target.value, qn):
                        fields[(owner, target.attr)].update(inferred)
                elif key := registry_key(target, qn):
                    registry[key].update(inferred)
            for value in returned:
                returns[qn].update(types(value, qn))
        after = sum(map(len, (*variables.values(), *fields.values(), *returns.values(), *registry.values())))
        if before == after and sealed:
            break
    scoped_variables = defaultdict(list)
    scoped_fields = defaultdict(list)
    for (scope, name), targets in variables.items():
        scoped_variables[scope].append((name, targets))
    for (cls, name), targets in fields.items():
        scoped_fields[cls].append((name, targets))
    for qn, extra in metadata.items():
        inferred = dict(extra.get("receivers", {}))
        ambiguous = []
        for name, targets in scoped_variables[qn]:
            if len(targets) == 1 and unknown not in targets:
                inferred[name] = next(iter(targets))
            elif targets:
                ambiguous.append(name)
        owner = nodes[qn].get("parent_qualified")
        for name, targets in (*scoped_fields[owner], *scoped_fields[qn]):
            if len(targets) == 1 and unknown not in targets:
                inferred["self." + name] = next(iter(targets))
            elif targets:
                ambiguous.append("self." + name)
        extra["receivers"] = {k: v for k, v in inferred.items() if k not in ambiguous}
        extra["ambiguous_receivers"] = ambiguous
        # Resolve compound receivers such as services().webhooks using the same evidence.
        nodes[qn]["extra_json"] = json.dumps(extra)

    # Store only receiver results actually needed by edges in the caller via a callback.
    def receiver_type(expression: str, scope: str) -> str | None:
        if scope not in metadata:
            return None
        found = types(parse(expression), scope)
        return next(iter(found)) if len(found) == 1 and unknown not in found else None
    return receiver_type
