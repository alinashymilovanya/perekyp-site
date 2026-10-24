"""Creates SQLite snapshots without stopping the monitor process."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def backup_database(
    database_path: str | Path,
    backup_directory: str | Path,
    label: str | None = None,
) -> Path:
    source_path = Path(database_path)
    destination_directory = Path(backup_directory)
    destination_directory.mkdir(parents=True, exist_ok=True)
    snapshot_label = label or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    destination_path = destination_directory / f"seen-{snapshot_label}.sqlite3"

    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return destination_path


def main() -> int:
    database_path = os.environ.get("DATABASE_PATH", "data/seen.sqlite3")
    backup_directory = os.environ.get("BACKUP_DIRECTORY", "data/backups")
    if not Path(database_path).exists():
        return 1
    backup_database(database_path, backup_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
