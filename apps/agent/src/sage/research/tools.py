"""LangChain tool adapters over the run-scoped ResearchService."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from langchain_core.tools import BaseTool, tool

from sage.research.models import (
    ResearchReadResponse,
    ResearchRole,
    ResearchSearchResponse,
)


class ResearchToolService(Protocol):
    """Narrow service surface consumed by model-callable research adapters."""

    async def search_documentation(
        self,
        *,
        role: ResearchRole,
        query: str,
        ecosystem: str | None = None,
        package: str | None = None,
        version: str | None = None,
        domains: Sequence[str] = (),
        max_results: int = 5,
    ) -> ResearchSearchResponse: ...

    async def search_web(
        self,
        *,
        role: ResearchRole,
        query: str,
        domains: Sequence[str] = (),
        recency_days: int | None = None,
        max_results: int = 5,
    ) -> ResearchSearchResponse: ...

    def read(self, *, role: ResearchRole, result_id: str) -> ResearchReadResponse: ...


def build_solver_research_tools(service: ResearchToolService) -> list[BaseTool]:
    """Build the Solver's bounded documentation and web research tools."""

    return build_research_tools(service, role=ResearchRole.SOLVER, allow_web=True)


def build_research_tools(
    service: ResearchToolService,
    *,
    role: ResearchRole,
    allow_web: bool,
) -> list[BaseTool]:
    """Build role-specific research tools without exposing arbitrary URLs."""

    @tool
    async def search_documentation(
        query: str,
        ecosystem: str | None = None,
        package: str | None = None,
        version: str | None = None,
        domains: list[str] | None = None,
        max_results: int = 5,
    ) -> str:
        """Search bounded version-aware documentation; prefer official domains."""

        response = await service.search_documentation(
            role=role,
            query=query,
            ecosystem=ecosystem,
            package=package,
            version=version,
            domains=domains or (),
            max_results=max_results,
        )
        return response.model_dump_json(exclude={"results": {"__all__": {"content"}}})

    @tool
    async def read_documentation(result_id: str) -> str:
        """Read bounded untrusted content from a same-run documentation result ID."""

        return service.read(role=role, result_id=result_id).model_dump_json()

    tools: list[BaseTool] = [search_documentation, read_documentation]
    if not allow_web:
        return tools

    @tool
    async def search_web(
        query: str,
        domains: list[str] | None = None,
        recency_days: int | None = None,
        max_results: int = 5,
    ) -> str:
        """Search public web sources when repository and official docs are insufficient."""

        response = await service.search_web(
            role=role,
            query=query,
            domains=domains or (),
            recency_days=recency_days,
            max_results=max_results,
        )
        return response.model_dump_json(exclude={"results": {"__all__": {"content"}}})

    @tool
    async def fetch_web_page(result_id: str) -> str:
        """Read cached bounded content for a same-run public search result ID."""

        return service.read(role=role, result_id=result_id).model_dump_json()

    return [*tools, search_web, fetch_web_page]
