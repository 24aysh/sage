"""Evidence-backed aliases, re-exports and receiver resolution for graph edges.

Ambiguity is retained; this module never imports or executes repository code.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable


def resolve_symbol(
    target: str, *, source: dict, nodes: dict[str, dict], files: dict[str, str],
    resolve_import: Callable[..., str | None], receiver: str = "", kind: str = "CALLS",
    by_name: dict[str, list[dict]],
) -> str | None:
    if target in nodes:
        return target
    if "." in target and not target.startswith(".") and not receiver:
        receiver, target = target.rsplit(".", 1)
    file_path = source["file_path"]
    file_node = nodes.get(file_path, {})
    metadata = json.loads(file_node.get("extra_json", "{}"))
    own = json.loads(source.get("extra_json", "{}"))
    parent = nodes.get(source.get("parent_qualified"), {})
    parent_extra = json.loads(parent.get("extra_json", "{}"))
    if receiver in own.get("ambiguous_receivers", []) or receiver in parent_extra.get("ambiguous_receivers", []):
        return None
    imports = {**metadata.get("imports", {}), **own.get("imports", {})}
    # A parameter/local assignment can shadow a file import. Scoped imports
    # remain resolvable; an unrelated global homonym must not win.
    for binding in own.get("bindings", []):
        if binding not in own.get("imports", {}):
            imports.pop(binding, None)

    def candidates(name: str, *, path: str | None = None) -> list[dict]:
        languages = {source.get("language")}
        if source.get("language") in {"javascript", "typescript", "tsx"}:
            languages = {"javascript", "typescript", "tsx"}
        elif source.get("language") in {"html", "css"}:
            languages = {"html", "css"}
        return [n for n in by_name.get(name, [])
                if (path is None or n["file_path"] == path)
                and n.get("language") in languages]

    def bind(module: str, name: str, owner: str, seen: frozenset = frozenset()) -> list[dict]:
        key = (module, name, owner)
        if key in seen or len(seen) >= 12:
            return []
        seen = seen | {key}
        path = resolve_import(module, source_path=owner, file_paths=files)
        if path is None and name:
            path = resolve_import(module + "." + name, source_path=owner, file_paths=files)
        if path is None:
            return []
        module_extra = json.loads(nodes[path].get("extra_json", "{}"))
        exported = module_extra.get("exports", {}).get(name, name)
        alias = module_extra.get("imports", {}).get(exported)
        if alias:
            return bind(alias["module"], alias["symbol"] or exported, path, seen)
        matches = candidates(exported, path=path)
        if not matches and name == "default":
            matches = [n for n in nodes.values() if n["file_path"] == path
                       and n["kind"] in {"Class", "Function"} and n.get("parent_qualified") == path]
        return matches

    receiver_type = own.get("receivers", {}).get(receiver) or parent_extra.get("receivers", {}).get(receiver)
    receiver_type = receiver_type or parent_extra.get("receivers", {}).get(receiver.removeprefix("this."))
    receiver_type = receiver_type or metadata.get("receivers", {}).get(receiver.removeprefix("this."))
    constructor = re.fullmatch(r"(?:new\s+)?([\w.]+)\([^()]*\)", receiver)
    if constructor and constructor[1] != "super" and not receiver_type:
        receiver_type = constructor[1]
    if receiver in {"self", "this", "super()", "super"} and parent:
        matches = [n for n in candidates(target) if n.get("parent_qualified") == parent["qualified_name"]]
        # A base-class method can be inherited without a same-class definition.
        if len(matches) == 1 and receiver not in {"super", "super()"}:
            return matches[0]["qualified_name"]
        inherited = []
        for base in parent_extra.get("bases", []):
            alias = imports.get(base)
            classes = bind(alias["module"], alias["symbol"] or base, file_path) if alias else candidates(base)
            parents = {n["qualified_name"] for n in classes}
            inherited.extend(n for n in candidates(target) if n.get("parent_qualified") in parents)
        if len(inherited) == 1:
            return inherited[0]["qualified_name"]
        return None
    if receiver_type:
        if receiver_type in nodes:
            matches = [n for n in candidates(target) if n.get("parent_qualified") == receiver_type]
            return str(matches[0]["qualified_name"]) if len(matches) == 1 else None
        names = re.findall(r"[\w.]+", receiver_type)
        receiver_type = next((n for n in names if n in imports), names[-1] if names else "")
        imported = imports.get(receiver_type)
        classes = bind(imported["module"], imported["symbol"] or receiver_type, file_path) if imported else candidates(receiver_type)
        parents = {n["qualified_name"] for n in classes}
        matches = [n for n in candidates(target) if n.get("parent_qualified") in parents]
    elif receiver in imports:
        imported = imports[receiver]
        if imported["symbol"]:
            classes = bind(imported["module"], imported["symbol"], file_path)
            parents = {n["qualified_name"] for n in classes}
            matches = [n for n in candidates(target) if n.get("parent_qualified") in parents]
        else:
            matches = bind(imported["module"], target, file_path)
    elif not receiver and target in imports:
        matches = bind(imports[target]["module"], imports[target]["symbol"] or target, file_path)
    elif not receiver and target in own.get("bindings", []):
        return None
    elif receiver:
        parents = {n["qualified_name"] for n in candidates(receiver) if n["kind"] in {"Class", "Type"}}
        matches = [n for n in candidates(target) if n.get("parent_qualified") in parents]
    else:
        matches = candidates(target)
        local = [n for n in matches if n["file_path"] == file_path]
        if not local and source.get("language") == "java":
            local = [n for n in matches if n["file_path"].rsplit("/", 1)[0] == file_path.rsplit("/", 1)[0]]
        if local:
            matches = local
    return str(matches[0]["qualified_name"]) if len(matches) == 1 else None
