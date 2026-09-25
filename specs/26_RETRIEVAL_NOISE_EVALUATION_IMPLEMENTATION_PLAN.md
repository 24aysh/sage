# Repository Retrieval Noise Evaluation: Implementation Plan

Status: implemented on `feat/retrieval-noise-eval`; no live paid dataset run has
been performed.
Prepared and implemented: 26 September 2026, after the retrieval harness merge
into `main`.

## 1. Objective and deliverables

Measure whether Sage's Jev relevance filter removes irrelevant files from the
real lexical/graph retrieval shortlist while preserving the correct files
provided by the user. Each Issue gets one deterministic retrieval followed by
one production Jev filtering operation on that exact result.

Deliver:

1. A fresh evaluation implementation in a dedicated root `evals/` directory.
2. Removal of the existing navigation evaluation implementation and its obsolete
   tests, fixtures, and executable documentation references.
3. The command:

   ```bash
   make eval-retrieval REPO="/path/to/test/repo" \
     ISSUE="/path/to/issue_files" ISSUE_COUNT=10 \
     GRAPH="/path/to/graph.sqlite3"
   ```

4. Automatic loading of `correct.json` from the Issue directory.
5. A `tqdm` bar showing progress through the selected Issues.
6. Terminal averages for noise reduction and correct-file retention, with
   explicit denominators and failure counts.
7. An `evals.md` report containing aggregate and per-Issue metrics, plus enough
   machine-readable evidence to audit every result without another API call.

This phase evaluates file selection. It does not run Solver, Reviewer, Docker,
repository edits, verification commands, or PR publication. It introduces no
new ranking algorithm, Jev prompt, threshold policy, or automatic tuning.
Executing the evaluation command deliberately makes paid Jev API requests.
Writing this specification does not make any such request.

## 2. Inspected implementation and reuse map

Paths in the following table are relative to `apps/agent/src/sage/`.

| Existing owner | Reuse in evaluation |
| --- | --- |
| `harness/retrieval/service.py::RepositoryRetrievalService` | Validate repository/index provenance and retrieve real Issue context |
| `domain/retrieval.py::RetrievalBudgets` | Use and record the production shortlist limits |
| `harness/retrieval/ranking.py` | Keep exact/FTS ranking, diversity, graph expansion, usefulness threshold, and rendering |
| `harness/jev/filter.py::file_candidates` | Group multiple retrieved items into one candidate per file |
| `harness/jev/filter.py::RelevanceFilter.apply` | Apply the real production judgment, thresholds, timeout, and failure policy |
| `domain/relevance.py::RelevanceReport` | Read candidate, rejected, withheld, and context-budget-omitted files, scores, confidence, time, and usage |
| `composition.py::build_retrieval_service`, `build_relevance_filter` | Construct existing capabilities without building agents |
| `config.py::JevSettings.from_env` | Validate Jev configuration without Solver/Reviewer credentials |
| `artifacts/files.py` | Reuse atomic JSON/text file writes |

The active Make targets are `retrieval-build`, `retrieval-preview`, and
`retrieval-solve`; there is no target literally named `make-file`. The new target
belongs in the existing root `Makefile`.

Important current behavior:

- Retrieval is lexical ranking plus bounded graph expansion. This evaluation
  measures that production candidate generator, not a new pure-FTS-only variant.
- `RetrievalBudgets.max_results` defaults to **12 items**, which may represent
  fewer than 12 unique files. The user's 16-file example is a metric example;
  normal runs must report their actual shortlist sizes without changing defaults
  to manufacture 16 candidates.
- Enabled Jev uses a 50,000-character candidate staging budget. Standalone
  retrieval then renders a 12,000-character final context; solves normally use
  4,000. The initial shortlist and final rendered context are different stages.
- `RelevanceReport.retained_files` is recorded **after final rendering**. It
  therefore cannot, by itself, measure only Jev's filtering effect.
- The filter uses one batched request per nonempty shortlist, inclusive score
  and confidence thresholds, no retries, and the configured timeout capped at
  two seconds. Empty candidate sets skip inference.
