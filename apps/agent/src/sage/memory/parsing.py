"""Bounded Tree-sitter extraction for the supported V1 languages."""

from __future__ import annotations

from pathlib import PurePosixPath

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

from sage.errors import MemoryIntegrityError
from sage.memory.models import FileStructure

PARSER_VERSION = "tree-sitter-0.25-smrt-v1"
_PYTHON_SUFFIXES = {".py", ".pyi"}
_JAVASCRIPT_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs"}
_TYPESCRIPT_SUFFIXES = {".ts", ".tsx", ".mts", ".cts"}


class TreeSitterExtractor:
    """Extract declarations and dependencies without model inference."""

    def extract(self, path: str, source: str) -> FileStructure:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix in _PYTHON_SUFFIXES:
            language_name = "python"
            language = Language(tree_sitter_python.language())
        elif suffix in _JAVASCRIPT_SUFFIXES:
            language_name = "javascript"
            language = Language(tree_sitter_javascript.language())
        elif suffix in _TYPESCRIPT_SUFFIXES:
            language_name = "typescript"
            grammar = (
                tree_sitter_typescript.language_tsx()
                if suffix == ".tsx"
                else tree_sitter_typescript.language_typescript()
            )
            language = Language(grammar)
        else:
            return FileStructure(
                language="unknown",
                parser_version=PARSER_VERSION,
                parse_status="unsupported",
            )

        try:
            tree = Parser(language).parse(source.encode("utf-8"))
        except Exception as error:
            raise MemoryIntegrityError("Tree-sitter parser initialization failed.") from error
        symbols: list[str] = []
        imports: list[str] = []
        exports: list[str] = []
        signatures: list[str] = []
        source_bytes = source.encode("utf-8")
        for node in _walk(tree.root_node):
            if node.type in {
                "function_definition",
                "class_definition",
                "function_declaration",
                "class_declaration",
                "interface_declaration",
                "type_alias_declaration",
                "enum_declaration",
                "method_definition",
            }:
                name = node.child_by_field_name("name")
                if name is not None:
                    value = _text(name, source_bytes)
                    symbols.append(value)
                    signatures.append(_signature(node, source_bytes))
            elif node.type in {
                "import_statement",
                "import_from_statement",
                "import_declaration",
                "call",
            }:
                value = _dependency(node, source_bytes)
                if value:
                    imports.append(value)
            elif node.type in {"export_statement", "export_clause"}:
                exports.append(_bounded_text(node, source_bytes))
        return FileStructure(
            language=language_name,
            symbols=_bounded_unique(symbols),
            imports=_bounded_unique(imports),
            exports=_bounded_unique(exports),
            signatures=_bounded_unique(signatures),
            parser_version=PARSER_VERSION,
            parse_status="partial" if tree.root_node.has_error else "parsed",
        )


def verify_grammars() -> dict[str, str]:
    extractor = TreeSitterExtractor()
    probes = {
        "python": ("probe.py", "def probe():\n    return 1\n"),
        "javascript": ("probe.js", "export function probe() { return 1 }\n"),
        "typescript": ("probe.ts", "export function probe(): number { return 1 }\n"),
    }
    return {
        name: extractor.extract(path, source).parse_status
        for name, (path, source) in probes.items()
    }


def _walk(root: Node) -> list[Node]:
    result: list[Node] = []
    stack = [root]
    while stack and len(result) < 20_000:
        node = stack.pop()
        result.append(node)
        stack.extend(reversed(node.children))
    return result


def _dependency(node: Node, source: bytes) -> str:
    if node.type == "call":
        function = node.child_by_field_name("function")
        if function is None or _text(function, source) not in {"require", "import"}:
            return ""
    source_node = node.child_by_field_name("source") or node.child_by_field_name("module_name")
    if source_node is not None:
        return _text(source_node, source).strip("'\"")[:500]
    return _bounded_text(node, source)


def _signature(node: Node, source: bytes) -> str:
    text = _text(node, source)
    first = text.split("{", 1)[0].split(":\n", 1)[0].split("\n", 1)[0]
    return " ".join(first.split())[:500]


def _bounded_text(node: Node, source: bytes) -> str:
    return " ".join(_text(node, source).split())[:500]


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _bounded_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))[:100]
