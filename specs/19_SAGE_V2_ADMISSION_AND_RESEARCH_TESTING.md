# Sage V2 Admission and Research Testing Guide

## Purpose

This guide tests the V2 Admission context and controller-side research feature
implemented from
[`18_SAGE_V2_ADMISSION_CONTEXT_AND_RESEARCH_TOOLS_DESIGN.md`](18_SAGE_V2_ADMISSION_CONTEXT_AND_RESEARCH_TOOLS_DESIGN.md).

The expected sequential graph is:

```text
Admission (OpenAI Solver model, read-only)
  -> READY -> Solver (same OpenAI model, full tools)
  -> verification -> Reviewer (Gemini)

Admission
  -> human input/design required -> GitHub questions, no branch or PR
```

The repository Docker sandbox remains network-disabled. Optional web and
documentation research runs in the trusted Sage controller and accepts no
model-selected free-form URL fetches.

---

## 1. Prerequisites

From the repository root, confirm:

```bash
git --version
uv --version
docker --version
docker info
```

Create local configuration if needed:

```bash
make env
```

Required live V2 secrets:

```dotenv
OPENAI_API_KEY=...
GEMINI_API_KEY=...
SAGE_RUNTIME=v2-prototype
SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true
```

Admission uses `SAGE_V2_SOLVER_MODEL`. There is no separate Admission model or
third model credential.

---

## 2. Deterministic checks

Install the locked environment and run the full suite without paid model or
research calls:

```bash
make setup
make v2-check
make check
```

Focused feature tests:

```bash
UV_CACHE_DIR=/tmp/sage-admission-test-cache \
uv run --project apps/agent pytest -q \
  apps/agent/tests/runtimes/v2/test_admission.py \
  apps/agent/tests/runtimes/v2/test_runtime.py \
  apps/agent/tests/research/test_service.py \
  apps/agent/tests/integrations/github/test_status.py \
  apps/agent/tests/workflow/test_github_issue.py
```

These tests use scripted models, a fake search provider, and fake GitHub APIs.
They make no live OpenAI, Gemini, Tavily, or GitHub requests.

---

## 3. Test a normal READY Issue locally

Use a committed repository and Issue file whose requirements are complete:

```bash
make v2-first-run \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/issue.md
```

Expected log sequence:

```text
Admission: activity
Admission: result
Solver: activity
Solver: result
Verifier: started
Reviewer: activity
Reviewer: result
```

Admission may also log bounded `Research: documentation search` or
`Research: web search` events when research is configured and needed.

The final run path is printed. Inspect:

```bash
run_dir=/absolute/path/printed/by/sage

sed -n '1,220p' "$run_dir/admission-final.json"
sed -n '1,260p' "$run_dir/admission-context.json"
sed -n '1,220p' "$run_dir/solver-plan.json"
sed -n '1,220p' "$run_dir/review.json"
git -C "$run_dir/repo" status --short
git -C "$run_dir/repo" diff --check HEAD --
```

Confirm:

- `admission-final.json` says `READY`;
- the context `base_sha` matches the run metadata;
- the Solver plan copies `admission_context_digest`;
- important `admission_evidence_ids` exist in the context;
- the context file is outside the candidate repository;
- the candidate contains only Issue-related changes; and
- review passes before the run reports `completed`.

---

## 4. Test human clarification locally

Create an Issue that deliberately omits one material fact, for example:

```markdown
# Change the retry behavior

Change the retry count used by the client.
```

Use a repository where no test, configuration, or convention defines the new
count. Run:

```bash
make solve \
  REPO=/absolute/path/to/repository \
  ISSUE=/absolute/path/to/incomplete-issue.md
```

An expected handled result has CLI exit code `2`; `make solve` displays it as a
warning. Inspect the newest run:

```bash
sed -n '1,220p' "$run_dir/admission-final.json"
sed -n '1,260p' "$run_dir/clarification.json"
git -C "$run_dir/repo" status --short --untracked-files=all
```

Confirm:

- the disposition is `NEEDS_HUMAN_INFORMATION` or
  `NEEDS_HUMAN_DESIGN_DECISION`;
- there are no more than three focused questions;
- questions explain why the answer is blocking;
- repository evidence names paths/lines where useful;
- no Solver activity appears after the Admission result;
- Git status is empty; and
- no Solver plan, candidate, branch, or Pull Request is created.

On GitHub, the existing Sage status comment should contain the same questions.
Answer them in the Issue, then create one new exact `/sage solve` or
`/sage fix` comment. Arbitrary replies do not trigger Sage.

---

## 5. Enable optional web and documentation research

The implemented adapter is Tavily. Configure it only in the trusted controller
environment:

```dotenv
SAGE_RESEARCH_ENABLED=true
SAGE_WEB_SEARCH_PROVIDER=tavily
SAGE_WEB_SEARCH_API_KEY=...
```

Optional restrictions:

```dotenv
SAGE_RESEARCH_ALLOWED_DOMAINS=docs.python.org,docs.example.com
SAGE_OFFICIAL_DOCUMENTATION_DOMAINS=docs.python.org,docs.example.com
```

`SAGE_RESEARCH_ALLOWED_DOMAINS` restricts accepted search queries and results.
`SAGE_OFFICIAL_DOCUMENTATION_DOMAINS` marks matching documentation results as
authoritative. Values are comma-separated public hostnames, without schemes or
paths.

Test with an Issue that refers to a dependency pinned in a manifest or
lockfile and requires its documented API. Expected behavior:

1. Admission inspects the manifest/lockfile first.
2. `search_documentation` includes the package and pinned version.
3. `read_documentation` accepts only a same-run `research-NNN` result ID.
4. Solver receives the accepted context instead of repeating discovery.
5. Any research result used by the plan appears in `research_result_ids`.
6. `research-summary.json` contains URLs/digests but no page bodies or queries.

Inspect safe research diagnostics:

```bash
sed -n '1,260p' "$run_dir/research-summary.json"
```

Never put the Tavily key in an Issue, repository variable, committed `.env`,
model prompt, or sandbox command.

---

## 6. Verify graceful operation without research

Leave these empty:

```dotenv
SAGE_WEB_SEARCH_PROVIDER=
SAGE_WEB_SEARCH_API_KEY=
```

Run a repository-local Issue. It should still complete. If an agent calls a
research tool, the tool returns a bounded `unavailable` result and instructs
the agent to continue with repository evidence where possible.

Research unavailability should become `ENVIRONMENT_BLOCKED` only when an
external public contract is genuinely required to decide whether work can
proceed.

---

## 7. GitHub Actions configuration

Repository secrets:

```text
OPENAI_API_KEY
GEMINI_API_KEY
LANGSMITH_API_KEY              # optional
SAGE_WEB_SEARCH_API_KEY        # optional
```

Repository variables:

```text
SAGE_RUNTIME=v2-prototype
SAGE_V2_ADMISSION_ENABLED=true
SAGE_WEB_SEARCH_PROVIDER=tavily  # omit when research is not configured
```

The workflow does not need broader GitHub permissions for research. The
optional research key is scoped to the solve composite action and is never
passed to Docker.

After the action implementation is committed, update both immutable action
pins in `.github/workflows/sage.yml` to that commit SHA before installing the
workflow in another repository.

Trigger one controlled canary with:

```text
/sage solve
```

For a READY Issue, expect a draft Pull Request. For an incomplete Issue, expect
the existing status comment to show questions and no branch/PR.

Uploaded diagnostics may contain:

```text
admission-final.json
admission-context-summary.json
clarification.json
research-summary.json
```

The full `admission-context.json`, Issue document, raw research content, and
repository checkout are not uploaded.

---

## 8. LangSmith and logs

Enable tracing only after approving that data transfer:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=sage-v2
```

Expected trace hierarchy includes Admission, Solver, Reviewer, repository tool
spans, and research tool spans under one Sage V2 workflow. Logs and safe
research artifacts intentionally omit prompts, full repository excerpts,
queries, page bodies, and credentials.

---

## 9. Rollback checks

To temporarily restore the specification-16 Solver -> Reviewer flow:

```dotenv
SAGE_V2_ADMISSION_ENABLED=false
```

To keep Admission but disable external research:

```dotenv
SAGE_RESEARCH_ENABLED=false
```

Run `make v2-check` after either change. These flags do not change V1.

---

## 10. Troubleshooting

### Admission reports invalid model output

Check whether it called `save_admission_context` before returning and copied
the returned digest exactly into `AdmissionResult.context_digest`.

### Solver plan is rejected

When Admission is enabled, the plan must copy the context digest and may cite
only evidence IDs present in the saved context. Research IDs must exist in the
same run.

### Context is stale

Admission context is bound to the accepted base SHA, normalized Issue text,
and repository evidence file hashes. A mismatch is a safe invariant failure;
start a fresh exact-SHA run rather than editing artifacts.

### Research provider is unavailable

Confirm the provider is exactly `tavily`, its key is configured as a secret,
and the controller can reach Tavily. Do not enable network access in Docker.

### A domain is rejected

Use a public hostname such as `docs.python.org`, without `https://`, a path,
port, IP literal, wildcard, or credentials.

### GitHub shows questions but no PR

That is the intended human-required route. Answer the questions, update the
Issue contract, and create one new exact command comment.