- The existing service checks repository identity, ready state, and current
  `HEAD`; the evaluator must additionally validate the parser version and keep
  the same index snapshot throughout a run.

## 3. Dataset contract

### Files and ordering

```text
/path/to/issue_files/
  issue-1.md
  issue-2.md
  issue-3.md
  correct.json
```

`ISSUE_COUNT=N` means exactly `issue-1.md` through `issue-N.md`, in numeric order.
It must be a positive integer. Do not use lexicographic ordering, silently skip
missing numbers, or substitute other Issue filenames. Additional Issues and
additional valid gold keys outside the selected range are allowed and ignored.

The entire selected dataset is validated before any paid request. Each Issue
must be a readable, nonempty UTF-8 file. Use its full text for retrieval and Jev;
do not rewrite it, strip sections, or truncate it to make a request fit.

### Valid JSON representation

JSON requires quoted object keys and arrays for file lists:

```json
{
  "issue_1": [
    "path/to/file/main.py",
    "path/to/file/test_api.py"
  ],
  "issue_2": [
    "src/server.go",
    "src/server_test.go"
  ]
}
```

Map `issue-N.md` to `issue_N`. Each selected key must exist and contain a
nonempty array of strings. Reject duplicate JSON object keys, malformed JSON,
nulls, scalar strings, nested objects, and empty gold lists before evaluation.
There is no separate `CORRECT` argument.

### Path identity

Treat paths as case-sensitive, repository-relative POSIX file identities.
Normalize leading `./`, repeated `/`, and `.` segments consistently in both gold
labels and observed paths. Reject absolute/drive/UNC paths, backslashes, `..`
segments, NUL/control characters, empty paths, and globs. Preserve meaningful
spaces and Unicode; do not fuzzy-match basenames, strip prefixes heuristically,
follow symlinks, or case-fold paths. Deduplicate normalized gold paths and report
duplicate counts so they cannot inflate denominators.

Do not require every gold path to exist at the indexed base: a correct fix can
add a file. Record paths absent from the committed tree and paths with unsupported
grammars as coverage limitations. Keep them in the gold denominator; never
silently remove difficult labels. A typo may look like a new-file label, so the
report must list these paths for inspection.

### Label meaning and leakage

Gold files are the files expected to require modification. A file useful as
background may still count as noise under this benchmark. Name the metric
"noise relative to required-modification labels" in the report and do not claim
it measures all useful reasoning context.

Gold labels are used only by metric computation and report generation. Never
send them, their counts, their membership, or a proposed solution to retrieval
query construction or Jev. Only Issue text and the production retrieved metadata
may influence file selection.

All selected Issues are evaluated against one recorded repository `HEAD`.
Datasets requiring different pre-fix commits need separate runs/checkouts in this
first version. A checkout containing the solved implementations can bias results;
record the commit and dataset hashes so the benchmark base is inspectable.

## 4. Measurement boundaries

For Issue `i`, use sets of unique normalized file paths:

| Symbol | Meaning | Source |
| --- | --- | --- |
| `G` | User-provided correct files | `correct.json` |
| `B` | Raw files actually offered to Jev | Paths in the single raw `RetrievalResult.items`; cross-check with `report.candidate_files` |
| `J` | Files accepted by Jev, before final rendering | On a successful `filtered` report only: `B - set(report.rejected_files)` |
| `F` | Files actually present in the final rendered context | Paths in the returned filtered result; cross-check with `report.retained_files` |
| `D` | Correct files dropped by Jev | `(G ∩ B) - J` |
| `M` | Correct files missed by raw retrieval | `G - B` |
| `C` | Files accepted by Jev but omitted by rendering | `J - F` |

Require `F ⊆ J ⊆ B`, disjoint accepted/rejected sets, complete score/confidence
coverage for `B`, and agreement with the reported context omissions. Contract
mismatches are evaluation errors, not results to approximate.

