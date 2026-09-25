"""Structural guardrails for the single supported Sage architecture."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src" / "sage"
REMOVED_PATHS = {
    "memory",
    "runtimes",
    "workflow",
    "artifacts/v2.py",
    "domain/runtime.py",
    "providers/factory.py",
    "research",
    "legion_memory",
    "providers/embeddings.py",
    "providers/typesafe.py",
    "integrations/qdrant.py",
    "domain/embeddings.py",
    "orchestration/navigation.py",
    "orchestration/navigation_candidates.py",
    "orchestration/context.py",
    "agents/memory_tools.py",
    "agents/repository_tools.py",
    "harness/jev/session.py",
    "harness/jev/candidates.py",
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
        "sandbox",
        "verification",
        "workflows",
    ),
    "artifacts": ("agents", "orchestration", "workflows"),
    "repository": ("agents", "orchestration", "workflows"),
    "sandbox": ("agents", "orchestration", "workflows"),
    "verification": ("agents", "orchestration", "workflows"),
    "harness": ("agents", "cli", "composition", "integrations", "orchestration", "sandbox", "workflows"),
    "harness/memory": (
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
                for alias in node.names:
                    candidate = f"{node.module}.{alias.name}"
                    relative = candidate.removeprefix("sage.").replace(".", "/")
                    imports.add(
                        candidate if (SOURCE_ROOT / f"{relative}.py").is_file()
                        else node.module
                    )
        elif isinstance(node, ast.Import):
            imports.update(
                alias.name
                for alias in node.names
                if alias.name == "sage" or alias.name.startswith("sage.")
            )
    return imports


def test_removed_architectures_and_state_engines_are_absent() -> None:
    present = {
        str(relative)
        for path in _source_files()
        for relative in (path.relative_to(SOURCE_ROOT), *path.relative_to(SOURCE_ROOT).parents)
    }

    assert REMOVED_PATHS.isdisjoint(present)


def test_domain_depends_only_on_domain_contracts() -> None:
    for path in (SOURCE_ROOT / "domain").glob("*.py"):
        assert all(
            module.startswith("sage.domain.") for module in _sage_imports(path)
        ), path
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module
                else [alias.name for alias in node.names] if isinstance(node, ast.Import)
                else []
            )
            assert all(
                module.split(".")[0] in sys.stdlib_module_names | {"pydantic", "sage"}
                for module in modules
            ), path


def test_memory_has_no_provider_or_jev_dependency_or_embedding_package() -> None:
    forbidden = ("sage.providers", "sage.config", "sage.harness.jev")
    for path in (SOURCE_ROOT / "harness" / "memory").glob("*.py"):
        assert not any(module.startswith(forbidden) for module in _sage_imports(path)), path
    import tomllib
    project = tomllib.loads((SOURCE_ROOT.parents[1] / "pyproject.toml").read_text())
    assert not any(dependency.startswith("qdrant") for dependency in project["project"]["dependencies"])


def test_layers_do_not_reach_back_into_entrypoints_or_outer_workflows() -> None:
    for layer, forbidden in LAYER_FORBIDDEN_IMPORTS.items():
        for path in (SOURCE_ROOT / layer).rglob("*.py"):
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
        elif path == SOURCE_ROOT / "cli" / "__init__.py":
            # Preserve the installed sage.cli:main entrypoint, without logic.
            assert len(body) == 2
            assert isinstance(body[1], ast.ImportFrom)
            assert body[1].module == "sage.cli.app"
            assert [(alias.name, alias.asname) for alias in body[1].names] == [("main", None)]
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

    # Explicit harness subpackages replace scattered implementations; no new runtime.
    assert len(files) <= 110
    # Allow per-role timing and interruption reporting without new modules or dependencies.
    assert nonblank_lines <= 16_400
    assert orchestrator_lines <= 400
    for path in files:
        # Workflow now coordinates the two explicit harness preparation owners.
        limit = 16 if path == SOURCE_ROOT / "workflows" / "solve.py" else 14
        assert len(_sage_imports(path)) <= limit, path
