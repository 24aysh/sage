"""Read-only Admission context persistence, validation, and tools."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from langchain_core.tools import BaseTool, tool

from sage.artifacts.v2 import V2ArtifactStore
from sage.domain.admission import (
    AdmissionContextSnapshot,
    AdmissionRequirement,
    AdmissionResult,
    EvidenceReference,
    EvidenceSourceType,
    RepositoryEvidenceInput,
    ResearchEvidenceInput,
)
from sage.domain.runtime import RuntimeContext
from sage.errors import AgentRuntimeError, RepositoryError
from sage.repository.paths import resolve_workspace_path
from sage.research.models import ResearchRole, ResearchSourceType
from sage.research.service import ResearchService
from sage.research.tools import build_research_tools
from sage.runtimes.repository_tools import build_repository_read_tools

_CLARIFICATION_ROUND = re.compile(r"sage-clarification:v1\s+round=([0-9]+)")


class AdmissionContextSession:
    """Controller-owned state for one immutable Admission evidence snapshot."""

    def __init__(
        self,
        *,
        context: RuntimeContext,
        issue_text: str,
        artifacts: V2ArtifactStore,
        research: ResearchService,
    ) -> None:
        self._context = context
        self._issue_digest = _digest_text(issue_text)
        self._artifacts = artifacts
        self._research = research
        self._saved: AdmissionContextSnapshot | None = None

    @property
    def saved(self) -> AdmissionContextSnapshot | None:
        return self._saved

    def save(
        self,
        *,
        summary: str,
        requirements: tuple[AdmissionRequirement, ...],
        relevant_paths: tuple[str, ...],
        relevant_symbols: tuple[str, ...],
        repository_conventions: tuple[str, ...],
        candidate_verification_commands: tuple[str, ...],
        assumptions: tuple[str, ...],
        open_questions: tuple[str, ...],
        repository_evidence: tuple[RepositoryEvidenceInput, ...],
        research_evidence: tuple[ResearchEvidenceInput, ...],
    ) -> AdmissionContextSnapshot:
        if self._saved is not None:
            raise RepositoryError("Admission context has already been saved.")
        evidence = self._resolve_evidence(repository_evidence, research_evidence)
        created_at = datetime.now(UTC)
        payload = {
            "version": 1,
            "base_sha": self._context.prepared_run.base_sha,
            "issue_digest": self._issue_digest,
            "summary": summary,
            "requirements": requirements,
            "relevant_paths": relevant_paths,
            "relevant_symbols": relevant_symbols,
            "repository_conventions": repository_conventions,
            "candidate_verification_commands": candidate_verification_commands,
            "assumptions": assumptions,
            "open_questions": open_questions,
            "evidence": evidence,
            "created_at": created_at,
        }
        draft = AdmissionContextSnapshot.model_construct(
            **payload,
            digest="0" * 64,
        )
        snapshot = AdmissionContextSnapshot(
            **payload,
            digest=draft.calculate_digest(),
        )
        self._artifacts.write_admission_context(snapshot)
        self._artifacts.write_admission_context_summary(snapshot)
        self._saved = snapshot
        return snapshot

    def validate(self, issue_text: str) -> AdmissionContextSnapshot:
        snapshot = self._saved
        if snapshot is None:
            raise AgentRuntimeError("Admission finished without saving context.")
        if self._context.repository.get_head_sha() != snapshot.base_sha:
            raise AgentRuntimeError("Admission context base SHA is stale.")
        if _digest_text(issue_text) != snapshot.issue_digest:
            raise AgentRuntimeError("Admission context Issue digest is stale.")
        if snapshot.calculate_digest() != snapshot.digest:
            raise AgentRuntimeError("Admission context artifact digest is invalid.")
        for item in snapshot.evidence:
            if item.source_type is not EvidenceSourceType.REPOSITORY:
                continue
            path = resolve_workspace_path(
                self._context.prepared_run.workspace_dir,
                item.locator,
            )
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise AgentRuntimeError("Admission repository evidence is unavailable.") from error
            if digest != item.content_digest:
                raise AgentRuntimeError("Admission repository evidence became stale.")
        return snapshot

    def _resolve_evidence(
        self,
        repository_items: tuple[RepositoryEvidenceInput, ...],
        research_items: tuple[ResearchEvidenceInput, ...],
    ) -> tuple[EvidenceReference, ...]:
        evidence: list[EvidenceReference] = []
        identifiers: set[str] = set()
        for item in repository_items:
            if item.evidence_id in identifiers:
                raise RepositoryError("Admission evidence IDs must be unique.")
            identifiers.add(item.evidence_id)
            path = resolve_workspace_path(
                self._context.prepared_run.workspace_dir,
                item.path,
            )
            try:
                content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as error:
                raise RepositoryError(f"Unable to hash evidence file: {item.path}") from error
            excerpt = self._context.repository.read_file(
                path=item.path,
                start_line=item.line_start,
                end_line=item.line_end,
            )
            evidence.append(
                EvidenceReference(
                    evidence_id=item.evidence_id,
                    source_type=EvidenceSourceType.REPOSITORY,
                    title=item.title,
                    locator=item.path,
                    excerpt=excerpt[:4_000],
                    content_digest=content_digest,
                    line_start=item.line_start,
                    line_end=item.line_end,
                    authoritative=True,
                )
            )
        for item in research_items:
            if item.evidence_id in identifiers:
                raise RepositoryError("Admission evidence IDs must be unique.")
            result = self._research.get_result(item.result_id)
            if result is None:
                raise RepositoryError("Admission referenced an unknown research result.")
            identifiers.add(item.evidence_id)
            evidence.append(
                EvidenceReference(
                    evidence_id=item.evidence_id,
                    source_type=(
                        EvidenceSourceType.OFFICIAL_DOCUMENTATION
                        if result.source_type is ResearchSourceType.OFFICIAL_DOCUMENTATION
                        else EvidenceSourceType.WEB
                    ),
                    title=result.title,
                    locator=result.url,
                    excerpt=(result.content or result.snippet)[:2_000],
                    content_digest=result.content_digest,
                    detected_version=result.detected_version,
                    fetched_at=result.fetched_at,
                    authoritative=result.authoritative,
                )
            )
        return tuple(evidence)


def build_admission_tools(
    context: RuntimeContext,
    session: AdmissionContextSession,
    research: ResearchService,
) -> list[BaseTool]:
    """Build Admission's repository-read, research, and context-save tools."""

    @tool
    async def save_admission_context(
        summary: str,
        requirements: list[AdmissionRequirement],
        relevant_paths: list[str],
        relevant_symbols: list[str],
        repository_conventions: list[str],
        candidate_verification_commands: list[str],
        assumptions: list[str],
        open_questions: list[str],
        repository_evidence: list[RepositoryEvidenceInput],
        research_evidence: list[ResearchEvidenceInput],
    ) -> str:
        """Persist complete reusable evidence before returning AdmissionResult."""

        snapshot = session.save(
            summary=summary,
            requirements=tuple(requirements),
            relevant_paths=tuple(relevant_paths),
            relevant_symbols=tuple(relevant_symbols),
            repository_conventions=tuple(repository_conventions),
            candidate_verification_commands=tuple(candidate_verification_commands),
            assumptions=tuple(assumptions),
            open_questions=tuple(open_questions),
            repository_evidence=tuple(repository_evidence),
            research_evidence=tuple(research_evidence),
        )
        return f"Saved Admission context ({snapshot.digest})."

    return [
        *build_repository_read_tools(context),
        *build_research_tools(research, role=ResearchRole.ADMISSION, allow_web=True),
        save_admission_context,
    ]


