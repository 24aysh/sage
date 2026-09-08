"""Tree-sitter structural extraction.

The port intentionally keeps a compact, grammar-driven common denominator.
Language-specific enrichment can be added behind this normalized boundary
without changing SQLite or tool contracts.
"""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from tree_sitter import Node
from tree_sitter_language_pack import get_parser
from sage.legion_memory.symbol_metadata import python_metadata, tree_metadata
from sage.legion_memory.tsconfig import parse_tsconfig

PARSER_VERSION = "legion-tree-sitter-v4"
MAX_FILE_BYTES = 2_000_000

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".json": "json",
    ".jsonc": "json",
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".c": "c",
    ".h": "c",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".php": "php",
    ".scala": "scala",
    ".dart": "dart",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".ex": "elixir",
    ".exs": "elixir",
    ".zig": "zig",
    ".jl": "julia",
    ".tf": "hcl",
    ".hcl": "hcl",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".nix": "nix",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".svelte": "svelte",
    ".vue": "vue",
    ".r": "r",
    ".pl": "perl",
    ".pm": "perl",
    ".m": "objc",
    ".sol": "solidity",
}

_CLASS_TYPES = frozenset(
    {
        "class_definition",
        "class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "struct_item",
        "enum_item",
        "trait_item",
        "type_spec",
        "class_specifier",
        "struct_specifier",
        "record_declaration",
        "object_declaration",
        "protocol_declaration",
    }
)
_FUNCTION_TYPES = frozenset(
    {
        "function_definition",
        "function_declaration",
        "method_definition",
        "method_declaration",
        "function_item",
        "method",
        "singleton_method",
        "constructor_declaration",
        "local_function_statement",
        "function_signature",
        "arrow_function",
        "function_expression",
    }
)
_IMPORT_TYPES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "use_declaration",
        "namespace_use_declaration",
        "preproc_include",
        "include_statement",
        "using_directive",
    }
)
_CALL_TYPES = frozenset(
    {
        "call",
        "call_expression",
        "function_call",
        "method_invocation",
        "invocation_expression",
        "command",
        "macro_invocation",
    }
)
_IMPL_TYPES = frozenset({"impl_item", "extension_declaration"})
@dataclass(frozen=True)
class NodeRecord:
    """One normalized graph node."""

    kind: str
    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    language: str
    parent_qualified: str | None = None
    signature: str | None = None
    is_test: bool = False
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeRecord:
    """One normalized directed graph edge."""

    kind: str
    source_qualified: str
    target_qualified: str
    file_path: str
    line: int
    confidence: float = 1.0
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedFile:
    """Complete replaceable graph contribution from one file."""

    file_path: str
    file_hash: str
    language: str
    nodes: tuple[NodeRecord, ...]
    edges: tuple[EdgeRecord, ...]
    warnings: tuple[str, ...] = ()


