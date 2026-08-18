from pathlib import Path

from sage.repository.tree import list_tree


def test_list_tree_skips_generated_directories(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "app.py").write_text("", encoding="utf-8")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "dependency.js").write_text("", encoding="utf-8")

    tree = list_tree(tmp_path, max_depth=2)

    assert "src/" in tree
    assert "app.py" in tree
    assert "node_modules" not in tree