Do not count symbol multiplicity, graph nodes, all potential FTS matches,
`total_candidates`, or repeated relationship references as retrieved files.
The primary population is the bounded file shortlist Jev actually judges.

Keep the three stages in evidence and reporting. Headline filter metrics compare
`B` with `J`. Final context metrics compare `B` with `F` in separate columns.
This avoids crediting Jev for deterministic context-budget pruning.

## 5. Metrics and averaging

Use full-precision arithmetic internally; format percentages to two decimal
places only when rendering. Undefined metrics are JSON `null` and Markdown `N/A`,
never `NaN`, infinity, or a fabricated zero.

### Noise and noise reduction

For a nonempty file set `S`:

```text
correct_count(S) = |G ∩ S|
noise_count(S)   = |S - G|
noise_pct(S)     = 100 × (1 - |G ∩ S| / |S|)

noise_before_pct      = noise_pct(B)
noise_after_jev_pct   = noise_pct(J)
noise_reduction_pp   = noise_before_pct - noise_after_jev_pct
noise_files_removed = |B - G| - |J - G|
```

The primary reduction is **percentage points**, not relative percent. Negative
values mean a worse proportion of noise and must remain negative. Also show
absolute irrelevant-file removals, because noise proportion can remain 100%
even when many irrelevant files are removed.

Optionally include the clearly named secondary measure:

```text
noise_removed_pct = 100 × noise_files_removed / |B - G|
```

It is undefined when raw retrieval has no noise. Do not call this the primary
noise-reduction measure or mix it with percentage-point differences.

### Correct-file retention

Implement the user's formula exactly, using all labeled correct files:

```text
retain_pct = 100 × (1 - |D| / |G|)
```

This measures the penalty for Jev dropping known-correct files. A correct file
missed by retrieval is in `M`, not `D`: Jev never saw it. Therefore this value
alone does not say how many correct files survive in the actual output.

Always report the complementary metrics alongside it:

```text
retrieved_correct_survival_pct = 100 × |G ∩ J| / |G ∩ B|
raw_correct_recall_pct         = 100 × |G ∩ B| / |G|
post_jev_correct_recall_pct    = 100 × |G ∩ J| / |G|
final_context_correct_recall_pct = 100 × |G ∩ F| / |G|
```

Survival is undefined if raw retrieval contains no correct files. This explicit
separation preserves the requested formula while exposing retrieval misses and
actual filter losses. Target retention/survival is 100%; report results as
observations, without an automatic pass threshold that would hide regression.

### Worked examples

| Case | Gold | Raw files / correct | Jev accepted / correct | Noise before | Noise after | Reduction | User retain | Retrieved-correct survival |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| User's noise example | 3 | 16 / 3 | 5 / 3 | 81.25% | 40.00% | 41.25 pp | 100.00% | 100.00% |
| Drops two of eight correct files | 8 | 16 / 8 | 8 / 6 | 50.00% | 25.00% | 25.00 pp | 75.00% | 75.00% |
| Retrieval missed four correct files | 8 | 10 / 4 | 4 / 3 | 60.00% | 25.00% | 35.00 pp | 87.50% | 75.00% |

In the first row, 11 irrelevant files were removed. In the last row, post-Jev
correct recall is only 37.50%, even though the user-formula retention is 87.50%.

### Empty, skipped, and failed cases

| Case | Required treatment |
| --- | --- |
| `B` empty after valid retrieval | `no_candidates`; no API call; noise and filter-effect metrics `N/A`; raw/post recall zero; exclude from filter averages |
| Valid judgment rejects all files (`J` empty) | Successful evaluation with `all_rejected`; after-noise and paired noise reduction `N/A`; retention and recall remain computable; survival is zero if raw correct files existed |
| `B` nonempty but contains no correct files | Noise before 100%; user retention 100% if filtering succeeds, with zero recall and survival `N/A` shown prominently |
| All accepted files omitted from `F` | Jev metrics remain valid; final-context noise `N/A`, final recall zero; record budget omissions |
| Timeout, request/response size failure, invalid answer, unavailable provider | `filter_unavailable`; preserve raw metrics; all filter-effect metrics `N/A`; withheld files are not Jev rejections |
| Retrieval unavailable/stale/corrupt | `retrieval_unavailable`; no Jev call and no quality metrics |
| Cancelled in-flight judgment | `interrupted`; no invented judgment or tokens; keep earlier completed results |

