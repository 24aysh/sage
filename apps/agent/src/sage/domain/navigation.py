"""Structured deterministic repository search results."""

from pydantic import BaseModel, ConfigDict, Field


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
