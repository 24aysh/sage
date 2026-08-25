# Sage

Sage turns GitHub issues into draft pull requests. It studies the codebase,
plans the change, writes and tests the code in an isolated workspace, and sends
the result through an independent review. If an issue lacks key information,
Sage asks for clarification before making changes. It never merges code.

## Architecture

```mermaid
flowchart TD
    A[GitHub issue command] --> B[Authorize request and prepare exact commit]
    B --> C[Check issue and repository context]
    C -->|More information needed| D[Ask the maintainer]
    C -->|Ready| E[Coding agent]
    E --> F[Save implementation plan]
    F --> G[Edit and test in an isolated workspace]
    G --> H[Build candidate from Git changes]
    H --> I[Independent reviewer]
    I -->|Changes requested| E
    I -->|Approved| J[Run final checks]
    J --> K[Create branch and draft pull request]
```

```mermaid
flowchart LR
    GH[GitHub] <--> CT[Workflow controller]
    CT <--> CM[Coding model]
    CT <--> RM[Review model]
    CT <--> RT[Repository tools]
    CT <--> RS[Optional documentation and web research]
    CT --> AR[Run records]
    RT <--> WS[Isolated repository workspace]
    RT --> SB[Offline Docker sandbox]
```
