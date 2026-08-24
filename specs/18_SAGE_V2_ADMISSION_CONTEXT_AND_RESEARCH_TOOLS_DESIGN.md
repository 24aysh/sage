# Sage V2 Admission Context and Research Tools Design

## Document status

> **Status:** Implemented locally on 25 August 2026. Deterministic unit,
> workflow, Actions-policy, and compilation checks are the release gate; a
> controlled live provider/GitHub canary remains required after immutable
> action pins are updated.
>
> **Date:** 25 August 2026
>
> **Extends:**
> [`16_SAGE_V2_ARCHITECTURE_MIGRATION.md`](16_SAGE_V2_ARCHITECTURE_MIGRATION.md),
> which remains authoritative for the tool-driven Solver, saved Solver plan,
> independent Reviewer, Git-derived candidate, and draft Pull Request flow.

This design adds a read-only Admission node before the existing V2 Solver,
persists the repository context gathered by Admission for reuse by Solver, and
adds safe controller-side web and documentation research tools. The configured
model roles remain exactly the same:

```text
OpenAI coding model: Admission + Solver
Google Gemini model: Reviewer
```

Admission is a graph node and a distinct traced activity, but it is not a third
configured model role. It reuses the Solver model, provider, retry policy, and
accounting boundary.

Two implementation refinements avoid duplicate orchestration:

- the existing tool graph's typed terminal response is the Admission submit
  boundary, so a redundant `submit_admission` tool/extra model turn was not
  added; and
- Reviewer receives the verified Admission context and safe research
  provenance through its existing structured provider boundary. Documentation
  and web tool loops remain on Admission/Solver, avoiding a second Gemini tool
  runtime solely for occasional lookup.

---

## 1. Decision summary

The target flow is:

```text
GitHub gate and exact-SHA checkout
                 |
                 v
      Admission (Solver model, read-only)
      issue + repository + optional research
                 |
       save bounded evidence snapshot
                 |
       +---------+----------+
       |                    |
       | human input needed | ready
       v                    v
update existing Issue   Solver consumes saved context
status comment with     and fills only missing gaps
focused questions              |
       |                       v
 no branch or PR         save plan -> edit -> test
                               |
                               v
                      independent Reviewer
                               |
                      pass -> draft Pull Request
```

The following decisions are locked by this specification:

1. Admission runs before any repository mutation.
2. Admission reuses the configured Solver model; no Planner model or third
   model configuration is introduced.
3. Admission may use only read-only repository and research tools.
4. Admission must persist a typed, bounded evidence snapshot before returning
   a terminal decision.
5. A `READY` decision routes to the existing Solver, which receives that
   snapshot and avoids repeating the initial repository scan.
6. A human-owned blocker halts before implementation and updates the existing
   GitHub status comment with no more than three actionable follow-up
   questions.
7. A new exact `/sage solve` or `/sage fix` comment starts a fresh run after
   the maintainer supplies the requested information.
8. Web and documentation access is implemented as controller-side tools. The
   repository Docker sandbox remains network-disabled and credential-free.
9. Repository evidence is preferred. Official, version-matched documentation
   is preferred over general web results.
10. External content is untrusted data, never workflow instructions.
11. The existing Solver plan, Reviewer gate, deterministic verification, Git
    guards, creation-only push, and draft-PR publication behavior remain
    unchanged.
12. There is no global model-call limit. Existing turn, retry, wall-clock,
    no-progress, and finalization-reserve guards remain in force.

---

## 2. Motivation

The current two-role V2 asks the Solver to inspect the repository, decide what
the Issue means, write a plan, implement it, and verify it. A separate
readiness check is useful because some Issues lack a required product decision,
credential, reproduction detail, external contract, or acceptance criterion.
Starting edits in those cases wastes model and CI time and risks guessing at a
human-owned decision.

A naive Admission node would inspect the repository and then discard its work.
The Solver would perform the same discovery again, roughly doubling the
initial repository-reading cost. This design removes that duplication by
making Admission's useful output a reusable context artifact rather than only
a classification label.

The current tool set is also limited to the checked-out repository. That is
insufficient when correct implementation depends on:

- the current API of a declared dependency;
- behavior that changed between dependency versions;
- an official migration or compatibility guide;
- a compiler, runtime, framework, or provider error message; or
- a public protocol or standards document.

Research access improves coding ability only if it preserves the existing
sandbox and trust boundaries. Giving arbitrary network access to shell commands
inside the target repository would expose credentials, enable uncontrolled
downloads, and allow untrusted repository code to choose network destinations.
The research capability therefore belongs in a narrow controller-owned service
with normalized responses, explicit budgets, provenance, and SSRF controls.

---

## 3. Goals

The implementation must:

1. Determine whether Sage can safely act with the Issue and available context.
2. Stop before mutation when a missing human fact or design decision is truly
   blocking.
3. Explain every human-required decision with focused questions and concrete
   repository evidence.
4. Reuse Admission's repository discovery in Solver rather than repeat the
   initial scan.
5. Keep the persisted context compact, typed, auditable, and bound to the
   accepted base SHA and Issue text.
6. Let Solver retrieve additional context when implementation reveals a real
   gap.
7. Preserve the existing two configured model roles.
8. Preserve sequential execution; only one model request is active at a time.
9. Add read-only web and documentation research without enabling network access
   in the repository sandbox.
10. Prefer authoritative and version-relevant sources.
11. Give all external evidence explicit provenance and freshness metadata.
12. Prevent web content from controlling agent behavior or leaking secrets.
13. Keep research failures bounded and observable.
14. Reuse existing GitHub clarification, status, artifact, provider, tool-loop,
    and tracing infrastructure.
15. Keep normal tests deterministic and free of live paid API calls.

---

## 4. Non-goals

This design does not introduce:

- a new Planner agent;
- a third model provider or model configuration;
- parallel agents or concurrent model calls;
- autonomous recursive delegation;
- unrestricted browsing or arbitrary URL access;
- network access from commands in the target repository;
- installation of packages from research results;
- copying complete web pages into prompts or artifacts;
- long-term cross-repository memory;
- automatic continuation after a human-required result;
- automatic acceptance of a proposed default;
- replacement of the Solver-authored plan;
- replacement of deterministic tests, Git validation, or Reviewer approval;
- automatic merge or ready-for-review promotion; or
- a requirement that web search be configured for ordinary repository-local
  Issues.

Admission answers only whether work may proceed autonomously. Solver still owns
the implementation plan.

---

## 5. Relationship to the current V2 architecture

The design extends, rather than replaces, the architecture from specification
16.

| Current responsibility | Treatment in this design |
| --- | --- |
| GitHub authorization and deduplication | Reuse unchanged. |
| Exact accepted base SHA | Reuse and bind Admission context to it. |
| Isolated workspace | Reuse unchanged. |
| Network-disabled Docker sandbox | Preserve unchanged. |
| Solver model and tool loop | Reuse for Admission with a read-only registry, then reuse normally for Solver. |
| Solver-authored saved plan | Preserve; Admission context is evidence, not the plan. |
| Tool-driven file mutation | Preserve unchanged. |
| Deterministic verification | Preserve unchanged. |
| Gemini Reviewer | Preserve unchanged; optionally add official-document lookup. |
| Git-derived candidate | Preserve unchanged. |
| GitHub status comment | Extend through the existing clarification renderer. |
| Draft Pull Request publication | Preserve unchanged and only reach it after `READY`, implementation, verification, and review pass. |
| LangSmith tracing and operational logs | Add Admission and Research spans under the existing workflow trace. |

---

## 6. Model and node ownership

### 6.1 Admission node

Admission reuses the Solver's configured OpenAI coding model. It receives a
separate system instruction and a separate tool registry, and appears as a
separate node in logs and traces.

Admission owns:

- interpreting the Issue as a readiness question;
- performing bounded read-only repository discovery;
- identifying relevant files, symbols, conventions, and verification commands;
- looking up external documentation only when local evidence is insufficient;
- distinguishing model-solvable uncertainty from human-owned ambiguity;
- saving the reusable context snapshot; and
- returning one structured admission decision.

Admission does not own:

- the implementation plan;
- file mutation;
- shell command execution;
- dependency installation;
- candidate verification;
- review; or
- publication.

### 6.2 Solver node

Solver remains the implementation owner from specification 16. It additionally
receives the accepted Admission context and must treat it as its starting
evidence. Solver may inspect more files or perform more research when a concrete
implementation gap appears, but should not repeat the same initial tree,
manifest, and relevant-file reads merely to recreate the snapshot.

Solver continues to save its own plan before mutation. The Admission snapshot
may inform the plan but is not itself an implementation plan.

### 6.3 Reviewer node

Reviewer remains an independent Gemini role. It receives:

- the Issue and selected Issue discussion;
- the saved Solver plan;
- the authoritative candidate;
- deterministic verification evidence;
- the compact Admission context; and
- citations for research that materially informed implementation.

Reviewer remains read-only. It may receive an official-documentation lookup
tool for checking a versioned external contract. General web search is not
enabled for Reviewer by default because review should be grounded in the Issue,
repository, candidate, and authoritative documentation rather than open-ended
web opinion.

---

## 7. Admission decisions

The terminal Admission disposition is one of:

| Disposition | Meaning | Route |
| --- | --- | --- |
| `READY` | The Issue has enough context for an autonomous attempt. Some non-blocking uncertainty may remain. | Continue to Solver. |
| `NEEDS_HUMAN_INFORMATION` | A fact available only from a maintainer or reporter is required. | Persist clarification and halt. |
| `NEEDS_HUMAN_DESIGN_DECISION` | Multiple materially different valid behaviors exist and repository evidence does not choose one. | Persist clarification and halt. |
| `ENVIRONMENT_BLOCKED` | Required infrastructure, credential, service, platform, or reproducible environment is unavailable to Sage. | Halt with an operational summary; ask a question only when a human answer can resolve it. |
| `UNSUPPORTED` | The requested work is outside the allowed coding or publication boundary. | Halt with a precise unsupported reason. |

Admission must bias toward `READY` when uncertainty can be resolved through
repository inspection, official documentation, a deterministic test, or a
reasonable implementation choice already supported by project conventions.
It must not ask a human to perform work that the available tools can do.

Human intervention is justified only when the missing answer would materially
change the implementation, public behavior, data safety, or acceptance result.

### 7.1 Examples that should be `READY`

- The Issue names a failing test and the repository contains the implementation.
- The dependency version is present in a lockfile and official documentation
  defines the relevant API.
- Two internal approaches are possible but project conventions strongly favor
  one.
- A precise error message can be traced through code or public documentation.
- Minor naming and code-organization choices are left open.

### 7.2 Examples that need human information

- A reported bug depends on a private payload or reproduction that is not in
  the Issue or repository.
- The requested integration requires an account-specific identifier Sage
  cannot infer or retrieve.
- Expected output is referenced but not supplied and no test or contract
  defines it.

### 7.3 Examples that need a human design decision

- Two incompatible public API behaviors both satisfy the Issue wording and the
  repository has no precedent.
- A data migration may be destructive or backward-incompatible and the desired
  policy is unstated.
- The Issue requests authorization or security policy changes without naming
  the intended trust boundary.

---

## 8. Typed domain contracts

The following schemas are normative. Names may be adjusted to match repository
conventions, but their semantics and validation constraints must be preserved.

