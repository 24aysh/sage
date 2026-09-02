# Sage

Sage turns an authorized GitHub Issue into a reviewed draft pull request. An
OpenAI-backed Solver plans and edits through narrow repository tools, ordinary
Python verifies the Git candidate, and an independent Gemini-backed Reviewer
judges the actual diff. Repairable findings start a fresh Solver session. Sage
never merges code.

There is one supported architecture and one construction path—no runtime
selector or retained earlier implementation.

## Start in 60 seconds

Requirements: Python 3.14, `uv`, Git, and Docker.

```bash
make env
# Add OPENAI_API_KEY and GEMINI_API_KEY to .env.
make bootstrap
make first-run REPO=/absolute/repository ISSUE=/absolute/issue.md
```

Run the complete model-free development check with:

```bash
make check
```

See [architecture](docs/architecture.md) for system ownership and
[testing](docs/testing.md) for offline, Docker, live-solve, and GitHub checks.

## Change map

| I need to change… | Start here |
| --- | --- |
| Solver behavior or tools | `apps/agent/src/sage/agents/solver.py` |
| Reviewer criteria | `apps/agent/src/sage/agents/reviewer.py` |
| Solve/repair routing | `apps/agent/src/sage/orchestration/solve.py` |
| Repository capability | `apps/agent/src/sage/repository/service.py` |
| Model/provider behavior | `apps/agent/src/sage/providers/` |
| GitHub trigger/publication | `apps/agent/src/sage/integrations/github/` and `apps/agent/src/sage/workflows/github.py` |
| Settings | `apps/agent/src/sage/config.py` and `.env.example` |
| Run evidence | `apps/agent/src/sage/artifacts/store.py` |
| Web visual design | `apps/web/DESIGN.md` |

The implemented consolidation record is
[docs/refactor-plan.md](docs/refactor-plan.md). Retained specifications provide
design archaeology; current guidance lives under `docs/`.
