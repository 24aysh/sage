# Legion Memory correctness, quality benchmarks, and evaluation plan

## Status and scope

- Date: 8 September 2026.
- Status: targeted phases A–E implemented on 9 September 2026; see current
  architecture/testing guides for supported patterns and limitations. Phase F
  (evaluation harness) and phase G are not implemented in this change.
  Primary success means better verified issue resolution,
  not fewer tool calls or a smaller token total.
- Sage inspected: `70b3cab4e29300f1fecc53954c07aa3dcf061faa`, branch `legion`.
- Reference: local `code-review-graph/`, package 2.3.8. All 17 file hashes in
  [reference_manifest.json](../apps/agent/tests/legion_memory/reference_manifest.json)
  match the inspected source. The snapshot has no independent Git revision;
  this is not certification against today's upstream main branch.
- Excluded completely: run `20260908T145819Z-9f3e41cf`, as requested.
- Allowed run evidence: `20260908T151413Z-7bf658f5` and
  `20260908T151935Z-1db663e4`, specifically their memory and terminal artifacts.
- No live solve, embedding request, Qdrant mutation, fixture mutation, or Git
  initialization was performed for this audit. Diagnostic graphs used temporary
  SQLite files and controlled fake vector results.

This supplements [spec 25](25_LEGION_MEMORY_IMPLEMENTATION_PLAN.md), particularly
its still-open retrieval-quality and reference-parity gates. It does not replace
the current architecture or testing documentation. GitHub Actions integration
remains deferred. The evaluation harness remains a later implementation scope;
live benchmark execution remains explicit, user-authorized work. No benchmark
result or claim of improved agent quality exists yet.

## 1. Conclusion

There is no evidence that simply copying the remaining tool names will deliver
the reference's headline savings. There are, however, reproducible Legion
correctness and integration defects that can waste context or supply misleading
navigation. Fix those before expanding the test repository or tuning prompts.

The main findings are:

1. The benchmark expectation is mismatched: corpus-to-answer compression is
   not end-to-end issue-solving token or tool savings.
2. Semantic retrieval discards separately discovered lexical symbol matches.
   Both inspected memory runs were semantic-only despite 31 lexical candidates.
3. Retrieval can promote an unresolved call to a false resolved relationship.
4. The fixture's important dependency-injection call chain is incomplete, so
   flow and caller/callee navigation miss the business path.
5. Context is additive, and all 21 graph tool schemas are bound even when none
   is called. There is no measured accounting of navigation work displaced.
6. Repair sessions lose the initial memory packet while sharing the previous
   session's enrichment deduplication and character allowance.
7. The current fixture is small and easy to navigate, despite having 50 files.

These findings explain concrete ways Legion can fail to help. They do **not**
establish a measured causal token penalty for every recent run. The two allowed
runs are not a successful memory/no-memory comparison; both terminated with
`human_required_after_start`. Their terminal summaries report unavailable
Python/pytest verification. That environment report was not independently
reproduced here and must be checked before a future comparison.

## 2. What the reference actually measures and supplies

### 2.1 Benchmark interpretation

The inspected [reference README](../code-review-graph/README.md), under
“Benchmarks”, explicitly describes its approximately 65x median as
**whole-corpus tokens divided by graph-query tokens**. Its example corpora span
136,052 to 948,793 estimated tokens. The capture used code-review-graph 2.3.7
and local `all-MiniLM-L6-v2` embeddings, not Gemini Embedding 2.

The same README warns that a competent agent searches and reads matching files
instead of reading the entire corpus. Its
[agent baseline](../code-review-graph/code_review_graph/eval/benchmarks/agent_baseline.py)
compares graph queries with grep plus the top three matching files. Even that
is a retrieval benchmark, not a full solve with tool definitions, planning,
edits, tests, review, and repair. Its changed-file review-context benchmark can
produce ratios below one for small changes because graph output adds context.

Therefore:

- Do not compare Sage's cumulative `Total tokens` with the headline ratio.
- Separate retrieval context size, successful solve usage, latency, and cost.
- Gemini/Qdrant can reproduce a retrieval architecture, not MiniLM's exact
  semantic rankings. Algorithm parity requires identical candidate rankings
  in controlled tests; live model relevance requires separate validation.
- A hard issue can spend most of its work on correctness and testing, not
  discovery. Navigation memory cannot eliminate that necessary work.
- Total usage is a secondary trade-off, not the primary quality score. It is
  not guaranteed to increase: initial memory adds input, but may avoid later
  reads, failed approaches, or repair histories. Measure both outcomes. More
  tokens can be worthwhile if they buy demonstrably better fixes.

### 2.2 Research informing the evaluation design

The following are primary sources checked on 8 September 2026. They motivate
the design; the thresholds, dataset sizes, and release gates below are Legion
decisions, not claims that the papers prescribe them.

