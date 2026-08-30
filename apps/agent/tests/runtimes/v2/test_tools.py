from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sage.artifacts.v2 import V2ArtifactStore
from sage.config import Settings
from sage.domain.requests import PreparedRun
from sage.domain.runtime import RuntimeContext
from sage.errors import RepositoryError
from sage.runtimes.v2.tools import SolverPlanSession, build_solver_tools


class Repository:
    def __init__(self) -> None:
        self.mutations = 0

    def replace_text(self, **kwargs) -> str:
        del kwargs
        self.mutations += 1
        return "changed"


def test_mutation_requires_implementable_saved_plan(tmp_path: Path) -> None:
    repository = Repository()
    context = RuntimeContext(
        prepared_run=PreparedRun(
            run_id="run",
            source_repo=tmp_path,
            run_dir=tmp_path / "run",
            workspace_dir=tmp_path,
            base_ref="HEAD",
            base_sha="a" * 40,
        ),
        sandbox=object(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        settings=Settings(openai_api_key="test"),
    )
    context.prepared_run.run_dir.mkdir()
    plans = SolverPlanSession(V2ArtifactStore(context.prepared_run.run_dir))
    tools = {tool.name: tool for tool in build_solver_tools(context, plans)}

    with pytest.raises(RepositoryError, match="Save an implementable"):
        asyncio.run(
            tools["replace_text"].ainvoke(
                {
                    "path": "app.py",
                    "old_text": "1",
                    "new_text": "2",
                    "expected_occurrences": 1,
                }
            )
        )
    assert repository.mutations == 0
    assert "apply_patch" not in tools


def test_save_plan_unlocks_mutation_and_persists_outside_repository(
    tmp_path: Path,
) -> None:
    repository = Repository()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = RuntimeContext(
        prepared_run=PreparedRun(
            run_id="run",
            source_repo=tmp_path,
            run_dir=run_dir,
            workspace_dir=tmp_path,
            base_ref="HEAD",
            base_sha="a" * 40,
        ),
        sandbox=object(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        settings=Settings(openai_api_key="test"),
    )
    tools = {
        tool.name: tool
        for tool in build_solver_tools(
            context,
            SolverPlanSession(V2ArtifactStore(run_dir)),
        )
    }
    asyncio.run(
        tools["save_plan"].ainvoke(
            {
                "issue_summary": "Change one value.",
                "approach": "Edit app.py.",
                "tasks": [
                    {
                        "task_id": "edit",
                        "objective": "Edit app.py.",
                        "expected_paths": ["app.py"],
                        "criterion_ids": ["value"],
                    }
                ],
                "acceptance_criteria": [
                    {"criterion_id": "value", "requirement": "Value is two."}
                ],
                "relevant_paths": ["app.py"],
                "verification_commands": [],
                "assumptions": [],
                "risks": [],
                "status": "implementable",
                "blocker": None,
            }
        )
    )
    asyncio.run(
        tools["replace_text"].ainvoke(
            {
                "path": "app.py",
                "old_text": "1",
                "new_text": "2",
                "expected_occurrences": 1,
            }
        )
    )

    assert repository.mutations == 1
    assert (run_dir / "solver-plan.json").is_file()


def test_save_plan_rejects_repository_paths_as_research_ids(tmp_path: Path) -> None:
    repository = Repository()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    context = RuntimeContext(
        prepared_run=PreparedRun(
            run_id="run",
            source_repo=tmp_path,
            run_dir=run_dir,
            workspace_dir=tmp_path,
            base_ref="HEAD",
            base_sha="a" * 40,
        ),
        sandbox=object(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        settings=Settings(openai_api_key="test"),
    )
    tools = {
        tool.name: tool
        for tool in build_solver_tools(
            context,
            SolverPlanSession(V2ArtifactStore(run_dir)),
        )
    }

    with pytest.raises(RepositoryError, match="relevant_paths"):
        asyncio.run(
            tools["save_plan"].ainvoke(
                {
                    "issue_summary": "Change one value.",
                    "approach": "Edit app.py.",
                    "tasks": [
                        {
                            "task_id": "edit",
                            "objective": "Edit app.py.",
                            "expected_paths": ["app.py"],
                            "criterion_ids": ["value"],
                        }
                    ],
                    "acceptance_criteria": [
                        {
                            "criterion_id": "value",
                            "requirement": "Value is two.",
                        }
                    ],
                    "relevant_paths": ["app.py"],
                    "verification_commands": [],
                    "assumptions": [],
                    "risks": [],
                    "status": "implementable",
                    "research_result_ids": ["app.py"],
                    "blocker": None,
                }
            )
        )

    assert not (run_dir / "solver-plan.json").exists()
