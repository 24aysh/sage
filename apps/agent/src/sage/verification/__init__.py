"""Deterministic hard verification for V2 candidates."""

from sage.verification.discovery import (
    discover_solver_verification_commands,
)
from sage.verification.runner import Verifier

__all__ = [
    "Verifier",
    "discover_solver_verification_commands",
]
