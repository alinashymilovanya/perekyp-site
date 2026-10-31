"""Small SQLite store for deduplicating listing notifications."""

import json
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


@dataclass(frozen=True)
class Account:
    """Подписка человека: тариф, срок и начало пробного периода."""

    chat_id: str
    plan: str
    plan_until: str | None
    trial_started_at: str | None


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
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS item_details ("
            "item_id TEXT PRIMARY KEY, fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "payload TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS detail_requests ("
            "item_id TEXT PRIMARY KEY, url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', "
            "requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS accounts ("
            "chat_id TEXT PRIMARY KEY, plan TEXT NOT NULL DEFAULT 'free', plan_until TEXT, "
            "trial_started_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS usage_counters ("
            "chat_id TEXT NOT NULL, day TEXT NOT NULL, signals INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (chat_id, day))"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS deals ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT NOT NULL, listing_id TEXT, "
            "model TEXT NOT NULL DEFAULT '', storage_gb INTEGER, bought_price_rub INTEGER, "
            "sold_price_rub INTEGER, expenses_rub INTEGER NOT NULL DEFAULT 0, "
            "status TEXT NOT NULL DEFAULT 'in_progress', "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, sold_at TEXT)"
        )
        self._ensure_column("notified_listings", "model", "TEXT")
        self._ensure_column("notified_listings", "storage_gb", "INTEGER")
        # Tariff delay: a Free signal is queued with a future delivery moment
        # instead of being dropped, so the person still gets it later.
        self._ensure_column("pending_notifications", "deliver_at", "TEXT")
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

    def save_item_detail(self, detail) -> None:
        """Cache the facts read from the listing page — pages cost requests."""
        self._connection.execute(
            "INSERT OR REPLACE INTO item_details(item_id, fetched_at, payload) "
            "VALUES (?, CURRENT_TIMESTAMP, ?)",
            (detail.item_id, json.dumps(detail.as_payload(), ensure_ascii=False)),
        )
        self._connection.commit()

    def load_item_detail(self, item_id: str):
        """Return the cached ItemDetail for a listing, or None."""
        from .item_detail import ItemDetail

        row = self._connection.execute(
            "SELECT payload FROM item_details WHERE item_id = ?", (str(item_id),)
        ).fetchone()
        if row is None:
            return None
        try:
            return ItemDetail.from_payload(json.loads(row[0]))
        except (ValueError, TypeError, KeyError, IndexError):
            return None

    def count_item_details(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM item_details").fetchone()
        return int(row[0])

    def add_detail_request(self, item_id: str, url: str, title: str = "") -> None:
        """Remember a listing whose page still has to be read by the home agent."""
        self._connection.execute(
            "INSERT OR IGNORE INTO detail_requests(item_id, url, title) VALUES (?, ?, ?)",
            (str(item_id), url or "", title or ""),
        )
        self._connection.commit()

    def detail_requests(self, limit: int = 50) -> tuple[tuple[str, str, str], ...]:
        rows = self._connection.execute(
            "SELECT item_id, url, title FROM detail_requests "
            "ORDER BY requested_at DESC, item_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def drop_detail_request(self, item_id: str) -> None:
        self._connection.execute("DELETE FROM detail_requests WHERE item_id = ?", (str(item_id),))
        self._connection.commit()

    def count_detail_requests(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM detail_requests").fetchone()
        return int(row[0])

    def prune_detail_requests(self, days: int = 3) -> None:
        self._connection.execute(
            "DELETE FROM detail_requests WHERE requested_at < datetime('now', ?)",
            (f"-{int(days)} day",),
        )
        self._connection.commit()

    def enqueue_notification(
        self,
        chat_id: str,
        kind: str,
        text: str,
        photo: str | None = None,
        *,
        deliver_at: str | None = None,
    ) -> None:
        """Park a notification in the outbox until Telegram is reachable again.

        ``deliver_at`` also delays a healthy notification: the Free tariff
        promises «задержка до 30 минут», so the record waits in the same
        outbox instead of a separate scheduler.
        """
        self._connection.execute(
            "INSERT INTO pending_notifications(chat_id, kind, text, photo, deliver_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(chat_id), kind, text, photo, deliver_at),
        )
        self._connection.commit()

    def pending_notifications(
        self,
        limit: int = 10,
        *,
        due_at: str | None = None,
    ) -> tuple[tuple[int, str, str, str, str | None], ...]:
        """Notifications waiting in the outbox, oldest first.

        ``due_at`` filters out delayed records whose moment has not come yet.
        """
        if due_at is None:
            rows = self._connection.execute(
                "SELECT id, chat_id, kind, text, photo FROM pending_notifications "
                "ORDER BY id LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT id, chat_id, kind, text, photo FROM pending_notifications "
                "WHERE deliver_at IS NULL OR deliver_at <= ? ORDER BY id LIMIT ?",
                (str(due_at), limit),
            ).fetchall()
        return tuple((int(row[0]), str(row[1]), str(row[2]), str(row[3]), row[4]) for row in rows)

    def delete_notification(self, notification_id: int) -> None:
        self._connection.execute("DELETE FROM pending_notifications WHERE id = ?", (notification_id,))
        self._connection.commit()

    def count_pending_notifications(self, *, due_at: str | None = None) -> int:
        if due_at is None:
            row = self._connection.execute("SELECT COUNT(*) FROM pending_notifications").fetchone()
        else:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM pending_notifications "
                "WHERE deliver_at IS NULL OR deliver_at <= ?",
                (str(due_at),),
            ).fetchone()
        return int(row[0])

    # --- accounts, quotas and deals (monetisation) -------------------------

    def account(self, chat_id: str) -> Account:
        """Аккаунт человека; при первом обращении создаётся на тарифе Free."""
        self._connection.execute(
            "INSERT OR IGNORE INTO accounts(chat_id) VALUES (?)", (str(chat_id),)
        )
        self._connection.commit()
        row = self._connection.execute(
            "SELECT chat_id, plan, plan_until, trial_started_at FROM accounts WHERE chat_id = ?",
            (str(chat_id),),
        ).fetchone()
        return Account(
            chat_id=str(row[0]),
            plan=str(row[1] or "free"),
            plan_until=row[2],
            trial_started_at=row[3],
        )

    def set_plan(
        self,
        chat_id: str,
        plan: str,
        *,
        plan_until: str | None = None,
        trial_started_at: str | None = None,
    ) -> Account:
        """Выдаёт тариф и срок; ``trial_started_at`` заполняется один раз."""
        self.account(chat_id)
        self._connection.execute(
            "UPDATE accounts SET plan = ?, plan_until = ?, "
            "trial_started_at = COALESCE(trial_started_at, ?), "
            "updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (str(plan), plan_until, trial_started_at, str(chat_id)),
        )
        self._connection.commit()
        return self.account(chat_id)

    def ensure_account(self, chat_id: str, default_plan: str = "free") -> Account:
        """Создаёт аккаунт с явным тарифом по умолчанию и возвращает его.

        Нужен, чтобы действующие подписчики не превратились в Free при
        обновлении: бот передаёт сюда свой ``DEFAULT_PLAN``.
        """
        self._connection.execute(
            "INSERT OR IGNORE INTO accounts(chat_id, plan) VALUES (?, ?)",
            (str(chat_id), str(default_plan)),
        )
        self._connection.commit()
        return self.account(chat_id)

    def signals_used(self, chat_id: str, day: str) -> int:
        row = self._connection.execute(
            "SELECT signals FROM usage_counters WHERE chat_id = ? AND day = ?",
            (str(chat_id), str(day)),
        ).fetchone()
        return int(row[0]) if row else 0

    def consume_signal(self, chat_id: str, day: str) -> int:
        """Списывает один сигнал и возвращает счётчик за сутки."""
        self._connection.execute(
            "INSERT INTO usage_counters(chat_id, day, signals) VALUES (?, ?, 1) "
            "ON CONFLICT(chat_id, day) DO UPDATE SET signals = signals + 1",
            (str(chat_id), str(day)),
        )
        self._connection.commit()
        return self.signals_used(chat_id, day)

    def add_deal(
        self,
        chat_id: str,
        *,
        listing_id: str | None = None,
        model: str = "",
        storage_gb: int | None = None,
        bought_price_rub: int | None = None,
        sold_price_rub: int | None = None,
        expenses_rub: int = 0,
        status: str = "in_progress",
    ) -> int:
        cursor = self._connection.execute(
            "INSERT INTO deals(chat_id, listing_id, model, storage_gb, bought_price_rub, "
            "sold_price_rub, expenses_rub, status, sold_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
            "CASE WHEN ? IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END)",
            (
                str(chat_id),
                listing_id,
                model,
                storage_gb,
                bought_price_rub,
                sold_price_rub,
                int(expenses_rub),
                status,
                sold_price_rub,
            ),
        )
        self._connection.commit()
        return int(cursor.lastrowid)

    def list_deals(self, chat_id: str, limit: int = 50) -> tuple[dict, ...]:
        rows = self._connection.execute(
            "SELECT id, listing_id, model, storage_gb, bought_price_rub, sold_price_rub, "
            "expenses_rub, status, created_at FROM deals WHERE chat_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit),
        ).fetchall()
        return tuple(
            {
                "id": int(row[0]),
                "listing_id": row[1],
                "model": str(row[2] or ""),
                "storage_gb": row[3],
                "bought_price_rub": row[4],
                "sold_price_rub": row[5],
                "expenses_rub": int(row[6] or 0),
                "status": str(row[7]),
                "created_at": str(row[8]),
                "profit_rub": _deal_profit(row[4], row[5], row[6]),
            }
            for row in rows
        )

    def deals_summary(self, chat_id: str) -> dict:
        """Итог трекера: сколько сделок, сколько денег и средняя маржа."""
        rows = self.list_deals(chat_id, limit=1_000)
        sold = [row for row in rows if row["profit_rub"] is not None]
        profit = sum(int(row["profit_rub"]) for row in sold)
        revenue = sum(int(row["sold_price_rub"] or 0) for row in sold)
        return {
            "count": len(rows),
            "sold": len(sold),
            "profit_rub": profit,
            "revenue_rub": revenue,
            "avg_profit_rub": round(profit / len(sold)) if sold else 0,
        }

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

def _deal_profit(
    bought_price_rub: int | None,
    sold_price_rub: int | None,
    expenses_rub: int | None,
) -> int | None:
    """Прибыль по сделке; None, пока не известны и покупка, и продажа."""
    if bought_price_rub is None or sold_price_rub is None:
        return None
    return int(sold_price_rub) - int(bought_price_rub) - int(expenses_rub or 0)
