"""OpenAI Agents SDK implementation of the V0 runtime contract."""

from __future__ import annotations

from agents import Agent, Runner

from issue_agent.config import Settings
from issue_agent.domain.results import AgentFinalOutput
from issue_agent.domain.runtime import RuntimeContext
from issue_agent.errors import AgentRuntimeError
from issue_agent.runtimes.openai_agents.prompt import CODING_AGENT_INSTRUCTIONS
from issue_agent.runtimes.openai_agents.tools import build_tools


class OpenAIAgentsRuntime:
    """Single-agent V0 implementation backed by the OpenAI Agents SDK."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def solve(
        self,
        *,
        issue_text: str,
        context: RuntimeContext,
    ) -> AgentFinalOutput:
        agent = Agent(
            name="V0 Issue Solver",
            instructions=CODING_AGENT_INSTRUCTIONS,
            model=self._settings.openai_model,
            tools=build_tools(context),
            output_type=AgentFinalOutput,
        )
        agent_input = (
            "Solve the following repository issue.\n\n"
            f"Repository base SHA:\n{context.prepared_run.base_sha}\n\n"
            f"Issue:\n{issue_text}\n\n"
            "Work only through the provided repository tools."
        )
        try:
            result = await Runner.run(
                agent,
                agent_input,
                max_turns=self._settings.max_turns,
            )
        except Exception as error:
            raise AgentRuntimeError(f"OpenAI agent run failed: {error}") from error

        final_output = result.final_output
        if isinstance(final_output, AgentFinalOutput):
            return final_output
        try:
            return AgentFinalOutput.model_validate(final_output)
        except Exception as error:
            raise AgentRuntimeError(
                "OpenAI agent returned an invalid structured result."
            ) from error
