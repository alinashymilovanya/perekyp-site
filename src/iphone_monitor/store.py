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
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS subscribers ("
            "chat_id TEXT PRIMARY KEY, added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS listing_prices ("
            "item_id TEXT PRIMARY KEY, model TEXT NOT NULL, storage_gb INTEGER, "
            "condition TEXT, price_rub INTEGER NOT NULL, "
            "seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS pending_notifications ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, "
            "kind TEXT NOT NULL, text TEXT NOT NULL, photo TEXT, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._ensure_column("notified_listings", "model", "TEXT")
        self._ensure_column("notified_listings", "storage_gb", "INTEGER")
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS listing_prices_model "
            "ON listing_prices(model, storage_gb, condition)"
        )
        self._connection.commit()

    def _ensure_column(self, table: str, column: str, kind: str) -> None:
        """Add a column to an existing database without losing data."""
        existing = {
            row[1]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def has_seen(self, item_id: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM seen_listings WHERE item_id = ?", (item_id,)
        ).fetchone()
        return row is not None

    def mark_seen(self, item_id: str) -> None:
        self.mark_seen_many((item_id,))

    def mark_seen_many(self, item_ids: tuple[str, ...] | list[str]) -> None:
        """Persist one successful scan in a single transaction."""
        self._connection.executemany(
            "INSERT OR IGNORE INTO seen_listings(item_id) VALUES (?)",
            ((item_id,) for item_id in item_ids),
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
        model: str | None = None,
        storage_gb: int | None = None,
    ) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO notified_listings "
            "(item_id, notified_at, title, price_rub, market_price_rub, margin_rub, url, "
            "model, storage_gb) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                notified_at,
                title,
                price_rub,
                market_price_rub,
                margin_rub,
                url,
                model,
                storage_gb,
            ),
        )
        self._connection.commit()

    def count_similar_notified(
        self,
        *,
        model: str,
        storage_gb: int | None,
        price_rub: int,
        since: str,
    ) -> int:
        """How often the same model, storage and price was already announced."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM notified_listings "
            "WHERE price_rub = ? AND COALESCE(model, '') = ? "
            "AND COALESCE(storage_gb, -1) = ? AND notified_at >= ?",
            (price_rub, model, -1 if storage_gb is None else storage_gb, since),
        ).fetchone()
        return int(row[0])

    def top_notified_since(self, start_at: str, limit: int = 5) -> tuple[NotifiedListing, ...]:
        rows = self._connection.execute(
            "SELECT item_id, title, price_rub, market_price_rub, margin_rub, url "
            "FROM notified_listings WHERE notified_at >= ? "
            "ORDER BY margin_rub IS NULL, margin_rub DESC LIMIT ?",
            (start_at, limit),
        ).fetchall()
        return tuple(NotifiedListing(*row) for row in rows)

    def recent_notifications(self, limit: int = 50) -> tuple[dict, ...]:
        """Latest notifications newest first, ready for the public dashboard."""
        rows = self._connection.execute(
            "SELECT item_id, notified_at, title, price_rub, market_price_rub, margin_rub, url "
            "FROM notified_listings ORDER BY notified_at DESC, margin_rub DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(
            {
                "item_id": row[0],
                "notified_at": row[1],
                "title": row[2],
                "price_rub": row[3],
                "market_price_rub": row[4],
                "margin_rub": row[5],
                "url": row[6],
            }
            for row in rows
        )

    def count_notified_since(self, start_at: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM notified_listings WHERE notified_at >= ?", (start_at,)
        ).fetchone()
        return int(row[0])

    def count_seen(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM seen_listings").fetchone()
        return int(row[0])

    def record_prices(self, listings) -> None:
        """Remember the observed asking prices so market medians survive restarts."""
        rows = [
            (
                listing.item_id,
                listing.model,
                listing.storage_gb,
                listing.condition,
                listing.price_rub,
            )
            for listing in listings
            if listing.item_id and listing.price_rub
        ]
        if not rows:
            return
        self._connection.executemany(
            "INSERT OR REPLACE INTO listing_prices "
            "(item_id, model, storage_gb, condition, price_rub, seen_at) "
            "VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            rows,
        )
        self._connection.commit()

    def comparable_prices(
        self,
        model: str,
        storage_gb: int | None,
        condition: str | None = None,
        *,
        exclude_item_id: str | None = None,
        limit: int = 200,
    ) -> tuple[int, ...]:
        rows = self._connection.execute(
            "SELECT price_rub FROM listing_prices "
            "WHERE model = ? AND storage_gb IS ? AND condition IS ? AND item_id IS NOT ? "
            "AND seen_at >= datetime('now', '-30 day') "
            "ORDER BY seen_at DESC LIMIT ?",
            (model, storage_gb, condition, exclude_item_id, limit),
        ).fetchall()
        return tuple(int(row[0]) for row in rows)

    def enqueue_notification(self, chat_id: str, kind: str, text: str, photo: str | None = None) -> None:
        """Park a notification in the outbox until Telegram is reachable again."""
        self._connection.execute(
            "INSERT INTO pending_notifications(chat_id, kind, text, photo) VALUES (?, ?, ?, ?)",
            (str(chat_id), kind, text, photo),
        )
        self._connection.commit()

    def pending_notifications(self, limit: int = 10) -> tuple[tuple[int, str, str, str, str | None], ...]:
        rows = self._connection.execute(
            "SELECT id, chat_id, kind, text, photo FROM pending_notifications "
            "ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple((int(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4]) for row in rows)

    def delete_notification(self, notification_id: int) -> None:
        self._connection.execute("DELETE FROM pending_notifications WHERE id = ?", (notification_id,))
        self._connection.commit()

    def count_pending_notifications(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM pending_notifications").fetchone()
        return int(row[0])

    def add_subscriber(self, chat_id: str) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO subscribers(chat_id) VALUES (?)", (str(chat_id),)
        )
        self._connection.commit()

    def remove_subscriber(self, chat_id: str) -> None:
        self._connection.execute(
            "DELETE FROM subscribers WHERE chat_id = ?", (str(chat_id),)
        )
        self._connection.commit()

    def list_subscribers(self) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT chat_id FROM subscribers ORDER BY added_at, chat_id"
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def count_subscribers(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM subscribers").fetchone()
        return int(row[0])

    def import_state(self, payload: dict) -> dict:
        """Merge a state snapshot: history, announced deals, prices, readers.

        Used once when the bot moves to a new machine, so it neither forgets
        what it already announced nor announces the same deal twice.
        """
        counts = {"seen": 0, "notified": 0, "prices": 0, "subscribers": 0}
        for row in payload.get("seen_listings", []):
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO seen_listings(item_id, first_seen_at) "
                "VALUES (?, COALESCE(?, CURRENT_TIMESTAMP))",
                (str(row["item_id"]), row.get("first_seen_at") or None),
            )
            counts["seen"] += max(cursor.rowcount, 0)
        for row in payload.get("notified_listings", []):
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO notified_listings "
                "(item_id, notified_at, title, price_rub, market_price_rub, margin_rub, "
                "url, model, storage_gb) "
                "VALUES (?, COALESCE(?, CURRENT_TIMESTAMP), ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(row["item_id"]),
                    row.get("notified_at") or None,
                    row["title"],
                    int(row["price_rub"]),
                    row.get("market_price_rub"),
                    row.get("margin_rub"),
                    row["url"],
                    row.get("model"),
                    row.get("storage_gb"),
                ),
            )
            counts["notified"] += max(cursor.rowcount, 0)
        for row in payload.get("listing_prices", []):
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO listing_prices "
                "(item_id, model, storage_gb, condition, price_rub, seen_at) "
                "VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))",
                (
                    str(row["item_id"]),
                    row["model"],
                    row.get("storage_gb"),
                    row.get("condition"),
                    int(row["price_rub"]),
                    row.get("seen_at") or None,
                ),
            )
            counts["prices"] += max(cursor.rowcount, 0)
        for row in payload.get("subscribers", []):
            chat_id = row["chat_id"] if isinstance(row, dict) else row
            cursor = self._connection.execute(
                "INSERT OR IGNORE INTO subscribers(chat_id) VALUES (?)", (str(chat_id),)
            )
            counts["subscribers"] += max(cursor.rowcount, 0)
        self._connection.commit()
        return counts

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
