"""Read-only installation diagnostics for the Sage GitHub workflow."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

_FULL_PIN = re.compile(r"uses:\s+[^\s@]+@[0-9a-f]{40}(?:\s+#.*)?$")
_EXTERNAL_USE = re.compile(r"^\s*-?\s*uses:\s+([^\s]+)", re.MULTILINE)


def main() -> int:
    """Check local installation prerequisites without reading secret values."""

    root = Path.cwd().resolve()
    status = 0
    required = (
        root / ".github/actions/sage-gate/action.yml",
        root / ".github/actions/sage-solve/action.yml",
        root / ".github/workflows/sage.yml",
        root / "docs/testing.md",
        root / ".env.example",
    )
    for path in required:
        if path.is_file():
            _ok(f"tracked file exists: {path.relative_to(root)}")
        else:
            _error(f"required file is missing: {path.relative_to(root)}")
            status = 1

    for path in required[:3]:
        if not path.is_file():
            continue
        body = path.read_text(encoding="utf-8")
        for reference in _EXTERNAL_USE.findall(body):
            if reference.startswith("./"):
                continue
            line = f"uses: {reference}"
            if _FULL_PIN.fullmatch(line):
                continue
            _error(f"moving or placeholder action reference in {path.relative_to(root)}")
            status = 1

    guide = root / "docs/testing.md"
    if guide.is_file():
        guide_body = guide.read_text(encoding="utf-8")
        if "OPENAI_API_KEY" in guide_body and "github-doctor" in guide_body:
            _ok("testing guide documents the secret name and doctor command")
        else:
            _error("testing guide is missing required setup documentation")
            status = 1

    environment_example = root / ".env.example"
    if environment_example.is_file():
        body = environment_example.read_text(encoding="utf-8")
        required_names = (
            "SAGE_GITHUB_API_TIMEOUT_SECONDS",
            "SAGE_GITHUB_MAX_COMMENTS",
            "SAGE_GITHUB_MAX_COMMENT_PAGES",
            "SAGE_GITHUB_MAX_CONTEXT_CHARS",
        )
        if all(name in body for name in required_names):
            _ok("optional GitHub controller limits are documented")
        else:
            _error(".env.example is missing GitHub controller limits")
            status = 1

    if shutil.which("docker") is None:
        _error("Docker is not installed or not on PATH")
        status = 1
    else:
        docker = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if docker.returncode == 0:
            _ok("Docker daemon is reachable")
        else:
            _error("Docker is installed, but its daemon is not reachable")
            status = 1
    return status


def _ok(message: str) -> None:
    print(f"OK: {message}")


def _error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