def validate_admission_result(
    result: AdmissionResult,
    *,
    session: AdmissionContextSession,
    issue_text: str,
) -> AdmissionContextSnapshot:
    snapshot = session.validate(issue_text)
    if result.context_digest != snapshot.digest:
        raise AgentRuntimeError("Admission result references the wrong context digest.")
    return snapshot


def render_admission_context(
    snapshot: AdmissionContextSnapshot,
    *,
    max_chars: int,
) -> str:
    """Render valid compact JSON, dropping least-priority excerpts when necessary."""

    payload = snapshot.model_dump(mode="json")
    evidence = list(payload.pop("evidence"))
    payload["evidence"] = []
    payload["evidence_omitted"] = len(evidence)
    for item in evidence:
        candidate = [*payload["evidence"], item]
        proposal = {
            **payload,
            "evidence": candidate,
            "evidence_omitted": len(evidence) - len(candidate),
        }
        rendered = json.dumps(proposal, indent=2, sort_keys=True)
        if len(rendered) > max_chars:
            break
        payload = proposal
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if len(rendered) > max_chars:
        raise AgentRuntimeError("Admission context metadata exceeds its safe input cap.")
    return rendered


def next_clarification_round(issue_text: str, *, maximum: int) -> int:
    prior = max((int(value) for value in _CLARIFICATION_ROUND.findall(issue_text)), default=0)
    return min(prior + 1, maximum)


def clarification_limit_reached(issue_text: str, *, maximum: int) -> bool:
    prior = max((int(value) for value in _CLARIFICATION_ROUND.findall(issue_text)), default=0)
    return prior >= maximum


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