```python
class AdmissionDisposition(StrEnum):
    READY = "READY"
    NEEDS_HUMAN_INFORMATION = "NEEDS_HUMAN_INFORMATION"
    NEEDS_HUMAN_DESIGN_DECISION = "NEEDS_HUMAN_DESIGN_DECISION"
    ENVIRONMENT_BLOCKED = "ENVIRONMENT_BLOCKED"
    UNSUPPORTED = "UNSUPPORTED"


class EvidenceSourceType(StrEnum):
    REPOSITORY = "repository"
    ISSUE = "issue"
    OFFICIAL_DOCUMENTATION = "official_documentation"
    WEB = "web"


class EvidenceReference(BaseModel):
    id: str
    source_type: EvidenceSourceType
    title: str
    locator: str
    excerpt: str
    content_digest: str
    line_start: int | None = None
    line_end: int | None = None
    detected_version: str | None = None
    fetched_at: datetime | None = None
    authoritative: bool = False


class AdmissionRequirement(BaseModel):
    id: str
    statement: str
    evidence_ids: tuple[str, ...]
    status: Literal["supported", "assumed", "blocked"]


class AdmissionContextSnapshot(BaseModel):
    version: Literal[1] = 1
    base_sha: str
    issue_digest: str
    summary: str
    requirements: tuple[AdmissionRequirement, ...]
    relevant_paths: tuple[str, ...]
    relevant_symbols: tuple[str, ...]
    repository_conventions: tuple[str, ...]
    candidate_verification_commands: tuple[str, ...]
    assumptions: tuple[str, ...]
    open_questions: tuple[str, ...]
    evidence: tuple[EvidenceReference, ...]
    created_at: datetime
    digest: str


class AdmissionResult(BaseModel):
    version: Literal[1] = 1
    disposition: AdmissionDisposition
    summary: str
    rationale: str
    confidence: float
    context_digest: str
    clarification: ClarificationPacket | None = None
```

Validation rules:

1. `context_digest` must identify the exact persisted snapshot.
2. `READY` must not include a clarification packet.
3. Human-required dispositions must include a valid clarification packet.
4. `ENVIRONMENT_BLOCKED` may include a clarification only when the maintainer
   can supply a fact or make a decision that changes the route.
5. `UNSUPPORTED` must not disguise a vague or merely difficult task.
6. Every requirement and question that cites repository or research evidence
   must refer to an existing evidence ID.
7. Repository evidence must include a path and content digest; line numbers are
   required for excerpts from normal text files.
8. External evidence must include a canonical public URL, retrieval time, and
   authority classification.
9. Snapshot collections and strings must have explicit maximum sizes.
10. The snapshot digest is computed over canonical serialized content excluding
    the digest field itself.

The existing `BlockingQuestion` and `ClarificationPacket` contracts in
`sage.domain.admission` should be extended rather than duplicated.

---

## 9. Persisted context lifecycle

### 9.1 Artifact location

Admission artifacts are controller-owned run artifacts, not repository files:

```text
.sage/runs/<workspace>/<run-id>/
  admission-context.json
  admission-final.json
  clarification.json             # only when human input is required
  research-summary.json          # only when research tools were used
  ...existing V2 artifacts...
```

The files must never appear in the candidate Git diff and must not be mounted
as writable files inside the target repository.

### 9.2 Save-before-submit protocol

Admission uses a run-scoped `AdmissionContextSession`, analogous to the current
`SolverPlanSession`:

1. Admission gathers evidence using bounded tools.
2. Admission calls `save_admission_context(...)` with the complete snapshot.
3. The controller validates, canonicalizes, hashes, and persists the snapshot.
4. Admission calls `submit_admission(...)`, referencing the saved digest.
5. The controller rejects a missing, stale, or mismatched digest.

The model never chooses the artifact path and cannot overwrite another run's
artifact.

### 9.3 Context passed to Solver

Solver receives a compact rendered form containing:

- accepted base SHA and Issue digest;
- normalized requirements;
- relevant paths and symbols;
- bounded evidence excerpts with stable IDs;
- repository conventions;
- suggested verification commands;
- assumptions and non-blocking uncertainty; and
- research citations that materially affect implementation.

Raw Admission messages and complete tool outputs are not replayed into Solver.
This avoids prompt growth and accidental propagation of irrelevant or hostile
content.

Solver instructions must say:

1. treat the snapshot as prior evidence, not infallible truth;
2. do not repeat reads already represented by valid evidence solely for
   orientation;
3. retrieve additional context when implementation requires it;
4. save its own implementation plan before mutation; and
5. cite the Admission evidence IDs used in important plan decisions.

### 9.4 Freshness and integrity

Before Solver starts, the controller verifies:

- the current accepted base SHA equals `snapshot.base_sha`;
- the normalized Issue content hashes to `snapshot.issue_digest`;
- each repository evidence digest still matches the checked-out file content;
  and
- the serialized snapshot matches its own digest.

Because Admission and Solver run sequentially in one isolated workspace before
mutation, any mismatch is an invariant failure, not a reason to silently trust
stale context. The run must fail safely with an internal-contract outcome.

After Solver mutation starts, the Admission snapshot remains a record of the
base repository. Reviewer evaluates the actual candidate and must not mistake
base excerpts for current file content.

### 9.5 Context bounds

Recommended defaults:

| Limit | Default |
| --- | ---: |
| Evidence references | 40 |
| Repository excerpt per reference | 4,000 characters |
| External excerpt per reference | 2,000 characters |
| Total rendered Solver context | 48,000 characters |
| Relevant paths | 40 |
| Relevant symbols | 60 |
| Requirements | 30 |
| Assumptions | 20 |
| Human questions | 3 |

When evidence exceeds the budget, Admission should keep the most relevant
excerpt and a digest/locator for later retrieval. It must not truncate JSON in
a way that invalidates the artifact.

---

## 10. Admission tool registry

Admission receives only:

| Tool | Purpose |
| --- | --- |
| `list_tree` | Discover bounded repository structure. |
| `search_text` | Locate literal names, errors, symbols, and conventions. |
| `read_file` | Read bounded repository text. |
| `search_documentation` | Search official, version-relevant documentation. |
| `read_documentation` | Read a bounded normalized result selected from documentation search. |
| `search_web` | Search public web sources when repository and official docs are insufficient. |
| `fetch_web_page` | Read a bounded public page selected under URL policy. |
| `save_admission_context` | Validate and persist the complete context snapshot. |
| `submit_admission` | Return the structured decision bound to the saved snapshot. |

Admission must not receive:

- `save_plan` or `revise_plan`;
- `replace_text`, `write_file`, `delete_file`, or `move_file`;
- `run_command`;
- raw patch tools;
- GitHub mutation tools; or
- publication credentials.

Tool availability, not prompt wording, enforces read-only behavior.

---

## 11. Research service architecture

Research tools use one provider-neutral service boundary:

```text
agent tool
   |
   v
ResearchService
   |-- query validation and per-run budget
   |-- DocumentationSearchProvider
   |-- WebSearchProvider
   |-- SafePageFetcher
   |-- URL/domain/network policy
   |-- content normalization and redaction
   |-- in-run cache and provenance recorder
   v
normalized bounded ResearchResult
```

The core runtime depends on protocols, not a vendor response shape:

```python
class WebSearchProvider(Protocol):
    async def search(self, request: WebSearchRequest) -> WebSearchResponse: ...


class DocumentationSearchProvider(Protocol):
    async def search(
        self, request: DocumentationSearchRequest
    ) -> DocumentationSearchResponse: ...


class PageFetcher(Protocol):
    async def fetch(self, request: PageFetchRequest) -> PageDocument: ...
```

Provider adapters normalize results before they reach an agent. API keys are
read once through `Settings`, passed only to the adapter, and never included in
model messages, repository commands, URLs, artifacts, or logs.

The first implementation should use existing HTTP/client dependencies when
they are sufficient. A new production dependency requires a concrete provider
SDK or parser benefit and must not duplicate an installed capability.

---

## 12. Research tool contracts

### 12.1 `search_documentation`

```text
search_documentation(
  query,
  ecosystem=None,
  package=None,
  version=None,
  max_results=5
)
```

Behavior:

- detects package/version evidence from repository manifests and lockfiles when
  the caller omits it;
- prioritizes official project, standards-body, and provider documentation;
- returns title, canonical URL, source authority, detected/applicable version,
  short snippet, and opaque result ID;
- clearly labels version mismatches and community sources; and
- does not return complete pages.

### 12.2 `read_documentation`

```text
read_documentation(result_id, section=None, max_chars=12_000)
```

Behavior:

- accepts only a result ID issued earlier in the same run;
- refetches or reads cached content through the safe fetcher;
- extracts readable text and useful headings;
- returns bounded content plus citation metadata; and
- records a digest for the Admission or Solver artifact.

### 12.3 `search_web`

```text
search_web(query, domains=None, recency_days=None, max_results=5)
```

Behavior:

- is a fallback after repository and official-documentation discovery;
- supports a small validated public-domain filter;
- returns normalized snippets, canonical URLs, dates when known, and opaque
  result IDs;
- identifies likely primary sources; and
- never treats ranking as evidence of correctness.

### 12.4 `fetch_web_page`

```text
fetch_web_page(result_id, max_chars=12_000)
```

Behavior:

- accepts only a result ID returned by a same-run search;
- does not accept a free-form URL from the model in the first implementation;
- applies URL, DNS, redirect, MIME, response-size, and timeout policy;
- converts supported public text/HTML to bounded plain text;
- strips scripts, styles, forms, embedded instructions, and control characters;
  and
- returns provenance alongside content.

Requiring a prior search result materially reduces arbitrary-target and SSRF
risk while still supporting coding research.

---

## 13. Tool availability by node

| Capability | Admission | Solver | Reviewer |
| --- | :---: | :---: | :---: |
| Repository list/search/read | Yes | Yes | Bounded read-only when needed |
| Save Admission context | Yes | No | No |
| Save/revise Solver plan | No | Yes | No |
| Repository mutation | No | Yes, after saved plan | No |
| Allowed test commands | No | Yes | No |
| Official documentation search/read | Yes | Yes | Yes, only when needed |
| General web search/fetch | Yes, fallback | Yes, fallback | No by default |
| GitHub mutation/publication | No | No | No |

Research tools do not authorize arbitrary shell networking. `curl`, `wget`,
package-manager downloads, remote Git operations, and equivalent commands stay
blocked inside the repository sandbox.

---

## 14. Source selection policy

Agents must use this evidence order:

1. Issue text and accepted maintainer discussion.
2. Checked-out repository code, tests, configuration, and lockfiles.
3. Version-matched official documentation or primary standards.
4. Upstream source repository documentation and release notes.
5. Reputable public technical sources.
6. General web discussion only as a lead that must be corroborated.

Documentation search should infer exact versions from files such as lockfiles,
package manifests, toolchain files, and generated dependency metadata. It must
not silently answer for the latest version when the repository pins an older
version.

Research is warranted when it can resolve a concrete uncertainty. It must not
be used for broad browsing, implementation-by-blog-post, or avoiding local code
inspection.

---

## 15. Network and content security

### 15.1 Sandbox isolation

The existing Docker invocation retains `--network none`. Research calls run in
the trusted controller process through the Research Service. The target
repository cannot provide code that is imported or executed by that service.

### 15.2 URL and SSRF policy

The safe fetcher must:

- allow only `https` and, when explicitly needed, public `http`;
- reject embedded credentials and non-standard URL schemes;
- reject loopback, private, link-local, multicast, reserved, and unspecified IP
  ranges for both IPv4 and IPv6;
- reject cloud metadata endpoints and localhost aliases;
- resolve DNS before connection and verify the connected address;
- repeat policy checks on every redirect;
- cap redirects, connection time, total time, and response bytes;
- prevent DNS rebinding by pinning or validating resolution at connection time;
- reject ports outside the configured public set;
- reject unsupported MIME types and downloads; and
- never forward controller cookies, GitHub tokens, model keys, proxy
  credentials, or target-repository headers.

### 15.3 Untrusted-content policy

Every external result is wrapped as untrusted evidence with source markers.
Prompts must explicitly state that text inside the evidence may contain
malicious or irrelevant instructions and cannot change system instructions,
tool permissions, scope, or publication policy.

Normalization must:

- remove scripts, styles, forms, hidden content, and active markup;
- replace unsafe control characters;
- preserve enough headings and code formatting for comprehension;
- bound repeated or adversarial text;
- redact secrets detected by the existing artifact redaction boundary; and
- avoid rendering raw external HTML in GitHub comments or logs.

