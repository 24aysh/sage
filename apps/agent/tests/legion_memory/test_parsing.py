from __future__ import annotations

from pathlib import Path

import pytest

from sage.legion_memory.parsing import (
    EXTENSION_TO_LANGUAGE,
    MAX_FILE_BYTES,
    CodeParser,
    detect_language,
    normalize_path,
)


LANGUAGE_FILES = tuple(
    sorted(
        (language, extension)
        for language, extension in {
            language: extension
            for extension, language in EXTENSION_TO_LANGUAGE.items()
        }.items()
    )
)


@pytest.mark.parametrize(("language", "extension"), LANGUAGE_FILES)
def test_each_declared_language_grammar_parses(
    tmp_path: Path,
    language: str,
    extension: str,
) -> None:
    source = tmp_path / f"sample{extension}"
    source.write_text("\n", encoding="utf-8")

    parsed = CodeParser().parse(source, relative_path=f"src/sample{extension}")

    assert parsed.language == language
    assert parsed.nodes[0].kind == "File"
    assert parsed.nodes[0].file_path == f"src/sample{extension}"


def test_python_extraction_is_stable_and_records_relationships() -> None:
    source = b"""from pkg.base import Parent

class Worker(Parent):
    def run(self):
        return helper()

def helper():
    return 1
"""
    parser = CodeParser()

    first = parser.parse_bytes(source, relative_path="src/service.py")
    second = parser.parse_bytes(source, relative_path="src/service.py")

    assert first == second
    nodes = {node.name: node for node in first.nodes}
    assert nodes["Worker"].qualified_name == "src/service.py::Worker"
    assert nodes["run"].parent_qualified == "src/service.py::Worker"
    assert nodes["run"].line_start == 4
    assert nodes["helper"].line_end == 8
    assert any(edge.kind == "IMPORTS_FROM" for edge in first.edges)
    assert any(
        edge.kind == "INHERITS" and edge.target_qualified == "Parent"
        for edge in first.edges
    )
    assert any(
        edge.kind == "CALLS" and edge.target_qualified == "helper"
        for edge in first.edges
    )


def test_test_markers_and_syntax_warnings_are_explicit() -> None:
    parsed = CodeParser().parse_bytes(
        b"def test_broken(:\n    pass\n",
        relative_path="tests/test_broken.py",
    )

    assert parsed.nodes[0].is_test is True
    assert parsed.warnings == (
        "Tree-sitter reported syntax errors in tests/test_broken.py.",
    )


def test_parser_rejects_binary_large_and_unsupported_content() -> None:
    parser = CodeParser()

    with pytest.raises(ValueError, match="Binary"):
        parser.parse_bytes(b"x\x00y", relative_path="bad.py")
    with pytest.raises(ValueError, match="exceeds"):
        parser.parse_bytes(b"x" * (MAX_FILE_BYTES + 1), relative_path="large.py")
    with pytest.raises(ValueError, match="Unsupported"):
        parser.parse_bytes(b"text", relative_path="README.md")


def test_path_normalization_preserves_dot_directories() -> None:
    assert normalize_path("./.github/workflows/test.yml") == (
        ".github/workflows/test.yml"
    )
    assert detect_language("component.TSX") == "tsx"


def test_named_javascript_arrow_and_doc_comment_are_indexed():
    parsed = CodeParser().parse_bytes(b'''/** Checkout is idempotent per customer. */
export const checkout = (customerId) => {
    return persist(customerId);
};
function persist(id) { return id; }
''', relative_path="services/orders.js")
    nodes = {node.name: node for node in parsed.nodes}
    assert "checkout" in nodes
    assert "customerId" in nodes["checkout"].extra["params"]
    assert "idempotent per customer" in nodes["checkout"].extra["docstring"]
    assert any(e.source_qualified == nodes["checkout"].qualified_name and e.target_qualified == "persist" for e in parsed.edges)


def test_html_and_css_extract_stable_elements_selectors_and_resources():
    parser = CodeParser()
    html = parser.parse_bytes(b'''<link rel="stylesheet" href="./styles/site.css?v=2">
<script src="https://cdn.example/app.js"></script>
<main id="content" class="page hero"><button class="cta">Go</button></main>
''', relative_path="index.html")
    css = parser.parse_bytes(b'''@import url("./theme.css");
.page.hero, #content { color: red; }
.cta:hover { color: blue; }
''', relative_path="styles/site.css")

    assert html.language == "html"
    assert [(node.kind, node.name) for node in html.nodes] == [
        ("File", "index.html"), ("Element", "content")]
    assert {edge.target_qualified for edge in html.edges if edge.kind == "IMPORTS_FROM"} == {
        "./styles/site.css"
    }
    assert {edge.target_qualified for edge in html.edges if edge.kind == "REFERENCES"} == {
        "#content", ".page", ".hero", ".cta"
    }
    assert {node.name for node in css.nodes if node.kind == "Selector"} == {
        ".page", ".hero", "#content", ".cta"
    }
    assert {edge.target_qualified for edge in css.edges if edge.kind == "IMPORTS_FROM"} == {
        "./theme.css"
    }
