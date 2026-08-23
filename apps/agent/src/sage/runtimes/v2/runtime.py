"""Runtime adapter for the sequential Sage V2 LangGraph."""

from __future__ import annotations

import asyncio
import logging

from langgraph.errors import GraphRecursionError
from pydantic import ValidationError

from sage.artifacts.v2 import V2ArtifactStore
from sage.config import Settings
from sage.context.compiler import ContextBudgetError, ContextCompiler
from sage.domain.results import AgentFinalOutput, SolveOutcome
from sage.domain.runtime import RuntimeContext
from sage.errors import AgentRuntimeError
from sage.observability import workflow_trace_config
from sage.providers.errors import ProviderErrorCategory, ProviderInvocationError
from sage.providers.factory import ProviderSet, build_constrained_provider_set
from sage.providers.manager import ModelCallBudgetError, ModelCallManager
from sage.repository.scout import RepositoryScout
from sage.runtimes.v2.graph import GRAPH_NAME, V2Services, build_graph
from sage.runtimes.v2.validation import InvalidModelContractError
from sage.verification import Verifier

logger = logging.getLogger(__name__)


class V2GraphRuntime:
    """Opt-in sequential Planner/Solver/Reviewer runtime."""

    def __init__(
        self,
        settings: Settings,
        providers: ProviderSet | None = None,
    ) -> None:
        self._settings = settings
        self._providers = providers or build_constrained_provider_set(settings)

    async def solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        """Run one fresh V2 graph and return a safe terminal result."""

        artifacts = V2ArtifactStore(context.prepared_run.run_dir)
        calls = ModelCallManager(
            settings=self._settings,
            providers=self._providers,
            usage_writer=artifacts.write_usage,
            run_id=context.prepared_run.run_id,
        )
        services = V2Services(
            settings=self._settings,
            repository=context.repository,
            scout=RepositoryScout(
                workspace=context.prepared_run.workspace_dir,
                sandbox=context.sandbox,
                max_output_chars=self._settings.max_tool_output_chars,
                timeout_seconds=self._settings.command_timeout_seconds,
            ),
            compiler=ContextCompiler(
                repository=context.repository,
                settings=self._settings,
            ),
            calls=calls,
            verifier=Verifier(
                repository=context.repository,
                artifacts=artifacts,
                max_log_chars=self._settings.max_verification_log_chars,
            ),
            artifacts=artifacts,
            base_sha=context.prepared_run.base_sha,
            workspace=context.prepared_run.workspace_dir,
        )
        logger.info(
            "V2 workflow: started run=%s graph=%s profile=%s max_model_calls=%d",
            context.prepared_run.run_id,
            GRAPH_NAME,
            self._settings.model_profile,
            self._settings.max_model_calls,
        )
        graph = build_graph(services)
        try:
            result = await graph.ainvoke(
                {"issue_text": issue_text},
                config=workflow_trace_config(
                    run_id=context.prepared_run.run_id,
                    graph_name=GRAPH_NAME,
                    model_profile=self._settings.model_profile,
                ),
            )
            final = AgentFinalOutput.model_validate(result.get("final_output"))
        except asyncio.CancelledError:
            raise
        except ProviderInvocationError as error:
            final = _provider_terminal(error, calls=calls)
            artifacts.write_usage(final.provenance)
            artifacts.write_terminal(final)
        except ModelCallBudgetError:
            final = _runtime_terminal(
                SolveOutcome.BUDGET_EXHAUSTED,
                "Sage reached the configured V2 model-call or time budget.",
                calls=calls,
            )
            artifacts.write_usage(final.provenance)
            artifacts.write_terminal(final)
        except ContextBudgetError:
            final = _runtime_terminal(
                SolveOutcome.BUDGET_EXHAUSTED,
                "Required V2 role context exceeded its configured safe cap.",
                calls=calls,
            )
            artifacts.write_usage(final.provenance)
            artifacts.write_terminal(final)
        except GraphRecursionError as error:
            raise AgentRuntimeError(
                "V2 graph recursion limit was reached unexpectedly."
            ) from error
        except (ValidationError, InvalidModelContractError):
            final = _runtime_terminal(
                SolveOutcome.INVALID_MODEL_OUTPUT,
                "A V2 role returned an invalid structured result.",
                calls=calls,
            )
            artifacts.write_usage(final.provenance)
            artifacts.write_terminal(final)
        except AgentRuntimeError:
            # Preflight and deterministic contract failures remain explicit
            # controller errors instead of being disguised as provider failures.
            raise
        logger.info(
            "V2 workflow: finished run=%s outcome=%s model_calls=%d",
            context.prepared_run.run_id,
            final.outcome.value,
            len(calls.records),
        )
        return final


def _provider_terminal(
    error: ProviderInvocationError,
    *,
    calls: ModelCallManager,
) -> AgentFinalOutput:
    if error.category is ProviderErrorCategory.RATE_LIMITED:
        outcome = SolveOutcome.RATE_LIMITED
        summary = "A required V2 model remained rate limited after bounded policy."
    elif error.category is ProviderErrorCategory.SCHEMA_ERROR:
        outcome = SolveOutcome.INVALID_MODEL_OUTPUT
        summary = "A required V2 model did not return the structured contract."
    else:
        outcome = SolveOutcome.PROVIDER_UNAVAILABLE
        summary = (
            f"The configured {error.provider} provider was unavailable for the "
            "required V2 role."
        )
    return _runtime_terminal(outcome, summary, calls=calls)


def _runtime_terminal(
    outcome: SolveOutcome,
    summary: str,
    *,
    calls: ModelCallManager,
) -> AgentFinalOutput:
    return AgentFinalOutput(
        summary=summary,
        outcome=outcome,
        provenance=calls.provenance(),
    )
