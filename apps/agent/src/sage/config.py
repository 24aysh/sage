"""Central application configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sage.errors import ConfigurationError

DEFAULT_SOLVER_MODEL = "gpt-5.4-mini"
DEFAULT_REVIEWER_MODEL = "gemini-3.5-flash"


class ConfiguredVerificationCommand(BaseModel):
    """One trusted, controller-configured sandbox verification command."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    check_id: str = Field(
        alias="id",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    command: str = Field(min_length=1, max_length=4_000)
    required: bool = True
    timeout_seconds: int = Field(default=60, ge=1, le=600)

    @model_validator(mode="after")
    def validate_command(self) -> ConfiguredVerificationCommand:
        if self.check_id == "git-diff-check":
            raise ValueError("Configured check ID is reserved.")
        if any(character in self.command for character in ("\x00", "\r", "\n")):
            raise ValueError("Configured verification command must be one line.")
        return self


class JevSettings(BaseModel):
    """Optional batched relevance filtering before Solver context assembly."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    mode: Literal["off", "shadow", "on"] = "off"
    api_key: str | None = Field(default=None, repr=False, exclude=True)
    model: str = Field(default="jev-1.13.0", min_length=1, max_length=120)
    timeout_seconds: float = Field(default=2, gt=0, le=2)
    score_threshold: float = Field(default=2.0, ge=0, le=3, allow_inf_nan=False)
    confidence_threshold: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    capture: bool = False
    log_input: bool = True

    @model_validator(mode="after")
    def credential(self) -> JevSettings:
        if self.mode != "off" and (not self.api_key or not self.api_key.strip()):
            raise ValueError("TYPESAFE_API_KEY is required for enabled Jev relevance filtering.")
        return self

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> JevSettings:
        values = os.environ if values is None else values
        retired = ("SAGE_JEV_NAVIGATION_POLICY", "SAGE_JEV_MAX_FOLLOWUP_ACTIONS",
                   "SAGE_JEV_RUN_WAIT_SECONDS", "SAGE_JEV_READ_PROBABILITY_THRESHOLD",
                   "SAGE_JEV_SEARCH_PROBABILITY_THRESHOLD", "SAGE_JEV_GRAPH_PROBABILITY_THRESHOLD",
                   "SAGE_JEV_ACTION_CONFIDENCE_THRESHOLD")
        if values.get("SAGE_JEV_NAVIGATION_MODE", "off") != "off" and any(values.get(key, "").strip() for key in retired):
            raise ConfigurationError("Old Jev navigation settings are retired; remove policy/action thresholds and use SAGE_JEV_RELEVANCE_SCORE_THRESHOLD and SAGE_JEV_RELEVANCE_CONFIDENCE_THRESHOLD.")
        try:
            return cls(mode=values.get("SAGE_JEV_NAVIGATION_MODE", "off"),
                api_key=values.get("TYPESAFE_API_KEY"),
                model=values.get("SAGE_JEV_MODEL", "jev-1.13.0"),
                timeout_seconds=values.get("SAGE_JEV_TIMEOUT_SECONDS", "2"),
                score_threshold=values.get("SAGE_JEV_RELEVANCE_SCORE_THRESHOLD", "2.0"),
                confidence_threshold=values.get("SAGE_JEV_RELEVANCE_CONFIDENCE_THRESHOLD", ".5"),
                log_input=_parse_bool(values.get("SAGE_JEV_LOG_INPUT", "true"), name="SAGE_JEV_LOG_INPUT"),
                capture=_parse_bool(values.get("SAGE_JEV_CAPTURE", "false"), name="SAGE_JEV_CAPTURE"))
        except (ValueError, ValidationError):
            raise ConfigurationError("Invalid Jev settings; check mode, key, relevance thresholds and timeout.") from None


class Settings(BaseModel):
    """Trusted controller settings loaded from the host environment."""

    model_config = ConfigDict(frozen=True)

    jev: JevSettings = Field(default_factory=JevSettings)

    openai_api_key: str = Field(repr=False)
    gemini_api_key: str | None = Field(default=None, repr=False)
    langsmith_api_key: str | None = Field(default=None, repr=False)
    langsmith_tracing: bool = False
    langsmith_project: str = Field(default="sage-v2", min_length=1, max_length=200)
    langsmith_workspace_id: str | None = Field(default=None, max_length=200)
    openai_max_retries: int = Field(default=2, ge=0, le=10)
    google_model_context_approved: bool = True
    solver_model: str = Field(
        default=DEFAULT_SOLVER_MODEL,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    reviewer_model: str = Field(
        default=DEFAULT_REVIEWER_MODEL,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )
    max_turns: int = Field(default=30, ge=1)
    runs_dir: Path = Path(".sage/runs")
    sandbox_image: str = "sage-sandbox:v2"
    command_timeout_seconds: int = Field(default=60, ge=1)
    max_tool_output_chars: int = Field(default=12_000, ge=1_000)
    solver_instructions_file: str = Field(default="sage-solver.md", min_length=1, max_length=240)
    reviewer_instructions_file: str = Field(default="sage-reviewer.md", min_length=1, max_length=240)
    max_rate_limit_retries_per_call: int = Field(default=1, ge=0, le=1)
    max_retry_after_seconds: int = Field(default=30, ge=0, le=60)
    model_request_timeout_seconds: int = Field(default=600, ge=1, le=900)
    run_deadline_seconds: int = Field(default=4_800, ge=600, le=5_100)
    finalization_reserve_seconds: int = Field(default=300, ge=60, le=900)
    solver_input_chars: int = Field(default=96_000, ge=8_000, le=200_000)
    legion_initial_context_chars: int = Field(default=4000, ge=500, le=12_000)
    reviewer_input_chars: int = Field(default=48_000, ge=8_000, le=120_000)
    repair_input_chars: int = Field(default=48_000, ge=8_000, le=120_000)
    max_candidate_diff_chars: int = Field(default=96_000, ge=4_000, le=200_000)
    max_verification_log_chars: int = Field(default=24_000, ge=2_000, le=100_000)
    verification_preflight: bool = False
    verification_preflight_dependencies: tuple[str, ...] = Field(default=(), max_length=32)
    verification_commands: tuple[ConfiguredVerificationCommand, ...] = Field(
        default=(),
        max_length=3,
    )

    @model_validator(mode="after")
    def validate_langsmith(self) -> Settings:
        if any(
            character in self.langsmith_project
            for character in ("\x00", "\r", "\n")
        ):
            raise ValueError("LANGSMITH_PROJECT must be a single non-empty line.")
        if self.langsmith_tracing and self.langsmith_api_key is None:
            raise ValueError(
                "LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true."
            )
        return self

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load settings from a single explicit environment boundary."""

        values = os.environ if environ is None else environ
        api_key = values.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required.")
        gemini_api_key = values.get("GEMINI_API_KEY", "").strip() or None
        langsmith_api_key = values.get("LANGSMITH_API_KEY", "").strip() or None
        langsmith_tracing = _parse_bool(
            values.get("LANGSMITH_TRACING", "false"),
            name="LANGSMITH_TRACING",
        )
        google_context_approved = _parse_bool(
            values.get("SAGE_GOOGLE_MODEL_CONTEXT_APPROVED", "true"),
            name="SAGE_GOOGLE_MODEL_CONTEXT_APPROVED",
        )
        verification_commands = _parse_verification_commands(
            values.get("SAGE_VERIFICATION_COMMANDS_JSON", "[]")
        )
        if gemini_api_key is None:
            raise ConfigurationError("GEMINI_API_KEY is required.")
        if not google_context_approved:
            raise ConfigurationError(
                "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true is required."
            )

        try:
            return cls(
                jev=JevSettings.from_env(values),
                solver_instructions_file=values.get("SAGE_SOLVER_INSTRUCTIONS_FILE", "sage-solver.md").strip(),
                reviewer_instructions_file=values.get("SAGE_REVIEWER_INSTRUCTIONS_FILE", "sage-reviewer.md").strip(),
                openai_api_key=api_key,
                gemini_api_key=gemini_api_key,
                langsmith_api_key=langsmith_api_key,
                langsmith_tracing=langsmith_tracing,
                langsmith_project=(
                    values.get("LANGSMITH_PROJECT", "sage-v2").strip() or "sage-v2"
                ),
                langsmith_workspace_id=(
                    values.get("LANGSMITH_WORKSPACE_ID", "").strip() or None
                ),
                openai_max_retries=values.get("OPENAI_MAX_RETRIES", "2"),
                google_model_context_approved=google_context_approved,
                solver_model=values.get("SOLVER_MODEL", DEFAULT_SOLVER_MODEL).strip(),
                reviewer_model=values.get(
                    "REVIEWER_MODEL", DEFAULT_REVIEWER_MODEL
                ).strip(),
                max_turns=values.get("SAGE_MAX_TURNS", "30"),
                runs_dir=values.get("SAGE_RUNS_DIR", ".sage/runs"),
                sandbox_image=values.get(
                    "SAGE_SANDBOX_IMAGE", "sage-sandbox:v2"
                ),
                command_timeout_seconds=values.get(
                    "SAGE_COMMAND_TIMEOUT_SECONDS", "60"
                ),
                max_tool_output_chars=values.get(
                    "SAGE_MAX_TOOL_OUTPUT_CHARS", "12000"
                ),
                max_rate_limit_retries_per_call=values.get(
                    "SAGE_MAX_RATE_LIMIT_RETRIES_PER_CALL", "1"
                ),
                max_retry_after_seconds=values.get(
                    "SAGE_MAX_RETRY_AFTER_SECONDS", "30"
                ),
                model_request_timeout_seconds=values.get(
                    "SAGE_MODEL_REQUEST_TIMEOUT_SECONDS", "600"
                ),
                run_deadline_seconds=values.get(
                    "SAGE_RUN_DEADLINE_SECONDS", "4800"
                ),
                finalization_reserve_seconds=values.get(
                    "SAGE_FINALIZATION_RESERVE_SECONDS", "300"
                ),
                solver_input_chars=values.get("SAGE_SOLVER_INPUT_CHARS", "96000"),
                legion_initial_context_chars=values.get("SAGE_LEGION_INITIAL_CONTEXT_CHARS", "4000"),
                reviewer_input_chars=values.get(
                    "SAGE_REVIEWER_INPUT_CHARS", "48000"
                ),
                repair_input_chars=values.get("SAGE_REPAIR_INPUT_CHARS", "48000"),
                max_candidate_diff_chars=values.get(
                    "SAGE_MAX_CANDIDATE_DIFF_CHARS", "96000"
                ),
                max_verification_log_chars=values.get(
                    "SAGE_MAX_VERIFICATION_LOG_CHARS", "24000"
                ),
                verification_commands=verification_commands,
                verification_preflight=_parse_bool(values.get("SAGE_VERIFICATION_PREFLIGHT", "false"), name="SAGE_VERIFICATION_PREFLIGHT"),
                verification_preflight_dependencies=json.loads(values.get("SAGE_VERIFICATION_PREFLIGHT_DEPENDENCIES_JSON", "[]")),
            )
        except (ValidationError, json.JSONDecodeError) as error:
            raise ConfigurationError(f"Invalid Sage configuration: {error}") from error


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigurationError(f"{name} must be true or false.")


def _parse_verification_commands(
    value: str,
) -> tuple[ConfiguredVerificationCommand, ...]:
    try:
        raw = json.loads(value)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            "SAGE_VERIFICATION_COMMANDS_JSON must be valid JSON."
        ) from error
    if not isinstance(raw, list):
        raise ConfigurationError(
            "SAGE_VERIFICATION_COMMANDS_JSON must contain a JSON list."
        )
    try:
        return tuple(ConfiguredVerificationCommand.model_validate(item) for item in raw)
    except (TypeError, ValidationError) as error:
        raise ConfigurationError(
            "SAGE_VERIFICATION_COMMANDS_JSON contains an invalid command."
        ) from error
