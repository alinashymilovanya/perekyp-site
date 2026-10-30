import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor.backup import backup_database, prune_backups
from iphone_monitor.main import maybe_run_backup
from iphone_monitor.store import SeenStore


class PruneBackupsTests(unittest.TestCase):
    def test_keeps_newest_snapshots_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(10):
                path = directory / f"seen-2026-01-{index + 1:02d}T03-00-00Z.sqlite3"
                path.write_bytes(b"x")
                os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
            removed = prune_backups(directory, keep=3)
            remaining = sorted(path.name for path in directory.glob("seen-*.sqlite3"))
            self.assertEqual(len(removed), 7)
            self.assertEqual(
                remaining,
                [
                    "seen-2026-01-08T03-00-00Z.sqlite3",
                    "seen-2026-01-09T03-00-00Z.sqlite3",
                    "seen-2026-01-10T03-00-00Z.sqlite3",
                ],
            )

    def test_missing_directory_is_not_an_error(self):
        self.assertEqual(prune_backups(Path(tempfile.gettempdir()) / "nope-xyz", keep=2), [])

    def test_keep_must_be_positive(self):
        with self.assertRaises(ValueError):
            prune_backups(Path(tempfile.gettempdir()), keep=0)


class DailyBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / "seen.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.execute("create table demo(id integer)")
        connection.commit()
        connection.close()
        self.store = SeenStore(self.database)
        self.settings = SimpleNamespace(
            database_path=str(self.database), summary_timezone="Europe/Astrakhan"
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_runs_once_per_day_after_the_night_hour(self):
        night = datetime(2026, 1, 5, 3, 30, tzinfo=ZoneInfo("Europe/Astrakhan"))
        created = maybe_run_backup(self.store, self.settings, now=night)
        self.assertIsNotNone(created)
        self.assertTrue(Path(created).is_file())
        self.assertEqual(self.store.get_metadata("last_backup_date"), "2026-01-05")
        self.assertIsNone(maybe_run_backup(self.store, self.settings, now=night))

    def test_skips_before_the_night_hour(self):
        early = datetime(2026, 1, 5, 1, 0, tzinfo=ZoneInfo("Europe/Astrakhan"))
        self.assertIsNone(maybe_run_backup(self.store, self.settings, now=early))
        self.assertFalse((self.root / "backups").exists())

    def test_keeps_only_the_configured_number_of_snapshots(self):
        directory = self.root / "backups"
        for index in range(9):
            path = backup_database(self.database, directory, label=f"2026-01-0{index + 1}")
            os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
        maybe_run_backup(
            self.store,
            self.settings,
            now=datetime(2026, 1, 20, 4, 0, tzinfo=ZoneInfo("Europe/Astrakhan")),
        )
        snapshots = list(directory.glob("seen-*.sqlite3"))
        self.assertEqual(len(snapshots), 7)


if __name__ == "__main__":
    unittest.main()
