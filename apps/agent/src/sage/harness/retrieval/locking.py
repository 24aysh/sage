"""Process-level serialization of builds without blocking ready graph readers."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sage.errors import RetrievalBuildError


@contextmanager
def index_lock(path: Path) -> Iterator[None]:
    """Fail promptly when another process owns this graph's build lifecycle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RetrievalBuildError("Repository index is busy in another process; retry later.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
