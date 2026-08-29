from sage.memory.parsing import TreeSitterExtractor, verify_grammars


def test_python_extraction_uses_syntax_tree_for_symbols_and_imports() -> None:
    result = TreeSitterExtractor().extract(
        "sage/engine.py",
        "from pathlib import Path\n\nclass Engine:\n    def run(self) -> None:\n        pass\n",
    )

    assert result.language == "python"
    assert result.parse_status == "parsed"
    assert "Engine" in result.symbols
    assert "run" in result.symbols
    assert result.imports


def test_typescript_and_javascript_grammars_are_abi_compatible() -> None:
    assert verify_grammars() == {
        "python": "parsed",
        "javascript": "parsed",
        "typescript": "parsed",
    }


def test_unsupported_source_is_an_expected_skip() -> None:
    result = TreeSitterExtractor().extract("README.md", "# heading")
    assert result.language == "unknown"
    assert result.parse_status == "unsupported"
