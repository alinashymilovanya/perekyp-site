"""Small SQLite store for deduplicating listing notifications."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NotifiedListing:
    item_id: str
    title: str
    price_rub: int
    market_price_rub: int | None
    margin_rub: int | None
    url: str


class SeenStore:
    def __init__(self, path: str | Path):
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS seen_listings "
            "(item_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS notified_listings ("
            "item_id TEXT PRIMARY KEY, notified_at TEXT NOT NULL, title TEXT NOT NULL, "
            "price_rub INTEGER NOT NULL, market_price_rub INTEGER, margin_rub INTEGER, url TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS monitor_metadata ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
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

    def record_notified(
        self,
        *,
        item_id: str,
        title: str,
        price_rub: int,
        market_price_rub: int | None,
        margin_rub: int | None,
        url: str,
        notified_at: str,
    ) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO notified_listings "
            "(item_id, notified_at, title, price_rub, market_price_rub, margin_rub, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, notified_at, title, price_rub, market_price_rub, margin_rub, url),
        )
        self._connection.commit()

    def top_notified_since(self, start_at: str, limit: int = 5) -> tuple[NotifiedListing, ...]:
        rows = self._connection.execute(
            "SELECT item_id, title, price_rub, market_price_rub, margin_rub, url "
            "FROM notified_listings WHERE notified_at >= ? "
            "ORDER BY margin_rub IS NULL, margin_rub DESC LIMIT ?",
            (start_at, limit),
        ).fetchall()
        return tuple(NotifiedListing(*row) for row in rows)

    def get_metadata(self, key: str) -> str | None:
        row = self._connection.execute(
            "SELECT value FROM monitor_metadata WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else None

    def set_metadata(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO monitor_metadata(key, value) VALUES (?, ?)", (key, value)
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
