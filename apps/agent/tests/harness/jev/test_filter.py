"""Deterministic tests of batched file decisions, not paid model quality tests."""

import asyncio
from types import SimpleNamespace

import pytest

from sage.config import JevSettings
from sage.domain.memory import MemoryRetrievalItem, MemoryRetrievalResult, MemoryRetrievalStatus, MemoryRetrievalOutcome
from sage.domain.relevance import RelevanceDecision, RelevanceUnavailable
from sage.harness.context.tools import build_repository_read_tools
from sage.harness.jev.filter import RelevanceFilter


@pytest.fixture
def retrieval(tmp_path):
    items = tuple(MemoryRetrievalItem(rank=index, kind="Function", name=name,
        qualified_name=f"{path}::{name}", file_path=path, line_start=1, line_end=2,
        language="rust", score=10, signature=f"fn {name}()")
        for index, (path, name) in enumerate([
            ("orders.rs", "process"), ("orders.rs", "save"), ("noise.rs", "unrelated")], 1))
    return MemoryRetrievalResult(status=MemoryRetrievalStatus.USED,
        outcome=MemoryRetrievalOutcome.USEFUL_CONTEXT, summary="lexical", memory_file=tmp_path / "graph.db",
        indexed_sha="base", items=items, returned=3, total_candidates=3, context="unfiltered noise.rs",
        context_chars=19)


class Provider:
    model = "jev-1.13.0"
    capture = None

    def __init__(self, scores=(3, 0), confidence=.9, error=None):
        self.scores, self.confidence, self.error = scores, confidence, error
        self.requests, self.closed = [], False

    async def score_files(self, *, candidates, **kwargs):
        self.requests.append((candidates, kwargs))
        if self.error:
            raise self.error
        return RelevanceDecision(model=self.model,
            scores={c.id: self.scores[index] for index, c in enumerate(candidates)},
            confidences={c.id: self.confidence for c in candidates}, input_tokens=120, output_tokens=10)

    async def aclose(self):
        self.closed = True


def apply(retrieval, *, mode="on", provider=None, **kwargs):
    service = RelevanceFilter(settings=JevSettings(mode=mode, api_key="test"), provider=provider or Provider())
    return asyncio.run(service.apply(issue="Fix process", retrieval=retrieval, max_chars=4000, **kwargs))


def test_one_question_per_file_discards_all_items_in_rejected_file(retrieval):
    provider, records, reports = Provider(), [], []
    result = apply(retrieval, provider=provider, usage_recorder=records.append, report_writer=reports.append)
    assert len(provider.requests) == 1 and len(provider.requests[0][0]) == 2
    assert [item.name for item in result.items] == ["process", "save"]
    assert "noise.rs" not in result.context and "orders.rs" in result.context
    assert result.context_chars == len(result.context) <= 4000
    report = result.relevance_filter
    assert report.retained_files == ("orders.rs",) and report.rejected_files == ("noise.rs",)
    assert report.discarded_items == 1 and report.withheld_items == 0
    assert records[0].input_tokens == 120 and records[0].parent_tool_call is None
    assert reports[0]["policy"] == "file-relevance-v1"


@pytest.mark.parametrize("scores,confidence,retained", [((2, 2), .5, 3), ((1.99, 0), .9, 0), ((3, 3), .49, 0)])
def test_score_confidence_boundaries_and_all_rejected(retrieval, scores, confidence, retained):
    result = apply(retrieval, provider=Provider(scores, confidence))
    assert len(result.items) == retained
    if not retained:
        assert result.context == "" and result.status is MemoryRetrievalStatus.NO_MATCH
        assert result.outcome is MemoryRetrievalOutcome.RELEVANCE_REJECTED


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_off_skips_calls_and_shadow_keeps_original_candidates(retrieval, mode):
    provider = Provider()
    result = apply(retrieval, mode=mode, provider=provider)
    assert len(result.items) == 3
    assert len(provider.requests) == (1 if mode == "shadow" else 0)
    assert result.relevance_filter.discarded_items == 0
    assert result.relevance_filter.would_discard_files == (("noise.rs",) if mode == "shadow" else ())


@pytest.mark.parametrize("error", [RelevanceUnavailable("http_529"), TimeoutError()])
def test_failure_withholds_unjudged_memory_without_claiming_rejections(retrieval, error):
    records = []
    result = apply(retrieval, provider=Provider(error=error), usage_recorder=records.append)
    assert not result.items and not result.context
    assert result.relevance_filter.status == "unavailable"
    assert result.relevance_filter.discarded_items == 0
    assert result.relevance_filter.withheld_items == 3
    assert records[0].input_tokens is None


def test_cancellation_records_partial_usage_and_report_then_propagates(retrieval):
    records, reports = [], []
    with pytest.raises(asyncio.CancelledError):
        apply(retrieval, provider=Provider(error=asyncio.CancelledError()),
              usage_recorder=records.append, report_writer=reports.append)
    assert records[0].outcome == "cancelled" and records[0].latency_ms >= 0
    assert reports[0]["status"] == "cancelled"


def test_empty_and_deadline_budget_skip_inference(retrieval):
    provider = Provider()
    result = apply(retrieval.model_copy(update={"items": (), "returned": 0}), provider=provider)
    assert result.relevance_filter.status == "skipped" and not provider.requests
    result = apply(retrieval, provider=provider, remaining_seconds=0)
    assert result.relevance_filter.reason == "time_budget" and not provider.requests


def test_tool_schemas_and_explicit_reads_have_no_jev_hook():
    context = SimpleNamespace(repository=SimpleNamespace(
        read_file=lambda **kw: "1 | original source", search_text=lambda **kw: "original matches"))
    tools = {t.name: t for t in build_repository_read_tools(context)}
    for name in ("read_file", "search_text"):
        assert "exploration_goal" not in tools[name].args
    assert asyncio.run(tools["read_file"].ainvoke({"path": "any.rs"})) == "1 | original source"
    assert asyncio.run(tools["search_text"].ainvoke({"query": "anything"})) == "original matches"


def test_context_budget_omissions_are_not_model_rejections(retrieval):
    service = RelevanceFilter(settings=JevSettings(mode="on", api_key="test"), provider=Provider((3, 3)))
    result = asyncio.run(service.apply(issue="Fix process", retrieval=retrieval, max_chars=300))
    assert len(result.context) <= 300
    assert result.relevance_filter.discarded_items == 0
    assert result.relevance_filter.context_omitted_items == 3 - len(result.items)


def test_oversized_issue_withholds_without_truncating_or_calling_provider(retrieval):
    provider = Provider()
    service = RelevanceFilter(settings=JevSettings(mode="on", api_key="test"), provider=provider)
    result = asyncio.run(service.apply(issue="x" * 6001, retrieval=retrieval, max_chars=4000))
    assert not provider.requests and not result.items
    assert result.relevance_filter.reason == "issue_size"
    assert result.relevance_filter.withheld_items == 3


def test_shadow_failure_does_not_claim_actual_discards(retrieval):
    result = apply(retrieval, mode="shadow", provider=Provider(error=RelevanceUnavailable("timeout")))
    assert len(result.items) == 3
    assert result.relevance_filter.status == "unavailable"
    assert result.relevance_filter.discarded_items == result.relevance_filter.withheld_items == 0
