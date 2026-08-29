# Sage Memory Engine V1 Specification

## 1. Purpose

Sage's Memory Engine is a **semantic, content-addressed overlay on Git's repository tree**.

Its purpose is not to solve GitHub issues by itself and not to replace source-code reading.

Its responsibility is:

> **Reduce the repository search space so the Solver can begin from a small, relevant set of files instead of repeatedly exploring the repository from scratch.**

Once relevant files are localized, the **current raw source code is always authoritative**, and the Solver performs normal code reasoning.

---

## 2. Core System Boundary

### Memory Engine responsibility

The Memory Engine answers:

> **Which files should Sage look at?**

It may:

- remember previously explored repository regions;
- search semantic memories;
- navigate repository structure;
- retain several plausible branches at once;
- materialize a bounded multi-file context;
- expand that context when the Solver needs more information.

### Solver responsibility

The Solver answers:

> **What inside these files matters, and what should be changed?**

The Solver:

- reads current raw source;
- reasons about implementation details;
- follows code behavior;
- designs the fix;
- edits files;
- runs tests;
- produces the pull request.

### Absolute authority

If memory and source disagree:

> **Git/current raw source wins.**

---

# 3. Repository Memory Topology

## 3.1 Git is the physical backbone

The complete repository structure remains represented by Git.

Git objects provide the source identities:

- Git **tree OID** → directory source identity;
- Git **blob OID** → file source identity.

SMRT does not duplicate the complete repository tree.

It stores only a **partial semantic overlay containing repository regions Sage has actually explored**.

---

## 3.2 Persistent semantic node types

V1 has only two persistent semantic node types.

### DirectoryNode

Attached to a Git tree/path.

Represents Sage's learned understanding of an explored directory.

### FileNode

Attached to a Git blob/path.

Represents Sage's learned understanding of an explored file.

### No SymbolNode

V1 must **not create persistent SymbolNodes**.

Symbols remain metadata on FileNodes.

This keeps the memory layer focused on file localization rather than building a complete program graph.

---

# 4. Identity Model

Every semantic memory should distinguish three concepts.

## 4.1 Source identity

The exact Git content identity.

For files:

```text
blob_oid
```

For directories:

```text
tree_oid
```

This determines whether source content/structure changed.

---

## 4.2 Logical identity

The current repository path.

Example:

```text
src/auth/session.ts
```

Logical identity may change even if the underlying Git blob does not.

This allows rename reuse.

---

## 4.3 Semantic object identity

Semantic objects are immutable, content-addressed objects.

A semantic object should be identified by a canonical digest covering the fields needed to reproduce its meaning, including relevant items such as:

- node type;
- source identity;
- semantic payload;
- deterministic metadata where applicable;
- semantic provenance;
- schema version;
- summarizer/prompt version.

Existing semantic objects are never silently mutated into descriptions of different source content.

---

# 5. Cold Start Behaviour

## 5.1 No full-repository indexing pass

When Sage encounters a repository for the first time:

```text
no useful SMRT
→ Sage explores repository normally
→ memories are created only for explored regions
```

There must be **no mandatory up-front full semantic indexing** of the repository.

---

## 5.2 Lazy memory creation

A semantic node is created when Sage actually explores that region.

Unexplored files/directories:

- remain visible through Git;
- do not receive placeholder SMRT nodes;
- do not receive semantic summaries;
- do not consume summarizer calls.

The first solve may therefore behave approximately like a normal cold solve.

Future solves benefit from whatever the first solve learned.

---

# 6. Deterministic Structural Metadata

## 6.1 Tree-sitter support in V1

Tree-sitter metadata is generated only for:

- Python;
- TypeScript;
- JavaScript.

Other languages may still receive DirectoryNode/FileNode semantic memories when explored, but without parser-derived structural metadata.

---

## 6.2 File structural metadata

For supported languages, FileNodes may contain deterministic information such as:

```yaml
structure:
  symbols: []
  imports: []
  exports: []
```

Useful signatures may also be retained where extraction is straightforward.

The parser layer must remain shallow.

V1 does **not** attempt:

