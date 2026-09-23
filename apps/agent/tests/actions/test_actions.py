import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[4]
ACTIONS = ROOT / ".github" / "actions"
WORKFLOW = ROOT / ".github" / "workflows" / "sage.yml"
ENV_EXAMPLE = ROOT / ".env.example"
FULL_SHA_REFERENCE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
SAGE_ACTION_SHA = "903e2d6ff6d865851cd32fea5e0f5da66a67b759"
SECRET_CONFIGURATION = {
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "LANGSMITH_API_KEY",
    "TYPESAFE_API_KEY",
}


def test_composite_action_manifests_are_valid_and_pinned() -> None:
    for path in sorted(ACTIONS.glob("*/action.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document["runs"]["using"] == "composite"
        for step in document["runs"]["steps"]:
            reference = step.get("uses")
            if reference is not None:
                assert FULL_SHA_REFERENCE.fullmatch(reference), (path, reference)
            if "run" in step:
                assert step.get("shell") == "bash"
                assert "set -euo pipefail" in step["run"]
                syntax = subprocess.run(
                    ["bash", "-n"],
                    input=step["run"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                assert syntax.returncode == 0, (path, syntax.stderr)


def test_gate_action_is_model_secret_free_and_uses_pinned_source() -> None:
    body = (ACTIONS / "sage-gate" / "action.yml").read_text(encoding="utf-8")
    document = yaml.safe_load(body)

    assert "OPENAI_API_KEY" not in body
    assert "openai-api-key" not in body
    assert "GEMINI_API_KEY" not in body
    assert "github.action_path" in body
    assert "sage github gate" in body
    assert "sage github finalize" in body
    setup_uv = next(
        step
        for step in document["runs"]["steps"]
        if step.get("uses", "").startswith("astral-sh/setup-uv@")
    )
    assert setup_uv["with"]["ignore-empty-workdir"] is True


def test_solve_action_uses_exact_credential_free_target_checkout() -> None:
    body = (ACTIONS / "sage-solve" / "action.yml").read_text(encoding="utf-8")
    document = yaml.safe_load(body)

    assert set(document["inputs"]) == {
        "github-token",
        "openai-api-key",
        "gemini-api-key",
        "typesafe-api-key",
        "langsmith-api-key",
        "base-sha",
        "status-comment-id",
    }
    assert document["inputs"]["gemini-api-key"]["required"] is True
    assert document["inputs"]["typesafe-api-key"] == {
        "description": "Optional Jev credential scoped to the solve controller step.",
        "required": False,
    }
    assert document["inputs"]["langsmith-api-key"] == {
        "description": "Optional LangSmith API key scoped to the solve controller step.",
        "required": False,
    }
    assert "ref: ${{ inputs.base-sha }}" in body
    assert "persist-credentials: false" in body
    assert "fetch-depth: 0" in body
    assert "github.action_path" in body
    assert "OPENAI_API_KEY: ${{ inputs.openai-api-key }}" in body
    assert "GEMINI_API_KEY: ${{ inputs.gemini-api-key }}" in body
    assert "TYPESAFE_API_KEY: ${{ inputs.typesafe-api-key }}" in body
    assert "LANGSMITH_API_KEY: ${{ inputs.langsmith-api-key }}" in body
    assert "ANTHROPIC_API_KEY" not in body
    assert "SAGE_V2_SOLVER_MODEL" not in body
    assert "SAGE_V2_REVIEWER_MODEL" not in body
    assert "SAGE_GITHUB_TOKEN: ${{ inputs.github-token }}" in body
    assert "QDRANT" not in body
    assert 'sandbox_image="${SAGE_SANDBOX_IMAGE:-sage-sandbox:v2}"' in body
    assert "docker build" in body
    assert "sage github solve" in body
    assert "upload-artifact" not in body

    solve_step = next(
        step
        for step in document["runs"]["steps"]
        if "sage github solve" in step.get("run", "")
    )
    for step in document["runs"]["steps"]:
        if step is solve_step:
            continue
        rendered = yaml.safe_dump(step)
        assert "OPENAI_API_KEY" not in rendered
        assert "GEMINI_API_KEY" not in rendered
        assert "TYPESAFE_API_KEY" not in rendered
        assert "LANGSMITH_API_KEY" not in rendered
        assert "SAGE_LEGION_QDRANT" not in rendered
    assert "docker build" not in yaml.safe_dump(solve_step["env"])


def test_workflow_filters_exact_issue_commands_and_uses_least_privilege() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert document["on"] == {"issue_comment": {"types": ["created"]}}
    assert document["permissions"] == {}
    assert document["concurrency"]["cancel-in-progress"] is False
    jobs = document["jobs"]
    assert set(jobs) == {"gate", "solve", "finalize"}
    assert jobs["gate"]["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    assert jobs["solve"]["permissions"] == {
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }
    assert "env" not in jobs["solve"]
    assert jobs["finalize"]["permissions"] == {
        "issues": "write",
        "pull-requests": "read",
    }
    assert jobs["gate"]["timeout-minutes"] == 10
    assert jobs["solve"]["timeout-minutes"] == 90
    assert jobs["solve"]["timeout-minutes"] * 60 > 4_800 + 300
    assert jobs["finalize"]["timeout-minutes"] == 5
    gate_filter = jobs["gate"]["if"]
    assert "pull_request == null" in gate_filter
    assert "comment.body == '/sage solve'" in gate_filter
    assert "comment.body == '/sage fix'" not in gate_filter


def test_workflow_configures_every_non_secret_example_value_in_yaml() -> None:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    example_names = {
        line.partition("=")[0]
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*=.*", line)
    }
    configuration = document["env"]

    assert set(configuration) == (
        example_names - SECRET_CONFIGURATION
    )
    assert all(isinstance(value, str) for value in configuration.values())
    assert not SECRET_CONFIGURATION & configuration.keys()
    assert configuration["SOLVER_MODEL"] == "gpt-5.4-mini"
    assert configuration["REVIEWER_MODEL"] == "gemini-3.5-flash"
    assert not any("EMBEDDING" in key or "QDRANT" in key for key in configuration)
    assert configuration["SAGE_JEV_NAVIGATION_MODE"] == "off"
    assert configuration["SAGE_JEV_LOG_INPUT"] == "false"
    assert configuration["SAGE_JEV_CAPTURE"] == "false"
    assert "SAGE_LEGION_QDRANT_PATH" not in configuration


def test_workflow_pins_sage_and_external_actions_and_scopes_model_secret() -> None:
    body = WORKFLOW.read_text(encoding="utf-8")
    document = yaml.safe_load(body)
    references = re.findall(r"^\s*uses:\s+([^\s#]+)", body, re.MULTILINE)

    assert references
    assert all(FULL_SHA_REFERENCE.fullmatch(reference) for reference in references)
    sage_references = [reference for reference in references if reference.startswith("24aysh/sage/")]
    assert sage_references
    assert {reference.rsplit("@", 1)[1] for reference in sage_references} == {
        SAGE_ACTION_SHA
    }
    jobs = document["jobs"]
    assert "OPENAI_API_KEY" not in yaml.safe_dump(jobs["gate"])
    assert "OPENAI_API_KEY" not in yaml.safe_dump(jobs["finalize"])
    assert "GEMINI_API_KEY" not in yaml.safe_dump(jobs["gate"])
    assert "GEMINI_API_KEY" not in yaml.safe_dump(jobs["finalize"])
    assert "TYPESAFE_API_KEY" not in yaml.safe_dump(jobs["gate"])
    assert "TYPESAFE_API_KEY" not in yaml.safe_dump(jobs["finalize"])
    assert "LANGSMITH_API_KEY" not in yaml.safe_dump(jobs["gate"])
    assert "LANGSMITH_API_KEY" not in yaml.safe_dump(jobs["finalize"])
    assert "SAGE_LEGION_QDRANT" not in yaml.safe_dump(jobs["gate"])
    assert "SAGE_LEGION_QDRANT" not in yaml.safe_dump(jobs["finalize"])
    assert "secrets.OPENAI_API_KEY" in yaml.safe_dump(jobs["solve"])
    assert "secrets.GEMINI_API_KEY" in yaml.safe_dump(jobs["solve"])
    assert "secrets.TYPESAFE_API_KEY" in yaml.safe_dump(jobs["solve"])
    assert "secrets.LANGSMITH_API_KEY" in yaml.safe_dump(jobs["solve"])
    assert "QDRANT" not in yaml.safe_dump(jobs["solve"])
    assert "secrets.SAGE_LEGION_EMBEDDINGS_ENABLED" not in body
    assert "vars." not in body
    solve_action = next(
        step
        for step in jobs["solve"]["steps"]
        if "/sage-solve@" in step.get("uses", "")
    )
    assert "runtime" not in solve_action["with"]
    assert "model-profile" not in solve_action["with"]
    assert "admission-enabled" not in solve_action["with"]
    assert set(solve_action["with"]) == {
        "github-token",
        "openai-api-key",
        "gemini-api-key",
        "typesafe-api-key",
        "langsmith-api-key",
        "base-sha",
        "status-comment-id",
    }
    assert "ANTHROPIC_API_KEY" not in body
    assert "anthropic-api-key" not in body
    assert "pull_request_target" not in body
    assert "cancel-in-progress: false" in body


def test_workflow_uploads_only_allowlisted_diagnostics() -> None:
    body = WORKFLOW.read_text(encoding="utf-8")
    allowed = {
        "metadata.json",
        "github.json",
        "agent-final.json",
        "solver-plan.json",
        "solver-final.json",
        "changed-files.json",
        "diff.patch",
        "usage.json",
        "legion-memory.json",
        "navigation.json",
        "terminal.json",
        "verification-summary.json",
        "review.json",
    }
    uploaded = set(
        re.findall(r"diagnostics-path }}/([^\s]+)", body)
    )

    assert uploaded == allowed
    assert "repo/" not in body
    assert "issue.md" not in body
    assert "retention-days: 7" in body