def normalize_path(path: str | Path) -> str:
    """Return one stable repository-relative POSIX path spelling."""

    normalized = PurePosixPath(str(path).replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def detect_language(path: str | Path) -> str | None:
    """Resolve a supported grammar from a file suffix."""

    return EXTENSION_TO_LANGUAGE.get(Path(path).suffix.lower())


def hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class CodeParser:
    """Extract a bounded common structural graph through language-pack grammars."""

    def __init__(self) -> None:
        self._parsers: dict[str, object] = {}

    def parse(self, path: Path, *, relative_path: str) -> ParsedFile:
        return self.parse_bytes(path.read_bytes(), relative_path=relative_path)

    def parse_bytes(
        self,
        content: bytes,
        *,
        relative_path: str,
        file_hash: str | None = None,
    ) -> ParsedFile:
        """Parse source bytes from an accepted Git snapshot."""

        relative = normalize_path(relative_path)
        language = detect_language(relative)
        if language is None:
            raise ValueError(f"Unsupported source file: {relative}")
        if len(content) > MAX_FILE_BYTES:
            raise ValueError(f"Source file exceeds {MAX_FILE_BYTES} bytes: {relative}")
        if b"\x00" in content:
            raise ValueError(f"Binary source file is not indexable: {relative}")

        parser = self._parsers.get(language)
        if parser is None:
            parser = get_parser(language)
            self._parsers[language] = parser
        tree = parser.parse(content)  # type: ignore[attr-defined]
        extractor = _Extractor(relative, language, content)
        if language == "python":
            python_file_extra, _ = python_metadata(content)
            extractor.blueprints = python_file_extra.get("blueprints", {})
        extractor.extract(tree.root_node)
        file_extra = tree_metadata(tree.root_node, content, language)
        if language == "json" and PurePosixPath(relative).name.startswith("tsconfig"):
            try:
                file_extra["tsconfig"] = parse_tsconfig(content)
            except (ValueError, AttributeError):
                file_extra["config_warning"] = "Unsupported tsconfig syntax; aliases may be unresolved."
        extractor.nodes[0] = replace(extractor.nodes[0], extra=file_extra)
        if language == "python":
            file_extra, metadata = python_metadata(content)
            enriched = []
            for n in extractor.nodes:
                extra = {**n.extra, **(file_extra if n.kind == "File" else metadata.get((n.name, n.line_start), {}))}
                enriched.append(replace(n, extra=extra, signature=str(extra["signature"])[:500] if "signature" in extra else n.signature))
            extractor.nodes = enriched
        warnings = (
            (f"Tree-sitter reported syntax errors in {relative}.",)
            if tree.root_node.has_error
            else ()
        )
        return ParsedFile(
            file_path=relative,
            file_hash=file_hash or hash_bytes(content),
            language=language,
            nodes=tuple(extractor.nodes),
            edges=tuple(extractor.edges),
            warnings=warnings,
        )


class _Extractor:
    def __init__(self, file_path: str, language: str, content: bytes) -> None:
        self.file_path = file_path
        self.blueprints: dict[str, str] = {}
        self.language = language
        self.content = content
        self._line_starts = [0]
        self._line_starts.extend(
            index + 1 for index, value in enumerate(content) if value == ord("\n")
        )
        self.nodes: list[NodeRecord] = [
            NodeRecord(
                kind="File",
                name=PurePosixPath(file_path).name,
                qualified_name=file_path,
                file_path=file_path,
                line_start=1,
                line_end=max(1, content.count(b"\n") + 1),
                language=language,
                is_test=_is_test(file_path, ""),
            )
        ]
        self.edges: list[EdgeRecord] = []
        self._qualified_names = {file_path}

    def extract(self, root: Node) -> None:
        cursor = root.walk()
        depth = 0
        contexts: dict[int, tuple[tuple[str, ...], str, str | None]] = {
            0: ((), self.file_path, None)
        }
        while True:
            node = cursor.node
            scopes, parent_qualified, callable_qn = contexts[depth]
            node_type = node.type
            start_byte = max(0, int(node.start_byte))
            end_byte = min(len(self.content), int(node.end_byte))
            line_start = bisect_right(self._line_starts, start_byte)
            line_end = bisect_right(
                self._line_starts,
                max(start_byte, end_byte - 1),
            )
            node_text = self._text(node)
            child_context = (scopes, parent_qualified, callable_qn)
            if node_type in _IMPORT_TYPES:
                for target in _import_targets(self.language, node_text):
                    self.edges.append(
                        EdgeRecord(
                            kind="IMPORTS_FROM",
                            source_qualified=self.file_path,
                            target_qualified=target,
                            file_path=self.file_path,
                            line=line_start,
                        )
                    )

            if node_type in _CALL_TYPES and callable_qn is not None:
                target = self._call_target(node)
                if target:
                    self.edges.append(
                        EdgeRecord(
                            kind="CALLS",
                            source_qualified=callable_qn,
                            target_qualified=target,
                            file_path=self.file_path,
                            line=line_start,
                            confidence=0.8,
                            extra={"receiver": self._receiver(node)},
                        )
                    )

            if node_type in _CALL_TYPES:
                self._framework_call(node, callable_qn or parent_qualified, line_start)
            if node_type in {"identifier", "type_identifier"} and callable_qn:
                parent = node.parent
                definition = parent.child_by_field_name("name") if parent else None
                # Declarations and local assignment targets are not references.
                if definition != node and parent is not None and parent.type not in {
                    "parameters", "parameter", "formal_parameter", "variable_declarator",
                    "assignment", "import_specifier", "attribute", "member_expression",
                }:
                    self.edges.append(EdgeRecord(
                        "REFERENCES", callable_qn, node_text, self.file_path, line_start,
                        confidence=0.6,
                    ))
            if node_type in {"attribute", "member_expression", "field_access"} and callable_qn:
                text = self._text(node)
                config = re.fullmatch(r"process\.env\.([A-Za-z_][\w]*)", text)
                if config:
                    self._virtual_relation("CONSUMES", callable_qn, "config:" + config[1], line_start)
                if "." in text:
                    receiver, name = text.rsplit(".", 1)
                    self.edges.append(EdgeRecord("REFERENCES", callable_qn, name,
                        self.file_path, line_start, confidence=0.6, extra={"receiver": receiver}))
            if node_type in {"subscript", "subscript_expression"} and callable_qn:
                config = re.fullmatch(r"(?:os\.environ|process\.env)\[['\"]([^'\"]+)['\"]\]", node_text)
                if config:
                    self._virtual_relation("CONSUMES", callable_qn, "config:" + config[1], line_start)

            if node_type in _IMPL_TYPES:
                impl_name = self._implementation_name(node)
                child_scopes = (*scopes, impl_name) if impl_name else scopes
                child_parent = (
                    self._qualified(child_scopes) if impl_name else parent_qualified
                )
                child_context = (child_scopes, child_parent, callable_qn)
            else:
                kind = self._symbol_kind(node_type)
                name = self._name(node, node_text=node_text) if kind else None
                if kind and name:
                    if kind == "Function" and _is_test(self.file_path, name):
                        kind = "Test"
                    declaration_scopes = scopes
                    if self.language == "go" and node_type == "method_declaration":
                        receiver = self._text(node.child_by_field_name("receiver"))
                        receiver_type = re.search(r"\w+\s+\*?(\w+)", receiver)
                        if receiver_type:
                            declaration_scopes = (*scopes, receiver_type[1])
                            parent_qualified = self._qualified(declaration_scopes)
                    qn = self._unique_qualified((*declaration_scopes, name), line_start)
                    record = NodeRecord(
                        kind=kind,
                        name=name,
                        qualified_name=qn,
                        file_path=self.file_path,
                        line_start=line_start,
                        line_end=line_end,
                        language=self.language,
                        parent_qualified=parent_qualified,
                        signature=self._signature(node_text),
                        is_test=_is_test(self.file_path, name),
                        extra=self._metadata(node),
                    )
                    self.nodes.append(record)
                    self._framework_declaration(node, record)
                    self.edges.append(
                        EdgeRecord(
                            kind="CONTAINS",
                            source_qualified=parent_qualified,
                            target_qualified=qn,
                            file_path=self.file_path,
                            line=record.line_start,
                        )
                    )
                    if kind in {"Class", "Type"}:
                        for target in _inheritance_targets(self.language, node_text):
                            implemented = re.search(r"\bimplements\s+[^\n{]*\b" + re.escape(target) + r"\b", node_text.split("{", 1)[0])
                            self.edges.append(
                                EdgeRecord(
                                    kind="IMPLEMENTS" if implemented else "INHERITS",
                                    source_qualified=qn,
                                    target_qualified=target,
                                    file_path=self.file_path,
                                    line=record.line_start,
                                    confidence=0.8,
                                )
                            )
                    child_scopes = (
                        (*scopes, name) if kind in {"Class", "Type"} else scopes
                    )
                    child_parent = (
                        qn if kind in {"Class", "Type"} else parent_qualified
                    )
                    child_callable = (
                        qn if kind in {"Function", "Test"} else callable_qn
                    )
                    child_context = (
                        child_scopes,
                        child_parent,
                        child_callable,
                    )

            contexts[depth + 1] = child_context
            if cursor.goto_first_child():
                depth += 1
                continue
            if cursor.goto_next_sibling():
                continue
            while cursor.goto_parent():
                depth -= 1
                if cursor.goto_next_sibling():
                    break
            else:
                return

    def _symbol_kind(self, node_type: str) -> str | None:
        if node_type in _CLASS_TYPES:
            return "Type" if "type_alias" in node_type else "Class"
        if node_type in _FUNCTION_TYPES:
            return "Function"
        return None

    def _name(self, node: Node, *, node_text: str) -> str | None:
        if node.type in {"arrow_function", "function_expression"}:
            parent = node.parent
            name = parent.child_by_field_name("name") if parent is not None else None
            return _clean_name(self._text(name)) if name else f"callback_{node.start_point.row + 1}"
        direct = node.child_by_field_name("name")
        if direct is not None:
            return _clean_name(self._text(direct))
        declarator = node.child_by_field_name("declarator")
        if declarator is not None:
            declarator_text = self._text(declarator)
            identifiers = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", declarator_text)
            if identifiers:
                return _clean_name(identifiers[-1])
        header = node_text.split("\n", 1)[0]
        identifiers = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", header)
        return _clean_name(identifiers[-1]) if identifiers else None

    def _implementation_name(self, node: Node) -> str | None:
        type_node = node.child_by_field_name("type")
        if type_node is not None:
            return _clean_name(self._text(type_node).split("<", 1)[0])
        names = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", self._text(node))
        return _clean_name(names[1]) if len(names) > 1 else None

    def _call_target(self, node: Node) -> str | None:
        target = node.child_by_field_name("function") or node.child_by_field_name("name")
        if target is not None and target.type in {"attribute", "member_expression", "field_access", "selector_expression"}:
            target = (target.child_by_field_name("attribute") or target.child_by_field_name("property")
                      or target.child_by_field_name("field") or target.child_by_field_name("name") or target)
        text = self._text(target) if target is not None else self._text(node)
        text = text.split("(", 1)[0].strip()
        matches = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", text)
        return matches[-1] if matches else None

    def _receiver(self, node: Node) -> str:
        receiver = node.child_by_field_name("object")
        if receiver is not None:
            return self._text(receiver)[:200]
        target = node.child_by_field_name("function")
        text = self._text(target)
        if "::" in text and "." not in text:
            return text.rsplit("::", 1)[0][:200]
        return text.rsplit(".", 1)[0][:200] if "." in text else ""

    def _virtual_relation(self, kind: str, source: str, target: str, line: int) -> None:
        self.edges.append(EdgeRecord(kind, source, target, self.file_path, line,
                                     confidence=0.9, extra={"framework": True}))

    def _endpoint(self, handler: str, method: str, path: str, line: int) -> None:
        if _is_test(self.file_path, handler.rsplit("::", 1)[-1]):
            return
        name = f"{method.upper()} {path}"
        qn = f"{self.file_path}::endpoint:{name}"
        if qn not in self._qualified_names:
            self._qualified_names.add(qn)
            self.nodes.append(NodeRecord("Endpoint", name, qn, self.file_path,
                line, line, self.language, self.file_path, extra={"route": path, "method": method.upper()}))
        self._virtual_relation("HANDLES", handler, qn, line)

    def _framework_call(self, node: Node, owner: str, line: int) -> None:
        target = self._text(node.child_by_field_name("function"))
        if not target:
            target = self._receiver(node) + "." + self._text(node.child_by_field_name("name"))
        arguments = node.child_by_field_name("arguments")
        children = arguments.named_children if arguments else []
        literal = self._text(children[0]) if children else ""
        # Only literal event/config/route names: no evaluation of repository code.
        match = re.fullmatch(r"['\"]([^'\"\n]{1,200})['\"]", literal)
        if match:
            name = match[1]
            method = target.rsplit(".", 1)[-1]
            if method in {"emit", "publish", "publishEvent"}:
                self._virtual_relation("PUBLISHES", owner, "event::" + name, line)
            elif method in {"on", "once", "subscribe"} and len(children) > 1:
                callback = children[1]
                handler = self._text(callback) if callback.type in {"identifier", "attribute", "member_expression"} else f"callback_{callback.start_point.row + 1}"
                self._virtual_relation("HANDLES", handler, "event::" + name, line)
            elif method in {"get", "post", "put", "patch", "delete", "all", "use"} and name.startswith("/") and len(children) > 1:
                callback = children[-1]
                handler = self._text(callback) if callback.type in {"identifier", "attribute", "member_expression"} else f"callback_{callback.start_point.row + 1}"
                self._endpoint(handler, method, name, line)
            if target in {"os.getenv", "os.environ.get", "System.getenv", "System.getProperty"} or target.endswith((".getProperty", ".getenv")):
                self._virtual_relation("CONSUMES", owner, "config:" + name, line)
        if target.endswith("publishEvent") and children:
            event = re.match(r"new\s+([\w.]+)", literal)
            if event:
                self._virtual_relation("PUBLISHES", owner, "event::" + event[1], line)

    def _framework_declaration(self, node: Node, record: NodeRecord) -> None:
        if record.kind not in {"Function", "Test"}:
            return
        prefix = self._text(node.parent) if node.parent and node.parent.type == "decorated_definition" else self._text(node)
        prefix = prefix.split("{", 1)[0] if self.language != "python" else prefix.split("def ", 1)[0]
        for owner, method, path, options in re.findall(r"@(\w+)\.(route|get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]([^)]*)\)", prefix):
            if owner in self.blueprints:
                path = self.blueprints[owner].rstrip("/") + "/" + path.lstrip("/")
            methods = [method] if method != "route" else ["GET"]
            declared = re.search(r"methods\s*=\s*\[([^]]*)\]", options)
            if method == "route" and declared:
                methods = re.findall(r"['\"]([A-Za-z]+)['\"]", declared[1])
            for http_method in methods:
                self._endpoint(record.qualified_name, http_method, path, record.line_start)
        for method, path in re.findall(r"@(Get|Post|Put|Patch|Delete|Request)Mapping\(\s*(?:value\s*=\s*|path\s*=\s*)?['\"]([^'\"]+)['\"]", prefix):
            self._endpoint(record.qualified_name, "ANY" if method == "Request" else method, path, record.line_start)
        if "@EventListener" in prefix:
            parameters = node.child_by_field_name("parameters")
            if parameters and parameters.named_children:
                event = self._text(parameters.named_children[0].child_by_field_name("type"))
                if event:
                    self._virtual_relation("HANDLES", record.qualified_name, "event::" + event, record.line_start)
        for key in re.findall(r"@Value\(\s*['\"]\$\{([^}:]+)(?::[^}]*)?\}", prefix):
            self._virtual_relation("CONSUMES", record.qualified_name, "config:" + key, record.line_start)

    def _metadata(self, node: Node) -> dict[str, object]:
        extra: dict[str, object] = tree_metadata(node, self.content, self.language)
        extra["bases"] = list(_inheritance_targets(self.language, self._text(node)))
        if self.language == "go":
            receiver = re.search(r"(\w+)\s+\*?(\w+)", self._text(node.child_by_field_name("receiver")))
            if receiver:
                extra["receivers"][receiver[1]] = receiver[2]
        for field_name in ("parameters", "return_type"):
            value = node.child_by_field_name(field_name)
            if value is not None:
                extra["params" if field_name == "parameters" else field_name] = self._text(value)[:1000]
        previous = node.prev_named_sibling
        container = node.parent
        while (previous is None or "comment" not in previous.type) and container is not None and container.type in {
            "variable_declarator", "lexical_declaration", "export_statement",
        }:
            previous = container.prev_named_sibling
            container = container.parent
        if previous is not None and "comment" in previous.type:
            extra["docstring"] = " ".join(self._text(previous).strip("/* ").split())[:1500]
        return extra

    @staticmethod
    def _signature(text: str) -> str:
        text = text.strip()
        first = text.split("\n", 1)[0]
        for marker in ("{", ":"):
            if marker in first:
                first = first.split(marker, 1)[0] + marker
                break
        return " ".join(first.split())[:500]

    def _text(self, node: Node | None) -> str:
        if node is None:
            return ""
        start = max(0, int(node.start_byte))
        end = min(len(self.content), int(node.end_byte))
        return self.content[start:end].decode(
            "utf-8", errors="replace"
        )

    def _qualified(self, scopes: tuple[str, ...]) -> str:
        return self.file_path + ("::" + ".".join(scopes) if scopes else "")

    def _unique_qualified(self, scopes: tuple[str, ...], line: int) -> str:
        candidate = self._qualified(scopes)
        if candidate in self._qualified_names:
            candidate = f"{candidate}@{line}"
        self._qualified_names.add(candidate)
        return candidate


def _clean_name(value: str) -> str | None:
    cleaned = " ".join(value.replace("\x00", "").split()).strip("'\"` ")
    if not cleaned or len(cleaned) > 256:
        return None
    return cleaned


def _is_test(file_path: str, name: str) -> bool:
    path = file_path.casefold()
    symbol = name.casefold()
    parts = set(PurePosixPath(path).parts)
    return (
        bool(parts & {"test", "tests", "spec", "specs", "__tests__"})
        or PurePosixPath(path).name.startswith(("test_", "spec_"))
        or PurePosixPath(path).stem.endswith(("_test", "_spec"))
        or symbol.startswith(("test_", "test", "spec_", "should_"))
    )


def _import_targets(language: str, text: str) -> tuple[str, ...]:
    patterns: tuple[str, ...]
    if language == "python":
        patterns = (
            r"\bfrom\s+([.A-Za-z_][\w.]*)\s+import\b",
            r"\bimport\s+([A-Za-z_][\w.]*)",
        )
    elif language in {"javascript", "typescript", "tsx", "svelte", "vue"}:
        patterns = (
            r"\bfrom\s+['\"]([^'\"]+)['\"]",
            r"\bimport\s+['\"]([^'\"]+)['\"]",
            r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]",
        )
    elif language == "go":
        patterns = (r"['\"]([^'\"]+)['\"]",)
    elif language == "java" or language in {"kotlin", "scala"}:
        patterns = (r"\bimport\s+([A-Za-z_][\w.*]*)",)
    elif language == "rust":
        patterns = (r"\buse\s+([^;]+)",)
    elif language in {"c", "cpp", "objc"}:
        patterns = (r"#\s*include\s*[<\"]([^>\"]+)[>\"]",)
    elif language == "php":
        patterns = (r"\buse\s+([^;]+)", r"\b(?:require|include)(?:_once)?\s+['\"]([^'\"]+)")
    else:
        patterns = (r"\b(?:import|use|include)\s+['\"]?([A-Za-z0-9_./:\\-]+)",)
    found: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            target = " ".join(match.split()).strip("'\"` ")[:500]
            if target and target not in found:
                found.append(target)
    return tuple(found[:50])


def _inheritance_targets(language: str, text: str) -> tuple[str, ...]:
    header = text.split("{", 1)[0].split("\n", 1)[0]
    found: list[str] = []
    if language == "python":
        match = re.search(r"\bclass\s+\w+\s*\(([^)]*)\)", header)
        candidates = match.group(1).split(",") if match else []
    else:
        match = re.search(r"\b(?:extends|implements|inherits)\s+([^:{]+)", header)
        candidates = re.split(r"[,\s]+", match.group(1)) if match else []
    for candidate in candidates:
        if candidate in {"implements", "extends", "inherits"}:
            continue
        names = re.findall(r"[A-Za-z_$][A-Za-z0-9_.$]*", candidate)
        if names:
            value = names[-1].split(".")[-1]
            if value not in found:
                found.append(value)
    return tuple(found[:20])