- full call-graph construction;
- whole-program dataflow;
- LSP/SCIP dependency indexing;
- persistent symbol-level graph nodes;
- complete static analysis.

---

# 7. Semantic Node Schema

## 7.1 FileNode

Conceptual schema:

```yaml
type: file

path: src/auth/session.ts
blob_oid: <git-blob-oid>

semantic:
  summary: "..."
  responsibilities:
    - "..."
  concepts:
    - "..."

structure:
  symbols:
    - "..."
  imports:
    - "..."
  exports:
    - "..."

semantic_state: valid | stale | missing
summarizer_version: "..."
schema_version: "..."
```

---

## 7.2 DirectoryNode

Conceptual schema:

```yaml
type: directory

path: src/auth
tree_oid: <git-tree-oid>

semantic:
  summary: "..."
  responsibilities:
    - "..."
  not_responsible_for:
    - "..."
  concepts:
    - "..."

coverage:
  semantic_state: partial | complete
  explored_children: [...]

derived_from:
  - <immediate-child-memory-id>

semantic_state: valid | stale | missing
summarizer_version: "..."
schema_version: "..."
```

---

## 7.3 `not_responsible_for`

`not_responsible_for` exists **only on DirectoryNodes**.

Its purpose is negative routing evidence.

Example:

```yaml
not_responsible_for:
  - token persistence
  - database migrations
```

It must not be treated as ordinary positive search text.

---

## 7.4 Directory semantics are partial by default

Because memories are created lazily, a DirectoryNode describes:

> **What Sage currently knows about this directory from explored child memories.**

It must not implicitly claim exhaustive knowledge of all repository children.

---

# 8. Directory Memory Construction

Directory memories are generated **bottom-up from immediate child memories**.

Do not rebuild a parent by rereading all descendant raw files.

Example:

```text
session.ts memory
token.ts memory
password.ts memory
        ↓
    auth/ memory
```

The immediate child semantic cards are the semantic evidence for the parent.

---

# 9. Persistence

## 9.1 Canonical storage

V1 uses **one Postgres database** as the canonical persistence layer.

Postgres stores:

- immutable semantic objects;
- snapshot records;
- path/object mappings;
- semantic hierarchy references;
- semantic provenance;
- node freshness/state;
- snapshot status;
- current/latest snapshot pointer;
- retention metadata.

---

## 9.2 No object store in V1

Do not require:

- S3;
- R2;
- another blob/object store.

One Postgres database is sufficient for the V1 architecture.

---

## 9.3 Derived indexes are not canonical

Any local search index is disposable.

If a derived index is lost, canonical semantic memory still exists in Postgres.

---

# 10. Snapshot Model

## 10.1 Merkle-style semantic overlay

Each repository memory state is represented by a snapshot referencing a root semantic overlay object.

The overlay is sparse.

Only explored regions are represented.

Example:

```text
Git repository
├── src/auth/session.ts       explored
├── src/auth/token.ts         explored
├── src/payments/...          unexplored
└── tools/...                 unexplored

SMRT snapshot
└── explored semantic structure only
```

---

## 10.2 No structural placeholders for unexplored files

SMRT must **not create nodes solely to mirror unexplored Git paths**.

Git remains the complete structural truth.

---

## 10.3 Copy-on-write snapshots

A new snapshot is derived from the latest snapshot using copy-on-write references.

If one file changes:

```text
old file object
→ new file object

affected ancestors
→ new semantic/tree objects when required

everything unchanged
→ shared references
```

Do not physically clone the complete snapshot.

---

## 10.4 Snapshot states

At minimum:

```text
BUILDING
READY
```

Only `READY` snapshots can become the repository's latest usable memory.

A failed/incomplete build must never replace the last valid `READY` snapshot.

---

# 11. Sparse Search Backend

## 11.1 V1 backend

V1 uses:

> **SQLite FTS5**

through a logical `SparseSearchBackend` abstraction.

---

## 11.2 FTS5 role

FTS5 is:

- local;
- derived;
- disposable;
- used only for sparse lexical retrieval over known semantic memories.

FTS5 is **not** the source of truth.