Do not report an empty result as zero-percent noise and therefore perfect
improvement. Count and list empty-output Issues so the exclusion cannot hide
over-aggressive filtering.

### Aggregate reporting

Use unweighted **macro averages over Issues**, not pooled file ratios:

- Average noise reduction: mean per-Issue `noise_reduction_pp` over successful
  judged Issues where both `B` and `J` are nonempty.
- The accompanying before/after average noise uses exactly that same paired
  subset, so subtracting the two means equals the mean reduction.
- Average `% retain`: mean `retain_pct` over successful judged Issues with
  nonempty `B`; include all-rejected Issues.
- Average retrieved-correct survival: mean survival over successful judged
  Issues where `G ∩ B` is nonempty, including all-rejected Issues as zero.
- Raw recall: report over every valid lexical result, including no-candidate
  Issues. Post-Jev recall: report over successful judgments and valid empty
  retrievals, with the population named explicitly.

Every average prints its eligible count, excluded count, and exclusion reasons.
If there are no eligible Issues, print `N/A (0 eligible)`. Also print requested,
processed, successfully judged, no-candidate, all-rejected, unavailable,
interrupted, and pending counts. Never average API failures as successful noise
removal. Summary status is `partial` when an operational failure or interruption
prevents a complete evaluation.

## 6. New directory and module ownership

```text
evals/
  __init__.py
  README.md
  retrieval/
    __init__.py
    __main__.py       # argument parsing, environment boundary call, exit policy
    dataset.py        # strict Issue/gold loading and path normalization
    models.py         # evaluation-only typed inputs, issue records, summary
    metrics.py        # pure set metrics and macro aggregation
    runner.py         # index snapshot, production retrieval/filter execution
    report.py         # atomic JSON/Markdown publication and terminal summary

apps/agent/tests/evals/
  __init__.py
  test_dataset.py
  test_metrics.py
  test_runner.py
  test_report.py
```

Evaluation contracts remain under `evals/`; do not add benchmark fields to
production domain contracts. `evals` may import Sage; Sage must not import
`evals`. Module initializers contain only package descriptions. Use existing
Pydantic, standard-library sets/statistics/hashlib, and the existing factories.

Use `python -m evals.retrieval` from the repository root with the existing agent
environment. Use `python -m pytest` from the root for evaluation tests so the root
package is importable; update the canonical Make test invocation accordingly,
keeping its existing test path and configuration. Do not install an editable
second application or add ad hoc `sys.path` mutations.

`tqdm` already exists transitively in `apps/agent/uv.lock`. Declare it explicitly
in an `eval` dependency group because this code imports it directly; retain the
existing locked version where possible. Include the group for `eval-retrieval`
and the offline test gate. Do not add a benchmark framework or TypeSafe SDK.

## 7. Remove the old evaluation surface first

Before implementing the replacement, delete these tracked files:

- `apps/agent/evals/navigation.py`
- `apps/agent/evals/navigation-manifest.example.json`
- `apps/agent/tests/harness/jev/test_evaluation.py`

Remove active replay/run/compare recipes referencing those files from
`docs/testing.md` and update the evaluator paragraph in `docs/architecture.md`.
Mark older specification claims as historical where necessary. Keep production
Jev transport, production capture/accounting, filter tests, graph tests, and
solve artifacts: those are shared runtime capabilities, not obsolete evaluation
code. Do not delete user datasets or existing run artifacts.

Search references before removal and verify no executable import, Make target,
or active testing recipe depends on the deleted evaluator. Git history retains
the old full-solve comparison implementation; no compatibility launcher is
needed for this requested fresh start.