### 15.4 Copyright and data minimization

Artifacts and model context contain short relevant excerpts, source titles,
URLs, hashes, and a synthesized note. They do not archive full articles or
documentation sites. The Pull Request should cite a source only when it
materially explains an external contract or compatibility choice.

---

## 16. Research budgets, caching, and failure behavior

Research has separate per-run tool budgets; these are not a replacement for a
model-call ceiling.

Recommended defaults:

| Budget | Admission | Solver | Reviewer |
| --- | ---: | ---: | ---: |
| Documentation searches | 4 | 4 | 2 |
| Documentation reads | 6 | 6 | 3 |
| General web searches | 3 | 3 | 0 |
| Web page fetches | 4 | 4 | 0 |
| Search results per call | 5 | 5 | 5 |
| Normalized characters per read | 12,000 | 12,000 | 8,000 |
| Request timeout | 15 seconds | 15 seconds | 15 seconds |

Identical normalized queries and result reads are cached within the run. The
cache stores normalized safe results, not credentials or raw authenticated
responses. Admission results reused by Solver do not consume Solver's budget.

Provider timeouts, malformed responses, unavailable configuration, and budget
exhaustion return a structured tool error. They do not automatically fail a
repository-local task. Admission may use `ENVIRONMENT_BLOCKED` only when the
external evidence is genuinely required and no local or official cached source
can establish the contract.

Retries are bounded at the adapter boundary and must not retry validation,
policy, or unsupported-content errors.

---

## 17. Human clarification and GitHub behavior

### 17.1 Reuse the existing status path

The implementation must reuse:

- `ClarificationPacket` and `BlockingQuestion`;
- `AgentFinalOutput.clarification`;
- existing human-required `SolveOutcome` values;
- `render_workflow_status`; and
- the existing bot-owned status comment update.

It must not create a second Issue-commenting subsystem.

### 17.2 Question requirements

A human-required result includes one to three consolidated questions. Each
question contains:

- the exact information or decision required;
- why it changes or blocks implementation;
- repository evidence supporting the question;
- mutually exclusive options when the decision space is known; and
- an optional proposed default, clearly labeled as a proposal rather than an
  automatically accepted answer.

Questions must be answerable without reading private run artifacts. They must
not ask for broad restatements such as "provide more details" when a specific
missing fact can be named.

### 17.3 Status comment rendering

The existing status comment is updated to a terminal clarification state such
as:

```text
### Sage: more information is required

Sage inspected the Issue and repository but stopped before implementation.

Summary
<bounded summary>

1. <focused question>
   Why this blocks Sage: <reason>
   Repository evidence:
   - <path:line and concise observation>
   Options:
   - <option A>
   - <option B>
   Proposed default: <optional default>

Reply with the requested information, then create one new exact
`/sage solve` or `/sage fix` comment.
```

The renderer must include `BlockingQuestion.repository_evidence`; the current
renderer already supports the other fields and should be extended in place.
All content passes through existing Markdown escaping and size bounds.

### 17.4 No publication on halt

For human-required, environment-blocked, or unsupported Admission results:

- Solver is not invoked;
- no repository mutation is possible;
- no candidate branch is created;
- no Pull Request is opened; and
- the workflow terminates successfully as a handled Sage outcome unless an
  actual controller failure occurred.

### 17.5 Rerun semantics

Sage does not resume from an arbitrary Issue reply. The maintainer provides the
answer in the Issue discussion and then creates one new exact command comment.
The next gate builds the accepted Issue context, including relevant answers,
and starts a fresh exact-SHA run.

Admission context is run-scoped and is not blindly reused across runs because
the Issue discussion, repository base SHA, dependencies, or maintainer decision
may have changed. Provider-side prompt caching may reduce repeated cost, but it
is an optimization and not part of correctness.

The existing clarification round marker remains the source of round count. The
initial implementation keeps the current maximum of two rounds. On the second
unresolved round, the status should explicitly recommend that a maintainer
rewrite the Issue with the requested contract before running Sage again.

---

## 18. Graph and routing

The V2 graph becomes:

```text
START
  |
  v
preflight
  |
  v
admission
  |
  +-- READY -------------------------> solver
  |
  +-- NEEDS_HUMAN_* ----------------> clarification_terminal
  |
  +-- ENVIRONMENT_BLOCKED ----------> blocked_terminal
  |
  +-- UNSUPPORTED ------------------> unsupported_terminal

solver -> verify -> reviewer -> existing repair/review loop -> final guards
```

Routing is deterministic Python over a validated `AdmissionResult`; the model
does not name graph nodes or construct transitions.

Failure policy:

- malformed Admission output may receive the same bounded structured-output
  repair behavior used at existing provider boundaries;
- provider authentication, quota, rate-limit, timeout, and invalid-response
  failures map through existing typed provider outcomes;
- a context digest mismatch is an internal contract failure;
- mutation before `READY` is structurally impossible because Admission lacks
  mutation tools; and
- no Admission failure may silently fall through to Solver.

---

## 19. Solver integration

The Solver prompt gains an `ADMISSION CONTEXT` section rendered from the
validated snapshot. The existing Issue and base SHA remain explicit.

The tool loop should support an optional `context_evidence_id` on repository
and research reads so repeated evidence can be recognized and logged. This is
an optimization aid, not an authorization token.

Solver plan requirements gain:

- the Admission context digest;
- evidence IDs for material requirements;
- any newly discovered requirement not present in Admission; and
- a reason when the Solver intentionally departs from an Admission assumption.

The current `SolverPlanSession` remains the owner of plan persistence and the
plan-before-mutation guard. Admission must not write or revise the Solver plan.

If Solver discovers a genuinely human-owned blocker that Admission could not
have known, it may still terminate with the existing human-required outcome and
a clarification packet. This is not an error in itself, but it is measured as
an Admission false-ready result.

---

## 20. Reviewer integration

Reviewer receives the Admission summary and evidence relevant to the plan, but
not the complete raw research transcript. Reviewer checks:

1. the candidate satisfies the Issue;
2. the Solver plan covers the Issue requirements;
3. implementation matches the saved plan or records justified revisions;
4. external API assumptions are supported by cited, applicable documentation;
5. deterministic verification is sufficient; and
6. no research claim overrides repository or test evidence.

If official documentation is needed to validate a blocking finding, Reviewer
may use its bounded documentation tools. A research-tool failure alone must not
be presented as a code defect unless the external contract is necessary to
judge correctness.

---

## 21. Configuration

All configuration continues through the central typed `Settings` boundary.
Recommended environment variables are:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SAGE_V2_ADMISSION_ENABLED` | `true` | Rollout/rollback switch for the Admission node. |
| `SAGE_V2_ADMISSION_MAX_TURNS` | `12` | Read-only Admission tool-loop recursion bound. |
| `SAGE_V2_ADMISSION_CONTEXT_CHARS` | `48000` | Maximum rendered context passed to Solver. |
| `SAGE_MAX_CLARIFICATION_ROUNDS` | `2` | Maximum repeated human-clarification rounds. |
| `SAGE_RESEARCH_ENABLED` | `true` | Register research tools; tools report unavailable when no provider is configured. |
| `SAGE_WEB_SEARCH_PROVIDER` | empty | Selected provider adapter. |
| `SAGE_WEB_SEARCH_API_KEY` | empty | Provider credential; secret, never a GitHub variable. |
| `SAGE_RESEARCH_TIMEOUT_SECONDS` | `15` | Per-request timeout. |
| `SAGE_RESEARCH_MAX_RESULT_CHARS` | `12000` | Normalized response bound. |
| `SAGE_RESEARCH_ALLOWED_DOMAINS` | empty | Optional administrator allowlist in addition to built-in public-network policy. |

The existing `SAGE_V2_SOLVER_MODEL` configures both Admission and Solver. There
is deliberately no `SAGE_V2_ADMISSION_MODEL` in the first implementation.
Reviewer continues to use `SAGE_V2_REVIEWER_MODEL`.

`SAGE_RESEARCH_ENABLED=true` means the capabilities are available to the graph,
not that every task must use them. When no search provider is configured,
repository-local work continues and the tool returns a clear unavailable
result. A provider secret becomes required only when an administrator selects
that provider.

The GitHub composite action should add an optional masked search-provider key
input only when a concrete adapter is implemented. It must not broaden GitHub
token permissions. Local `make v2-first-run` should pass the same environment
configuration used by Actions.

---

## 22. Artifacts and diagnostics

### 22.1 Local run artifacts

The full run directory may contain:

| Artifact | Content |
| --- | --- |
| `admission-context.json` | Typed bounded context and evidence. |
| `admission-final.json` | Decision, rationale, confidence, and context digest. |
| `clarification.json` | Human questions for non-ready outcomes. |
| `research-summary.json` | Provider-neutral calls, source metadata, cache hits, errors, latency, and budgets without keys or full pages. |
| Existing Solver/verification/review artifacts | Unchanged. |

### 22.2 GitHub diagnostic upload

The allowlisted Actions artifact should include `admission-final.json` and
`clarification.json` when present. It should not upload the complete
`admission-context.json` or fetched page bodies by default because they may
contain private repository excerpts or unnecessary third-party text.

A safe `admission-context-summary.json` may be generated for Actions containing
only:

- schema version;
- base SHA and Issue digest;
- context digest;
- counts and truncation flags;
- relevant path names already present in changed-file diagnostics;
- external source titles and public URLs; and
- no repository excerpts.

Artifact uploads remain allowlisted explicitly; no directory-wide wildcard is
introduced.

---

## 23. Logging and LangSmith observability

Operational logs should make the new node visible in both local and GitHub
execution:

```text
Admission: activity
  Task: assess autonomous readiness and collect reusable context
  Model: openai/<configured solver model>

Research: documentation search
  Provider: <provider>
  Results: 5
  Cache: miss

Admission: context saved
  Evidence: 14
  Relevant paths: 8
  Digest: <short digest>

Admission: finished
  Decision: READY
  Questions: 0

Solver: activity
  Admission context: <short digest>
```

Logs must not print:

- secrets;
- full Issue bodies;
- complete model prompts or outputs;
- full queries when they may contain repository-private data;
- fetched page contents; or
- full repository excerpts.

LangSmith structure should be:

```text
Sage V2 workflow
  Admission
    model turns
    repository tool spans
    research tool spans
  Solver
    model turns
    repository/research tool spans
  Reviewer
    model turn and optional documentation spans