---

## 11.3 Search scope

The sparse index contains only known:

- DirectoryNodes;
- FileNodes.

It does **not** index the entire raw repository source.

---

## 11.4 Indexed fields

Recommended fielded index:

```text
path
identifiers
imports
summary
responsibilities
concepts
```

`not_responsible_for` is excluded from positive FTS text.

---

# 12. Query Normalization and Exact Evidence

## 12.1 V1 normalization

Keep V1 simple.

For sparse search:

- preserve original source values separately;
- use lowercase forms for normal search;
- no stemming;
- no complex camelCase/PascalCase decomposition initially;
- no aggressive stopword removal.

Examples:

```text
SessionManager
→ exact: SessionManager
→ FTS: sessionmanager

invalidateSession
→ exact: invalidateSession
→ FTS: invalidatesession
```

If evaluation later proves identifier splitting useful, it may be added without changing the architecture.

---

## 12.2 Exact-match channel

FTS5 must not be the only retrieval channel.

Before/beside sparse ranking, use deterministic exact evidence such as:

- exact path;
- exact filename;
- exact known symbol;
- import/module name;
- known identifier;
- direct code reference.

Exact deterministic evidence should outrank vague lexical similarity.

---

# 13. Retrieval Architecture

The Memory Engine must maintain multiple hypotheses instead of forcing a single branch too early.

## 13.1 Diverse adaptive beam

Normal active beam:

```text
3–4 branches
```

Default target:

```text
4 branches
```

The important property is **diversity**, not merely count.

Prefer independent hypotheses such as:

```text
src/auth
src/api
src/db
tests/auth
```

instead of four nearly identical children of the same subtree.

---

## 13.2 Candidate sources

Beam candidates may come from:

1. hierarchical SMRT children;
2. global FTS5 memory search;
3. deterministic Git/path/identifier evidence;
4. plausible unexplored Git branches.

The fourth source is essential.

Because memory is partial, Sage must not become trapped only in areas it previously explored.

---

## 13.3 Round behaviour

Conceptually:

```text
current 3–4 branches
        ↓
expand candidates
        ↓
pool children/candidates
        ↓
rank/fuse evidence
        ↓
enforce diversity
        ↓
prune back to ~4 branches
```

Do not allow uncontrolled growth such as:

```text
4 → 16 → 64 → ...
```

A pruned branch is not permanently forbidden and may be reopened when later evidence supports it.

---

# 14. Search Ranking Philosophy

Avoid overfitting V1 to a large hand-designed weighted scoring equation.

Use evidence tiers.

Conceptually:

```text
Tier 1:
exact path

Tier 2:
exact symbol / identifier / filename

Tier 3:
strong FTS result + responsibility/context corroboration

Tier 4:
semantic lexical match
```

Possible penalties:

- `not_responsible_for` conflict;
- stale semantic state;
- weak/partial semantic evidence.

FTS5 BM25 is useful inside the lexical ranking process, but must not override stronger deterministic evidence.

---

# 15. Navigation Termination and Context Forest

## 15.1 Do not force one final file

SMRT should terminate with a **small multi-file context forest**, not one guessed file.

Example:

```text
PRIMARY
src/auth/session.ts

SUPPORTING
src/api/password_reset.ts
src/db/session_repository.ts

VERIFICATION
tests/auth/session_test.ts
```

---

## 15.2 Branch terminal states

A branch may become:

```text
ACTIVE
TERMINAL_FILE
PRUNED
```

If one branch reaches a good file early, park that file and continue unresolved branches.

Do not terminate all navigation merely because the first relevant file was found.

---

## 15.3 Candidate size

The exact file-count/token limits remain configurable.

The architectural goal is:

> **small enough to bound initial Solver context, large enough to preserve competing hypotheses.**

The initial context is not permanent and may grow during solving.

---

# 16. Solver Context Expansion

The context forest is a **soft boundary with controlled expansion**.

The Solver is not imprisoned inside the first files, but healthy SMRT should prevent silent whole-repository wandering.

---

## 16.1 Deterministic direct expansion

