"""Shared repository areas excluded from discovery and model context."""

IGNORED_GLOBS = (
    ".git/**",
    "node_modules/**",
    ".next/**",
    "dist/**",
    "build/**",
    "target/**",
    "vendor/**",
    "__pycache__/**",
    ".venv/**",
)
IGNORED_NAMES = frozenset(
    {
        ".git",
        ".next",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "vendor",
    }
)
