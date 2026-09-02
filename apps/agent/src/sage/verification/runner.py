"""Sequential sandbox verification and stable failure fingerprints."""

from __future__ import annotations

import hashlib
import re

from sage.artifacts.store import RunArtifacts
from sage.config import Settings
from sage.domain.solver import SolverPlan
from sage.domain.verification import (
    VerificationCheckResult,
    VerificationCommand,
    VerificationResult,
    VerificationStatus,
)
from sage.repository.service import Repository
from sage.verification.discovery import discover_solver_verification_commands

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TEMP_PATH = re.compile(r"/(?:tmp|home)/[^\s:]+")
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-Z]+\b")
_SECRETISH = re.compile(r"(?i)(api[_-]?key|authorization|token)\s*[:=]\s*\S+")


class Verifier:
    """Run a fixed verification plan without model involvement."""

    def __init__(
        self,
        *,
        repository: Repository,
        artifacts: RunArtifacts,
        max_log_chars: int,
    ) -> None:
        self._repository = repository
        self._artifacts = artifacts
        self._max_log_chars = max_log_chars

    def verify(
        self,
        commands: tuple[VerificationCommand, ...],
        *,
        pass_number: int,
    ) -> VerificationResult:
        checks: list[VerificationCheckResult] = []
        for command in commands:
            result = self._repository.run_command(
                command=command.command,
                timeout_seconds=command.timeout_seconds,
            )
            combined = _safe_log(result.stdout, result.stderr)
            log = combined[: self._max_log_chars]
            log_path = self._artifacts.write_verification_log(
                pass_number,
                command.check_id,
                log,
            )
            if result.timed_out:
                status = VerificationStatus.TIMEOUT
            elif result.exit_code == 0:
                status = VerificationStatus.PASS
            elif result.exit_code in {126, 127} and not command.required:
                status = VerificationStatus.UNAVAILABLE
            else:
                status = VerificationStatus.FAIL
            checks.append(
                VerificationCheckResult(
                    check=command,
                    status=status,
                    exit_code=result.exit_code,
                    output_excerpt=log[: min(4_000, self._max_log_chars)],
                    fingerprint=_fingerprint(command.check_id, status, log),
                    log_ref=str(log_path.relative_to(log_path.parents[2])),
                )
            )

        diff = self._repository.get_complete_diff()
        digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        required_failures = [
            check
            for check in checks
            if check.status is VerificationStatus.FAIL
            and check.check.required
        ]
        required_timeouts = [
            check
            for check in checks
            if check.status is VerificationStatus.TIMEOUT and check.check.required
        ]
        if required_failures:
            overall = VerificationStatus.FAIL
        elif required_timeouts:
            overall = VerificationStatus.TIMEOUT
        else:
            overall = VerificationStatus.PASS
        uncertainty = tuple(
            f"Optional verification {check.status.value}: {check.check.check_id}"
            for check in checks
            if not check.check.required and check.status is not VerificationStatus.PASS
        )
        verification = VerificationResult(
            status=overall,
            checks=tuple(checks),
            passing_check_count=sum(
                check.status is VerificationStatus.PASS for check in checks
            ),
            candidate_diff_digest=digest,
            uncertainty=uncertainty,
        )
        self._artifacts.write_verification_summary(pass_number, verification)
        return verification

    def verify_plan(
        self,
        plan: SolverPlan,
        settings: Settings,
        *,
        pass_number: int,
    ) -> VerificationResult:
        """Discover trusted checks and verify one saved Solver plan."""

        return self.verify(
            discover_solver_verification_commands(plan=plan, settings=settings),
            pass_number=pass_number,
        )


def _safe_log(stdout: str, stderr: str) -> str:
    value = f"STDOUT\n{stdout}\nSTDERR\n{stderr}"
    value = _ANSI.sub("", value)
    return _SECRETISH.sub(r"\1=[redacted]", value)


def _fingerprint(check_id: str, status: VerificationStatus, value: str) -> str:
    normalized = _TEMP_PATH.sub("/<path>", value)
    normalized = _TIMESTAMP.sub("<timestamp>", normalized)
    normalized = "\n".join(line.strip() for line in normalized.splitlines()[:40])
    payload = f"{check_id}\n{status.value}\n{normalized}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
