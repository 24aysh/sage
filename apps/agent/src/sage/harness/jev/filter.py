"""One bounded relevance decision between lexical retrieval and context delivery."""

import asyncio
import json
import logging
from collections.abc import Callable
from time import perf_counter

from sage.config import JevSettings
from sage.domain.memory import MemoryRetrievalOutcome, MemoryRetrievalResult
from sage.domain.relevance import FileCandidate, RelevanceProvider, RelevanceReport, RelevanceUnavailable
from sage.domain.usage import SemanticCallRecord
from sage.harness.memory.retrieval import select_context_files

logger = logging.getLogger(__name__)


def file_candidates(result: MemoryRetrievalResult) -> tuple[FileCandidate, ...]:
    """Group bounded graph locators, without source reads or model-generated paths."""
    grouped: dict[str, list[str]] = {}
    for item in result.items:
        grouped.setdefault(item.file_path, []).append(
            f"{item.language} {item.kind} {item.name}:{item.line_start} "
            f"{item.signature[:180]} reasons={','.join(item.reasons[:4])}"
        )
    return tuple(FileCandidate(id=f"f{index}", path=path, evidence="\n".join(rows)[:600])
                 for index, (path, rows) in enumerate(grouped.items()))


class RelevanceFilter:
    def __init__(self, *, settings: JevSettings, provider: RelevanceProvider | None) -> None:
        self.settings, self.provider = settings, provider

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()

    async def apply(
        self, *, issue: str, retrieval: MemoryRetrievalResult, max_chars: int,
        usage_recorder: Callable[[SemanticCallRecord], None] | None = None,
        report_writer: Callable[[dict], object] | None = None,
        remaining_seconds: float | None = None,
    ) -> MemoryRetrievalResult:
        candidates = file_candidates(retrieval)
        paths = tuple(c.path for c in candidates)
        selected = set(paths)
        report = RelevanceReport(mode=self.settings.mode, status="off", model=self.settings.model,
            candidate_files=paths, candidate_items=len(retrieval.items),
            score_threshold=self.settings.score_threshold,
            confidence_threshold=self.settings.confidence_threshold)
        decision = None
        started = perf_counter()
        attempted = False
        try:
            if self.settings.mode == "off":
                pass
            elif not candidates:
                report = report.model_copy(update={"status": "skipped", "reason": "no_candidates"})
            else:
                timeout = min(self.settings.timeout_seconds, remaining_seconds
                              if remaining_seconds is not None else self.settings.timeout_seconds)
                if timeout < .05:
                    raise RelevanceUnavailable("time_budget")
                if len(issue) > 6000:
                    raise RelevanceUnavailable("issue_size")
                if self.provider is None:
                    raise RelevanceUnavailable("provider_unavailable")
                attempted = True
                async with asyncio.timeout(timeout):
                    decision = await self.provider.score_files(
                        state={"issue": issue, "indexed_sha": retrieval.indexed_sha},
                        candidates=candidates, timeout=timeout,
                    )
                ids = {c.id for c in candidates}
                if set(decision.scores) != ids or set(decision.confidences) != ids:
                    raise RelevanceUnavailable("invalid_candidate_ids")
                selected = {c.path for c in candidates
                            if decision.scores[c.id] >= self.settings.score_threshold
                            and decision.confidences[c.id] >= self.settings.confidence_threshold}
                discarded = tuple(path for path in paths if path not in selected)
                report = report.model_copy(update={
                    "status": "filtered" if self.settings.mode == "on" else "shadow",
                    "scores": {c.path: decision.scores[c.id] for c in candidates},
                    "confidences": {c.path: decision.confidences[c.id] for c in candidates},
                    "rejected_files": discarded if self.settings.mode == "on" else (),
                    "would_discard_files": discarded if self.settings.mode == "shadow" else (),
                    "discarded_items": sum(i.file_path not in selected for i in retrieval.items)
                                       if self.settings.mode == "on" else 0,
                    "input_tokens": decision.input_tokens, "output_tokens": decision.output_tokens,
                })
        except (asyncio.CancelledError, KeyboardInterrupt):
            report = report.model_copy(update={"status": "cancelled", "reason": "cancelled",
                "withheld_files": paths, "withheld_items": len(retrieval.items)})
            raise
        except (RelevanceUnavailable, TimeoutError) as error:
            selected = set()
            report = report.model_copy(update={"status": "unavailable",
                "reason": error.reason if isinstance(error, RelevanceUnavailable) else "timeout",
                "withheld_files": paths if self.settings.mode == "on" else (),
                "withheld_items": len(retrieval.items) if self.settings.mode == "on" else 0})
        finally:
            report = report.model_copy(update={"latency_ms": (perf_counter() - started) * 1000})
            if attempted and usage_recorder is not None:
                usage_recorder(SemanticCallRecord(call_number=1, session=1, stage="solver-context",
                    policy=report.policy, model=self.settings.model, latency_ms=report.latency_ms,
                    outcome=report.status, input_tokens=decision.input_tokens if decision else None,
                    output_tokens=decision.output_tokens if decision else None))
            if report.status == "cancelled":
                self._publish(report, report_writer)
        effective_paths = selected if self.settings.mode == "on" else set(paths)
        result = select_context_files(retrieval, effective_paths, max_chars=max_chars,
            empty_outcome=MemoryRetrievalOutcome.RELEVANCE_UNAVAILABLE
                if report.status == "unavailable" else MemoryRetrievalOutcome.RELEVANCE_REJECTED
        ) if retrieval.items and not (
            self.settings.mode == "off" and retrieval.context_chars <= max_chars
        ) else retrieval
        retained = tuple(dict.fromkeys(i.file_path for i in result.items))
        report = report.model_copy(update={"retained_files": retained,
            "context_omitted_items": sum(i.file_path in effective_paths for i in retrieval.items) - len(result.items),
            "context_omitted_files": tuple(p for p in paths if p in effective_paths and p not in retained)})
        self._publish(report, report_writer)
        return result.model_copy(update={"relevance_filter": report,
            "duration_ms": retrieval.duration_ms + report.latency_ms})

    def _publish(self, report: RelevanceReport, writer: Callable[[dict], object] | None) -> None:
        value = report.model_dump(mode="json")
        logger.info("Jev relevance filter %s", json.dumps(value, ensure_ascii=True, separators=(",", ":")))
        if self.settings.capture and self.provider is not None and self.provider.capture:
            value["capture"] = self.provider.capture
        if writer is not None:
            writer(value)
