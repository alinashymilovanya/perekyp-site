"""Small SQLite store for deduplicating listing notifications."""

import sqlite3
from pathlib import Path


class SeenStore:
    def __init__(self, path: str | Path):
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS seen_listings "
            "(item_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.commit()

    def has_seen(self, item_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM seen_listings WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row is not None

    def mark_seen(self, item_id: str) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO seen_listings(item_id) VALUES (?)", (item_id,)
        )
        self._connection.commit()

    def is_empty(self) -> bool:
        row = self._connection.execute("SELECT 1 FROM seen_listings LIMIT 1").fetchone()
        return row is None

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