```

Trace metadata includes run ID, accepted base SHA prefix, model role, stage,
tool name, source type, result count, latency, cache status, and outcome. It
does not include provider keys or unrestricted raw external content.

Usage accounting separates:

- Admission model tokens;
- Solver model tokens;
- Reviewer model tokens;
- search calls;
- page/document reads;
- cache hits; and
- provider-reported research cost when available.

---

## 24. Module ownership and expected change surface

Implementation should reuse current abstractions and keep responsibilities
separate. The expected shape is:

| Area | Expected change |
| --- | --- |
| `sage.domain.admission` | Extend historical clarification types with Admission disposition/result contracts. |
| `sage.domain.admission_context` | Add evidence and persisted snapshot schemas if keeping them in `admission.py` would make that module unfocused. |
| `sage.runtimes.v2.admission` | Admission session, prompt rendering, structured terminal validation, and read-only tool registry. |
| `sage.runtimes.v2.runtime` | Insert Admission and deterministic routing before Solver; pass snapshot downstream. |
| `sage.runtimes.v2.prompts` | Add Admission instructions and compact context rendering; extend Solver/Reviewer inputs. |
| `sage.runtimes.v2.tools` | Reuse repository read wrappers; compose role-specific registries rather than duplicate them. |
| `sage.research.protocols` | Provider-neutral search/fetch interfaces and normalized models. |
| `sage.research.service` | Budgets, cache, provenance, source ordering, and error normalization. |
| `sage.research.policy` | URL, DNS, redirect, response, and content safety. |
| `sage.research.providers.<name>` | One concrete adapter per selected provider. |
| `sage.artifacts.v2` | Admission/research artifact writers with atomic writes and redaction. |
| `sage.integrations.github.status` | Render repository evidence in the existing clarification message. |
| `sage.workflow.github_issue` | Preserve typed human-required routing and handled terminal behavior. |
| `sage.config` | Typed environment configuration and validation. |
| Composite action/workflow templates | Optional provider secret/input and explicit diagnostic allowlist. |
| Documentation and tests | Feature-specific setup, manual testing, security, and failure guidance. |

Repository read tools currently live within role-specific builders. Shared
read-only wrappers should be extracted only once they are used by Admission,
Solver, and/or Reviewer. Mutation and plan guards remain Solver-specific.

---

## 25. Implementation phases

### Phase 1 — Domain contracts and artifacts

1. Extend Admission disposition and result schemas.
2. Add evidence/context schemas and canonical digest calculation.
3. Implement atomic V2 artifact writers.
4. Add validation for decision/clarification/context consistency.
5. Add focused schema, digest, size, and redaction tests.

### Phase 2 — Read-only Admission node

1. Extract reusable repository read tools without changing behavior.
2. Implement `AdmissionContextSession`.
3. Build the Admission-only tool registry.
4. Add Admission instructions and structured terminal handling.
5. Insert deterministic routing before Solver.
6. Verify that no mutation or command tool is reachable before `READY`.

### Phase 3 — Context reuse

1. Validate snapshot SHA, Issue digest, file digests, and artifact digest.
2. Render the bounded context into the Solver prompt.
3. Extend Solver plans with context/evidence provenance.
4. Add observability for reused and newly fetched evidence.
5. Add regression tests showing Solver does not repeat the baseline discovery
   when the snapshot already contains it.

### Phase 4 — GitHub clarification

1. Route Admission human-required decisions through existing outcomes.
2. Extend the existing renderer with repository evidence.
3. Preserve hidden clarification round markers.
4. Verify no branch/PR publication occurs.
5. Add workflow tests for answered Issue comments and a new exact command.

### Phase 5 — Research core and security

1. Add provider-neutral protocols and normalized models.
2. Implement budgets, in-run cache, provenance, and typed errors.
3. Implement URL/DNS/redirect/MIME/size policies.
4. Implement safe text normalization and redaction.
5. Add one concrete web/documentation provider adapter.
6. Add mocked security and adapter contract tests before registering tools.

### Phase 6 — Role-specific research tools

1. Register documentation tools for Admission and Solver.
2. Register general web fallback for Admission and Solver.
3. Register official-documentation-only access for Reviewer.
4. Add prompt rules for source order, version matching, and untrusted content.
5. Add research citations to context, plan, and review inputs.

### Phase 7 — Actions, observability, and documentation

1. Add optional secret/action plumbing for the selected provider.
2. Add explicit safe diagnostic artifacts.
3. Add local and Actions-style logs and LangSmith spans.
4. Update `.env.example`, workflow template, README, and architecture docs.
5. Add a user-friendly feature testing guide.
6. Run deterministic local and offline GitHub publication tests before one
   controlled live canary.

---

## 26. Testing strategy

Normal tests must mock all model, search-provider, network, and GitHub API
boundaries.

### 26.1 Domain and artifact tests

- accept each valid Admission disposition;
- reject `READY` with clarification;
- reject human-required without clarification;
- reject context-digest mismatch;
- produce stable canonical digests;
- reject oversized collections/excerpts;
- reject missing evidence references;
- preserve repository evidence in clarification artifacts; and
- atomically write artifacts outside the candidate repository.

### 26.2 Admission tests

- `READY` routes to Solver;
- human information/design outcomes do not invoke Solver;
- environment and unsupported outcomes halt safely;
- mutation, command, and plan tools are absent;
- context must be saved before admission submission;
- invalid structured output uses only the existing bounded repair path;
- provider failures map to existing typed outcomes; and
- local evidence is preferred before research calls.

### 26.3 Context-reuse tests

- Solver receives the exact validated digest;
- stale base SHA fails safely;
- changed evidence content fails safely;
- raw Admission transcript is not replayed;
- already-captured baseline reads are not repeated in a deterministic fake
  model scenario;
- Solver can fetch a newly discovered file; and
- Reviewer distinguishes base evidence from candidate content.

### 26.4 GitHub clarification tests

- status comment includes questions, reasons, evidence, options, and default;
- Markdown and hidden-marker injection are escaped;
- at most three questions render;
- existing bot-owned status comment is updated rather than duplicated;
- no branch or Pull Request is created;
- a maintainer answer plus new exact command enters a fresh run; and
- the second unresolved round gives rewrite guidance.

### 26.5 Research service tests

- provider responses normalize consistently;
- query/result budgets are enforced;
- identical calls hit the in-run cache;
- timeouts and provider errors are bounded;
- redirects are capped and revalidated;
- loopback, private, link-local, metadata, and rebinding targets are rejected;
- embedded credentials and unsafe schemes are rejected;
- unsupported MIME types and oversized bodies are rejected;
- scripts, forms, hidden content, and control characters are removed;
- secrets are redacted from results, artifacts, logs, and traces;
- arbitrary free-form fetch URLs are rejected;
- official and version-matched sources rank ahead of community/latest sources;
- version mismatch is visible to the model; and
- no real external request is made by the unit suite.

### 26.6 End-to-end deterministic scenarios

1. Complete local Issue -> `READY` -> Solver -> Reviewer pass -> publication
   handoff.
2. Missing acceptance fact -> question comment -> no mutation/publication.
3. Missing design choice -> options/default comment -> no publication.
4. Versioned dependency question -> official docs reused by Solver -> success.
5. Research provider unavailable -> repository-local Issue still succeeds.
6. Required public contract unavailable -> environment-blocked terminal.
7. Malicious web page -> instructions ignored, content sanitized, workflow safe.
8. Admission false-ready -> Solver returns clarification -> measured and safe.

### 26.7 Manual testing guide requirements

The feature testing guide must explain, in user-friendly steps:

- running a `READY` sample with `make v2-first-run`;
- running a sample Issue deliberately missing one fact;
- locating `admission-context.json` and `admission-final.json`;
- verifying that no candidate files changed on a human-required result;
- configuring an optional search provider without printing its key;
- testing documentation lookup with a pinned dependency;
- reading Admission/Research/Solver/Reviewer logs and LangSmith traces;
- running the offline GitHub-like publication harness; and
- interpreting unavailable research, context-integrity, and clarification-round
  failures.

---

## 27. Acceptance criteria

Implementation is complete only when all of the following are true:

- [ ] V2 has three graph nodes but exactly two configured model roles.
- [ ] Admission uses the Solver model and a read-only tool registry.
- [ ] Admission persists a validated context snapshot before routing.
- [ ] The snapshot is bound to base SHA, Issue digest, evidence digests, and its
      own canonical digest.
- [ ] Solver receives and uses the compact snapshot without replaying raw
      Admission history.
- [ ] Solver still creates its own saved plan before mutation.
- [ ] Human-required Admission outcomes contain one to three actionable
      questions.
- [ ] GitHub renders questions, blocking reasons, repository evidence, options,
      and proposed defaults in the existing status comment.
- [ ] Human-required outcomes create neither a branch nor a Pull Request.
- [ ] A new exact command after maintainer answers starts a fresh run.
- [ ] The repository sandbox remains network-disabled and credential-free.
- [ ] Research calls run only through the controller-owned service.
- [ ] Documentation search prefers official, version-matched sources.
- [ ] General web search is a bounded fallback, not the primary evidence source.
- [ ] URL, DNS, redirect, MIME, size, timeout, redaction, and prompt-injection
      protections have deterministic tests.
- [ ] Research tools are role-specific and Reviewer has no general web search by
      default.
- [ ] Research unavailability does not break repository-local tasks.
- [ ] Logs and LangSmith show Admission, research tools, Solver, and Reviewer
      without exposing sensitive content.
- [ ] Full Admission context and raw fetched pages are excluded from default
      GitHub diagnostic uploads.
- [ ] No global model-call ceiling is reintroduced.
- [ ] Existing V2 verification, review, Git, and publication tests still pass.
- [ ] A feature-specific manual testing guide exists.

---

## 28. Rollout and rollback

Rollout order:

1. merge contracts and disabled graph plumbing;
2. enable Admission in deterministic tests and local manual runs;
3. enable repository-only Admission in an offline GitHub-like harness;
4. enable one controlled GitHub canary without research-provider credentials;
5. enable official documentation search for a controlled canary;
6. enable general web fallback only after security and observability checks; and
7. make Admission the V2 default after canaries demonstrate acceptable
   false-ready and false-block rates.

`SAGE_V2_ADMISSION_ENABLED=false` provides a temporary rollback to the current
specification-16 Solver -> Reviewer flow. Research can be disabled separately
with `SAGE_RESEARCH_ENABLED=false`. Neither switch changes V1 behavior.

Rollback must not require deleting artifacts or changing GitHub permissions.

---

## 29. Metrics

Measure at least:

- Admission disposition counts;
- human-question count and clarification rounds;
- percentage of human-required runs later resolved by a maintainer;
- false-ready rate: Solver later discovers a human-owned blocker;
- false-block rate from sampled maintainer overrides;
- Admission repository reads and tokens;
- Solver repeated-read rate for Admission evidence;
- context size and truncation rate;
- documentation/web calls, latency, cache hits, errors, and cost;
- source authority and version-match distribution;
- end-to-end success and draft-PR publication rate; and
- total model/research cost compared with the current two-node baseline.

The primary context-persistence success metric is a material reduction in
Solver's duplicate initial reads, not merely successful artifact creation.

---

## 30. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Admission becomes a Planner bottleneck | Limit it to readiness and evidence; Solver owns the plan. |
| Admission adds cost | Reuse its context, keep a smaller turn bound, cache research, and measure duplicate reads. |
| Admission blocks solvable Issues | Bias to `READY` when tools can resolve uncertainty; track false-blocks. |
| Stale context misleads Solver | Bind to SHA/Issue/file digests and fail on mismatch. |
| Snapshot bloats Solver prompt | Typed excerpts, hard bounds, relevance ordering, and locator-only overflow. |
| Search results are wrong or outdated | Prefer official versioned sources and preserve dates/provenance. |
| Prompt injection in web pages | Controller sanitization, untrusted-data framing, and unchanged tool permissions. |
| SSRF or credential leakage | Search-result IDs, public-network checks, redirect validation, no forwarded credentials. |
| Research provider outage breaks all runs | Optional configuration and graceful unavailable tool results. |
| Questions spam the Issue | Update one bot-owned status comment and cap questions/rounds. |
| Reviewer treats base context as candidate state | Label snapshot as base evidence and provide authoritative candidate separately. |

---

## 31. Deferred choices

The following implementation choices remain provider-pluggable rather than
hardcoded in this architecture:

- the first web search provider;
- whether that provider also offers a dedicated documentation index;
- provider-specific billing metadata; and
- the initial curated official-documentation domain registry.

Selecting a provider must not change the domain contracts, role permissions,
sandbox boundary, URL policy, artifact format, or graph routing described here.

---

## 32. Final target behavior

For a complete Issue, Sage reads the Issue and repository once during
Admission, saves a bounded evidence snapshot, passes it to the tool-driven
Solver, verifies the real workspace, obtains independent Reviewer approval,
and creates the existing draft Pull Request.

For an incomplete Issue, Sage stops before mutation and explains exactly what
the maintainer must answer in the GitHub Actions status response. No branch or
Pull Request is created.

When local code is insufficient, Admission and Solver may consult bounded,
version-aware documentation and public web evidence through controller-owned
tools. The repository sandbox remains offline, credentials remain outside the
workspace, and external text never gains authority over the workflow.
