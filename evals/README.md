# Retrieval evaluation

This package measures the production retrieval shortlist before and after Sage's
Jev file-relevance filter. It does not run Solver, Reviewer, Docker, or a solve.
The benchmark labels in `correct.json` are used only for local metric calculation
and are never included in Jev input.

Prepare one repository checkout and ready retrieval graph, then create a dataset:

```text
issues/
  issue-1.md
  issue-2.md
  correct.json
```

`correct.json` maps `issue_1`, `issue_2`, and so on to nonempty arrays of exact,
repository-relative POSIX paths. Run:

```bash
make eval-retrieval REPO="/path/to/repo" ISSUE="/path/to/issues" \
  ISSUE_COUNT=2 GRAPH="/path/to/graph.sqlite3"
```

The command validates the complete dataset and graph before calling TypeSafe,
forces Jev evaluation mode `on`, reuses the configured production model and
thresholds, and makes at most one Jev request per nonempty Issue shortlist. It
writes an immutable graph snapshot, `results.json`, `evals.md`, and one JSON
record per Issue under `.sage/evals/retrieval/` by default. Set `OUTPUT_DIR` to
an absent or empty directory to choose another destination.

This is a paid live evaluation and requires `TYPESAFE_API_KEY`. For ordinary
batches, set `SAGE_JEV_LOG_INPUT=false` and `SAGE_JEV_CAPTURE=false`; Issue text
and repository metadata may otherwise appear in local logs or capture files.
