"""Выгружает текущее состояние бота в assets/seed_state.json.

Нужно, чтобы при переезде бота на другой сервер (Amvera) он не забыл,
какие объявления уже видел и о каких уже сообщил.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "seen.sqlite3"
DEFAULT_OUT = ROOT / "assets" / "seed_state.json"


def collect(database_path: Path) -> dict:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        seen = [
            {"item_id": row["item_id"], "first_seen_at": row["first_seen_at"]}
            for row in connection.execute(
                "SELECT item_id, first_seen_at FROM seen_listings ORDER BY first_seen_at"
            )
        ]
        notified = [
            {
                "item_id": row["item_id"],
                "notified_at": row["notified_at"],
                "title": row["title"],
                "price_rub": row["price_rub"],
                "market_price_rub": row["market_price_rub"],
                "margin_rub": row["margin_rub"],
                "url": row["url"],
                "model": row["model"] if "model" in row.keys() else None,
                "storage_gb": row["storage_gb"] if "storage_gb" in row.keys() else None,
            }
            for row in connection.execute(
                "SELECT item_id, notified_at, title, price_rub, market_price_rub, "
                "margin_rub, url, model, storage_gb FROM notified_listings "
                "ORDER BY notified_at"
            )
        ]
        prices = [
            {
                "item_id": row["item_id"],
                "model": row["model"],
                "storage_gb": row["storage_gb"],
                "condition": row["condition"],
                "price_rub": row["price_rub"],
                "seen_at": row["seen_at"],
            }
            for row in connection.execute(
                "SELECT item_id, model, storage_gb, condition, price_rub, seen_at "
                "FROM listing_prices ORDER BY seen_at"
            )
        ]
        # Читателей НЕ выгружаем: файл лежит в публичном репозитории,
        # а chat_id приезжает на сервер из переменной TELEGRAM_CHAT_ID.
        subscribers: list[dict] = []
    finally:
        connection.close()
    return {
        "seen_listings": seen,
        "notified_listings": notified,
        "listing_prices": prices,
        "subscribers": subscribers,
    }


def main(argv: list[str]) -> int:
    database = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
    out = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUT
    if not database.is_file():
        print(f"нет базы: {database}", file=sys.stderr)
        return 1
    payload = collect(database)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"записал {out}: "
        f"видели {len(payload['seen_listings'])}, "
        f"уведомили {len(payload['notified_listings'])}, "
        f"цен {len(payload['listing_prices'])}, "
        f"читателей {len(payload['subscribers'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
