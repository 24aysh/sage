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
IGNORED_NAMES = frozenset(pattern.removesuffix("/**") for pattern in IGNORED_GLOBS)

# Keep untracked execution/dependency noise outside the authoritative candidate
# while still allowing tracked files under these names to be modified.
IGNORED_UNTRACKED_PATHSPECS = tuple(
    f":(exclude,glob)**/{name}/**" for name in sorted(IGNORED_NAMES)
)