## 8. Execution pipeline

### Preflight and index snapshot

1. Validate all four required CLI inputs and the full selected dataset.
2. Load only Jev settings. This command explicitly evaluates active filtering:
   pass an environment mapping with `SAGE_JEV_NAVIGATION_MODE=on` into the
   existing `JevSettings.from_env`, even if local solve mode is `off` or `shadow`.
   Log the effective evaluation mode. Do not mutate the caller's environment or
   `.env`; preserve configured model, thresholds, timeout, and existing retired
   setting validation. A missing `TYPESAFE_API_KEY` fails before any run.
3. Check `REPO` is its Git root and has a committed `HEAD`. Validate `GRAPH` is a
   readable ready SQLite graph for that repository and SHA using the service.
   Read/check schema and parser versions against production constants. Do not
   build, update, repair, or clear the supplied graph automatically.
4. Create a unique evaluation output directory and use SQLite's read-only source
   connection plus backup API to copy a consistent graph into that directory.
   Revalidate the copy's provenance, then use that snapshot for every Issue.
   This handles committed WAL content and prevents an external graph writer from
   changing the evaluated corpus mid-run. Do not copy just the `.sqlite3` bytes.
5. Record repository SHA/identity, schema/parser versions, dataset/Issue hashes,
   Sage implementation SHA and dirty state, index metadata, and effective budgets
   and thresholds. Record the snapshot's file hash after creation. Detect a change
   to `REPO` HEAD before subsequent Issues and abort with a partial report.

The supplied index must already exist; error guidance points to:

```bash
make retrieval-build REPO="/path/to/test/repo" \
  INDEX_FILE="/path/to/graph.sqlite3"
```

Dirty target worktree content is ignored because indexing/retrieval is based on
committed source. Record its dirty state so users can diagnose a mistaken base.
Gold paths and Issue files are read once into immutable per-run inputs.

### Per-Issue loop

Process sequentially in numeric order with one event loop and one reusable Jev
client for the run; always close it in `finally`. Concurrency and repeated trials
are outside this version's scope.

For each selected Issue:

1. Call `retrieve_issue_context` once with production defaults and
   `RetrievalBudgets(max_chars=50_000)`. Preserve the immutable raw result and
   derive `B` from its items.
2. If retrieval is unavailable, save its failure record without calling Jev.
   If the shortlist is empty, save `no_candidates` and zero coverage.
3. Otherwise call the existing `RelevanceFilter.apply` with `max_chars=12_000`,
   matching the standalone retrieval command. Save the returned result/report.
   Do not call the CLI or shell out to `retrieval-preview`: its adjacent output
   files would collide across Issues and it would discard the raw observation.
4. On a successful `filtered` report derive `J` from explicit rejections and `F`
   from output items, checking the invariants in section 4. Do not implement a
   second score/confidence threshold evaluator. `off` or `shadow` is a contract
   error for this evaluation command, not a valid success.
5. Compute metrics against `G`, write an Issue record atomically, update the
   aggregate report atomically, and advance the progress bar once.

The baseline is the raw result of this same retrieval. Do not rerun an `off`
arm with different context limits, retrieve more candidates after a rejection,
batch unrelated Issues into one Jev judgment, or increase request limits.

Record lexical time, Jev time, actual API input/output tokens, and attempts.
Unknown usage remains unknown. These are diagnostic fields, not claims about
Solver latency or dollar savings. No credential/config dumps are persisted.

Expected per-Issue failures are recorded and evaluation continues with the next
Issue; preserve the no-retry policy. Abort on shared invalid index/configuration,
authentication/authorization failures (using report reasons such as `http_401`
and `http_403`), report-write failures, contract inconsistencies, or unexpected
programming errors. Remaining rows are `not_run` with a reason. A rate limit or
timeout is a failure, not an opportunity to score withheld files as rejections.

Issues exceeding the current 6,000-character Jev limit remain unchanged and are
reported as filter unavailable. The 16,000-byte request and 64,000-byte response
limits continue to be enforced by the production provider. Expose exclusion
counts so size restrictions do not silently bias the aggregate.

