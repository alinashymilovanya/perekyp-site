import tempfile
import unittest
from pathlib import Path

from iphone_monitor.backup import backup_database
from iphone_monitor.store import SeenStore


class BackupTests(unittest.TestCase):
    def test_creates_a_readable_snapshot_of_the_sqlite_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "seen.sqlite3"
            with SeenStore(database_path) as store:
                store.mark_seen("123")
                backup_path = backup_database(database_path, root / "backups", "2026-10-24")

            with SeenStore(backup_path) as backup_store:
                self.assertTrue(backup_store.has_seen("123"))
