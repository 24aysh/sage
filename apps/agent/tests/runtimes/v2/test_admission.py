from __future__ import annotations

from pathlib import Path

import pytest

from sage.artifacts.v2 import V2ArtifactStore
from sage.config import Settings
from sage.domain.admission import (
    AdmissionRequirement,
    ReadinessDisposition,
    RepositoryEvidenceInput,
)
from sage.domain.requests import PreparedRun
from sage.domain.runtime import RuntimeContext
from sage.errors import AgentRuntimeError
from sage.research.service import ResearchService, UnavailableSearchProvider
from sage.runtimes.v2.admission import (
    AdmissionContextSession,
    build_admission_tools,
    clarification_limit_reached,
    next_clarification_round,
    render_admission_context,
)


class Repository:
    def __init__(self, root: Path, sha: str) -> None:
        self.root = root
        self.sha = sha

    def read_file(
        self,
        *,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        lines = (self.root / path).read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1 : end_line]
        return "\n".join(
            f"{number} | {line}"
            for number, line in enumerate(selected, start=start_line)
        )

    def get_head_sha(self) -> str:
        return self.sha

    def list_tree(self, **kwargs) -> str:
        del kwargs
        return "app.py"

    def search_text(self, **kwargs) -> str:
        del kwargs
        return "app.py:1:value"


def test_admission_context_is_controller_derived_persisted_and_revalidated(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    sha = "a" * 40
    settings = Settings(openai_api_key="test")
    context = RuntimeContext(
        prepared_run=PreparedRun(
            run_id="run",
            source_repo=workspace,
            run_dir=run_dir,
            workspace_dir=workspace,
            base_ref="HEAD",
            base_sha=sha,
        ),
        sandbox=object(),  # type: ignore[arg-type]
        repository=Repository(workspace, sha),  # type: ignore[arg-type]
        settings=settings,
    )
    research = ResearchService(
        provider=UnavailableSearchProvider(),
        enabled=True,
        max_result_chars=2_000,
    )
    session = AdmissionContextSession(
        context=context,
        issue_text="Change the value.",
        artifacts=V2ArtifactStore(run_dir),
        research=research,
    )

    snapshot = session.save(
        summary="The value is locally defined.",
        requirements=(
            AdmissionRequirement(
                requirement_id="value",
                statement="Change the value.",
                evidence_ids=("app",),
                status="supported",
            ),
        ),
        relevant_paths=("app.py",),
        relevant_symbols=("value",),
        repository_conventions=(),
        candidate_verification_commands=(),
        assumptions=(),
        open_questions=(),
        repository_evidence=(
            RepositoryEvidenceInput(
                evidence_id="app",
                path="app.py",
                line_start=1,
                line_end=1,
                title="Current value",
            ),
        ),
        research_evidence=(),
    )

    assert session.validate("Change the value.") == snapshot
    assert (run_dir / "admission-context.json").is_file()
    summary_text = (run_dir / "admission-context-summary.json").read_text()
    assert "value = 1" not in summary_text
    assert snapshot.evidence[0].locator == "app.py"
    assert len(render_admission_context(snapshot, max_chars=8_000)) <= 8_000
    tool_names = {
        tool.name for tool in build_admission_tools(context, session, research)
    }
    assert {"list_tree", "search_text", "read_file", "search_web"} <= tool_names
    assert not {
        "save_plan",
        "write_file",
        "replace_text",
        "delete_file",
        "move_file",
        "run_command",
    } & tool_names

    (workspace / "app.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(AgentRuntimeError, match="became stale"):
        session.validate("Change the value.")


def test_readiness_disposition_contains_ready_and_human_routes() -> None:
    assert ReadinessDisposition.READY.value == "READY"
    assert ReadinessDisposition.NEEDS_HUMAN_INFORMATION.value.endswith("INFORMATION")


def test_clarification_round_reuses_the_latest_github_marker() -> None:
    issue = "<!-- sage-clarification:v1 round=1 disposition=needs_human_information -->"

    assert next_clarification_round(issue, maximum=2) == 2
    assert clarification_limit_reached(issue, maximum=2) is False
    assert clarification_limit_reached(issue.replace("round=1", "round=2"), maximum=2)
