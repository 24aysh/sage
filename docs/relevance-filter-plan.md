# Lexical retrieval and Jev relevance filtering

## Scope and sequence

1. On the current branch, reuse the Tree-sitter language registry and graph
   retrieval for Go, Rust, C++, HTML, and CSS. Fix C++ declarator names and missing
   header suffixes; add actual symbol/path retrieval tests, not only grammar smoke
   tests. Preserve existing HTML/CSS relationships. No new parser dependency.
2. Create `feat/jev-relevance-filter` before changing Jev's pipeline. Carry the
   language improvements into that branch without committing user work.
3. Replace tool-triggered excerpt/action navigation with a single pre-context
   relevance filter shared by solve and `make legion-retrieve`.
4. Update configuration, artifacts, logging, evaluation, documentation, and tests;
   run the focused suites followed by canonical offline checks. Do not commit or
   make paid API calls without a separate request.

## Design

Keep lexical ranking and bounded graph expansion as deterministic candidate
generation. Reuse the existing retrieval items, file locators, signatures,
provenance checks, diversity selection, and context renderer. Group candidates
by file so one rejection consistently removes all retrieved items for that file.
Jev receives the Issue and bounded candidate metadata in one request, with one
independent Score question per file. Preserve lexical ordering among accepted
items; this is a relevance filter, not autonomous tool selection.

Reuse TypeSafe HTTP transport, authentication, payload/response limits, strict
score validation, token accounting, timeout handling, and optional input capture.
Use explicit relevant/unrelated score levels; configurable score and confidence
thresholds are policy, not probabilities of solve correctness. The TypeSafe skill
and live API, Score, confidence, and RAG classification docs informed this design:

- https://docs.typesafe.ai/api
- https://docs.typesafe.ai/primitives/score
- https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/cookbooks/classifying_rag_passages

Retain `SAGE_JEV_NAVIGATION_MODE=off|shadow|on` as the enable switch for migration,
but remove the old `excerpts`/`actions` policies and operation thresholds. `off`
is explicitly lexical-only; `shadow` records hypothetical filtering without
changing context; `on` supplies only accepted file items. Enabled modes reject
retired policy/action settings with migration guidance. Off mode ignores them
so the tools-only baseline remains usable.

No retries or repeated requests in the Solver loop. Reuse accepted locators for
repair histories, invalidating edited paths. Disable automatic structural
enrichment when filtering is active, so rejected candidates cannot immediately
reappear through a read/search side channel. Explicit repository/graph tools
remain Solver-controlled capabilities, not a Jev file-access allowlist.

## Failure, budgets, and accounting

- Skip inference for empty/unavailable retrieval and disabled mode.
- Enforce bounded candidates, Issue text, request bytes, response bytes, and time.
  Do not classify a budget omission as a model rejection.
- When filtering is on, timeout/invalid output/insufficient budget withholds
  unjudged memory, logs a visible fallback, and lets normal source tools continue.
  All-rejected is a valid empty context, distinct from unavailable filtering.
- Do not refill with rejected results to meet a context-size target. Re-render
  accepted items and remove relationship references to rejected candidates.
- Log candidate and accepted file paths, retrieved item/file counts, rejected
  counts, unjudged/withheld counts, latency, and actual token usage. Unknown usage
  stays unknown. Shadow rejections are hypothetical, not actual discards.
- Save structured evidence next to retrieval output and in solve artifacts.
  Reuse semantic-call accounting; solver time including/excluding Jev and Ctrl-C
  partial usage must include the pre-context call.

## Tradeoffs

One batched call removes network round trips from tool loops but still adds
startup latency. File-level scoring is cheaper and easier to audit than one
question per symbol, but may retain irrelevant symbols within an accepted file.
Metadata-only evidence avoids extra source reads; it can miss semantic relevance
that is not apparent from names/signatures/relationships. False rejection remains
possible, so explicit source inspection must remain unrestricted. No quality,
token, or latency improvement is claimed without paired live benchmarks.
Filtering and discard counts apply to the bounded retrieval shortlist, not every
lexical match in the repository.

## Verification

Test multilingual symbols and paths, C++ parameters/qualified declarators,
accepted/rejected/all-rejected files, score boundaries, malformed/missing IDs,
timeouts, cancellation, off/shadow modes, no tool-loop inference, repair reuse,
CLI/solve parity, deterministic rendering/budgets, and per-run token/time records.
Keep language changes separate from Jev changes when commits are later requested.

Status: implemented. Language support is committed on `main` (`1898810`);
`feat/jev-relevance-filter` includes that commit plus the pipeline replacement.

Verification completed:

- `make check`: 563 passed, 1 skipped (offline gate).
- `make graph`: passed.
- `make github-smoke`: passed with local substitutes, without paid model calls.
- `make github-doctor`: passed.
- `git diff --check`: clean.

The existing language registry, lexical/graph retrieval, rendering, HTTP
transport, and usage accounting were extended or reused; no dependency was added.
Live relevance quality, latency, and token savings still need paired benchmarks.
Pinned external GitHub action references are unchanged; publishing this branch's
behavior to consumers requires the normal release/pin update after review.
