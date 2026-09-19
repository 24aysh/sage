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

<img width="4265" height="7502" alt="diagram" src="https://github.com/user-attachments/assets/91d8c53c-b49f-47de-a04b-286a06b18be6" />


