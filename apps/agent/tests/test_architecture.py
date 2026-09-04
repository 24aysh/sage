"""Structural guardrails for the single supported Sage architecture."""

from __future__ import annotations

import ast
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "sage"
REMOVED_PATHS = {
    "memory",
    "runtimes",
    "workflow",
    "artifacts/v2.py",
    "domain/runtime.py",
    "providers/factory.py",
}
LAYER_FORBIDDEN_IMPORTS = {
    "agents": ("cli", "composition", "integrations", "orchestration", "sandbox", "workflows"),
    "orchestration": ("cli", "composition", "integrations", "sandbox", "workflows"),
    "providers": (
        "agents",
        "cli",
        "composition",
        "integrations",
        "orchestration",
        "repository",
        "research",
        "sandbox",
        "verification",
        "workflows",
    ),
    "artifacts": ("agents", "orchestration", "workflows"),
    "repository": ("agents", "orchestration", "workflows"),
    "research": ("agents", "orchestration", "workflows"),
    "sandbox": ("agents", "orchestration", "workflows"),
    "verification": ("agents", "orchestration", "workflows"),
    "legion_memory": (
        "agents",
        "cli",
        "composition",
        "integrations",
        "orchestration",
        "providers",
        "sandbox",
        "workflows",
    ),
}


def _source_files() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _sage_imports(path: Path) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "sage" or node.module.startswith("sage."):
                imports.add(node.module)
        elif isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name == "sage" or alias.name.startswith("sage.")
            )
    return imports


def test_removed_architectures_and_state_engines_are_absent() -> None:
    present = {
        str(path.relative_to(SOURCE_ROOT))
        for path in SOURCE_ROOT.rglob("*")
    }

    assert REMOVED_PATHS.isdisjoint(present)


def test_domain_depends_only_on_domain_contracts() -> None:
    for path in (SOURCE_ROOT / "domain").glob("*.py"):
        assert all(
            module.startswith("sage.domain.") for module in _sage_imports(path)
        ), path


def test_layers_do_not_reach_back_into_entrypoints_or_outer_workflows() -> None:
    for layer, forbidden in LAYER_FORBIDDEN_IMPORTS.items():
        for path in (SOURCE_ROOT / layer).glob("*.py"):
            for module in _sage_imports(path):
                assert not any(
                    module == f"sage.{name}" or module.startswith(f"sage.{name}.")
                    for name in forbidden
                ), (path, module)


def test_package_initializers_contain_no_implementation() -> None:
    for path in SOURCE_ROOT.rglob("__init__.py"):
        body = ast.parse(path.read_text(encoding="utf-8")).body
        if path == SOURCE_ROOT / "__init__.py":
            assert len(body) == 2
            assert isinstance(body[1], ast.Assign)
        else:
            assert len(body) == 1
            assert isinstance(body[0], ast.Expr)


def test_internal_module_graph_is_acyclic() -> None:
    modules = {
        "sage." + ".".join(path.relative_to(SOURCE_ROOT).with_suffix("").parts): path
        for path in _source_files()
        if path.name != "__init__.py"
    }
    edges = {
        module: {dependency for dependency in _sage_imports(path) if dependency in modules}
        for module, path in modules.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(module: str) -> None:
        if module in visiting:
            raise AssertionError(f"Internal dependency cycle reaches {module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in edges[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)

    for module in edges:
        visit(module)


def test_navigation_metrics_stay_within_refactor_budget() -> None:
    files = _source_files()
    nonblank_lines = sum(
        bool(line.strip())
        for path in files
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    orchestrator_lines = len(
        (SOURCE_ROOT / "orchestration" / "solve.py")
        .read_text(encoding="utf-8")
        .splitlines()
    )

    assert len(files) <= 84
    assert nonblank_lines <= 13_180
    assert orchestrator_lines <= 400
    for path in files:
        assert len(_sage_imports(path)) <= 14, path
