# Sage V2 local workflow fixture

This directory is a checked-in, non-sensitive project for a live local V2
smoke test. The project intentionally contains a one-character calculator bug
and a failing standard-library test that describes the expected behavior.

From the Sage repository root, configure the OpenAI and Gemini provider keys in
the untracked `.env`, review the Google context-use warning in the V2 testing
guide, then run:

```bash
make v2-first-run
```

The Make target copies `project/` into a temporary directory, initializes that
copy as a Git repository, runs the constrained sequential V2 workflow, and
validates the resulting run artifacts and diff. It never edits this checked-in
fixture. The retained candidate is printed at the end and lives below
`.sage/runs/`.

For a different repository, provide both inputs:

```bash
make v2-first-run REPO=/absolute/path/to/repo ISSUE=/absolute/path/to/issue.md
```

This is a live test: it sends the Issue and bounded fixture context to Google
and OpenAI and may incur provider charges. The target fails unless
the workflow returns a completed, non-empty candidate.