| Source | Design consequence for Legion |
| --- | --- |
| [SWE-bench evaluation guide](https://www.swebench.com/SWE-bench/guides/evaluation/) and [grading implementation](https://github.com/SWE-bench/SWE-bench/blob/main/swebench/harness/grading.py) | Evaluate actual patches in isolated environments. Require issue tests to pass and previously passing tests to remain passing; a fluent summary is not a score. |
| [SWE-bench Pro maintainers](https://github.com/scaleapi/SWE-bench_Pro-os) | Include genuine long-horizon, multi-file repository tasks. Pin harness/test revisions: the maintainers document test and evaluation corrections. Do not treat any dataset as permanently validated. |
| [RAGAs](https://aclanthology.org/2024.eacl-demo.16/) and [RAGChecker](https://arxiv.org/abs/2408.08067) | Diagnose retrieval and downstream use separately. Good retrieval is not proof of a correct answer or patch. Use deterministic/human gold for core scoring; model judges are optional secondary diagnostics. |
| [NIST trec_eval](https://github.com/usnistgov/trec_eval) | Use standard ranked-retrieval measures with explicit relevance judgments and identity normalization, not raw match counts or embedding similarity as quality. |
| [EvalPlus](https://arxiv.org/abs/2305.01210) | Passing a weak test set can overstate correctness. Add issue-specific boundary, regression, and adversarial checks. Its small-function tasks are not Legion's primary repository benchmark. |
| [Dror et al., statistical significance in NLP](https://aclanthology.org/P18-1128/) | Predeclare paired comparisons and the unit of analysis; report uncertainty instead of selecting one favorable trajectory. |

Candidate public dataset revisions inspected with the Hugging Face CLI skill:

- `princeton-nlp/SWE-bench_Verified`:
  `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`.
- `ScaleAI/SWE-bench_Pro`:
  `7ab5114912baf22bb098818e604c02fe7ad2c11f`.

These are metadata observations, not downloaded or validated task sets. Freeze
selected instance IDs, repository base SHAs, dataset revision, harness revision,
and image digests together at preparation time. Public historical tasks carry
training-contamination risk; pair them with newly authored/private held-out
issues and disclose that limitation. Do not publish a local subset score as an
official leaderboard score. Dataset and repository terms need separate checks.

### 2.3 Context delivery comparison

| Path | Reference behavior | Current Sage behavior |
| --- | --- | --- |
| Starting context | `get_minimal_context` returns a compact stats/risk/community/flow map and next-tool suggestions; its docstring targets about 100 tokens | Workflow retrieves a full Issue packet before the Solver; default maximum is 12 items / 12,000 characters |
| Follow-up navigation | Client calls search, graph, flow, or review tools as needed; reference instructions favor minimal detail and then source reads | Native graph tools provide the same categories, but 21 schemas are bound to every memory-enabled Solver session |
| Read/search enrichment | `enrich.py` supports structural context through a configured `PreToolUse` hook; it does not replace source reads | `read_file` / `search_text` append structural context automatically, at most 3,000 characters per call and 16,000 per MemorySession |
| Changes during work | Reference hook configuration updates the working-tree graph following write/edit operations | Legion intentionally indexes only the accepted committed base and does not update it for Solver edits |
| Review | Review tools can select change/impact context for the client | Sage's independent Reviewer remains a separate workflow role; Solver memory does not automatically reduce its input |

Sources: reference [minimal context](../code-review-graph/code_review_graph/tools/context.py),
[enrichment](../code-review-graph/code_review_graph/enrich.py),
[hooks](../code-review-graph/hooks/hooks.json); Sage
[workflow preflight](../apps/agent/src/sage/workflows/solve.py),
[Solver binding](../apps/agent/src/sage/agents/solver.py),
[messages](../apps/agent/src/sage/agents/prompts.py), and
[MemorySession](../apps/agent/src/sage/legion_memory/session.py).

Both systems still require source verification. MCP versus native functions,
SQLite versus Qdrant vector storage, and the phrase “navigation context” are
not demonstrated causes of poor retrieval. Do not remove source verification,
trust boundaries, or independent review to manufacture token savings.

## 3. Reproduced evidence and gaps

### G1 — Semantic results replace lexical evidence (P0)

In [retrieval.py](../apps/agent/src/sage/legion_memory/retrieval.py),
`retrieve_issue_context` sends the entire Markdown Issue to `hybrid_search`.
[search.py](../apps/agent/src/sage/legion_memory/search.py) quotes that entire
query as one FTS phrase. Full Issues rarely occur verbatim in node metadata.
The phrase approach also exists in the reference: the integration error is
treating a long Issue as a targeted search and then discarding the independent
Issue-signal retrieval stream.

When semantic results exist, `_retrieve_issue_context` clears its lexical
candidate dictionary and preserves only explicit file-path matches. Exact
symbol matches are not protected. Identifier boosts cannot recover a symbol
that is absent from the semantic candidate list.

Controlled offline reproduction on the shipment fixture:

- Issue: `Fix \`WebhookService.process\` retry handling after a transient failure.`
- Without vectors: `WebhookService.process` ranks first.
- With a fake, ready vector result containing only `config:MONGODB_URI`:
  the function disappears; the results are configuration nodes and their
  neighbors. This test uses no Gemini request and isolates candidate fusion.

Both allowed run artifacts show 31 lexical candidates, search mode `semantic`,
12 returned items, and an identical 3,231-character initial packet. The first
result is `MONGODB_URI`, the third is `MONGODB_DATABASE`, and
`WebhookService.process` is sixth. The configuration loader also consumes a
slot through expansion. The returned set omits `db/indexes.py` and
`tests/test_webhooks.py`, which the Solver subsequently reads.

### G2 — Expansion invents a resolved callee (P0)

`_expand_edges` calls `GraphStore.node(related)`. That method first tries a
qualified identity, then a file, then a unique bare name. A bare unresolved
edge target is therefore treated like a user search request.

Reproduction: the stored call from `WebhookEventRepository.claim` to
`self.collection.insert_one` correctly remains unresolved as `insert_one`.
Retrieving `Fix \`WebhookEventRepository.claim\`.` nevertheless emits
`tests/fakes.py::FakeCollection.insert_one` as a `callee_of` / `CALLS` fact.
The same misleading fact appears in both allowed run packets.

The reference's [GraphStore.get_node](../code-review-graph/code_review_graph/graph.py)
uses an exact qualified identity, apart from path-spelling normalization. It
does not make Legion's bare-name fallback at that boundary. This is a concrete
behavioral mismatch, not a request for a broader language resolver.

### G3 — Missing business-path resolution; raw edge counts mislead (P1)

An offline rebuild of the unmodified fixture produces:

| Measure | Result |
| --- | ---: |
| Indexed files / nodes / edges | 46 / 143 / 1,089 |
| CALLS edges with an exact node target | 70 / 201 |
| REFERENCES edges with an exact node target | 87 / 613 |
| Flows | 11 |
| Communities / singleton communities | 35 / 27 |

These target-resolution fractions are diagnostics, **not recall scores**:
external library calls should often remain unresolved.

However, important repository-local calls also remain unresolved:

- `services().webhooks.process(...)` does not connect the route to the service.
- `self.events.claim`, `self.shipments.get_shipment`,
  `self.shipments.apply_carrier_status`, `self.audit.record`, and
  `self.outbox.enqueue` do not resolve from `WebhookService.process`.
- The route's flow contains the route, dependency getter, validation function,
  and validation exception, but not `WebhookService.process` or its downstream
  repositories.

The fixture wires these objects in `services/container.py`, but its service
constructor arguments and Flask extension getter are untyped. Current
[metadata extraction](../apps/agent/src/sage/legion_memory/symbol_metadata.py)
and [resolution](../apps/agent/src/sage/legion_memory/resolution.py) do not
propagate that composition evidence end to end.

A direct single-file reference-parser probe also left these injected member
calls unresolved. That probe is not a full reference postprocessing build;
it does **not** establish that the reference resolves this fixture correctly.
Treat bounded composition inference as a Sage usefulness requirement, not an
already-proven upstream behavior that a blind port will fix.

### G4 — Ranking and community selection lack relevance gates (P1)

Lexical-only retrieval is also noisy. For the fixture's full Issue it returns
3,875 characters, ranks an endpoint extracted from `tests/test_api.py` first,
and does not include `WebhookService.process` in its 12 returned items.
Generic terms such as `shipment` match many signatures. Replacing semantic
search with lexical-only search is therefore not the proposed fix.

Community generation shares seeded Leiden with the reference, but differs in
edge population and postprocessing. Both exclude File nodes; Legion's
`_symbol_graph` also drops `CONTAINS`, while reference clustering can retain
class-to-method containment. Reference detection defaults to minimum community
size two and has oversized-community splitting/naming logic. Legion retains
singletons and lacks equivalent splitting. See both
[Legion](../apps/agent/src/sage/legion_memory/communities.py) and
[reference](../code-review-graph/code_review_graph/communities.py) implementations.

Initial retrieval only attempts community expansion while the candidate pool
is smaller than `max_results`; it does not assess whether existing candidates
are useful or redundant. More edges and communities do not by themselves
produce a better Issue packet.

### G5 — Additive context and tool exposure have unmeasured overhead (P1)

The two allowed runs made **zero native graph calls**, but received respectively
10 and 8 successful read enrichments. This is not proof memory was ignored:
the initial packet and enriched reads were still supplied. It is evidence that
binding more graph tools alone is not a demonstrated solution.

Serializing the current 21 native graph definitions through
`convert_to_openai_tool`, using compact JSON, produces 6,948 characters. This
is a reproducible schema-size diagnostic, not an exact provider token count.
The definitions remain bound on model calls whether or not the tools are used.

`_minimal_result` projects node fields but retains the general result envelope.
`get_minimal_context_tool` returns node/community/flow objects, unlike the
reference's smaller starting map. An oversized native response drops all data
and asks the model to narrow its query, potentially adding another call.
Enrichment is appended to source, and the agent loop retains message history.
It can save tokens only if it displaces sufficient later inspection.

Current “used” status means context was returned/exposed, not that it was
correct, followed, or economically beneficial. Current timing also separates
vector latency from the shorter graph/ranking timers; the latter must not be
presented as total memory latency.

### G6 — Repair visibility and graph freshness differ (P1)

`build_repair_message` starts a fresh Solver history without the initial memory
packet. The same `MemorySession` retains `_enriched` and `_enrichment_chars`,
so a useful fact supplied only in the preceding history can be suppressed in
the new one. This is confirmed from call sites; neither of the two inspected
runs reached repair, so it is not attributed as their cause.

Base-only indexing is an intentional safety adaptation, not an instruction to
mutate the shared graph after every write. Nevertheless, unchanged line ranges
and callers from the accepted base can become stale for edited files. Future
work needs explicit stale-fact handling within that boundary.

### G7 — Large-repository vector readiness needs capacity planning (P1)

[VectorIndex.synchronize](../apps/agent/src/sage/legion_memory/vectors.py)
defaults to 2,000 eligible non-File nodes and rejects larger builds. The default
build deadline is 300 seconds and document embedding is one node per provider
request. Increasing fixture size without planning these limits may silently
turn a solve comparison into lexical fallback, or fail an explicit embedding
build. The settings already exist in `.env.example`; unlimited spending is not
the solution.

This did **not** cause the two inspected runs: both report ready vectors and
97 reused embeddings, with no document embedding calls. Vector cleanup also
completed. There is no evidence that missing embeddings or unimplemented
cleanup caused their retrieval quality.

### G8 — Existing tests are not end-to-end parity certification (P1)

The focused retrieval, parity, remaining-features, vector, and native-tool
suites pass: **60 tests**. They provide valuable component coverage, but do not
cover the reproduced exact-match eviction, unresolved-edge re-binding,
full-Issue relevance, or fresh-repair visibility failures above.

Reference-derived fixtures and a hash manifest do not execute a normalized
reference-versus-Legion comparison. JSONC/inherited TypeScript configuration,
community postprocessing, and broader framework cases remain documented gaps.
They should be independently gated, not described as causing this Python
fixture's observed rankings without evidence.

## 4. Is `test-legion` large enough?

The currently present fixture is `test-legion/python-shipment-service`:

| Authored content, excluding caches and dependencies | Measured size |
| --- | ---: |
| Total files | 50 |
| Python files | 45 |
| `__init__.py` files | 10 |
| Production Python, including `wsgi.py` | 520 lines |
| Test Python | 254 lines |
| All Python source | 25,864 characters |

Using the reference's approximate characters/4 accounting, the **whole Python
fixture is about 6,466 tokens**, versus its published example corpora beginning
around 136,000. This is an approximate scale comparison, not a model tokenizer
measurement. The issue clearly names webhooks, idempotency, tenants, carriers,
and MongoDB indexes; the important filenames are easy to find. Comments in the
buggy implementation explicitly identify the intended bugs.

The issue's retry/concurrency semantics are meaningfully challenging, but
solving them requires reasoning and tests more than finding code in a large
repository. The fixture is suitable for correctness and small-repo overhead
regression tests. It is **not** a convincing benchmark for large-repository
navigation savings. Its earlier intended “35–40 tool calls” is not a validated
complexity measure and cannot be guaranteed from its file count.

There is also a repository-boundary trap: this fixture was intentionally not
Git-initialized. `git -C test-legion/python-shipment-service rev-parse
--show-toplevel` returns the Sage repository root. Legion's current root
resolution accepts that ancestor. Running the build directly against this
nested directory can index Sage instead of the intended fixture. The allowed
run artifacts instead identify the separate shipment repository and its 46
indexed files; do not attribute this trap to those runs. Keep the root fixture
uninitialized unless explicitly requested otherwise.

## 5. Implementation phases

### Phase A — Establish regression evidence and honest diagnostics

Reuse `MemoryRetrievalResult`, `LegionMemoryRunArtifact`, existing provider
usage records, and the current tests. Do not introduce a second tracing stack.

1. Add deterministic regression cases for G1, G2, and G6 using the existing
   temporary GraphStore and fake-vector helpers. Keep implementation and its
   passing regression test in the same logical change.
2. Add a normalized retrieval diagnostic: candidate identity, channel ranks,
   boosts, exact-match evidence, resolved versus unresolved relations, and
   selection/omission reasons. Keep detailed data in bounded local artifacts,
   not every console line.
3. Distinguish `available`, `retrieved`, `exposed`, `queried`, and `read_enriched`.
   Label “later read a retrieved path” as observed overlap, not proof of causal
   use. Retain existing fields compatibly while adding optional evidence.
4. Account separately for initial context, enrichment, graph responses, tool
   schemas, source reads, and fresh Solver/repair sessions. Character counts
   are exact; token estimates must be labeled. Actual provider totals and
   cached-input totals remain authoritative and must not be double-counted.
5. Report graph build, embedding synchronization, query embedding, ranking,
   and total memory preflight time separately. Preserve embedding usage as a
   separate ledger rather than pretending it is free or Solver usage.

Owners: `domain/memory.py`, `legion_memory/retrieval.py`, `session.py`, existing
usage/artifact/CLI formatters, and their tests. No new dependency required.

Acceptance: offline diagnostics reproduce the findings above; unavailable
memory still permits a normal solve; “memory used” is not advertised as a
token-savings measurement. Update `docs/testing.md` with examples of each state.

### Phase B — Correct edge identity and preserve lexical evidence

1. Separate exact stored-node lookup from user-facing symbol disambiguation.
   Reuse GraphStore, but make edge traversal, expansion, vectors, and structural
   analysis use exact identities. Audit all `store.node` callers before
   changing its public behavior. Unresolved targets stay unresolved; suggested
   name matches may be offered separately, never as verified edges.
2. Reuse `extract_issue_signals` to create bounded identifier/path queries and
   a concise semantic intent query. Do not treat the whole Issue as a single
   FTS phrase. Deduplicate query embeddings and cap the query count.
3. Fuse independent exact/path, lexical, and semantic candidate rankings.
   Preserve strong lexical hits before result truncation. Do not add lexical
   scores around 20 directly to RRF scores around 0.02; use rank fusion plus
   explicit, bounded exact-match priority.
4. Preserve channel provenance after fusion, and expand only exact resolved
   edges. Count accepted neighbors toward budgets; unresolved targets and
   repeated edges must not exhaust the useful-neighbor quota.
5. Add tests for semantic distractors, empty vectors, punctuation-heavy
   Markdown, ambiguous names, false test-double callees, large candidate pools,
   and deterministic ties. Calibrate Gemini relevance separately from rank
   fusion; do not bake one fixture's names into ranking rules.

Owners: `store.py`, `search.py`, `retrieval.py`, `queries.py`, `context.py`, tests.
Keep schema changes minimal; add a migration only if stored resolution state
cannot be represented safely in existing metadata. Preserve old databases or
provide a visible rebuild path.

Acceptance: the G1 exact function survives a semantic distractor; G2 never
claims a MongoDB call targets the fake; existing lexical/no-vector fallbacks
continue to work. A full Issue yields a true lexical+semantic union when both
channels produce candidates.

### Phase C — Repair the useful graph and verify reference differences

1. Extend existing Python metadata/resolution with bounded, evidence-backed
   composition propagation: constructor arguments, assigned receiver fields,
   factory return types, and local service-container members. For framework
   registries such as Flask extensions, infer only uniquely evidenced writes
   and reads. Multiple possible bindings remain ambiguous.
2. Preserve dispatch/receiver evidence and confidence. Never import or execute
   indexed application code, bind production calls to a test double solely by
   name, or resolve external libraries to unrelated local homonyms.
3. Model route prefixes and distinguish production route declarations from
   HTTP calls in tests. Use test endpoints as supporting evidence rather than
   interchangeable production entry points.
4. Rebuild flows from validated local calls and explicitly report dynamic
   gaps. Add independently authored golden paths from route through service,
   event repository, audit, and outbox; do not derive expected paths from the
   graph being tested.
5. Align relevant community input edges, minimum size, test reassignment,
   cohesion, splitting, and naming with normalized reference fixtures. Keep
   bounded work on large graphs. Test memberships/connectivity, not unstable
   integer IDs or a desired aggregate community count.
6. Add an optional pinned-reference differential harness outside production
   dependencies. Compare normalized node identities, resolved edges, flows,
   communities, query results, and compact response fields. Store small golden
   outputs so the normal offline suite does not require the ignored checkout.
   Record justified native/Gemini/Qdrant/base-snapshot differences explicitly.
7. Complete JSONC/inherited tsconfig and further language/framework coverage
   as a separately gated parity subphase. It is not a prerequisite for fixing
   the demonstrated Python retrieval defects.

Owners: `parsing.py`, `symbol_metadata.py`, `resolution.py`, `store.py`,
`communities.py`, `queries.py`, the existing parity manifest and tests.
Reuse Tree-sitter, Python AST, NetworkX, and igraph; avoid a new analysis
framework. Increment the parser/analysis identity when persisted output changes,
and test full versus incremental build convergence and vector invalidation.

Acceptance: the fixture's statically provable business path is navigable, or
an explicit unresolved boundary is reported where inference is unsafe.
Supported composition fixtures must resolve their independently specified
paths. No false edge is accepted to improve apparent recall. Reference parity
claims identify exactly which patterns and snapshots were tested.

### Phase D — Make context selective, compact, and session-aware

1. Replace “top N symbols” as the only selection rule with a bounded Issue map:
   entry point, behavior owner, relevant persistence/configuration, and tests
   when evidence exists. Deduplicate file/class/method overlap and explain
   selected relationships briefly. Generic infrastructure must not crowd out
   strongly supported Issue symbols. Do not force every role into every issue.
2. Use progressive disclosure: a compact initial packet of actionable paths
   and line ranges; targeted caller/callee/flow details only for unresolved
   questions. Reuse existing source-read tools for bounded ranges. Do not add
   source snippets everywhere or force an extra overview call.
3. Give `minimal` a genuinely smaller response contract. Keep full provenance
   in artifacts, retain necessary identity/freshness in model output, and omit
   repeated supported-pattern catalogs. On overflow, retain a valid useful
   prefix and explicit omission counts rather than discarding all data.
4. Bind a small task-relevant subset from the existing native registry for
   normal issue solving; use the existing query-pattern capability for related
   lookups. Keep the complete deterministic service API available. Define
   advanced profile/tool exposure only where needed; avoid a new runtime or
   forcing the agent through a costly discovery tool on every run.
5. Deduplicate facts already visible in the current model history across the
   initial packet, native responses, and source enrichment. Reset visibility
   when a fresh repair history starts, or explicitly carry a compact memory
   summary into it. Keep a separate run-level usage ledger and hard safety cap.
6. After edits, suppress invalidated locators or mark them accepted-base-only;
   use current source for the edited region. Do not overwrite the shared base
   database with the Solver's working tree. A future run-local graph overlay
   requires a separate design if suppression proves insufficient.
7. Test a no-match path with minimal/no unnecessary graph overhead, a repeated
   read in one session, the same read in a fresh repair session, bounded tool
   overflow, and edits that move or delete indexed symbols.

Owners: `retrieval.py`, `context.py`, `session.py`, `agents/memory_tools.py`,
`repository_tools.py`, `solver.py`, `prompts.py`, and orchestration call sites.
Any budget/profile setting belongs in `config.py` and `.env.example`; preserve
existing CLI arguments and make commands. Set numeric compact budgets from
offline quality-versus-size curves, not a promise to match a 100-token slogan.

Acceptance: compact output retains required evidence at the documented budget;
fresh repair histories receive needed facts; no source-verification or plan
gate is removed. Scripted model tests prove that the initial map can lead
directly to targeted reads, without requiring graph calls merely to increase
the graph-use metric. Update architecture and testing docs with actual behavior.

### Phase E — Make large-repository comparisons feasible and correctly scoped

1. Validate requested repository scope against the resolved Git root. Reject
   or clearly require confirmation for an unintended ancestor rather than
   silently indexing Sage when the requested fixture has no Git repository.
   Do not initialize the user's fixture automatically.
2. Report eligible nodes and configured node/deadline limits before hosted
   build work. Reuse content hashes, checkpointed vector reuse, and generation
   publication. Provide a documented explicit capacity setting for large
   repositories; do not silently embed an arbitrary prefix or remove cost caps.
3. Where provider-supported, improve throughput through bounded batching or
   bounded concurrency behind the existing provider adapter. Verify the actual
   provider contract at implementation time. Preserve retries, deadlines,
   cancellation, resumability, and cleanup guarantees.
4. Test more than 2,000 eligible nodes with a fake provider, interrupted builds,
   completed no-change reuse, query readiness, and retrieval fallback. Report
   build cost separately and amortize it explicitly in later benchmarks.
5. Add a deterministic, read-only verification-environment preflight using
   existing discovery/sandbox boundaries. Confirm the selected fixture's
   interpreter, dependencies, and test command inside the actual solve image
   before spending model calls on a benchmark. Apply equally to `solve` and
   `legion-solve`; never enable unrestricted networking to make tests pass.

Owners: memory service/vectors, existing Gemini and Qdrant adapters, settings,
workflow preflight, verification discovery, sandbox adapter, Makefile/docs as
needed. Existing cleanup is retained, not reimplemented. Add no dependency
unless the current adapter cannot support the required provider contract.

Acceptance: repository scope is visible and correct; oversized embeddings have
an actionable outcome; a resumed build reuses progress; missing test tooling
is a benchmark-invalid condition, not evidence memory failed at retrieval.

### Phase F — Implement the benchmark and evaluation harness

#### F1. Freeze the hypotheses and experimental contract

Primary question: **does enabling Legion Memory increase the probability that
the same Sage system produces a verified correct fix for a held-out issue?**

Use three layers rather than one blended “memory score”:

| Layer | Primary measure | What it can establish |
| --- | --- | --- |
| Retrieval, no Solver | Independently labeled required-evidence recall at the displayed budget, with ranking precision and false-edge checks | Whether the right information is retrieved accurately |
| Read-only repository understanding | Fully correct source-backed answers to predefined repository questions | Whether an agent can use the information to understand dependencies and behavior |
| End-to-end issue solving | Strict externally verified resolution rate, `Resolved@1` | Whether the delivered memory-enabled agent fixes more issues without regressions |

The end-to-end comparison is the release headline. Neither layer one nor layer
two can substitute for it. Do not use a weighted composite that trades a wrong
patch for a short prompt. Tools, tokens, dollars, and latency remain secondary
operational metrics; no reduction in any of them is required for a **quality**
improvement claim. A cost-effectiveness claim is a different, separately tested
claim. Do not change the metric after seeing results to obtain a positive story.

Default registered comparison is `no_memory` versus `legion_full`. Same Sage
commit, issue, accepted base tree, Solver/Reviewer models and settings,
verification configuration, image digest, and maximum turns/time/output limits.
Use the existing `make solve` and `make legion-solve` workflows internally,
not a specially advantaged replacement agent. Tool schemas and memory-specific
instructions are part of the treatment, not stripped out to hide overhead.

Prebuild each graph from its accepted base for the primary warm-index product
comparison. Time the solve after both arms finish environment preparation;
query/retrieval latency remains part of the memory arm. Report cold build and
embedding cost separately. A secondary cold-start comparison includes both
in total elapsed time. No unlimited memory-arm compute: resource caps are
registered equally, including cumulative limits if supported. Record the
provider/model identifier and evaluation dates because API behavior can drift.

One attempt means one normal bounded Sage workflow, including its configured
verification/review/repair cycles, yielding one final candidate. Repeated
attempts estimate reliability; never select the best of three and call it
`Resolved@1`. Also capture the first candidate before review repair for a
separate first-candidate metric; no hidden-grader feedback reaches repair.

#### F2. Curate task sets and keep gold outside the agent

Keep the shipment fixture as a smoke/overhead control, not headline evidence.
Implement dataset adapters for a common manifest, starting with user-supplied
Python repositories and a pinned Python subset of SWE-bench. Add selected
long-horizon SWE-bench Pro instances only when their language/environment is
supported. A local subset is always labeled as such, with its selection rules.

Use existing larger repos with substantial real source and subsystem boundaries:
roughly 100,000+ estimated source tokens is an initial **selection target**, not
a minimum size at which memory can help. Measure non-generated source lines,
symbols, dependency depth, and distractor subsystems. Never inflate file counts
with empty packages. Include small/easy and no-useful-memory controls so the
suite does not select only tasks favorable to retrieval.

Initial milestones (proposed sizes, not statistical guarantees):

- Offline smoke: current small fixture plus G1–G8 regression cases; no API calls.
- Development pilot: 12 issues across at least 3 repos, 2 independent attempts
  per arm: 48 full solves. Use only for harness debugging and power planning.
- Held-out confirmation: target at least 100 issues across at least 5 repos,
  3 independent attempts per arm: 600 full solves. The plan command must show
  this workload before authorization. Final count is fixed using pilot
  variance/discordance, desired detectable effect, and an approved spend cap;
  a smaller set is explicitly labeled exploratory if underpowered.

Stratify before looking at outcomes: exact-symbol versus behavior-only issue,
local versus cross-module change, state/retry/concurrency, API compatibility,
caller impact, and negative/irrelevant memory. Separate development and held-out
issues; also maintain an unseen-repository slice. Remove near-duplicate issues
and fix variants across splits. Retire/reversion a held-out set after tuning on
its failures rather than repeatedly calling it unseen.

Manifest contracts, implemented as strict versioned Pydantic models:

| Record | Required contents |
| --- | --- |
| `BenchmarkTask` | Task ID, source/dataset revision, repo URL or approved path, exact base SHA/tree hash, issue file and digest, split/strata, setup recipe and image digest, visible verification commands, resource requirements, opaque private-grading bundle ID and digest |
| `PrivateTaskGold` | FAIL_TO_PASS and PASS_TO_PASS test IDs/commands, reference patch for calibration only, acceptance groups, optional symbol/edge/path labels, acceptable alternative evidence sets, known distractors, blinded-review rubric |
| `ExperimentSpec` | Suite digest, Sage revision, selected arms, attempt count, fixed model/settings fingerprints, embedding recipe/vector generation, budgets, scheduling seed, primary metric, effect threshold, analysis method, failure policy, authorized execution mode |
| `AttemptResult` | Experiment/task/arm/repetition IDs, Sage run ID, provenance, observed treatment exposure, stage outcomes, immutable candidate digest, test-level external grades, failure category, usage/latency ledger, artifact paths |

Public manifests may reference private gold by opaque ID, but **never** mount
the gold bundle, reference patch, hidden tests, gold retrieval labels, previous
attempts, or final-fix Git objects into the Solver/Reviewer workspace. Build
memory only from the accepted base, not benchmark metadata. A shallow exact-base
checkout must be checked for later refs/objects; a full clone's future history
is not safe. Use the same approved public task-description fields in both arms.
Existing base tests stay visible; held-out additions are grader-only.

Before admitting a task, verify that its base fails the designated issue tests,
its reference repair passes them, and its regression tests pass at baseline.
Check requirements against tests manually; public dataset membership is not a
substitute for this. For newly authored issues, review gold independently of
Legion outputs and keep deliberate-bug answer comments out of benchmark copies.
Do not require patch-text/file-list equality with the reference: valid fixes
can have different structures. Gold changed files are hints for annotation,
not automatic exhaustive relevance labels.

#### F3. Implement independently controlled grading

The evaluator, not Sage's summary or Reviewer verdict, owns the outcome. After
Sage terminates, apply the captured candidate to a separate clean copy of the
exact base in a disposable, network-disabled grading container. Keep immutable
test definitions and trusted test execution/results outside agent-writable
paths. Test-agent modifications remain inspectable but cannot replace hidden
tests or forge a grade. Reject result tampering; explicitly control test-loader
hooks/configuration and validate every required test ID executed rather than
accepting only exit code zero. Use the dataset's pinned harness where possible
instead of trying to sandbox hostile Python inside the same interpreter.

For task i, arm a, repetition r, define `resolved[i,a,r] = 1` only if:

1. A valid candidate applies to the accepted base and violates no predeclared
   integrity/scope constraint.
2. Every required FAIL_TO_PASS test passes (at least one required for bug tasks).
3. Every designated PASS_TO_PASS regression test still passes. Required skipped,
   missing, or uncollected tests do not count as passed.
4. Any predeclared security/API/integration acceptance gates pass.

Otherwise score zero. If a task needs subjective criteria, define the blinded
human rubric and adjudication process before the run; never use an LLM judge
as the sole final correctness gate. Reject unsupported tasks during preparation
instead of quietly substituting subjective scores at reporting time.

Metrics besides strict resolution:

- Per-issue acceptance-group coverage: equal-weight requirement groups, then
  macro-average issues. Do not let hundreds of trivial tests overwhelm one
  failed requirement. Partial coverage is diagnostic, not a resolved issue.
- Regression-free rate and critical failure counts with explicit denominators.
- First-candidate verified success versus final verified success.
- Sage delivery success versus external candidate correctness, reported
  separately. If Sage reports blocked but leaves a correct patch, external
  patch correctness does not become a successful autonomous delivery.
- Optional candidate-test quality: run new agent tests against the buggy base
  and independent held-out checks/mutants. Merely adding more tests is not quality.

Calibrate the grader with known-good, no-op, wrong, partial, deliberately
regressing, test-deleting, forged-output, and test-skip candidates. Include a
legitimate alternative fix. Grading must fail closed when test evidence is
incomplete. A failing hidden test is never fed back to the running agent.

#### F4. Build retrieval and understanding evaluations

Retrieval gold must identify source-backed evidence roles and alternatives,
not only names returned by the current parser. Use independent location/symbol
annotations to expose parser misses; maintain an end-to-end recall denominator
including unindexed gold and a separately labeled conditional-on-index recall.

Score the actual displayed context, after budgets/truncation, as well as the
larger internal pool. Deduplicate identical evidence identities; class/file
wrappers do not count as repeated relevant hits. Report `k=5,10,12` and display
budgets of 2,000/4,000/8,000 characters as a quality-versus-budget curve; select
the production operating point on development data only.

- Precision@k: relevant identities / k, padding unfilled slots as nonrelevant;
  also report returned-result precision to make abstention interpretable.
- Recall@k: relevant identities found / independently labeled relevant set.
  For valid alternative sets, score required evidence-role coverage rather
  than penalizing a valid alternative for not retrieving every possible fix.
- MRR: reciprocal rank of the first required evidence item (zero if absent).
- nDCG@k: graded relevance 2=required, 1=supporting, 0=distractor; use
  `(2^relevance-1)/log2(rank+1)`, normalized against ideal ranking.
- Exact-anchor retention, required dependency-path coverage, and false-resolved
  edge rate. Unresolved relationships are misses/abstentions, not fabricated
  successes. Distinguish missing links from false asserted links.
- No-match accuracy on genuinely irrelevant/unsupported issues. Empty-gold
  recall/nDCG is N/A, reported as a separate negative slice, not silently one.

Reuse fixture judgments to validate formulas against hand-computed examples
and, optionally, NIST's evaluator. No production dependency on a RAG evaluation
framework is needed. Unknown/unjudged results get pooled human review before
the held-out labels freeze; don't treat all novel valid evidence as wrong.

For understanding tasks, create read-only questions with deterministic
structured targets: identify an implementation and its tests, trace a specific
dependency, identify an idempotency boundary, or name a caller affected by an
interface change. Return `answer`, `citations(path, range, symbol)`, and
`abstain_reason`. Grade semantic correctness against accepted alternatives and
validate citations at the base snapshot; merely citing an existing line is not
enough. Use blinded human adjudication for behavior answers that cannot be
graded deterministically. Retrieval gold stays hidden. Reuse the existing tool
loop and repository read tools, with writes/commands that mutate disabled; this
is an evaluator-owned task session, not a replacement solve architecture.

Predeclare a sample of incorrect/uncertain answers for human review. Optional
RAG-style model grading is secondary, versioned, and validated against that
sample; charge its usage to evaluation, never to the Solver score.

#### F5. Run matched treatments and explanatory ablations

| Arm | Purpose |
| --- | --- |
| `no_memory` | Unmodified production solve without a memory file; normal search/read tools remain available |
| `legion_full` | Unmodified production memory workflow, including initial retrieval, native tools, and enrichment |
| `lexical_graph` | Same memory exposure policy, but vectors disabled; isolates the incremental benefit of embeddings |
| `hybrid_initial_only` | Hybrid initial packet, no follow-up native graph tools or enrichment; tests whether the initial map suffices |
| `hybrid_no_enrichment` | Hybrid initial packet and native tools, automatic enrichment off |
| `lexical_source_packet` | Optional bounded lexical source-locator packet without graph expansion/vectors; a stronger retrieval control than reading a whole corpus |

Implement optional exposure controls as a narrow typed policy consumed by
existing MemorySession/tool binding, constructed by the evaluation workflow.
Production defaults stay unchanged. Do not add a version selector, parallel
agent architecture, or arbitrary prompt editing per arm. The two headline
arms must match normal CLI behavior in deterministic integration tests.

Development-only diagnostics may include an oracle packet or length-matched
distractor packet, clearly labeled and never included in headline scores.
Do not index solutions to create a purportedly realistic oracle. A relevant
packet beating a distractor supports a mechanism hypothesis; it is not proof
of repository-wide improvement by itself.

Randomize/alternate paired arm order within task/repetition. Use clean
workspaces and independent transcripts/query caches for every attempt. Static
base graph reuse is allowed and recorded; no learned cross-attempt memory,
repair patches, or grading feedback may carry over. Seed scheduling and fake
providers deterministically; do not claim a provider seed makes live model
responses deterministic. Record actual fallback: a planned hybrid arm with
unavailable vectors remains a hybrid-product reliability failure/fallback,
not an unlabeled successful semantic run.

Only the two primary arms run by default. Ablations require a separate explicit
selection/spend approval and are explanatory unless independently preregistered.

#### F6. Statistical analysis, failures, and claims

For N registered held-out issues and R attempts per arm:

```text
p[i,a] = mean_r(resolved[i,a,r])
Resolved@1[a] = mean_i(p[i,a])
delta_pp = 100 * mean_i(p[i,legion_full] - p[i,no_memory])
```

Report paired memory-only wins, baseline-only wins, both-pass and both-fail
counts for each repetition, per-issue differences, and repository/issue-type
slices. Macro-average issues first and also report repository-macro results.
Never count repeated attempts as independent new issues.

Use 10,000 paired bootstrap resamples of **issues within repository strata**,
keeping all repetitions and both arms of an issue together, with a fixed
analysis seed, for a 95% interval on the primary difference. The interpretation
is performance on the registered suite/repository mix. With a small number of
repos, do not infer all-repository generalization; report leave-one-repository-
out sensitivity and the unseen-repository slice. A single-repeat paired binary
check may also use exact McNemar; don't apply it to flattened correlated trials.
Predeclare any multiple-comparison correction before confirmatory ablations.

Proposed release gate, frozen before the held-out run:

- Mean strict-resolution improvement at least **5 percentage points**, and
  the paired 95% interval's lower bound is above zero.
- No integrity violation or new critical security regression; report every
  such event independently of aggregate success.
- Regression-free rate must meet a registered non-inferiority margin of
  **2 percentage points** (lower confidence bound on its difference above -2).
- Report tool/token/cost increases honestly. They may limit deployment, but
  are not substituted for the quality criterion.

These numerical margins are proposed product decisions, not established
benefits or universal standards. Power planning happens on development data;
freeze the final N and margins before unblinding held-out outcomes. No optional
stopping when significance first appears. If the sample is insufficient, label
the result inconclusive. A negative result is a valid evaluation outcome.

Failure accounting is part of the metric:

- Preflight-invalid tasks are excluded **before** any arm runs, with public
  reasons and counts. Freeze the eligible manifest afterward.
- After scheduling, no patch, budget exhaustion, invalid patch, agent failure,
  provider failure, and memory failure are retained in the primary
  intention-to-treat denominator, with reason codes. A product fallback that
  nevertheless solves the issue can score as solved, but its exposure label
  must remain fallback.
- Evaluator infrastructure errors produce an explicit ungraded category;
  the conservative primary report counts them as not resolved and reports
  grade coverage. A confirmatory conclusion requires complete grading.
  Report best/worst-case missing-grade sensitivity as a diagnostic, not a
  substitute for that gate.
- Any infrastructure-only retry rule is fixed beforehand (default at most one
  paired block retry), saves both original attempts, and never reruns only the
  worse model result. Complete-case sensitivity is secondary, never a way to
  hide failures. Don't remove memory outages from the headline product score.

Usage reporting includes total and cached input separately, output, commands,
per-stage tool counts, elapsed time, build/query embeddings, and grading cost.
Cost per verified resolution includes **all** spend in that arm divided by
successful attempts; report N/A if zero resolve. Missing price/usage data stays
unknown. It is not permissible to subtract memory tokens to construct a fake
“net” provider total. Cold build amortization must state the number of issues.

Final machine and Markdown reports say one of: `quality_improved`,
`no_detected_improvement`, `quality_regressed`, or `inconclusive`, with effect
size, interval, workload, and limitations. Classify complete, adequately powered
experiments as improved only when all registered gates pass; regressed when
the resolution interval is wholly negative or a registered safety gate fails;
no detected practical improvement when the upper bound is below the registered
5-point target without a regression finding; otherwise inconclusive. Incomplete
grading or an underpowered exploratory sample is always labeled inconclusive
for confirmatory claims, with any safety findings still displayed. Report an
efficiency claim only if
its separate quality-adjusted criterion was actually tested. No unconditional
claim that memory improves every issue, repo, or model.

#### F7. Implementation ownership, artifacts, and command contract

Reuse `solve_issue`, `SolveRequest/SolveResult`, `RunArtifacts`, `RunProvenance`,
MemorySession, repository/sandbox boundaries, and the existing Gemini/Qdrant
adapters. No second copy of the solve/verify/review loop.

Proposed additions, creating files only when their subphase lands:

| Owner | Responsibility |
| --- | --- |
| `sage/domain/evaluation.py` | Strict versioned task, experiment, grade, and report contracts; no integration imports |
| `sage/evaluation/` | Focused manifest validation, gold matching, retrieval metrics, paired statistics, report generation; no production workflow imports |
| `sage/workflows/evaluation.py` | Preparation, matched scheduling, calls to existing solve workflow, isolated grading lifecycle, resumption |
| `sage/composition.py` | Construct evaluation dependencies and arm policies; existing construction ownership retained |
| `sage/cli.py`, `Makefile` | Thin `sage eval` subcommands and `legion-eval-*` wrappers |
| `benchmarks/legion_memory/` | Versioned public suite/experiment templates, tiny deterministic fixtures, annotation instructions; no private gold in model-visible repos |
| `apps/agent/tests/evaluation/` | Formula, manifest, leakage, scheduler, grader, report, and failure-path tests; mocked model/network boundaries |

JSON/JSONL plus the standard library and existing Pydantic are sufficient for
the first harness. An upstream grader can run in a separate pinned evaluation
image/environment; don't make its large dependency tree a production Sage
dependency. Evaluation spend caps, manifests, and dataset paths are explicit
CLI/manifest inputs. Secrets use existing `config.py` loading, never suite files.
Document any genuinely new environment key in `.env.example` only when needed.

Write atomic, resumable records under `.sage/evals/<experiment-id>/`:

```text
experiment.json         # frozen resolved config, source hashes and schedule
tasks.jsonl             # eligible task IDs and preflight results
attempts.jsonl          # index of immutable attempt records / Sage run IDs
attempts/<id>.json      # per-attempt metadata, candidate digest, exposure
grades/<id>/            # evaluator-owned test records/logs; never agent-mounted
retrieval/<id>.json     # actual displayed evidence and ranking judgments
report.json
report.md
```

Use per-record atomic writes and resume validation; changing a model, snapshot,
patch, test bundle, arm, or grader revision creates a new identity, never a
cache hit. Preserve canonical Sage run artifacts and reference them rather
than copying secret-bearing traces. Limit report payloads; never emit API keys,
private issue text, or full private source in a publishable report by default.

New commands below are a **proposed interface, not available yet**:

```bash
# No model/network calls; validate schemas and print attempt counts/budgets.
make legion-eval-plan SUITE=benchmarks/legion_memory/suites/pilot.json

# Explicit image/repository preparation; downloads require FETCH=on.
make legion-eval-prepare SUITE=benchmarks/legion_memory/suites/pilot.json FETCH=on

# Deterministic offline regression/metric checks with fake embeddings.
make legion-eval-offline SUITE=benchmarks/legion_memory/suites/smoke.json

# Paid retrieval / read-only understanding / full-solve experiments: explicit.
make legion-eval-run EXPERIMENT=/absolute/frozen-experiment.json \
  LAYER=solve ARMS=no_memory,legion_full EXECUTE=on

# Grade existing captured patches without Solver/embedding calls.
make legion-eval-grade EXPERIMENT=/absolute/frozen-experiment.json

# Pure aggregation; no new grading, model calls, or uploads.
make legion-eval-report EXPERIMENT=/absolute/frozen-experiment.json
```

`LAYER` supports `retrieval`, `understanding`, and `solve`. Live execution reads
attempt counts/caps from the frozen experiment and refuses if `EXECUTE=on` or
spend limits are absent. `prepare` and `grade` can require Docker but not paid
model calls; preparation fetches are an explicit separate permission boundary.
Offline mode cannot instantiate network providers even if the user's `.env`
contains keys. Resume cannot expand an authorized task/arm list silently.

Define stable exit codes: 0=completed valid report (including a negative result),
1=execution/integrity error, 2=invalid configuration/missing authorization.
An optional `--require-quality-gate` flag returns 3 when a complete report fails
the registered gate. Missing comparison arms/grades mark a report incomplete,
never an apparently perfect score. Scripts must not interpret “no improvement”
as a reason to discard the result.

#### F8. Tests, delivery order, and user-friendly guide

Implementation chunks with their tests:

1. Contracts, manifests, leakage validation, and offline metric/report functions.
2. Isolated grader and known-candidate calibration, without a live agent.
3. Thin matched-run workflow using fake Solver/provider boundaries; arm parity
   with normal `solve`/`legion-solve`, immutable attempt identity and resumption.
4. Retrieval/understanding tasks and optional ablation exposure controls.
5. Paired statistics, uncertainty/failure reporting, Makefile/CLI, and guides.

Unit tests must cover hand-calculated precision/recall/MRR/nDCG, empty gold,
multiple correct evidence sets, duplicates/truncation, unindexed gold,
all-pass/all-fail/tied paired outcomes, asymmetric failures, missing usage,
unknown prices, and repeat clustering. Bootstrap results are deterministic for
a fixed analysis seed and handle zero-variance samples without false certainty.

Integration tests must reject future-commit leakage, hidden-test/reference-patch
exposure, wrong repo/SHA/vector identity, test-result forgery/skipping, offline
provider creation, repeated grading-cache collisions, and accidental live calls
from plan/report commands. Known-good/wrong/regressing patches exercise real
local grading independently of fake model behavior. Keep Docker-only checks
explicit; `make check` remains an offline, unpaid gate.

Update `docs/testing.md` with a short numbered path: prepare one valid task,
run the offline smoke suite, plan the paired experiment, explicitly authorize
the live run, grade, and interpret `report.md`. Include one synthetic example
with an inconclusive/negative result, visibly labeled **illustrative**. Keep
ordinary single-issue `make solve`, `make legion-memory`, `make legion-retrieve`,
and `make legion-solve` compatible; benchmarking wraps them, not replaces them.

Acceptance: implementers can run the entire tiny fixture pipeline offline with
known results; planned paid workload is visible before execution; any later
improvement claim follows F6. Implementing the harness does not authorize
running the 48- or 600-solve workloads.

### Phase G — Legion branding with retained third-party provenance

Use **Legion Memory** in prose and `legion-memory` in product/CLI identifiers.
Remove optional upstream comparisons from product-facing copy where they add
no value, and avoid marketing unmeasured parity or inherited benchmark results.

Do **not** globally replace third-party identities, source URLs, copyright
holders, permission notices, or historical benchmark attribution. Existing
adapted code remains subject to its source license; benchmark success does
not suspend attribution requirements. The [license text](https://opensource.org/license/mit)
requires preservation of its copyright and permission notice in copies or
substantial portions. Adding a license for original Legion work later is a
separate decision from preserving existing third-party notices now.

Implementation scope:

1. Inventory tracked product wording separately from provenance/legal material,
   ignored upstream source, dependency metadata, and private run artifacts.
2. Replace optional product wording with Legion branding; do not rename the
   upstream benchmark as a Legion benchmark or turn its package/version/hash
   manifest into a fictitious Legion origin record.
3. Preserve existing attribution. If consolidating repetitive source headers,
   first retain the full applicable notice and source mapping in an appropriate
   third-party notices file, ensure packaging includes it, and reference it from
   adapted modules. Do not defer required notices until performance is proven.
4. Preserve real reference paths and ignore/test exclusions unless the reference
   directory is deliberately relocated with all consumers updated. Do not
   rewrite Git history, ignored dependency trees, or historical run evidence.
5. Add a scoped branding check with explicit provenance exceptions, not a
   repository-wide assertion that the upstream name/license can never occur.

Acceptance: all newly introduced evaluation commands/reports are Legion-branded;
upstream identity survives only where needed for attribution, accurate history,
or reference tooling. A zero-occurrence purge is explicitly not an acceptance
criterion. No top-level license choice for original Sage code is made by this
plan, and retained third-party obligations are not contingent on F6's outcome.

## 6. Delivery order and completion criteria

Start A plus F1–F3's frozen contracts, task validation, and known-candidate
grader so fixes are not developed against a moving scoring rule. Then deliver
B, C/D, and E, followed by the remaining F harness and G branding work. Preserve
the audited Sage revision as historical baseline metadata; optional old-Legion
comparisons are separate from the primary same-commit memory/no-memory test.
Phases C and D can be split into focused changes. Keep regression tests with
their fixes. Do not make positive retrieval claims while expansion manufactures
resolved edges. GitHub Actions integration and paid benchmark execution are
not authorized by implementation of this plan.

Before claiming completion:

- Every G1–G8 item has a passing regression, an explicit intentional difference,
  or a documented remaining limitation; no blanket “exact parity” assertion.
- `make check` passes, with focused graph/agent/orchestration tests run first.
- `.env.example`, `docs/architecture.md`, and `docs/testing.md` describe only
  delivered settings/behavior. Preserve existing make command compatibility.
- Full and incremental graph builds converge, vector generation identity stays
  correct, and unavailable memory remains a safe, visible fallback.
- Native tools remain repository-bound and read-only; current source and
  independent verification continue to outrank retrieved graph claims.
- F1–F8 have implemented schemas, deterministic tests, calibrated independent
  grading, reproducible CLI/reporting, and explicit execution authorization.
- Quality claims use the registered matched suite, include failures, and meet
  the F6 statistical/safety gates. Retrieval improvements alone are not
  advertised as solved-issue gains; tool/token reductions are not required.
- Product branding is Legion-owned, but accurate third-party attribution and
  required permission notices survive independently of benchmark outcomes.

## 7. Audit verification and open decisions

Checks performed for the original audit (not new benchmark results):

- Verified all 17 pinned local reference hashes.
- Rebuilt the unchanged shipment fixture into a temporary graph; measured the
  counts in sections 3–4 and inspected critical resolved/unresolved calls.
- Reproduced exact-symbol eviction with a fake semantic result and false
  `FakeCollection.insert_one` callee evidence with lexical retrieval.
- Parsed the critical fixture files with the local reference parser; did not
  run the full upstream build/evaluation pipeline.
- Inspected only the two explicitly allowed run IDs listed above.
- Ran the existing focused suites: `test_retrieval.py`, `test_parity.py`,
  `test_remaining.py`, `test_vectors.py`, and `test_tools.py`: **60 passed**.

For this evaluation-plan revision, primary-source research and public dataset
metadata inspection were performed. No benchmark harness, new live solve,
hidden-test evaluation, or measured quality improvement has been implemented
or executed. Source links and Markdown are checked separately from runtime tests.

Open execution decisions, not blockers to implementing the harness:

1. Which user-approved repositories and issue IDs populate the pilot/held-out
   manifests? Start adapters with the pinned public candidates and supplied
   Python repos; freeze the actual selection before execution.
2. What acceptable hosted embedding build cost/time budget should the large
   benchmark use? Existing 2,000-node and five-minute defaults are not a claim
   of large-repository capacity.
3. Is later in-run graph refresh required beyond accepted-base parity? This
   plan preserves the current base-only boundary and handles stale facts;
   mutable or run-local overlay indexing needs explicit design approval.
4. What live experiment spend cap is approved? F2's proposed 48/600 solves are
   workload estimates, not authorization. Implementation defaults to offline
   and dry-run behavior until an explicit experiment is approved.
