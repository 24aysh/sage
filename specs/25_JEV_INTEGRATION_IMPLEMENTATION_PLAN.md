# Jev Integration with Sage: Implementation and Evaluation Plan

> Harness refactor update (24 September 2026): Jev's session, candidate policy,
> and HTTP adapter now live in `sage/harness/jev/`; context delivery lives in
> `sage/harness/context/`, and graph memory in `sage/harness/memory/`. A–E modes,
> payloads, thresholds, limits, fallback, timing, and capture behavior are
> preserved. Legion embeddings and Qdrant are removed. References below to
> embedding arms, caches, old module paths or embedding prerequisites are
> historical and superseded by the current architecture/testing guides.
> Future experiments compare graph-only memory and unchanged Jev policies;
> all cost totals cover Solver, Reviewer, and Jev, with no embedding calls.

## Status and scope

- Status: A–E code implemented on `jev`; offline verification is documented in
  the current testing guide. Batch E remains off by default and its disposable
  live canary plus held-out promotion gates are pending. No paid evaluation or
  efficiency claim has been made.
- Prepared: 21 September 2026.
- Inspected baseline: `9bf1c8a`, including the user's uncommitted
  [integration analysis](../jev_sage_integration_analysis.md) and
  [TypeSafe skill](../.agents/skills/typesafe-ai/SKILL.md).
- Current implemented behavior remains documented in
  [architecture.md](../docs/architecture.md) and [testing.md](../docs/testing.md).

This specification was explicitly requested as a new file. It retains the
implementation/evaluation plan, not a replacement architecture or a claim that
Jev improves Sage today. The current guides describe delivered behavior.

Implementation checkpoint (22 September 2026): typed JSON search and shared
source/graph rendering; direct HTTP adapter and lazy run lifecycle; separate
semantic accounting and bounded local captures; shared enrichment budgets;
Score-based excerpts; Choice-based one/two-action controller; offline replay
and explicit paid-run comparison harness. Deterministic selection and the
objective-bearing zero-action control live only in the evaluation harness.
Provisional acceptance thresholds are versioned and still require calibration.
GitHub opt-in plumbing and sanitized navigation diagnostics (E) are implemented.
Reranking, adaptive preparation, pre-review gating and model routing remain
unimplemented. A–E live exit conditions are not marked satisfied by offline tests.

Batch E checkpoint (23 September 2026): the optional TypeSafe secret is scoped
to the trusted GitHub solve step; all bounded policy settings are repository-owned
with mode off and sensitive logs/captures disabled; uploaded navigation evidence
is rebuilt from an operational allowlist. The immutable-SHA live canary is pending.

## 1. Recommended decision

Start with an optional Jev selector for bounded current-source excerpts returned
with `search_text`. Measure it against both today's Sage and deterministic
excerpt selection. Implement only the measurement, provider, and navigation
capabilities required for that experiment. The next two planned experiments
extend that foundation to selecting a complete read-only tool action and then
to executing a bounded two-action exploration sequence. Sections 8.1 and 8.2
define their implementation and scope; neither is enabled by the excerpt pilot.

The opportunity is to eliminate some Solver turns spent requesting evidence.
The useful result is fewer expensive calls, less total billed context, or lower
solve latency while preserving solution quality. A cheap Jev request alone is
not evidence of savings: irrelevant excerpts are replayed in later Solver
requests and can increase both cost and confusion.

Keep the existing Solver, verifier, Reviewer, mutation gates, and candidate
guards. Jev selects evidence or complete read-only actions from candidates
constructed by Python. It does not create paths, commands, edits, acceptance
criteria, or approval decisions.

Recommended sequence:

1. Establish navigation measurements and replayable evaluation cases.
2. Add a narrow TypeSafe provider and shadow evaluation.
3. Compare bounded deterministic versus Jev-selected search excerpts.
4. Enable Jev navigation selectively if the comparison supports it.
5. Implement and evaluate bounded tool-action selection, initially one action.
6. Evaluate a maximum of two dependent read-only actions before Solver handback.
7. Evaluate Legion reranking separately, after the action-selection experiments.
8. Evaluate adaptive memory preparation only if setup measurements justify it.

Pre-review rejection and model routing are conditional research directions,
not committed phases of the first implementation.

## 2. What the current repository actually supports

All implementation paths in this document are relative to
`apps/agent/src/sage/`, unless stated otherwise.

| Finding from current code | Consequence for integration |
| --- | --- |
| `agents/solver.py` binds `parallel_tool_calls=False`; `agents/loop.py` rejects multiple tool calls in one model response. | Search followed by several reads can consume several Solver calls. Enriching one response can reduce those calls without changing the graph contract. This is a structural opportunity, not a measured largest bottleneck. |
| `agents/repository_tools.py` already accepts synchronous read/search enrichment. | Reuse the tool boundary, but add a typed asynchronous navigation hook; an async Jev call cannot be inserted unchanged into the existing synchronous callback. |
| `repository/search.py` returns truncated display text from `rg`; it does not return typed matches. | Introduce structured internal search results before selecting excerpts. Do not recover paths by splitting rendered `path:line:column:text` strings. |
| `Repository.read_file` is synchronous and reads through the validated filesystem boundary. | The analysis's `asyncio.gather(read_file(...), ...)` is not executable as written. The primary benefit is fewer model turns, not concurrent local reads. |
| `legion_memory/context.py::source_snippets` already reads bounded excerpts through an injected source reader. | Extend or extract this helper for reuse; do not create a second subtly different snippet reader. Its existing caller is `get_review_context_tool`, which is outside the five-tool solve profile. |
| Legion defaults to five expansion seeds, eight edge neighbors per seed, and twelve output items; flow/community expansion is bounded separately. Solve startup context defaults to 4,000 characters. | The proposed “20 expanded seeds become four” is not today's default. Reranking may improve which seeds are chosen, but expansion and prompt-size savings must be measured. |
| Structural enrichment already caps additions at 3,000 characters per call, 16,000 per session, and 48,000 per run, with visibility tracking and edit invalidation. | New source excerpts must share the output budget and preserve these invariants. Independent allowances must not silently double context growth. |
| Local `solve` uses no memory; explicit `legion-solve` requests it. Accepted GitHub solves prepare fresh SQLite; embeddings default on, with reusable vector content. | Navigation can benefit both modes. Adaptive memory changes a documented GitHub policy and must respect explicit local memory requests. A fresh graph does not mean every embedding is regenerated. |
| Verification failures already bypass the Reviewer and return to repair. | Jev cannot save a Reviewer call on a path where the Reviewer is already skipped. |
| Repair sessions start fresh. Repeated unchanged failures and the model-call deadline limit progress; there is no fixed repair-count setting. | False Jev rejection can waste the remaining run budget or cause damaging edits. It is not automatically low risk because it cannot approve code. |
| Defaults are `gpt-5.4-mini` and `gemini-3.5-flash`. | Do not assume routing replaces an expensive flagship Solver. Assess actual configured models and costs. |
| `usage.json` records model tokens, latency, and tool names, but tool records omit arguments/results. | Existing artifacts cannot reconstruct exact search-to-read navigation choices. Add bounded evaluation evidence before claiming replay or calls avoided. |
| `ModelCalls` is created inside the orchestrator, after memory preparation. | Its deadline and call accounting do not cover memory setup. Measure whole-workflow time separately; future intake requires a workflow-owned budget. |

