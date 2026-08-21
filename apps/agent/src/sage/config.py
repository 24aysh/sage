"""Central application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sage.errors import ConfigurationError

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"


class Settings(BaseModel):
    """Trusted controller settings loaded from the host environment."""

    model_config = ConfigDict(frozen=True)

    openai_api_key: str = Field(repr=False)
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_max_retries: int = Field(default=2, ge=0, le=10)
    max_turns: int = Field(default=30, ge=1)
    runs_dir: Path = Path(".sage/runs")
    sandbox_image: str = "sage-sandbox:v0"
    command_timeout_seconds: int = Field(default=60, ge=1)
    max_tool_output_chars: int = Field(default=12_000, ge=1_000)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load settings from a single explicit environment boundary."""

        values = os.environ if environ is None else environ
        api_key = values.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required.")

        try:
            return cls(
                openai_api_key=api_key,
                openai_model=values.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
                openai_max_retries=values.get("OPENAI_MAX_RETRIES", "2"),
                max_turns=values.get("SAGE_MAX_TURNS", "30"),
                runs_dir=values.get("SAGE_RUNS_DIR", ".sage/runs"),
                sandbox_image=values.get(
                    "SAGE_SANDBOX_IMAGE", "sage-sandbox:v0"
                ),
                command_timeout_seconds=values.get(
                    "SAGE_COMMAND_TIMEOUT_SECONDS", "60"
                ),
                max_tool_output_chars=values.get(
                    "SAGE_MAX_TOOL_OUTPUT_CHARS", "12000"
                ),
            )
        except ValidationError as error:
            raise ConfigurationError(f"Invalid Sage configuration: {error}") from error