## 9. Progress, output, and interruption

### Progress bar

Use `tqdm(total=ISSUE_COUNT, unit="issue", desc="Retrieval evaluation")` on stderr.
Advance after each terminal per-Issue record, including no-candidate and failed
attempts. Show the current Issue, successful judgments, and failures in the
postfix. Display ETA from completed attempts; do not fabricate progress during
an in-flight call. Noninteractive runs still receive periodic readable progress
and the final summary without terminal escape clutter.

Use `tqdm`'s logging redirection for existing Sage/Jev logs so the bar remains
readable. Preserve explicit `SAGE_JEV_LOG_INPUT`/capture choices and recommend
`false` in the testing guide for ordinary batches. Default reports omit Issue
body, source text, raw HTTP headers, and provider captures. If capture is
explicitly enabled, keep it in a separate local per-Issue artifact, never embedded
in Markdown or the aggregate JSON. The report must still contain all paths and
numeric judgments needed to audit the metrics.

### Output location and artifacts

Default output is a unique directory under
`.sage/evals/retrieval/<UTC-timestamp>-<unique-id>/`. Print the exact path at start
and completion. Add `.sage/evals/` to `.gitignore`. Support the existing optional
Make `OUTPUT_DIR` variable as `--output-dir`; refuse a nonempty destination and
validate that writes cannot overlap the Issue directory or source graph. Reject
input datasets/graphs inside the reserved evaluation output namespace. Prefer an
output location outside the target checkout; when evaluating Sage itself, its
ignored `.sage/evals/` directory is allowed because it does not change committed
source. Do not overwrite tracked files, `correct.json`, Issue markdown, or the
user-supplied graph.

```text
<output>/
  evals.md
  results.json
  graph.sqlite3             # immutable evaluation snapshot
  issues/
    issue-1.json
    issue-2.json
```

`results.json` is versioned (`format_version: 1`) and contains run identity,
sanitized effective settings, provenance/hashes, counts, metric means and
eligibility, and every selected Issue's status. Issue records include raw items,
candidate/accepted/final paths, rejected/withheld/omitted paths, gold paths,
retrieval misses, correct drops, scores/confidences, statuses/reasons, and time/
usage. Label scores by the production policy version. Preserve raw and filtered
observations so all metrics can be independently recalculated.

`evals.md` contains:

- Run status, dataset/base/index identity, budgets, model, score/confidence
  thresholds, start/end times, and measurement/label definitions.
- Required average noise reduction in percentage points and average user-formula
  `% retain`, with eligible and excluded counts.
- Retrieved-correct survival, coverage, noise files removed, all-rejected counts,
  and operational failures, so the headline cannot hide loss of correct files.
- One row per selected Issue in numeric order: status, gold count, raw
  total/correct, Jev total/correct, before/after noise, reduction, correct drops,
  user retention, survival, raw/post recall, final-context totals/coverage, Jev
  latency, and tokens. Split into two adjacent tables if needed for readability.
- Per-Issue path lists showing missed correct files, correct files dropped by
  Jev, irrelevant files removed, and context-budget omissions; link to the JSON
  evidence. Escape Markdown and terminal control content safely.

Terminal output uses the same aggregation functions as Markdown/JSON. Example
for a dataset containing only the user's first example:

```text
Average noise reduction: 41.25 percentage points (1 eligible, 0 excluded)
Average retain: 100.00% (1 eligible, 0 excluded; denominator: all gold files)
Average retrieved-correct survival: 100.00% (1 eligible, 0 excluded)
Irrelevant files removed: 11
Report: <output>/evals.md
```

### Cancellation and exit status

Write an initial running report with pending rows, then checkpoint completed
Issue records and reports after each Issue. On Ctrl-C, save the current Issue as
interrupted, remaining Issues as not run, print the partial summary, close the
provider and progress bar, and exit nonzero. Do not restart or replay completed
Issues automatically. No resume feature is required in this version.

