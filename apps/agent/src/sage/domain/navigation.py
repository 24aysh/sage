"""Typed, bounded evidence and read-only navigation contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReadAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["read_file"] = "read_file"
    path: str = Field(min_length=1, max_length=500)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def bounded_range(self) -> ReadAction:
        if not 0 <= self.end_line - self.start_line < 40:
            raise ValueError("Navigation reads require at most forty lines.")
        return self


class SearchAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["search_text"] = "search_text"
    query: str = Field(min_length=1, max_length=200)
    path: str = Field(min_length=1, max_length=500)


class GraphAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["query_graph_tool"] = "query_graph_tool"
    pattern: Literal["callers_of", "callees_of", "tests_for"]
    target: str = Field(min_length=1, max_length=1000)


class ActionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    action: Annotated[ReadAction | SearchAction | GraphAction, Field(discriminator="kind")]
    evidence: str = Field(max_length=1000)


class SearchMatch(BaseModel):
    model_config = ConfigDict(frozen=True)
    path: str
    line: int = Field(ge=1)
    column: int = Field(ge=1)
    text: str


class SearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    text: str
    matches: tuple[SearchMatch, ...] = ()
    truncated: bool = False


class NavigationDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    model: str
    selected: tuple[str, ...] = ()
    scores: dict[str, float] = Field(default_factory=dict)
    confidences: dict[str, float] = Field(default_factory=dict)
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float = Field(default=1, ge=0, le=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class NavigationProvider(Protocol):
    model: str
    capture: dict | None

    async def rank_excerpts(self, *, state: dict, candidates: tuple[ActionCandidate, ...],
                            timeout: float) -> NavigationDecision: ...
    async def choose_action(self, *, state: dict, candidates: tuple[ActionCandidate, ...],
                            timeout: float) -> NavigationDecision: ...
    async def aclose(self) -> None: ...


class NavigationHook(Protocol):
    action_policy: bool
    async def aclose(self) -> None: ...

    def begin_session(self, *, stage: str) -> None: ...
    def invalidate(self, *paths: str) -> None: ...
    async def enrich(self, *, tool_name: str, source: str, path: str,
                     query: str = "", matches: tuple[SearchMatch, ...] = (),
                     start_line: int = 1, exploration_goal: str | None = None) -> str: ...


class NavigationUnavailable(Exception):
    """Expected, secret-safe optional-provider failure."""

    def __init__(self, reason: str, *, permanent: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.permanent = permanent