If the Solver discovers an exact dependency from source, it can directly materialize it.

Examples:

- import;
- exact module path;
- referenced filename;
- direct configuration path;
- known symbol/file relationship.

Example:

```text
session.ts imports ./token_store
→ materialize token_store.ts directly
```

No new global beam search is necessary.

---

## 16.2 Semantic/search expansion

If the Solver knows what it needs conceptually but not where it is:

```text
"find device-session persistence"
```

it requests another SMRT expansion.

SMRT performs bounded retrieval again.

---

## 16.3 Whole-repository wandering

While SMRT is healthy, the Solver should not silently revert to unrestricted exploration such as:

- broad recursive grep over everything;
- repeatedly listing the entire repository;
- reading hundreds of unrelated files.

If broader exploration is needed, the Solver asks SMRT to widen the search.

---

## 16.4 Editing rule

Before a file is modified, it must first be:

```text
materialized
→ read/understood
→ part of active context
→ edited
```

Do not edit a file directly from a search hit without contextualizing it.

---

## 16.5 Context provenance

Record why each file entered active context.

Examples:

```yaml
added_by: initial_smrt_forest
```

```yaml
added_by: deterministic_dependency
reason: "Imported by src/auth/session.ts"
```

```yaml
added_by: smrt_expansion
reason: "Matched device session persistence"
```

This provides an auditable retrieval/exploration trace.

---

# 17. Semantic Memory Generation

## 17.1 Dedicated cheaper summarizer

Persistent semantic memories are generated by a **dedicated, cheaper configurable summarizer model**.

It is not another autonomous agent.

The main Solver model does not directly author canonical memory.

---

## 17.2 Why Solver-generated memory is avoided

Solver reasoning is issue-conditioned.

Persistent memory should describe:

> **What this repository area generally does**

rather than:

> **What happened to matter for the issue that caused Sage to inspect it.**

---

## 17.3 File summarizer input

Use:

- path;
- current raw file source;
- deterministic Tree-sitter metadata.

Do **not** provide:

- current GitHub issue;
- Solver's fix hypothesis;
- patch plan.

---

## 17.4 Directory summarizer input

Use:

- directory path;
- currently explored immediate-child semantic cards.

Do not provide current issue context.

---

## 17.5 Structured output only

The summarizer returns only the locked schema.

Example:

```json
{
  "summary": "...",
  "responsibilities": ["..."],
  "concepts": ["..."]
}
```

Directory example:

```json
{
  "summary": "...",
  "responsibilities": ["..."],
  "not_responsible_for": ["..."],
  "concepts": ["..."]
}
```

---

## 17.6 Trust boundary

Repository text is untrusted data.

The summarizer:

- receives no dangerous tools;
- cannot directly mutate Git;
- cannot directly perform arbitrary DB writes;
- only returns a semantic-card candidate.

Trusted SMRT code validates and persists the result.

---

## 17.7 Versioning

Store summarizer/prompt version with generated memories.

Changing the summarizer later must not silently redefine existing immutable memory objects.

---

# 18. Updating Memory Across Git Commits

## 18.1 Update trigger

V1 performs memory catch-up only when:

```text
/sage solve
```

is processed.

There is no mandatory push-time/background memory maintenance.

This avoids doing work when Sage is not being used.

---

## 18.2 Catch-up base

At solve start:

```text
latest READY SMRT snapshot
        ↓
target Git commit
```

Use Git tree/blob OIDs to determine what changed.

---

## 18.3 Unchanged file

If blob OID is unchanged:

```text
reuse existing FileNode semantic object
```

---

## 18.4 Changed known file

If:

```text
old blob OID != current blob OID
```

the old semantic memory remains immutable.

The current file semantic state becomes:

```text
stale/missing
```

Do **not** regenerate immediately just because it changed.

Refresh only if retrieval actually needs it.

---

## 18.5 Changed unexplored file

If a file has never had an SMRT node:

```text
ignore it during semantic catch-up
```

Git still exposes it if future navigation decides to explore it.

---

## 18.6 Rename with identical blob

If path changes but blob OID remains identical:

```text
reuse semantic memory
update logical/path mappings
```

No new semantic summarization is required.

---

## 18.7 Deleted known file

The old semantic object may remain because an older retained snapshot references it.

The new snapshot does not map the deleted path.

Parent semantic memories that depended on the deleted child become stale.

---

# 19. Structural Freshness vs Semantic Freshness

A directory Git tree can change even when its learned semantic evidence did not.

Example:

```text
auth/
├── session.ts  explored
├── token.ts    explored
└── oauth.ts    unexplored
```

If only `oauth.ts` changes:

```text
auth tree OID changes
```

but the existing directory semantic card may still be valid because it was derived only from:

```text
session.ts memory
token.ts memory
```

Therefore distinguish:

```text
structural/source freshness
```

from:

```text
semantic freshness
```

A new structural object/version may reuse an unchanged semantic payload when its semantic dependencies remain unchanged.

---

# 20. Semantic Provenance

Directory semantic objects must record the explored immediate-child memories from which they were derived.

Example:

```yaml
derived_from:
  - session.ts@memory-X
  - token.ts@memory-T
```

This allows precise semantic invalidation.

Rule:

```text
Git structure changed
but semantic dependencies unchanged
→ semantic payload reusable
```

```text
semantic dependency changed
→ parent semantics stale
```

---

# 21. Lazy Semantic Revalidation

At solve start, perform deterministic Git catch-up.

Do not eagerly regenerate all stale semantic memories.

Example:

```text
changed:
auth/session.ts
payments/swap.ts
config/runtime.ts

current issue:
authentication bug
```

Only if retrieval reaches `auth/session.ts` should Sage pay for its semantic refresh.

The other changed memories remain stale until a future solve needs them.

Old stale memory may be used only as a cautious hint that a region could be worth refreshing.

Before relying on it as current semantic truth:

```text
refresh against current source
```

---

# 22. Parent Directory Update Strategy

Use a **hybrid delta + periodic reconstruction** strategy.

---

## 22.1 Small/local changes

When one or a few known child memories change:

```text
old parent card
+
old child card(s)
+
new child card(s)
        ↓
delta revision
```

---

## 22.2 Larger/repeated changes

When:

- many known children change; or
- the parent has accumulated several delta revisions;

regenerate the parent from all currently explored immediate-child memories.

Exact thresholds remain configuration/evaluation parameters.

---

## 22.3 Newly explored child

When a new child semantic memory appears for the first time:

```text
new semantic evidence
→ parent becomes semantically stale
```

Update the parent lazily when needed.

---

## 22.4 Upward propagation

Semantic changes propagate upward only while they actually change parent semantics.

Conceptually:

```text
child semantic changes
        ↓
update parent
        ↓
parent semantic payload changed?
   yes → propagate upward
   no  → stop
```

Do not perform unnecessary ancestor regeneration if canonical semantic output did not materially change.

---

# 23. Queue and Concurrency Model

## 23.1 Single active solve per repository

Sage maintains an issue queue.

Per repository:

```text
maximum active solve = 1
```

Conceptually:

```text
Issue A
→ finish/fail
→ Issue B
→ finish/fail
→ Issue C
```

V1 does not solve two issues for the same repository simultaneously.

---

## 23.2 Consequences

The memory engine does not require complex coordination for:

- simultaneous memory writers;
- competing snapshot publication;
- run leases;
- multi-solver snapshot races.

Postgres transactions are still required for normal atomicity.

---

# 24. Snapshot Retention

## 24.1 Sliding window

Retain exactly the latest:

```text
5 READY SMRT snapshots
```

for each repository.

Example:

```text
S2 S3 S4 S5 S6
```

When S7 becomes READY:

```text
drop S2 from retained roots
retain S3 S4 S5 S6 S7
```

---

## 24.2 Lazy cloning

A new snapshot is created from the latest snapshot using copy-on-write references.

Unchanged objects are shared.

Only revised parts receive new objects.

Semantic refresh may remain lazy even after the structural snapshot has advanced.

---

## 24.3 Cleanup

After the retention window advances:

```text
remove objects unreachable from all 5 retained snapshots
```