Direct Python exits: `0` when all selected Issues reach valid terminal outcomes
(including no-candidate/all-rejected); `1` for operational or persistence failures;
`2` for invalid arguments/dataset/preflight; `130` for handled Ctrl-C. Make may
normalize nonzero child exits to its own error status and must not treat them as
a successful benchmark. Quality regression alone does not change the exit code;
the command measures rather than enforces a quality threshold.

## 10. Make and dependency integration

Add `GRAPH ?=` and `ISSUE_COUNT ?=` and `.PHONY: eval-retrieval`. Reuse the
existing `REPO`, `ISSUE`, `ENV_FILE`/`ENV_PATH`, and optional `OUTPUT_DIR` variables.
In this target only, `ISSUE` is a directory. All values must be passed as quoted
arguments; paths with spaces are supported.

The recipe follows the existing trusted `.env` loading convention, disables
LangSmith tracing for the benchmark, and invokes from the Sage repository root:

```bash
uv run --project apps/agent --group eval python -m evals.retrieval \
  --repo "$repo" --issues-dir "$issues_dir" \
  --issue-count "$issue_count" --graph "$graph"
```

The shell names above describe already-validated Make arguments, not new required
environment settings. Add `--output-dir` only when explicitly provided. Keep
Python validation authoritative, including integer/count/file checks. Do not
delegate to `solve`, require Docker or generative-model credentials, silently
build the graph, or write fixture data in this recipe. Document that Jev filtering
is explicitly active for this command even when the solve default in `.env` is
off; model/threshold/timeout configuration remains shared with production.

Add the new command to `make help`, evaluation package README, and
`docs/testing.md`. Update `docs/architecture.md` only for the new evaluation owner
and the removal of the old evaluator, keeping current runtime behavior accurate.

## 11. Implementation sequence and checkpoints

### A. Remove obsolete evaluator and establish the new owner

- Delete the exact files in section 7 and remove active references.
- Establish `evals/retrieval/`, typed evaluation contracts, CLI skeleton, and the
  explicit `eval` dependency group for tqdm.
- Ensure only evaluator code imports evaluation contracts.

Acceptance: old evaluator imports/commands are gone; `python -m evals.retrieval
--help` works in the agent environment without credentials.

### B. Dataset and pure metric implementation

- Implement strict dataset validation, numeric selection, normalization,
  deduplication, and immutable labels/Issue text.
- Implement the set-based metrics, explicit null cases, and macro averaging.
- Add exact worked-example and denominator tests before live integration.

Acceptance: the 16-to-5 example yields 81.25% → 40.00%, 41.25 pp reduction,
11 irrelevant files removed, and 100% retention/survival. Two correct drops out
of eight produce 75% user retention.

### C. Production replay integration

- Reuse index validation, current production budgets, atomic graph snapshot,
  factories, retrieval, and `RelevanceFilter.apply`.
- Derive `B`, `J`, and `F` from actual observations and enforce invariants.
- Add status mapping, no-candidate handling, per-Issue failures, usage/timing,
  shared-failure termination, and provider cleanup.

Acceptance: a real temporary Git repository and index plus a fake Jev transport
exercise the full path. Gold labels never appear in model input. One retrieval
and at most one request happen per Issue; no Solver/Reviewer call happens.

### D. Reports, progress, and command

- Add atomic Issue records, aggregate JSON/Markdown, progress-compatible logging,
  interruption checkpoints, and exact output paths.
- Add the Make target/help and optional output directory support.
- Update documentation and the specification status after implementation.

Acceptance: the requested Make command processes numbered Issues in order,
shows progress, prints both requested averages, and produces a complete or
explicitly partial `evals.md` without touching input files.

### E. Verification and first real dataset

- Run focused offline evaluation tests, then `make check` and the existing
  required repository checks; record results in the implementation handoff.
