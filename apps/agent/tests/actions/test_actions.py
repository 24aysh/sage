import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[4]
ACTIONS = ROOT / ".github" / "actions"
FULL_SHA_REFERENCE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


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


def test_gate_action_is_model_secret_free_and_uses_pinned_source() -> None:
    body = (ACTIONS / "sage-gate" / "action.yml").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" not in body
    assert "openai-api-key" not in body
    assert "github.action_path" in body
    assert "sage github gate" in body
    assert "sage github finalize" in body


def test_solve_action_uses_exact_credential_free_target_checkout() -> None:
    body = (ACTIONS / "sage-solve" / "action.yml").read_text(encoding="utf-8")

    assert "ref: ${{ inputs.base-sha }}" in body
    assert "persist-credentials: false" in body
    assert "fetch-depth: 0" in body
    assert "github.action_path" in body
    assert "OPENAI_API_KEY: ${{ inputs.openai-api-key }}" in body
    assert "SAGE_GITHUB_TOKEN: ${{ inputs.github-token }}" in body
    assert "docker build" in body
    assert "sage github solve" in body
    assert "upload-artifact" not in body