Do not blindly delete every object referenced by the removed snapshot because many objects may also be shared by newer snapshots.

---

## 24.4 No general-purpose V1 GC complexity

Given the single-run queue and fixed five-snapshot window, V1 does not require:

- run leases;
- time-based retention;
- full reference-count machinery;
- background mark-and-sweep scheduling;
- long tombstone grace periods.

The retained five snapshots are the memory roots.

---

# 25. Snapshot Publication Safety

New snapshot publication must be atomic.

Conceptually:

```text
build snapshot
↓
validate
↓
transaction:
    mark snapshot READY
    update latest_snapshot
    trim retained window
↓
commit
```

If Sage crashes before publication:

```text
previous READY snapshot remains latest
```

Incomplete `BUILDING` state must never become authoritative.

---

# 26. Memory Failure Behaviour

Memory is an optimization, not a correctness dependency.

The failure rule is intentionally simple.

## 26.1 Any memory failure during a solve

If SMRT is considered failed/unhealthy:

```text
disable memory for that solve
→ do not attempt layered recovery
→ do not trust partial memory
→ solve issue using normal repository exploration
```

The failed memory state must not continue influencing that solve.

---

## 26.2 Fallback mode

Once fallback is triggered:

```text
current Git/source
→ normal unrestricted repository exploration
→ Solver completes issue
```

Decision 15's SMRT context restrictions no longer apply because SMRT is disabled.

---

## 26.3 Memory failure must not fail the GitHub issue

A memory failure alone is not a solve failure.

The Solver should still produce a patch/PR if the underlying issue can be solved.

---

# 27. Memory Failure Trace in Pull Request

If the solve used memory fallback, the generated draft PR description must include a concise diagnostic section.

Example:

```markdown
### Sage Memory

Status: fallback

SMRT could not be used during this solve.

Failure:
- component: FTS5
- stage: initial memory load
- error: <sanitized error>
- snapshot: <snapshot-id-if-known>
- target commit: <commit>
- fallback: full repository exploration

The patch was generated without repository memory.
```

Include useful fields such as:

- failed component;
- failure stage;
- error type;
- sanitized error message;
- snapshot ID if available;
- target commit;
- fallback action.

Do not dump giant raw stack traces into the PR description.

Full traces belong in Sage's internal/run logs.

---

# 28. Complete `/sage solve` Behaviour

The complete expected V1 lifecycle is:

```text
GitHub issue enters Sage queue
        ↓
wait until repository has no active Sage solve
        ↓
pick next issue
        ↓
resolve target repository commit
        ↓
load latest READY SMRT snapshot
        ↓
attempt solve-time Git/OID catch-up
        │
        ├── memory failure
        │       ↓
        │   disable SMRT for this solve
        │       ↓
        │   normal repository exploration
        │       ↓
        │   solve issue
        │       ↓
        │   PR includes memory-failure trace
        │
        └── memory healthy
                ↓
          derive current sparse snapshot
          using copy-on-write
                ↓
          mark changed semantic dependencies
          stale where required
                ↓
          query issue against:
              exact evidence
              +
              fielded FTS5
              +
              hierarchy
              +
              plausible unexplored Git branches
                ↓
          maintain 3–4 diverse hypotheses
                ↓
          descend/expand candidates
                ↓
          lazily refresh stale memories
          only when retrieval needs them
                ↓
          park useful terminal files
          while unresolved branches continue
                ↓
          produce small multi-file context forest
                ↓
          materialize current raw files
                ↓
          Solver begins implementation reasoning
                ↓
          direct exact dependency?
             yes → materialize directly
                ↓
          unknown additional location?
             yes → request bounded SMRT expansion
                ↓
          newly explored region?
             yes → lazily create semantic memory
                ↓
          edit only files that have been
          materialized/read into active context
                ↓
          run tests / validate patch
                ↓
          update required semantic cards lazily
                ↓
          publish new READY snapshot atomically
                ↓
          retain newest five snapshots
          and clean unreachable old objects
                ↓
          create draft PR
                ↓
          process next queued issue
```

---