- When repo, Issue directory, count, ready graph, and a configured TypeSafe key
  are supplied, run the explicit live evaluation command and inspect its real
  artifacts. Until then, report only offline verification, with no fabricated
  performance numbers or claim that Jev improves the supplied dataset.

## 12. Test plan

### Dataset and metrics

- Missing numbered file/key; malformed JSON; duplicate keys; wrong value types;
  empty labels; positive integer validation; numeric ordering including Issue 10.
- Gold/path deduplication; canonical `./` paths; case-sensitive distinctions;
  traversal/absolute/glob rejection; new files absent from the committed base.
- User's two examples and retrieval misses before filtering.
- Multiple symbols in one file count once; one path in multiple Issues counts
  independently in each Issue's metrics.
- Keep-all, drop-all, no raw matches, no correct raw files, initially zero noise,
  increased noise after losing correct files, and final-budget-only omissions.
- Macro vs pooled averages differ on unequal shortlist sizes; eligible/excluded
  populations and paired before/after populations are correct.

### Pipeline and reliability

- Real lexical/graph retrieval over a temporary committed repository; mock only
  the external Jev transport. Check raw candidate membership and metadata parity
  with production `retrieval-preview`'s enabled profile.
- Wrong repository, SHA, parser/schema, missing/corrupt index fail before paid
  work; source graph content remains intact; SQLite snapshot includes WAL data.
- Gold-label isolation, one request per Issue, shared client cleanup, and default
  `off` solve configuration overridden to evaluation `on` explicitly.
- Threshold boundaries use the real filter; distinguish rejected, withheld,
  final-budget-omitted, and never-retrieved correct files.
- Timeout, malformed response, auth failure, rate limit, oversize Issue/request,
  missing usage, cancellation, and contract mismatch retain honest denominators.
- No construction or invocation of a Solver, Reviewer, sandbox, or live solve
  operation occurs. The existing composition module imports role classes at
  module scope; that does not authorize constructing them. No network is used
  in normal tests.

### Reports and CLI

- All selected Issues appear, including failed and pending rows; Markdown and
  JSON agree with pure metrics; escaping does not break tables or terminals.
- Correct tqdm totals/updates for success, skip, failure, and interruption;
  progress/log output does not corrupt report data.
- Atomic incremental output, output collisions, read-only destinations, partial
  reports, no overwriting input data, and no raw credential/config leakage.
- Make invocation with paths containing spaces, missing inputs, `ENV_FILE`,
  automatic `correct.json`, nonzero failures, and optional `OUTPUT_DIR`.

## 13. Design rationale and limits

Production reuse prevents the evaluator from judging a different selection
algorithm than the one Sage uses. Fresh evaluation orchestration replaces the
old solve-comparison framework while retaining the existing runtime components.
One candidate snapshot and one Jev call per Issue provide a paired comparison
without doubling model cost. Sequential execution bounds resource use and makes
failure attribution clear; concurrency can be measured separately later.

Explicitly preserving the user's retention denominator and also displaying
retrieved-correct survival/recall avoids silently changing the requested metric.
Null handling for empty sets prevents rejecting every file from looking like
perfect noise reduction. Separating final rendering omissions keeps the claimed
filter effect attributable to Jev.

Labels based on modified files are a useful reproducible proxy, but cannot prove
all remaining context is useless or that Solver quality improves. This first
evaluation records file-selection effectiveness, API usage, and Jev latency.
End-to-end token savings, solve success, cost savings, repeated-trial uncertainty,
per-Issue base selection, alternate shortlist budgets, and threshold optimization
are future evaluation scopes.

TypeSafe's current [Score documentation](https://docs.typesafe.ai/primitives/score),
[confidence documentation](https://docs.typesafe.ai/confidence), and
[RAG passage classification cookbook](https://docs.typesafe.ai/cookbooks/classifying_rag_passages)
informed the judgment boundary: production scores and confidence drive file
selection, while ordinary code computes all benchmark metrics from user labels.
Confidence describes the returned distribution; it is not a substitute for
measured retention. No vendor demonstration result is treated as Sage evidence.
