"""Central application configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from sage.errors import ConfigurationError

DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
DEFAULT_V2_PROFILE = "constrained-cross-provider"
V2_PLANNER_MODEL = "gemini-3.7-flash"
V2_PLANNER_FALLBACK_MODEL = "gemini-3.5-flash-lite"
V2_SOLVER_MODEL = "gpt-5.4-mini"
V2_REVIEWER_MODEL = "claude-haiku-4-5"
V2_REVIEWER_FALLBACK_MODEL = "gemini-3.5-flash"


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


class Settings(BaseModel):
    """Trusted controller settings loaded from the host environment."""

    model_config = ConfigDict(frozen=True)

    openai_api_key: str = Field(repr=False)
    gemini_api_key: str | None = Field(default=None, repr=False)
    anthropic_api_key: str | None = Field(default=None, repr=False)
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_max_retries: int = Field(default=2, ge=0, le=10)
    runtime: str = Field(default="v1", pattern=r"^(v1|v2-prototype)$")
    model_profile: str = DEFAULT_V2_PROFILE
    google_model_context_approved: bool = False
    max_turns: int = Field(default=30, ge=1)
    runs_dir: Path = Path(".sage/runs")
    sandbox_image: str = "sage-sandbox:v0"
    command_timeout_seconds: int = Field(default=60, ge=1)
    max_tool_output_chars: int = Field(default=12_000, ge=1_000)
    max_model_calls: int = Field(default=6, ge=3, le=6)
    max_readiness_context_expansions: int = Field(default=1, ge=0, le=1)
    max_solver_context_expansions: int = Field(default=1, ge=0, le=1)
    max_implementation_repairs: int = Field(default=1, ge=0, le=1)
    max_review_repairs: int = Field(default=1, ge=0, le=1)
    max_rate_limit_retries_per_call: int = Field(default=1, ge=0, le=1)
    max_blocking_questions: int = Field(default=3, ge=1, le=3)
    max_clarification_rounds: int = Field(default=2, ge=1, le=2)
    max_retry_after_seconds: int = Field(default=30, ge=0, le=60)
    model_request_timeout_seconds: int = Field(default=600, ge=1, le=900)
    run_deadline_seconds: int = Field(default=4_800, ge=600, le=5_100)
    finalization_reserve_seconds: int = Field(default=300, ge=60, le=900)
    planner_input_chars: int = Field(default=48_000, ge=4_000, le=100_000)
    readiness_recheck_input_chars: int = Field(default=40_000, ge=4_000, le=100_000)
    solver_input_chars: int = Field(default=96_000, ge=8_000, le=200_000)
    reviewer_input_chars: int = Field(default=48_000, ge=8_000, le=120_000)
    repair_input_chars: int = Field(default=48_000, ge=8_000, le=120_000)
    max_candidate_diff_chars: int = Field(default=96_000, ge=4_000, le=200_000)
    max_verification_log_chars: int = Field(default=24_000, ge=2_000, le=100_000)
    verification_commands: tuple[ConfiguredVerificationCommand, ...] = Field(
        default=(),
        max_length=3,
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Load settings from a single explicit environment boundary."""

        values = os.environ if environ is None else environ
        runtime = values.get("SAGE_RUNTIME", "v1").strip() or "v1"
        api_key = values.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError("OPENAI_API_KEY is required.")
        gemini_api_key = values.get("GEMINI_API_KEY", "").strip() or None
        anthropic_api_key = values.get("ANTHROPIC_API_KEY", "").strip() or None
        google_context_approved = _parse_bool(
            values.get("SAGE_GOOGLE_MODEL_CONTEXT_APPROVED", "false"),
            name="SAGE_GOOGLE_MODEL_CONTEXT_APPROVED",
        )
        model_profile = values.get("SAGE_MODEL_PROFILE", DEFAULT_V2_PROFILE).strip()
        verification_commands = _parse_verification_commands(
            values.get("SAGE_VERIFICATION_COMMANDS_JSON", "[]")
        )
        if runtime == "v2-prototype":
            if model_profile != DEFAULT_V2_PROFILE:
                raise ConfigurationError(
                    "SAGE_MODEL_PROFILE must be constrained-cross-provider for V2."
                )
            if gemini_api_key is None:
                raise ConfigurationError("GEMINI_API_KEY is required for V2.")
            if anthropic_api_key is None:
                raise ConfigurationError("ANTHROPIC_API_KEY is required for V2.")
            if not google_context_approved:
                raise ConfigurationError(
                    "SAGE_GOOGLE_MODEL_CONTEXT_APPROVED=true is required for V2."
                )

        try:
            return cls(
                openai_api_key=api_key,
                gemini_api_key=gemini_api_key,
                anthropic_api_key=anthropic_api_key,
                openai_model=values.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
                openai_max_retries=values.get("OPENAI_MAX_RETRIES", "2"),
                runtime=runtime,
                model_profile=model_profile,
                google_model_context_approved=google_context_approved,
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
                max_model_calls=values.get("SAGE_MAX_MODEL_CALLS", "6"),
                max_readiness_context_expansions=values.get(
                    "SAGE_MAX_READINESS_CONTEXT_EXPANSIONS", "1"
                ),
                max_solver_context_expansions=values.get(
                    "SAGE_MAX_SOLVER_CONTEXT_EXPANSIONS", "1"
                ),
                max_implementation_repairs=values.get(
                    "SAGE_MAX_IMPLEMENTATION_REPAIRS", "1"
                ),
                max_review_repairs=values.get("SAGE_MAX_REVIEW_REPAIRS", "1"),
                max_rate_limit_retries_per_call=values.get(
                    "SAGE_MAX_RATE_LIMIT_RETRIES_PER_CALL", "1"
                ),
                max_blocking_questions=values.get(
                    "SAGE_MAX_BLOCKING_QUESTIONS", "3"
                ),
                max_clarification_rounds=values.get(
                    "SAGE_MAX_CLARIFICATION_ROUNDS", "2"
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
                planner_input_chars=values.get("SAGE_PLANNER_INPUT_CHARS", "48000"),
                readiness_recheck_input_chars=values.get(
                    "SAGE_READINESS_RECHECK_INPUT_CHARS", "40000"
                ),
                solver_input_chars=values.get("SAGE_SOLVER_INPUT_CHARS", "96000"),
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
            )
        except ValidationError as error:
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
