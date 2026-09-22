"""Run-scoped optional navigation; code owns authority, deadlines and exposure."""

import asyncio
import json
import logging
import sqlite3
from pathlib import PurePosixPath
from time import monotonic
from collections.abc import Callable

from sage.domain.navigation import (NavigationProvider, NavigationUnavailable, ReadAction,
    SearchAction, GraphAction, SearchMatch, ActionCandidate, NavigationDecision)
from sage.domain.solver import SavedSolverPlan
from sage.domain.usage import SemanticCallRecord
from sage.errors import RepositoryError, LegionMemoryError
from sage.legion_memory.context import minimal_result, bounded_json
from sage.legion_memory.retrieval import extract_issue_signals
from sage.orchestration.navigation_candidates import (POLICY_VERSION, digest, fingerprint,
    numbered_lines, shortlist, render_observation, excerpt_selection, accept_action)
from sage.repository.snippets import source_identity
from sage.repository.filesystem import workspace_relative_path
from sage.orchestration.context import SolveContext
from sage.providers.calls import ModelCalls

logger = logging.getLogger(__name__)


class NavigationSession:
    """One controller for excerpts, one action, or two dependent actions."""

    def __init__(self, *, context: SolveContext, provider: NavigationProvider, calls: ModelCalls, issue: str,
                 plan: Callable[[], SavedSolverPlan | None], clock: Callable[[], float] = monotonic) -> None:
        self.context, self.provider, self.calls = context, provider, calls
        self.settings = context.settings.jev
        self.action_policy = self.settings.policy == "actions"
        self.issue, self.plan, self.clock = issue[:2400], plan, clock
        self.anchors = extract_issue_signals(self.issue, max_chars=2400).paths if self.issue.strip() else ()
        self.root = context.prepared_run.workspace_dir
        self.memory = context.memory
        self.session = self.requests = self.operations = self.total_chars = 0
        self.wait_seconds = 0.0
        self.failures = 0
        self.disabled = False
        self.sequence = 0
        self.records: list[dict] = []
        self.capture_bytes = 0
        self.invalidated: set[str] = set()
        self.graph_disabled = False
        self.begin_session(stage="solver")
        self.session = 0

    def begin_session(self, *, stage: str) -> None:
        self.stage = stage
        self.session += 1
        self.session_requests = self.session_operations = self.session_chars = 0
        self.visible: dict[tuple[str, str], set[int]] = {}
        self.attempted: set[str] = set()

    async def aclose(self) -> None:
        await self.provider.aclose()

    def invalidate(self, *paths: str) -> None:
        self.attempted.clear()
        if paths:
            affected = {workspace_relative_path(self.root, p) for p in paths}
            self.invalidated.update(affected)
            if self.memory is not None:
                self.memory.invalidate(*affected)
            self.visible = {key: value for key, value in self.visible.items() if key[0] not in affected}
        else:
            self.visible.clear()
            self.graph_disabled = True

    def _nodes(self) -> list[dict]:
        if not self.memory or not self.memory.tools_enabled or self.graph_disabled:
            return []
        return [i.model_dump() for i in self.memory.retrieval.items
                if i.file_path not in self.invalidated][:20]

    def _mark_visible(self, path: str, source: str) -> None:
        try:
            identity = source_identity(self.root, path)
            self.visible.setdefault((path, identity), set()).update(numbered_lines(source))
        except (RepositoryError, OSError, ValueError):
            pass

    def _remaining(self, deadline: float) -> float:
        return min(deadline - self.clock(), self.calls.remaining_navigation_seconds())

    def _record(self, **values) -> None:
        parent = self.calls.latest_tool_call
        if values.get("status") not in {"explicit_read", "step_limit"} and logger.isEnabledFor(logging.INFO):
            # Deliberate allowlist: source, action arguments and replay captures stay out of summaries.
            summary = {key: values[key] for key in ("status", "step", "candidate_count",
                "candidate_retrieval_ms", "retrieval_ms", "latency_ms", "navigation_ms",
                "structural_retrieval_ms", "input_tokens", "output_tokens", "selected", "operation",
                "exposed_chars", "disabled") if key in values}
            logger.info("Jev navigation %s", json.dumps({"run_id": getattr(self.context.prepared_run, "run_id", None),
                "session": self.session, "stage": self.stage, "sequence": self.sequence,
                "request": self.requests, "root_tool_call_id": parent.tool_call_id if parent else None,
                "mode": self.settings.mode, "policy": self.settings.policy, "model": self.provider.model,
                **summary}, ensure_ascii=True, separators=(",", ":")))
        if len(self.records) < 128:
            self.records.append({"sequence": self.sequence, "session": self.session, "stage": self.stage,
                "parent_tool_call": parent.call_number if parent else None,
                "root_tool_call_id": parent.tool_call_id if parent else None, **values})
        self.context.artifacts.write_navigation({"policy_version": POLICY_VERSION,
            "mode": self.settings.mode, "policy": self.settings.policy,
            "max_followup_actions": self.settings.max_followup_actions,
            "model": self.provider.model, "requests": self.requests, "operations": self.operations,
            "added_chars": self.total_chars, "jev_wait_seconds": self.wait_seconds,
            "records": self.records, "records_truncated": len(self.records) >= 128})

    async def _decide(self, state: dict, candidates: tuple[ActionCandidate, ...], deadline: float,
                      step: int) -> NavigationDecision | None:
        timeout = min(self.settings.timeout_seconds, self.settings.run_wait_seconds - self.wait_seconds,
                      self._remaining(deadline))
        if self.disabled or self.requests >= 8 or self.session_requests >= 4 or timeout < .05:
            self._record(step=step, status="decision_budget")
            return None
        self.requests += 1
        self.session_requests += 1
        started = self.clock()
        decision = None
        status = "failed"
        try:
            method = self.provider.choose_action if self.action_policy else self.provider.rank_excerpts
            async with asyncio.timeout(timeout):
                decision = await method(state=state, candidates=candidates, timeout=timeout)
            if not self.action_policy:
                decision = decision.model_copy(update={"selected": excerpt_selection(decision)})
            if not set(decision.selected) <= {c.id for c in candidates}:
                raise NavigationUnavailable("unknown_candidate")
            status = "decided"
            self.failures = 0
            return decision
        except (asyncio.CancelledError, KeyboardInterrupt):
            status = "cancelled"
            raise
        except (NavigationUnavailable, TimeoutError) as error:
            status = error.reason if isinstance(error, NavigationUnavailable) else "timeout"
            self.failures += 1
            self.disabled = self.failures >= 2 or getattr(error, "permanent", False)
            return None
        finally:
            elapsed = max(0.0, self.clock() - started)
            self.wait_seconds += elapsed
            parent = self.calls.latest_tool_call
            self.calls.record_semantic_call(SemanticCallRecord(call_number=self.requests,
                parent_tool_call=parent.call_number if parent else None, session=self.session,
                stage=self.stage, policy=POLICY_VERSION + ":" + self.settings.policy,
                model=self.provider.model, latency_ms=elapsed * 1000, outcome=status,
                input_tokens=decision.input_tokens if decision else None,
                output_tokens=decision.output_tokens if decision else None))
            capture = getattr(self.provider, "capture", None) if self.settings.capture else None
            if capture:
                capture = {**capture, "candidates": [c.model_dump() for c in candidates]}
            size = len(json.dumps(capture).encode()) if capture else 0
            if self.capture_bytes + size > 256_000:
                capture = None
            else:
                self.capture_bytes += size
            self._record(step=step, status=status, latency_ms=elapsed * 1000,
                input_tokens=decision.input_tokens if decision and decision.input_tokens is not None else "unknown",
                output_tokens=decision.output_tokens if decision and decision.output_tokens is not None else "unknown",
                selected=list(decision.selected) if decision else [], disabled=self.disabled,
                objective_digest=digest(state["goal"]), candidate_set_digest=digest(
                    json.dumps([c.model_dump() for c in candidates], sort_keys=True)),
                candidates=[{"id": c.id, "action": c.action.model_dump()} for c in candidates],
                decision=decision.model_dump() if decision else None,
                **({"capture": capture} if capture else {}))

    async def enrich(self, *, tool_name: str, source: str, path: str, query: str = "",
                     matches: tuple[SearchMatch, ...] = (), start_line: int = 1,
                     exploration_goal: str | None = None) -> str:
        started = self.clock()
        self.sequence += 1
        deadline = self.clock() + min(8.0, self.calls.remaining_navigation_seconds())
        cap = max(0, min(3000, self.context.settings.max_tool_output_chars - len(source),
                         16_000 - self.session_chars, 48_000 - self.total_chars))
        if tool_name == "read_file":
            path = workspace_relative_path(self.root, path)
            self._mark_visible(path, source)
            self._record(status="explicit_read", path=path, source_digest=digest(source), chars=len(source))
        addition = ""
        structural_ms = 0.0
        if self.memory is not None and not self.graph_disabled:
            structural_started = self.clock()
            addition = self.memory.enrich(tool_name=tool_name, source_chars=len(source), available_chars=cap,
                path=path if tool_name == "read_file" else None, query=query or None,
                start_line=start_line, end_line=max(numbered_lines(source), default=start_line))
            structural_ms = (self.clock() - structural_started) * 1000
        goal = (exploration_goal or "").strip() if self.action_policy else query
        eligible = (0 < len(goal) <= 600 and (self.action_policy or tool_name == "search_text"))
        if eligible and cap - len(addition) >= 300:
            addition += await self._explore(tool_name=tool_name, source=source, path=path, query=query,
                matches=matches, goal=goal, room=cap - len(addition), deadline=deadline)
        else:
            self._record(status="no_goal_or_output_budget")
        self.total_chars += len(addition)
        self.session_chars += len(addition)
        self._record(status="returned", exposed_chars=len(addition),
            navigation_ms=(self.clock() - started) * 1000, structural_retrieval_ms=structural_ms)
        return addition

    async def _explore(self, *, tool_name: str, source: str, path: str, query: str,
                       matches: tuple[SearchMatch, ...], goal: str, room: int, deadline: float) -> str:
        output = ""
        observations = []
        result_digests = {digest(source)}
        scope = path if tool_name == "search_text" else str(PurePosixPath(path).parent)
        nodes = self._nodes()
        max_steps = self.settings.max_followup_actions if self.action_policy else 1
        for step in range(1, max_steps + 1):
            if room - len(output) < 300 or self._remaining(deadline) < .05:
                self._record(step=step, status="sequence_budget")
                break
            if self.operations >= 8 or self.session_operations >= 4:
                self._record(step=step, status="operation_budget")
                break
            saved = self.plan()
            anchors = (*self.anchors, *(saved.plan.relevant_paths if saved else ()))
            candidate_started = self.clock()
            candidates, identities = shortlist(root=self.root, matches=matches, source=source,
                path=path, scope=scope, query=query, actions=self.action_policy, nodes=nodes,
                visible=self.visible, attempted=self.attempted, anchors=anchors)
            self._record(step=step, status="candidates", candidate_count=len(candidates),
                candidate_retrieval_ms=(self.clock() - candidate_started) * 1000)
            if len(candidates) < (1 if self.action_policy else 2):
                self._record(step=step, status="no_candidates")
                break
            state = {"policy_version": POLICY_VERSION, "issue": self.issue, "goal": goal,
                "query": query[:200], "source": source[:2200], "observations": observations,
                "plan": saved.model_dump_json()[:1400] if saved else None}
            decision = await self._decide(state, candidates, deadline, step)
            if decision is None or not decision.selected:
                if decision is None and output:
                    marker = "\n[navigation stopped: optional decision unavailable]"
                    output += marker if len(output) + len(marker) <= room else ""
                self._record(step=step, status="handback_or_failure")
                break
            chosen = [c for id in decision.selected for c in candidates if c.id == id]
            if self.action_policy:
                chosen = chosen[:1]
                if not accept_action(decision, chosen[0]):
                    self._record(step=step, status="uncertain")
                    break
            if self.settings.mode == "shadow":
                self._record(step=step, status="shadow_no_dispatch")
                break
            stop = False
            for candidate in chosen[:2]:
                action = candidate.action
                if (room - len(output) < 300 or self.operations >= 8 or self.session_operations >= 4
                        or self._remaining(deadline) < .05):
                    stop = True
                    break
                started = self.clock()
                try:
                    # Revalidate observations after inference, before touching selected capabilities.
                    if any(source_identity(self.root, p) != identity for p, identity in identities.items()):
                        raise RepositoryError("Navigation evidence changed.")
                    if self._remaining(deadline) < .05:
                        raise RepositoryError("Navigation deadline exhausted.")
                    self.operations += 1
                    self.session_operations += 1
                    self.attempted.add(fingerprint(action))
                    retrieval_started, retrieval_status = self.clock(), "retrieval_failed"
                    try:
                        result, new_matches, graph = self._dispatch(action, deadline, room - len(output))
                        retrieval_status = "retrieved"
                    finally:
                        self._record(step=step, status=retrieval_status, operation=action.kind,
                            retrieval_ms=(self.clock() - retrieval_started) * 1000)
                    result_id = digest(result)
                    if result_id in result_digests or not result.strip() or result == "[no matches]":
                        self._record(step=step, status="no_novel_evidence", action=action.model_dump())
                        stop = True
                        break
                    block = render_observation(action, result, room - len(output))
                    if not block:
                        self._record(step=step, status="result_not_exposed")
                        stop = True
                        break
                    output += block
                    result_digests.add(result_id)
                    observations.append({"action": action.model_dump(), "result": block[:1800]})
                    source = block
                    matches = tuple(m for m in new_matches if f"{m.path}:{m.line}:{m.column}:{m.text}" in block)
                    if graph is not None:
                        self.memory.record_tool_call("navigation:query_graph_tool", graph, (self.clock() - started) * 1000)
                        data = graph.get("data", {})
                        nodes = [n for n in data.get("results", data.get("nodes", []))
                                 if n.get("file_path") not in self.invalidated and "line_start" in n]
                    if isinstance(action, ReadAction):
                        path = action.path
                        self._mark_visible(path, block)
                    self._record(step=step, status="executed", action=action.model_dump(),
                        result_digest=result_id, exposed_chars=len(block), elapsed_ms=(self.clock() - started) * 1000)
                except (RepositoryError, LegionMemoryError, sqlite3.Error, OSError, ValueError):
                    marker = "\n[navigation stopped: optional observation unavailable]"
                    output += marker if len(output) + len(marker) <= room else ""
                    self._record(step=step, status="capability_failure", action=action.model_dump())
                    stop = True
                    break
            if stop:
                break
        else:
            self._record(step=max_steps, status="step_limit")
        return output

    def _dispatch(self, action: ReadAction | SearchAction | GraphAction, deadline: float,
                  room: int) -> tuple[str, tuple[SearchMatch, ...], dict | None]:
        repository = self.context.repository
        if isinstance(action, ReadAction):
            source_identity(self.root, action.path)
            return repository.read_file(path=action.path, start_line=action.start_line,
                                        end_line=action.end_line), (), None
        if isinstance(action, SearchAction):
            timeout = int(min(2, self._remaining(deadline)))
            if timeout < 1:
                raise RepositoryError("Insufficient time for internal search.")
            result = repository.search_matches(query=action.query, path=action.path,
                                               max_results=5, timeout_seconds=timeout)
            return result.text, result.matches, None
        if not isinstance(action, GraphAction) or not self.memory or not self.memory.tools_enabled or self.graph_disabled:
            raise RepositoryError("Graph navigation unavailable.")
        result = self.memory.service.query_graph_tool(repo_root=self.memory.repo_root,
            memory_file=self.memory.memory_file, pattern=action.pattern, target=action.target, max_results=5)
        result = self.memory.filter_response(minimal_result(result))
        # Reserve the exact attribution overhead so graph visibility matches valid retained JSON.
        overhead = len(render_observation(action, "x" * 100, 10_000)) - 100
        rendered = bounded_json(result, max_chars=max(0, room - overhead))
        if len(rendered) > room - overhead:
            raise RepositoryError("Insufficient graph observation room.")
        retained = json.loads(rendered)
        if not retained.get("returned"):
            return "", (), None
        return rendered, (), retained
