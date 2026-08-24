"""Provider-neutral research requests, results, and safe diagnostics."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResearchRole(StrEnum):
    ADMISSION = "admission"
    SOLVER = "solver"
    REVIEWER = "reviewer"


class ResearchSourceType(StrEnum):
    OFFICIAL_DOCUMENTATION = "official_documentation"
    WEB = "web"


class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(min_length=2, max_length=500)
    max_results: int = Field(default=5, ge=1, le=5)
    domains: tuple[str, ...] = Field(default=(), max_length=5)
    recency_days: int | None = Field(default=None, ge=1, le=3_650)


class ProviderSearchItem(BaseModel):
    """Normalized provider response before run-scoped IDs are assigned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2_048)
    snippet: str = Field(default="", max_length=2_000)
    content: str = Field(default="", max_length=50_000)
    published_at: datetime | None = None


class ResearchResult(BaseModel):
    """One safe, bounded result addressable only within the current run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str = Field(pattern=r"^research-[0-9]{3}$")
    source_type: ResearchSourceType
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2_048)
    snippet: str = Field(default="", max_length=2_000)
    content: str = Field(default="", max_length=12_000)
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    detected_version: str | None = Field(default=None, max_length=100)
    authoritative: bool = False
    fetched_at: datetime


class ResearchSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = Field(pattern=r"^(completed|unavailable|budget_exhausted|error)$")
    results: tuple[ResearchResult, ...] = Field(default=(), max_length=5)
    message: str = Field(min_length=1, max_length=500)
    cache_hit: bool = False


class ResearchReadResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = Field(pattern=r"^(completed|not_found|unavailable|error)$")
    result: ResearchResult | None = None
    message: str = Field(min_length=1, max_length=500)


class ResearchSourceSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result_id: str
    source_type: ResearchSourceType
    title: str
    url: str
    content_digest: str
    authoritative: bool


class ResearchSummary(BaseModel):
    """Upload-safe research provenance without queries or page bodies."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    searches: int = Field(ge=0)
    cache_hits: int = Field(ge=0)
    errors: int = Field(ge=0)
    sources: tuple[ResearchSourceSummary, ...] = Field(default=(), max_length=40)
