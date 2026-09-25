import pytest

from evals.retrieval.metrics import calculate_metrics, summarize
from evals.retrieval.models import IssueEvaluation


def _record(number: int, metric, status: str = "judged") -> IssueEvaluation:
    return IssueEvaluation(
        number=number,
        issue_id=f"issue_{number}",
        issue_file=f"issue-{number}.md",
        issue_sha256="0" * 64,
        status=status,
        gold_files=("gold.py",),
        metrics=metric,
    )


def test_user_noise_example() -> None:
    gold = {"a.py", "b.py", "c.py"}
    raw = {*gold, *(f"noise-{index}.py" for index in range(13))}
    accepted = {*gold, "noise-1.py", "noise-2.py"}

    metric = calculate_metrics(
        gold_files=gold,
        raw_files=raw,
        accepted_files=accepted,
        final_files=accepted,
    )

    assert metric.noise_before_pct == pytest.approx(81.25)
    assert metric.noise_after_jev_pct == pytest.approx(40)
    assert metric.noise_reduction_pp == pytest.approx(41.25)
    assert metric.noise_files_removed == 11
    assert metric.retain_pct == 100
    assert metric.retrieved_correct_survival_pct == 100


def test_two_correct_drops_out_of_eight_retain_seventy_five_percent() -> None:
    gold = {f"correct-{index}.py" for index in range(8)}
    raw = {*gold, *(f"noise-{index}.py" for index in range(8))}
    accepted = {*list(gold)[:6], "noise-0.py", "noise-1.py"}

    metric = calculate_metrics(
        gold_files=gold,
        raw_files=raw,
        accepted_files=accepted,
        final_files=accepted,
    )

    assert metric.retain_pct == 75
    assert metric.retrieved_correct_survival_pct == 75


def test_empty_and_unavailable_results_keep_metrics_undefined() -> None:
    empty = calculate_metrics(
        gold_files={"a.py"}, raw_files=(), accepted_files=(), final_files=()
    )
    unavailable = calculate_metrics(
        gold_files={"a.py"}, raw_files={"noise.py"}, accepted_files=None, final_files=None
    )

    assert empty.noise_before_pct is None
    assert empty.noise_after_jev_pct is None
    assert empty.retain_pct is None
    assert empty.raw_correct_recall_pct == 0
    assert empty.post_jev_correct_recall_pct == 0
    assert unavailable.noise_before_pct == 100
    assert unavailable.noise_after_jev_pct is None
    assert unavailable.retain_pct is None


def test_macro_average_does_not_pool_files_and_has_explicit_eligibility() -> None:
    first = calculate_metrics(
        gold_files={"a.py"}, raw_files={"a.py", "x.py"}, accepted_files={"a.py"}, final_files={"a.py"}
    )
    second = calculate_metrics(
        gold_files={"b.py"}, raw_files={"b.py", "x.py", "y.py", "z.py"},
        accepted_files={"b.py", "x.py", "y.py"}, final_files={"b.py", "x.py", "y.py"},
    )
    empty = calculate_metrics(
        gold_files={"c.py"}, raw_files=(), accepted_files=(), final_files=()
    )

    summary = summarize((_record(1, first), _record(2, second), _record(3, empty, "no_candidates")))

    assert summary.average_noise_reduction_pp.mean == pytest.approx((50 + (75 - 2 / 3 * 100)) / 2)
    assert summary.average_noise_reduction_pp.eligible == 2
    assert summary.average_noise_reduction_pp.excluded == 1
    assert summary.average_raw_correct_recall_pct.eligible == 3