# 29. Behavioural Invariants

The following rules should be treated as hard V1 invariants.

## Invariant 1

**Git is complete repository truth. SMRT is a partial learned overlay.**

## Invariant 2

**No semantic node is created merely because a Git path exists.**

## Invariant 3

**Persistent memory has DirectoryNodes and FileNodes only.**

## Invariant 4

**Raw source is authoritative after localization.**

## Invariant 5

**Semantic objects are immutable.**

## Invariant 6

**Unchanged Git content should reuse existing memory whenever possible.**

## Invariant 7

**A source change does not automatically justify an LLM semantic refresh.**

Refresh only when that semantic knowledge is actually needed.

## Invariant 8

**Directory semantic invalidation follows semantic provenance, not Git tree change alone.**

## Invariant 9

**Retrieval maintains multiple plausible branches instead of betting on one early LLM guess.**

## Invariant 10

**Known memory must not prevent discovery of unexplored Git regions.**

## Invariant 11

**Memory narrows source exploration; it does not replace source reasoning.**

## Invariant 12

**While SMRT is healthy, broad repository wandering should go through controlled context expansion.**

## Invariant 13

**If memory fails, Sage completely falls back to normal source exploration for that solve.**

## Invariant 14

**Memory failure is reported in the PR but does not itself fail the issue.**

## Invariant 15

**Only one Sage solve is active per repository at a time.**

## Invariant 16

**Exactly five READY snapshots are retained as the sliding memory window.**

---

# 30. V1 Components

A clean implementation can be divided into the following logical modules.

```text
MemoryEngine
├── GitStateResolver
├── SnapshotManager
├── SemanticObjectStore      → Postgres
├── TreeSitterExtractor
├── SemanticSummarizer       → cheaper configurable model
├── SemanticValidator
├── SparseSearchBackend
│   └── SQLiteFTS5Backend
├── ExactEvidenceMatcher
├── BeamNavigator
├── ContextForestManager
├── MemoryRefreshManager
├── ParentRevisionManager
├── RetentionManager
└── MemoryFailureReporter
```

These are logical responsibilities, not necessarily separate services.

V1 should prefer a simple implementation over unnecessary distributed infrastructure.

---

# 31. Explicit Non-Goals for V1

V1 does not attempt to build:

- a complete code knowledge graph;
- persistent SymbolNodes;
- a complete call graph;
- whole-program static analysis;
- a mandatory full-repository semantic index;
- vector embeddings/vector database retrieval;
- continuous background memory updating;
- concurrent issue solving for the same repository;
- perfect semantic understanding before source is read;
- an autonomous memory-recovery system after failure;
- an immutable restriction that the Solver may never leave the initial context forest.

---

# 32. Configuration Knobs to Benchmark Later

The following are intentionally **not architectural locks** and should remain configurable:

- exact beam-width adjustment rules around the default 3–4;
- maximum candidates expanded per round;
- maximum initial context-forest files;
- context token budget;
- FTS5 field weights;
- evidence ranking thresholds;
- number of delta parent revisions before full reconstruction;
- changed-child threshold for full directory reconstruction;
- summarizer model;
- summarizer prompt details;
- semantic-card maximum lengths;
- stale-memory hint penalties.

These should be chosen through actual Sage repository benchmarks rather than arbitrary architecture assumptions.

---

# 33. Final V1 Mental Model

The simplest way to think about the system is:

```text
Git
= everything that actually exists

SMRT
= everything Sage has learned

FTS5
= fast lookup over what Sage has learned

Tree-sitter
= deterministic structural facts

Cheap summarizer
= issue-independent semantic description

Beam navigator
= preserve several plausible repository hypotheses

Context forest
= bounded starting point for the Solver

Solver
= reads current source and actually solves the issue
```

And across solves:

```text
Solve N
   ↓
learn a little more
   ↓
publish copy-on-write snapshot
   ↓
keep latest 5
   ↓
Solve N+1 starts from accumulated knowledge
```

The Memory Engine therefore behaves as a **lazy, reusable repository-learning layer** rather than a precomputed index or a replacement for normal code reasoning.
