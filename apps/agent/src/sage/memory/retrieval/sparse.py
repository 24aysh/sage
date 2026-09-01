"""Run-local SQLite FTS5 index derived from canonical memory."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from sage.errors import MemoryStorageError
from sage.memory.models import SearchDocument

_TOKEN = re.compile(r"[\w./:@+-]+", re.UNICODE)


class SQLiteSparseIndex:
    """A disposable lexical index; PostgreSQL remains canonical."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        try:
            self._connection = sqlite3.connect(str(path))
            self._connection.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "path UNINDEXED, summary, responsibilities, concepts, symbols, "
                "imports, tokenize='unicode61 remove_diacritics 2')"
            )
        except sqlite3.Error as error:
            raise MemoryStorageError("SQLite FTS5 is unavailable.") from error

    def rebuild(self, documents: Sequence[SearchDocument]) -> None:
        try:
            with self._connection:
                self._connection.execute("DELETE FROM memory_fts")
                self._connection.executemany(
                    "INSERT INTO memory_fts(path, summary, responsibilities, "
                    "concepts, symbols, imports) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            item.path,
                            item.summary,
                            " ".join(item.responsibilities),
                            " ".join(item.concepts),
                            " ".join(item.symbols),
                            " ".join(item.imports),
                        )
                        for item in documents
                    ],
                )
        except sqlite3.Error as error:
            raise MemoryStorageError("Unable to build the sparse memory index.") from error

    def search(self, query: str, *, limit: int) -> list[tuple[str, float]]:
        if limit < 1:
            return []
        terms = [item.casefold()[:100] for item in _TOKEN.findall(query)[:32]]
        if not terms:
            return []
        expression = " OR ".join('"' + item.replace('"', '""') + '"' for item in terms)
        try:
            rows = self._connection.execute(
                "SELECT path, bm25(memory_fts, 0.0, 2.0, 1.5, 1.2, 2.5, 1.8) "
                "FROM memory_fts WHERE memory_fts MATCH ? "
                "ORDER BY 2, path LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.Error as error:
            raise MemoryStorageError("Unable to query the sparse memory index.") from error
        return [(str(path), -float(rank)) for path, rank in rows]

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def available() -> bool:
        try:
            connection = sqlite3.connect(":memory:")
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
            connection.close()
            return True
        except sqlite3.Error:
            return False
