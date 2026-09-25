"""Bounded JSONC aliases from indexed files only; no package or filesystem reads."""

from __future__ import annotations

import json
import posixpath
import re


def parse_tsconfig(content: bytes) -> dict:
    # Match strings before comments so URLs and comment-looking paths survive.
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/')
    text = tokens.sub(lambda m: m[0] if m[0].startswith('"') else " ", content.decode("utf-8-sig"))
    text = re.sub(r'("(?:\\.|[^"\\])*")|,(\s*[}\]])',
                  lambda m: m[1] if m[1] else m[2], text)
    data = json.loads(text)
    options = data.get("compilerOptions", {})
    if not isinstance(options, dict):
        raise ValueError("Invalid compilerOptions")
    result = {}
    if "baseUrl" in options:
        if not isinstance(options["baseUrl"], str):
            raise ValueError("Invalid baseUrl")
        result["baseUrl"] = options["baseUrl"][:500]
    if "paths" in options:
        paths = options["paths"]
        if not isinstance(paths, dict):
            raise ValueError("Invalid paths")
        result["paths"] = {key: values[:10] for key, values in list(paths.items())[:50]
                           if isinstance(key, str) and key.count("*") <= 1 and isinstance(values, list)
                           and all(isinstance(v, str) and len(v) <= 500 for v in values)}
    if "extends" in data:
        if not isinstance(data["extends"], str) or not data["extends"].startswith("."):
            raise ValueError("Only relative single-file tsconfig extends is supported")
        result["extends"] = data["extends"][:500]
    return result


def config_aliases(config: str, nodes: dict[str, dict], seen: frozenset[str] = frozenset()) -> dict:
    if config in seen or len(seen) >= 12 or config not in nodes:
        return {}
    options = json.loads(nodes[config]["extra_json"]).get("tsconfig", {})
    directory = posixpath.dirname(config)
    inherited = {}
    if parent := options.get("extends"):
        path = posixpath.normpath(posixpath.join(directory, parent))
        if not path.endswith((".json", ".jsonc")):
            path += ".json"
        if path.startswith("../") or path.startswith("/") or path not in nodes or path in seen:
            return {}
        inherited = config_aliases(path, nodes, seen | {config})
    base = posixpath.normpath(posixpath.join(directory, options["baseUrl"])) if "baseUrl" in options else inherited.get("base", directory)
    if base.startswith(("../", "/")):
        return {}
    aliases = dict(inherited.get("paths", {}))
    if "paths" in options:
        aliases = {key: [posixpath.normpath(posixpath.join(base, value)) for value in values]
                   for key, values in options["paths"].items()}
    return {"base": base, "paths": aliases}