The assessment inspected implementation, call sites, and focused tests. It did
not benchmark live solves or establish current production latency percentages.

## 3. What Jev contributes, and its limits

The current model page lists `jev-1.13.0`, priced at $0.042 per million input
tokens with free output. It lists a 64k-token total request budget and a
32k-token budget for state plus the longest question. Pin the version during
evaluation and record the returned model ID. These are vendor-published values,
not Sage measurements. [Model reference](https://docs.typesafe.ai/models)

For example, a request actually billed as 5,000 input tokens costs approximately
$0.00021 at that rate. This arithmetic excludes the downstream cost of exposing
extra text to the Solver. No fixed network latency or speedup is assumed.

Jev accepts shared state and typed questions, with independently evaluated
answers. Use one relevance question per candidate in a small bundle. Put the
candidate reference in the instructions: question IDs themselves are not model
input. Independent answers cannot consume each other's results.
[HTTP API](https://docs.typesafe.ai/api),
[fan-out pattern](https://docs.typesafe.ai/patterns/fan-out)

Use `Score` for graded relevance, with concrete descriptions applied uniformly
to each candidate. `Noul` is appropriate for a genuinely binary condition;
`Choice` is appropriate when choosing one mutually exclusive option. Do not
treat a probability distribution over competing files as independent file
relevance. Confidence describes distribution concentration and must be calibrated
against Sage outcomes. [Score guidance](https://docs.typesafe.ai/primitives/score),
[confidence guidance](https://docs.typesafe.ai/confidence)

TypeSafe documents limitations involving indirect reasoning, numeric precision,
irrelevant long context, adversarial state, and text generation. These strengthen
the case for compact relevance judgments and weaken the case for whole-patch
correctness decisions. Treat repository and Issue content as untrusted evidence;
Jev is not an injection detector that can certify it safe.
[Jev limitations](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

The reranking cookbook demonstrates candidate-generation followed by semantic
selection. Its legal-document results are not a coding-agent benchmark and do
not establish Sage thresholds or expected improvement.
[Reranking cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe)

## 4. Opportunity assessment

| Proposal | Potential benefit | Negative case / condition | Decision |
| --- | --- | --- | --- |
| Search-result source excerpts selected by Jev | Avoid search-to-read Solver calls; useful with memory disabled. | Adds a network hop and potentially irrelevant persistent context. Simple selection may perform equally well. | First controlled experiment. |
| Deterministic excerpt batching | Captures some navigation savings without inference overhead. | Lexical order may choose misleading matches. | Required comparison and possible winning implementation. |
| Bounded tool-action selection | Replace a Solver decision with a choice among complete reads, literal searches, and eligible graph queries. | Missing candidates or an unclear exploration objective can produce useless actions; an extra judgment after a Solver decision saves no call. | Planned next experiment after excerpt selection; section 8.1. |
| Two-action exploration sequence | Gather dependent evidence before the next Solver call. | Serial Jev/tool latency, error propagation, and extra context can exceed the work saved. | Separately evaluate after single-action selection; section 8.2. |
| Jev after every read or graph query without explicit bounds | Broader automation of exploration. | Uncontrolled calls, duplicate evidence, recursive exploration, stale graph ranges. | Excluded; use the explicit objective and bounded controller in sections 8.1–8.2. |
| Legion seed reranking | Better owners, tests, and callers within the existing packet budget. | Network latency may exceed saved SQLite work; a discarded seed can hide useful relationships. | Separate experiment after bounded action selection. |
| Skip memory setup on localized tasks | Avoid graph/vector preparation when it does not help. | Localization is hard to infer from an Issue; skipped graph tools cannot currently be enabled later without new lifecycle work. | Conditional, explicit auto policy later. |
| Jev fast-fail before Gemini | Avoid review of some verification-passing but incomplete candidates. | False rejection triggers costly repair; typed scores lack the actionable findings a Reviewer produces. Most good candidates pay an extra call. | Shadow-only research initially. |
| Solver model routing | Lower model cost or improve escalation decisions. | Current default is already a mini model; errors can increase retries and reduce completion. | Defer pending paired model outcomes. |
| Semantic mutation suspicion | May highlight off-plan changes. | Adds overhead without reliably replacing any authority check. | Not part of the efficiency pilot. |
| Generated tool arguments, Jev approval, or replacement guards | No necessary benefit for the pilot. | Poor model fit or weakened authority boundaries. | Excluded. |

Allowing the Solver to request multiple read tools, or adding a bounded
multi-read tool, is another credible alternative. It would change tool schemas
or the loop's tested sequential contract and requires ordering rules around
writes. Record it as a later comparison if search enrichment leaves many
independent read turns; do not combine that change with the Jev experiment.

## 5. First implementation: search evidence selection

### 5.1 Execution flow

```text
Solver calls search_text
  -> deterministic bounded search and typed matches
  -> existing source response and Legion structural enrichment
  -> check remaining output/time/call budgets
  -> build a small candidate set from current search hits
  -> Jev scores candidate relevance in one request
  -> Python selects zero to a few excerpts
  -> validated current-source reads
  -> append bounded, attributed excerpts to the same ToolMessage
  -> Solver continues
```

The model still emits one tool call, and the graph still returns one matching
tool response. This is an explicitly model-backed navigation capability invoked
by an optional tool hook. The repository search/read functions remain
deterministic and do not call TypeSafe themselves.

This first experiment selects excerpts for a fixed operation. Sections 8.1–8.2
extend the same response boundary to choosing between different operations.
They remain distinct policies for evaluation and never both run on one request.

### 5.2 Candidate construction and request design

Extend the existing search implementation to produce typed matches, truncation
metadata, and the existing human-readable result through one search operation.
Use `rg --json` internally, handling unsupported/non-text paths conservatively.
Keep `Repository.search_text()` and the model-facing arguments compatible.
Avoid a second search just to build Jev input. Retain literal query semantics,
path validation, exclusions, timeout, and output bounds.

Group nearby hits into source windows, deduplicate overlapping windows, and
diversify by path. Candidates include identity, relative path, match line,
matched text, and proposed line range. Python chooses ranges; Jev cannot widen
them or invent candidates. Initial candidates come from live search, avoiding a
new dependency on Legion availability or freshness.

Request state contains the Issue, query and scope, compact saved-plan fields
when available, candidate evidence, and already-visible excerpt identities.
Exploration before `save_plan` must work. Read the current plan version on each
request. If relevant intent cannot fit within the request budget, skip the
optimization rather than silently treating a clipped Issue as complete.

One `Score` per candidate asks how useful inspecting that candidate is for the
specified Issue and search. Suggested rubric, to validate on Sage cases:

1. The match shares words but supplies no apparent evidence about the behavior.
2. The match provides supporting context about the behavior or its dependencies.
3. The match identifies implementation, a contract, or a test directly involved
   in the behavior.

Describe each level fully; do not use unexplained numbers as criteria. The
policy can select no candidates when none appear useful. Tune selection from
held-out data using scores and their distributions; do not set a universal
confidence cutoff such as 0.8.

Do not add speculative questions about security, task complexity, readiness,
or whether the Solver needs to reason. They do not change this bounded read
operation and would add cost without a defined consumer.

### 5.3 Proposed initial bounds

These are conservative experimental limits, not measured optimum values:

| Resource | Initial policy |
| --- | --- |
| Eligible shortlist | At most eight deduplicated windows; skip Jev for zero or one eligible window and return ordinary search output. |
| Source exposure | At most two selected windows, approximately forty lines each. |
| Added excerpts | At most 3,000 characters, additionally bounded by remaining tool capacity after ordinary output and Legion enrichment. |
| Combined optional enrichment | Share the existing 3,000-per-call, 16,000-per-session, and 48,000-per-run limits with structural enrichment. |
| Jev request size | At most 16,000 UTF-8 bytes across serialized state/questions, with a generous margin below vendor token limits. Character/byte caps are not reported as billed tokens. |
| Jev requests | At most four per Solver session and eight per solve. |
| Time | Two-second total deadline per decision, eight seconds total Jev wait per solve, and never beyond the remaining Solver budget minus finalization reserve. |
| Transient failure | No inline retries; return the original result. Disable further requests after two consecutive transient failures. |
| Authentication/configuration error | Disable the capability for the run and record a redacted category. Never retry invalid requests. |

If the deadline is too short for reliable service responses, measure that
before increasing it. A consistently unavailable optimization should be
disabled, not quietly turned into a long retry stage.

Source preservation takes priority: do not truncate original search results to
make room for excerpts. If there is insufficient room for an attributed useful
window, do not call Jev. Keep file path, actual line range, and truncation markers
attached to every excerpt; truncate at complete line boundaries where possible.

### 5.4 Reads, visibility, and fallback

Use the current `Repository.read_file` boundary. Initially read the two selected
windows sequentially; local file reads are synchronous and cheap relative to
the model round trip. If profiling later justifies workers, use bounded worker
execution for independent file reads only. Do not move SQLite sessions or
mutable visibility accounting across threads casually.

Extract the existing snippet helper into a repository-owned module if its
second caller requires shared range/rendering behavior. Adapt its old caller
in the same change and retain its public response contract.

Track what was actually shown, not merely fetched or scored. Deduplication is
per Solver transcript, since repair begins with a fresh history. Identify
visible source by path, range, and content digest. Repeated reads of changed
content must remain possible. Invalidate affected records after writes, moves,
deletes, branch changes, and command execution that may change files; a branch
switch clears all records. Current file content, not a base SHA alone, defines
freshness in a mutable checkout.

For the first version, avoid cross-request result caching beyond visibility
deduplication. A later cache would also need Issue, query, plan version, model,
question version, and candidate content in its key.

Jev timeout, service failure, invalid response, uncertainty, budget exhaustion,
or unavailable selected file leaves the ordinary tool result usable. Validate
returned candidate IDs and finite distributions before applying any selection.
Unexpected programming errors should remain diagnosable; do not blanket-catch
all exceptions. Cancellation must propagate and close the provider session.

Never recurse into another enriched tool to fetch excerpts. No background
prefetch continues after the tool result or overlaps Solver mutations.

## 6. Architecture and dependency ownership

Use a narrow provider-neutral evidence-ranking interface rather than a generic
System One framework supporting every primitive from day one. Its input is the
bounded task/search/candidate packet; output is typed scores plus provider usage
and provenance. The action-selection milestone adds a separate typed
`choose_action` interface to the same adapter for the concrete second use case
in section 8.1. Do not emulate action choice by misinterpreting relevance scores.

| Owner | Proposed work |
| --- | --- |
| `domain/navigation.py` (new) | Immutable candidate/result contracts and narrow async ranking/enrichment protocols; standard library and Pydantic only. |
| `providers/typesafe.py` (new) | Request construction, TypeSafe transport, response validation, error normalization, and version/usage extraction. |
| `repository/search.py`, `repository/service.py` | Structured internal search results while preserving the existing public text API; bounded internal search timeout override for the action milestones. |
| `repository/snippets.py` (new, extracted) | Shared bounded source-window reading/rendering from the existing helper; no model calls. |
| `legion_memory/context.py`, `service.py` | Delegate the existing snippet use to the shared implementation; preserve its behavior. |
| `orchestration/navigation.py` (new) | Run/session selection policy, eligibility, deadlines, visibility, and optional enrichment budget accounting. |
| `agents/repository_tools.py`, `solver.py`, `prompts.py` | Thin async hook, current plan access, visibility reset and invalidation notifications; optional objective-bearing schemas and prompt guidance for the action milestones. |
| `orchestration/context.py`, `solve.py` | Supply the run-scoped capability through typed interfaces. |
| `config.py`, `composition.py`, `workflows/solve.py` | Typed opt-in settings, concrete construction, one client lifetime per solve and cleanup. |
| `domain/usage.py`, `providers/calls.py`, `artifacts/store.py` | Separate semantic-call accounting, bounded evidence, atomic persistence, and remaining-deadline access. |

Agents must not import `orchestration/navigation.py`; inject its implementation
through the domain protocol. Legion must not import a concrete provider.
`composition.py` remains the concrete construction owner. Use ordinary objects
with explicit run scope, not globals, runtime factories, or a new agent role.

Prefer a small direct HTTP adapter over Sage's already-resolved `httpx`, declared
as a direct dependency when used. The pilot needs one endpoint, Pydantic
validation, a persistent async client, and no retries; this avoids importing an
entire SDK to expose one ranking method. The tradeoff is owning status/error
mapping, covered with HTTP contract fixtures. Do not upgrade unrelated packages.

The official SDK is a valid alternative if implementation reveals significant
contract complexity. Its async API supports explicit timeout and disabling
retries; do not inherit its retry policy invisibly. It also warns that body
logging is not redacted. The original plan kept bodies out of normal logs.
The subsequent user-requested local logging extension enables complete bounded
input logs at INFO in navigation mode `on`, with `SAGE_JEV_LOG_INPUT=false` as
the privacy opt-out; shadow remains summary-only. See the current testing guide
for timing/usage fields and sensitive-log handling. Artifact capture remains a
separate opt-in. [Async SDK reference](https://docs.typesafe.ai/sdk/python/api/clients/async),
[retry configuration](https://docs.typesafe.ai/sdk/python/api/retries)

The current source tree contains 100 Python modules and architecture tests cap
it at 106, with additional source-size and import-fan-out constraints. The four
new modules above fit the module-count budget; assess the other limits during
implementation. Do not scaffold deferred features or relax architectural tests
to accommodate unnecessary abstractions.

### Configuration and compatibility

Proposed configuration is `SAGE_JEV_NAVIGATION_MODE=off|shadow|on` (default
`off`), `TYPESAFE_API_KEY`, `SAGE_JEV_MODEL=jev-1.13.0`, plus bounded request/run
time and call budgets. Keep experimental ranking thresholds in a versioned
policy until evaluation selects them. A deterministic selector is required in
the evaluation harness; it does not need to become another runtime architecture.

Mode `off` constructs no client, requires no credential, sends no data, and
preserves tool schemas and behavior. The excerpt policy also preserves existing
tool schemas. The action policy has an explicit additive objective argument,
as described in section 8.1. Explicitly enabled mode validates its
credential at configuration time. A runtime vendor outage falls back to ordinary
navigation and records why. Document the additional destination for bounded
Issue/source content when users opt in.

For GitHub enablement, extend the existing trusted Action secret plumbing,
configuration documentation, tests, and allowlisted diagnostics. Never expose
TypeSafe credentials to repository code or the sandbox. Do not change existing
memory defaults or publication behavior as part of navigation rollout.

## 7. Measurement must distinguish selection from real savings

### 7.1 Evidence and accounting

Reuse `RunArtifacts` atomic writers. Extend `RunProvenance` with a default-empty
semantic-call collection instead of labeling Jev as a Solver or Reviewer.
Keep current model call numbers, tool links, session counts, and review cycles
unchanged. Account for every attempted Jev request, including failures; unknown
usage remains unknown rather than being counted as free.

Record model and policy version, stage, session and parent tool-call identity,
latency, actual input/output usage, candidate count, selected IDs, result status,
and fallback reason. Internal excerpt reads are navigation operations, not
model-requested tool calls. Link them to the parent search for analysis.

Add new provenance fields with defaults and update all serializers/readers,
including GitHub diagnostics, so older run artifacts remain readable. Do not
change existing Solver/Reviewer cost totals silently; report the additional
semantic usage explicitly and include it in experiment-wide totals.

A bounded local `navigation.json` can retain candidate locators, scores,
selection/exposure records, subsequent explicit reads, and evidence digests.
Exact state/questions require an explicitly enabled local evaluation capture,
with byte/count caps. Hashes alone cannot reconstruct mutable source evidence.
Do not upload raw request state or source excerpts in GitHub diagnostics; export
only a deliberate sanitized summary.

Measure workflow wall time from resource preparation through finalization,
separately from Solver, review, memory, Jev, and verification durations. This
avoids misreading the existing post-memory `ModelCalls` deadline as total time.

### 7.2 Evaluation design

Build cases from fixed Issue/base-SHA pairs, covering explicit paths, ambiguous
symbols, implementation/test discovery, misleading matches, long files,
cross-file changes, repair sessions, and memory on/off. Include unfavorable
cases, not only Issues whose next read is easy to predict.

Use two levels of evaluation:

1. Offline deterministic tests and replay: validate bounds, fallbacks, ranking
   behavior, and candidate coverage. Historical next-read matches are a useful
   proxy, not ground truth for relevance or calls saved. Human-adjudicated
   excerpts and known fix/test locations supplement that proxy without being
   supplied to the selector at runtime.
2. Paired live solves on the same Issue/base with fixed model/settings and
   comparable vector-cache state. Compare current Sage, deterministic excerpt
   selection, and Jev excerpt selection. Repeat enough pairs to assess variance;
   report sample counts and intervals, including inconclusive results.

Tune policy on one set and evaluate on held-out Issues, preferably also held-out
repositories. Do not derive evidence from the eventual patch or review that
would not have existed at selection time.

Prefer replay-first shadow evaluation. Runtime shadow calls, when needed, use
the same bounded request path but expose no selected excerpts to the Solver.
They still consume time and money; their wall time is not the unmodified
baseline. Avoid introducing a background request queue just for shadow mode.

### 7.3 Metrics and promotion rule

Report all of the following per arm:

- Verified completion rate and independently checked solution correctness;
  passing the existing Reviewer is useful evidence, not an infallible label.
- Total cost across all attempted runs divided by verified completions, along
  with costs per attempt and by outcome. Include failures and wasted repairs.
- Solver calls and input/output/cached tokens, Reviewer and embedding usage,
  Jev usage, and dated price assumptions. Token count and cost are distinct.
- Whole-solve median/tail latency, setup time, and Jev critical-path overhead.
- Excerpt exposure, redundant subsequent reads, omitted relevant candidates,
  fallbacks, turn-limit failures, and additional repair sessions.

The relevant cost relationship is:

```text
net cost saved = avoided Solver/Reviewer/embedding spend
                - Jev spend
                - extra downstream context and repair spend

net time saved = avoided critical-path work
                - Jev wait, excerpt work, and induced extra work
```

Use actual aggregate provider usage for the live comparison, including prompt
cache effects. Do not claim one selected excerpt equals one eliminated call.

Promote when paired evidence supports a repeatable improvement in at least one
of token usage, total cost, or latency without an unacceptable correctness or
completion regression. Small gains count; no arbitrary minimum percentage is
required. If one efficiency metric improves while another worsens, document
the tradeoff and restrict enablement to the benefiting workload rather than
calling the change universally beneficial. Predeclare evaluation tolerances;
a small pilot cannot prove the absence of rare regressions.

If deterministic selection matches Jev, ship the simpler selection policy for
that workload and reserve Jev for demonstrated ambiguous cases. If neither
beats today's Sage, keep navigation off.

## 8. Follow-on experiments and their decision gates

### 8.1 Bounded tool-action selection

#### Objective and point of interception

Let Jev choose the next useful read-only operation from a closed set of fully
formed actions, before paying for another Solver decision. This is broader than
ranking excerpts and works without Legion; graph actions are optional additions.
It does not replace the Solver's planning, editing, diagnosis, or finalization.

Run selection inside the optional navigation hook after a successful
Solver-requested `search_text` or `read_file`, before returning its ToolMessage.
The model-call sequence becomes:

```text
Solver chooses the initial read/search and states an exploration objective
  -> Python executes that requested operation
  -> Python constructs valid follow-up actions from observed evidence
  -> Jev selects one action or RETURN_TO_SOLVER
  -> Python revalidates and executes the selected action
  -> original result plus attributed follow-up evidence reaches the Solver
  -> next Solver call
```

Do not invoke the Solver to generate the shortlist first, or invoke Jev merely
to approve an action the Solver already selected. Neither substitutes for the
navigation decision whose cost this experiment aims to remove. Actual avoided
calls still require paired-run measurement; internal actions are not presumed
to replace Solver calls one for one.

#### Explicit scope

Initially allow only these complete action types:

| Action | How Python constructs it | Conditions and bounds |
| --- | --- | --- |
| `read_file` | A current search hit, validated explicit Issue/plan locator, or an eligible graph locator supplies the path and proposed range. Known implementation and test paths use the same action type. | Existing repository path validation; one window of at most forty lines; current text file; graph ranges on invalidated paths are ineligible. |
| `search_text` | An exact identifier from the objective's supplied evidence, current source, or a typed graph result supplies the query. Use existing bounded identifier extraction where it fits. | Literal query only, at most 200 characters and five returned hits; preserve the originating search scope, or the originating read's directory. Jev cannot broaden scope or rewrite the query. |
| `query_graph_tool` | A known qualified symbol supplies the target; Python instantiates `callers_of`, `callees_of`, or `tests_for` as separate candidate actions. | At most five results; only an already-enabled, validated memory session and supported solve-profile queries. No hidden graph build, embedding call, or target-resolution search. |
| `RETURN_TO_SOLVER` | Always present, with no repository operation. | Normal continuation, not failure or a solve outcome. |

Exclude mutation, branch switching, shell/verification commands, network access,
plan changes, candidate approval, arbitrary tool names/arguments, and unrestricted
graph traversal. Exclude embedding-backed semantic search from this experiment
because its network cost and lifecycle need a separate budget. Direct triggering
by `list_tree`, graph-tool, edit, or command results is also out of scope
initially. A later explicit read/search may start a new sequence with a fresh
objective and freshness checks, including during repair. Internal graph queries
may still be selected as listed above.

An allowed action executes an existing deterministic capability. Use a typed
dispatch table with a fixed allowlist, not `getattr` on a model-provided name or
invocation of the full model-callable tool registry. A read-only search running
through the existing sandbox boundary does not authorize an arbitrary command.

#### Exploration objective and freshness

For the action policy only, add an optional bounded `exploration_goal` argument
to the Solver's `search_text` and `read_file` adapters. It describes the evidence
the Solver wants, for example, “find this timeout's owner and its regression
test.” Limit it to 600 characters and require nonblank text when supplied.
It accompanies a call the Solver was already making; it needs no separate turn.
The deterministic repository APIs do not consume this argument.

Omitting the objective executes the ordinary tool without Jev action selection.
Do not infer a new goal with another model call or persist an old objective into
unrelated reads. The goal expires when that root tool response is returned.
The Issue and current saved plan remain authoritative; a goal never grants
mutation or broadens the task. Exploration before a plan exists is supported
using the Issue, explicit goal, and current evidence.

Bind request state to run/session identity, root tool-call ID, accepted SHA,
current plan version/digest when present, and source evidence digests. Reuse the
existing visibility/invalidation rules. Graph facts retain accepted-base
provenance and cannot establish that the current edited source matches them.
Revalidate eligible memory and affected paths immediately before dispatch; if
identity or freshness changes, return to the Solver without executing the
stale action. Do not retain an action queue across sessions or mutations.

#### Candidate and decision contracts

Extend `domain/navigation.py` with immutable, discriminated action arguments
for the three allowed operation types, an `ActionCandidate`, and an
`ActionSelection` result. Proposed fields include:

- Candidate: run-local ID, action type and complete typed arguments, source
  evidence references, expected evidence category, and a deterministic argument
  fingerprint. Policy bounds are fields validated by Python, not model estimates.
- Selection input: Issue, optional current plan context, root objective/query,
  bounded current observations, already-visible evidence, candidates, and the
  decision-policy version.
- Selection output: selected candidate ID or handback, probability distribution,
  confidence, and provider usage/provenance. No free-form executable arguments.

Construct at most six executable candidates, plus handback. Deduplicate
equivalent arguments and suppress already-visible unchanged evidence. Prefer
explicit matches, then other grounded candidates; reserve representation for
different available operation types instead of filling every slot with adjacent
lines from one file. Use stable ordering for replay. Candidate construction
must not call a model or perform exploratory searches/graph queries just to
populate the shortlist. Cheap path existence/readiness checks are permitted.

The request uses one `Choice` over complete candidate actions plus handback.
Each option describes the action and points to its evidence; IDs alone do not
explain the option. The question asks which available operation best advances
the explicit objective, with handback for missing evidence, ambiguity, or a
need for Solver reasoning. Complete actions avoid incompatible combinations of
independently selected tool names and parameters. This is a Sage-specific
application of TypeSafe's closed-set function selection.
[Function-calling cookbook](https://docs.typesafe.ai/cookbooks/function_calling),
[Choice reference](https://docs.typesafe.ai/primitives/choice)

Validate answer type, option membership, finite normalized probabilities, and
confidence. Use an action-specific acceptance policy calibrated on held-out
cases; do not copy excerpt-ranking thresholds. Low confidence, a rejected
selection, or explicit handback returns control without choosing a different
action behind the model's back. No eligible candidate means no Jev call. One
eligible candidate may still need a choice against handback; its existence is
not evidence that executing it helps.

#### Execution, transcript, and ownership

Start with at most one selected follow-up operation per root tool call. Extend
`orchestration/navigation.py` with candidate construction, selection policy, and
typed dispatch, reusing the provider/client, source-window helper, and budgets
already introduced. `providers/typesafe.py` implements `choose_action`; domain
protocols keep agents independent of the orchestration implementation.

`agents/repository_tools.py` captures the objective and typed root result and
awaits the injected capability. `agents/solver.py` binds the optional schemas
and supplies current plan/session state. Update `agents/prompts.py` to explain
the objective, read-only continuation, and reuse of supplied evidence. The
Solver must still inspect that evidence and reason about edits itself.

Reuse validated graph service operations and `MemorySession` response filtering.
Record graph exposure even when the call is internal, but label it as a
navigation operation rather than a model-requested graph tool. Extract any
shared filtering/rendering code to its current capability owner if necessary;
do not bypass provenance, deduplication, or stale-range suppression simply
because dispatch no longer passes through a model-tool wrapper.

Append deterministic action headers and bounded verbatim results to the
original ToolMessage. Headers identify operation, arguments/locators, provenance,
and truncation. There is still exactly one response to the Solver's original
tool-call ID. Do not synthesize an AI tool-call message, orphan ToolMessages, or
fake Solver turns. `agents/loop.py`, its graph edges, and its model-turn limit
remain unchanged. Internal actions have their own accounting limits.

Reuse the aggregate enrichment limits from section 5.3; graph results, source
windows, and search hits all consume that same allowance. Reserve enough output
space for an attributed result before selecting an action. If source/structural
output leaves insufficient room, skip the decision. Never remove original
evidence to fit follow-up output or use Jev to summarize it.

#### Configuration, implementation order, and promotion

Add `SAGE_JEV_NAVIGATION_POLICY=excerpts|actions`, defaulting to `excerpts`, and
`SAGE_JEV_MAX_FOLLOWUP_ACTIONS=1|2`, defaulting to `1` for the action policy.
Existing `off|shadow|on` controls whether the selected policy is called/applied.
An enabled root operation uses exactly one policy; excerpt ranking and action
selection must not run consecutively and spend two independent budgets.

When mode is off, expose today's tool schemas regardless of configured policy.
When the action policy is on or shadowed, expose the same optional objective
argument to support meaningful comparisons. Explicitly record these schema and
prompt versions; their extra tokens and changed Solver behavior are part of
the experiment. Keep defaults off until promotion.

Implementation steps:

1. Add typed candidates, deterministic builders, and dispatch tests using current
   repository/search/snippet capabilities and optional graph fixtures.
2. Add provider action choice and strict response validation, reusing transport
   and accounting; test handback independently of provider errors.
3. Add objective-bearing adapter schemas and the injected single-action hook;
   verify message pairing, disabled compatibility, and no changes to authority.
4. Capture shadow candidates/choices without dispatch; replay against adjudicated
   useful actions and actual subsequent Solver reads.
5. Run controlled single-action solves against the excerpt pilot, a deterministic
   action policy, and ordinary Sage. Promote only on end-to-end benefit with
   acceptable solution quality, following section 7.

Evaluate candidate coverage separately from selection accuracy. When the useful
action is absent, record a candidate-generation miss; do not count it solely as
a Jev classification error. Measure handback precision, unnecessary actions,
Solver calls, extra schema/context tokens, total cost, and latency. Include a
control with the same objective-bearing schemas/prompts but no action dispatch
to isolate their effect from the selector's effect.

### 8.2 Short read-only exploration sequences

#### Objective and scope

Extend the single-action controller to at most two selected operations before
the next Solver call. The second action may depend on evidence from the first:

```text
Solver search with explicit objective
  -> Jev selects implementation read
  -> Python reads current source and rebuilds the candidate set
  -> Jev selects a known test read or a literal identifier search
  -> Python executes that operation
  -> combined attributed evidence returns to the Solver
```

This is a bounded loop inside the root read/search capability. It introduces no
new agent, runtime, persistent cross-run state, graph node, or autonomous editing
loop. Allowed actions and authority remain exactly those in section 8.1.

The second request must wait for the first operation's result. It cannot be
speculatively answered in the first request because its evidence and candidates
do not exist yet. Independent excerpt reads in section 5 are a different
experiment. [Fan-out pattern](https://docs.typesafe.ai/patterns/fan-out)

#### Controller state and algorithm

Keep a root-call-local state object containing the objective, root evidence,
successful bounded observations, attempted action fingerprints, decision and
action counts, output consumption, current deadline, and stop reason. Reuse
run/session-wide counters and visible-source tracking; do not duplicate those
budgets in each loop iteration.

For each of at most two iterations:

1. Check freshness, remaining call/time/output budgets, and availability of useful
   candidate actions. Stop before inference when a deterministic check fails.
2. Build a fresh bounded shortlist from the root evidence and evidence already
   obtained in this sequence; never carry an unselected action blindly forward.
3. Ask Jev to choose one action or return control. Include previous operations
   and observations within the same request cap. Preserve root intent; if it
   cannot fit with the new evidence, stop rather than clip away the objective.
4. Validate the selection and recheck dispatch preconditions. Reserve the action
   slot before execution, including attempts that later fail.
5. Execute the existing deterministic capability, bound its result, and append
   an attributed observation. Use only retained evidence to construct the next
   shortlist. If there is no new retained evidence, return control immediately.
6. After the second operation, return without a third “are we done?” Jev call.

Internal dispatch bypasses enrichment hooks, so a selected `search_text` cannot
recursively start another sequence. Root objectives never renew themselves.
Both successful observations are returned together; mark them visible only when
the root tool response is assembled. Attempted-action records prevent repeated
work within the sequence before it becomes visible to the Solver.

#### Bounds and stopping conditions

| Resource | Initial action-policy limit |
| --- | --- |
| Selected operations per root call | One for the single-action experiment; two for this experiment. The originating Solver tool call is additional and is recorded separately. |
| Jev decisions per root call | At most the selected operation limit, including handback/error decisions. |
| Request size and shortlist | The existing 16,000-byte serialized request cap; six executable candidates plus handback per decision. |
| Run/session Jev budget | Share the section 5.3 limits of four requests per session, eight per run, and eight seconds total Jev wait per run; no reset for a new sequence. |
| Follow-up action budget | At most four attempted internal operations per session and eight per run; failures count. |
| Added evidence | The same combined 3,000-character per-root allowance and 16,000/48,000 session/run caps, including structural context and headers. Two actions do not double it. |
| Decision deadline | At most two seconds per request, shortened by remaining run/sequence time. |
| Sequence elapsed budget | At most eight seconds of additional scheduled work after the originating tool finishes, further limited by finalization reserve. |
| Internal search execution | At most two seconds per search, shortened to the remaining sequence budget; use the sandbox's real command timeout. |

Add a bounded per-operation timeout override to the repository search capability
without changing defaults for ordinary Solver searches. Never wrap a blocking
synchronous search in `asyncio.wait_for` and claim it is interruptible. Enforce
bounded output and admission checks for local reads/graph queries; skip oversized
source files (initially above 1 MiB) and use existing bounded graph queries.
Synchronous filesystem operations are not hard-preemptible, so the elapsed
budget is a scheduling guard with checks before/after those operations, not a
guaranteed wall-clock timeout. Any future worker implementation must finish or
cancel safely before returning control to mutations.

Stop and return the accumulated evidence when Jev chooses handback, confidence
is insufficient, the response is invalid, a provider/capability fails, the
shortlist is empty, a budget is exhausted, evidence is stale, or there is no
novel evidence. Do not retry or substitute a second-choice action after failure.
Also stop on an already-attempted normalized action or previously seen result
digest. This catches repeats; the hard two-operation cap bounds longer cycles.

On failure of the second operation, preserve the first successful observation
and the original root response, with a bounded factual failure marker. The
optimization failure does not change the successful root tool into an error.
If the root tool itself fails, follow existing tool-error behavior and do not
start Jev. Cancellation propagates and stops further scheduling. Expected
provider/repository failures have typed fallback; programming defects remain
visible rather than being silently suppressed.

Handback means only “the Solver decides next.” It cannot mark exploration
complete, satisfy a plan criterion, skip a mandatory check, or finalize a solve.

#### Implementation, evidence, and evaluation

Extend the existing action controller with an iterative loop parameterized by
the validated `max_followup_actions` value. Keep the one-action behavior as the
same implementation with a smaller bound, not a second controller. No additional
production dependency or generic planner framework is needed.

Extend `navigation.json` with sequence ID, root tool-call ID, objective/policy
digest, step number, candidate-set identity, selected action, typed execution
status, result digest, exposed characters, elapsed time, and stop reason. Record
actual Jev requests and internal operations separately from Solver turns and
model-requested tool calls. Normal diagnostics omit raw objectives/source;
opt-in local replay capture follows section 7's existing limits.

Ordinary shadow mode can observe only the first selection, since it does not
execute the action that would create the second step's evidence. Evaluate full
sequences either with explicitly captured replay fixtures or controlled isolated
live solves. Do not present a guessed second-step shadow choice as measured
behavior, and do not make shadow mode secretly execute extra operations.

Deliver and verify in order:

1. Add the two-iteration state and budget handling to the tested single-action
   controller; use fake provider choices and real temporary repository reads.
2. Test search-to-read, read-to-search, graph-to-read when memory is eligible,
   handback after step one, failure after step one, and second-step candidates
   that depend on the first result.
3. Verify repeat suppression, shared run caps, finalization reserve, output
   accounting, cancellation, and the absence of a third decision or recursive
   hook invocation. Retain the unchanged one-message graph contract.
4. Compare zero, one, and two internal actions with the same candidate policy,
   objective-bearing schemas, models, and aggregate output budgets. This
   isolates the value of an additional dependent step.
5. Promote the two-action setting separately from the single-action setting.
   If its extra latency or context outweighs the calls saved, retain one action
   even if tool selection itself is beneficial.

Report sequence completion/handback reasons, useful evidence gained per step,
second-step mistakes, redundant Solver reads, total Solver calls, actual cost,
and end-to-end latency. A sequence that executes two valid tools but changes no
subsequent Solver work is not evidence of an efficiency improvement.

### 8.3 Legion reranking

Proceed if baseline navigation evidence shows useful locations are generated
but poorly ordered. Split retrieval into candidate collection, optional seed
selection, and the existing bounded expansion/rendering, preserving current
lexical/vector fusion and fallback behavior.

Start with at most sixteen eligible candidates and retain the existing
five-seed budget. Preserve explicit Issue anchors, stable fallback ordering,
and diversity across useful owners/tests/callers. Uncertainty must not turn a
useful retrieval into `no_match`, since that also disables graph tools.

Keep Jev scores separate from lexical/RRF scores; these are different scales.
Apply the selected seed ordering consistently to final selection so a later
lexical sort does not silently undo it. Graph relationships still determine
expansion; Jev does not invent or certify edges.

The service and retrieval APIs are synchronous today. Stage the asynchronous
ranking through a typed boundary outside store operations, then revalidate
repository identity and indexed SHA before applying the result. Do not call
`asyncio.run` inside a live event loop or hold a write transaction during a
network request. Standalone memory commands retain deterministic behavior unless
explicitly opted into the experiment.

Measure relevant-owner/test recall and Solver discovery turns first. Existing
caps mean fewer graph operations or fewer prompt characters are not guaranteed.
No SQLite schema migration is needed merely to reorder run-scoped candidates.

### 8.4 Adaptive memory preparation

Proceed only if whole-workflow measurements show memory preparation matters.
Compare deterministic explicit-path routing before adding Jev intake.

Introduce an explicit `auto` policy; keep current GitHub behavior as the default
and honor explicit local `--memory-file` intent. Use only information available
before the build: Issue, validated paths, and a bounded cheap repository
inventory. Building the graph to decide whether to build it defeats the goal.

Consider lexical-only preparation versus full vector preparation before
complete omission. This may retain useful graph tools at lower startup cost.
It is not a guaranteed win when vector reuse is already effective.

Skipping changes resource construction as well as `_prepare_memory`: GitHub
currently constructs the memory service before calling the solve workflow.
Defer construction where necessary, and distinguish a deliberate skip from
`unavailable` in evidence. Do not silently change credential requirements for
existing enabled modes.

A skip cannot currently recover the five graph tools midway through a session.
Before broad rollout, either implement a bounded workflow-owned escalation that
builds the accepted-base graph and starts a fresh correctly bound session, or
keep skipping restricted to evaluated experiments. Never reuse edited files as
if they were the accepted-base snapshot.

Uncertain classification or Jev failure selects the existing full-memory path.
Use a workflow budget for this decision; the existing `ModelCalls` clock starts
too late to govern it.

### 8.5 Pre-review judgments

Do not enable automatic fast-fail in the initial integration. Run a separate
shadow study only on candidates that passed deterministic verification.

The desirable condition is that saved Gemini work exceeds the added Jev work
and false-repair cost. High-confidence incomplete-code detection does not prove
that condition. Broad questions such as “is the implementation correct?” require
reasoning and evidence that a diff may not contain; unchanged code can already
satisfy an acceptance criterion.

If later evidence supports a narrow check, require actionable, source-backed
findings assembled from known criterion IDs and evidence spans. Do not fabricate
a `ReviewResult` from probabilities. Bind any judgment to the plan and candidate
digests. Limit additional Jev-triggered repair, retain independent review of
every eventual pass, and send uncertainty to the normal Reviewer. Evaluate
false-rejection damage, not merely whether unsafe approval is impossible.

### 8.6 Model routing

Evaluate only with paired outcomes for the actual configured Solver models.
Issue complexity alone is an unreliable proxy. Record the model actually used
in call provenance and traces; current accounting assumes the configured
OpenAI Solver identity. Escalate only at a fresh session boundary with preserved
plan/candidate evidence and explicit total budgets. Do not switch models on
every turn or reintroduce an Admission agent.

## 9. Delivery sequence

These are logical implementation batches, not authorization to commit.

| Batch | Deliverable | Exit condition |
| --- | --- | --- |
| A: Evidence and deterministic baseline | Typed search results, shared snippet extraction, bounded navigation capture, paired evaluation format. | Existing search/source behavior preserved; candidate coverage measurable; no paid dependency in offline tests. |
| B: TypeSafe shadow capability | Narrow adapter/contracts, opt-in config, time/call budgets, usage accounting, lifecycle cleanup. | Mocked API contract and fallback tests pass; pinned live pilot reports actual cost and latency. |
| C: Selective search enrichment | Optional async tool hook, shared output budget, visible-source deduplication, invalidation, deterministic comparison. | Held-out paired solves demonstrate a worthwhile result; otherwise remain off. |
| D1: Single-action selection | Typed complete-action candidates, optional explicit objective, Choice-based provider method, one-action dispatch, and shadow capture (section 8.1). | Candidate coverage and selection evaluated separately; no authority or transcript regression; paired solves establish benefit beyond excerpt selection. |
| D2: Two-action exploration | Extend the same controller to two dependent operations with shared budgets and attributed evidence (section 8.2). | Zero/one/two-action comparison supports the additional step; repeat, timeout, cancellation, and partial-failure tests pass. |
| E: GitHub opt-in | Trusted secret plumbing, sanitized diagnostics, updated architecture/testing guides for the policies actually promoted. | Action tests and local publication smoke pass; disposable live canary verifies delivered behavior. Excerpts may ship independently of D1/D2. |
| F: Conditional follow-ons | Separate Legion reranking or adaptive-memory experiment after the action-selection evaluations. | Each has its own measurements and promotion decision; no automatic rollout based on navigation results. |

Do not implement all opportunity-table entries together. That would obscure
which change improves Sage and enlarge the failure surface before benefits are
known.

## 10. Verification and user-facing testing guide

Extend tests by existing ownership, using shared Legion fixtures from
`tests/legion_memory/conftest.py` where needed.

Required focused coverage:

- Repository search: literal matching, paths containing colons/spaces, Unicode,
  malformed or truncated structured output, exclusions, no-match and timeout;
  compatible human-readable output and validated excerpt boundaries.
- Provider: valid results and usage; missing/unknown IDs, non-finite or invalid
  probabilities, malformed bodies, authentication/validation errors, 429/529,
  connection failure, timeout, cancellation, and client cleanup.
- Navigation: zero/one candidates, no useful selection, duplicate windows,
  exhausted budgets, source preservation, partial read failure, no recursive
  enrichment, and no extra provider calls when disabled.
- Agent/session: one ToolMessage for one search; unchanged loop routing; pre-plan
  exploration; current plan revisions; repair visibility reset; invalidation
  after edits, moves, deletion, commands, and branch changes.
- Action selection: complete candidate arguments, exact allowed operation set,
  objective omission/expiry, prompt and schema compatibility in off mode,
  explicit handback, unknown IDs, action-specific confidence policy, candidate
  coverage, no model-based candidate construction, and no synthetic tool calls.
- Exploration sequences: at most two decisions/operations, no recursive hooks,
  dependent second-step candidates, shared budgets across root calls, repeat and
  no-progress stopping, real search timeout propagation, partial-success
  preservation, and no hidden execution in shadow mode.
- Memory interaction: total enrichment caps, `no_match` behavior, no implicit
  embeddings, current source versus accepted-base locators.
- Accounting/lifecycle: separate Jev usage, stable existing call numbering,
  unknown usage on failure, finalization reserve, atomic artifacts, sanitized
  diagnostics, and no raw bodies or credentials in logs.
- Architecture and Actions: dependency directions, module/source budgets,
  optional configuration and trusted secret handling.

Run focused tests first, then the repository's existing checks:

```bash
make check
make graph
make github-smoke
make github-doctor
```

`make check` is the offline suite plus compilation. There is no separately
configured Python formatter/linter/type-checker gate to invent. Docker/provider
or GitHub prerequisites may prevent optional checks; record actual outcomes.

When implemented, update `docs/testing.md` with a reproducible procedure:

1. Run the unchanged baseline with navigation off on a fixed Issue/base SHA.
2. Replay captured cases or run bounded shadow mode with explicit credentials.
3. Compare deterministic and Jev selection under identical excerpt budgets.
4. Inspect local decision evidence, `usage.json`, terminal outcome, verification,
   and independent review; include failed attempts in totals.
5. Repeat with memory enabled and on an ambiguous/cross-file Issue.
6. Exercise outage and deadline fallback with mock transport.
7. Enable GitHub only after trusted configuration and a disposable live canary.

For the action milestones, also document `excerpts` versus `actions`, objective
examples and omission behavior, and comparisons with zero/one/two follow-up
actions. Include memory-disabled runs and a sequence whose second action fails;
show that the original result and first successful evidence still reach the
Solver. Publish the candidate-coverage report and separate decisions on whether
single-action selection and two-action exploration should be enabled.

Record actual live commands once their interfaces exist; this spec does not
present proposed environment settings as already-supported functionality.

## 11. Completion criteria and current limitations

The first integration is complete only when it has a usable opt-in path,
bounded fallback behavior, correct accounting, focused and offline regression
coverage, updated current guides, and a report showing whether Jev adds value
over deterministic selection. Shipping an API client alone does not fulfill
the experiment.

The action milestones additionally require explicit objective handling, typed
candidate validation, correct handback and partial-success behavior, and
separate evaluation reports for one versus two follow-up operations. Completing
the excerpt pilot does not mark those milestones implemented or approved for
default enablement.

No improvement percentage, decision threshold, or latency promise is established
by this analysis. Vendor pricing and limits were checked in live documentation;
model effectiveness in Sage still requires evaluation. Existing local usage
artifacts were not sufficient to provide a representative navigation benchmark.

This document and its index entry are the only changes made for the planning
task. Runtime implementation, dependency installation, live model calls,
publication, and commits are outside this completed planning step.
