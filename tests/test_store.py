import tempfile
import unittest
from pathlib import Path

from iphone_monitor.store import SeenStore


class SeenStoreTests(unittest.TestCase):
    def test_tracks_seen_listing_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SeenStore(Path(directory) / "seen.sqlite3")

            self.assertFalse(store.has_seen("123"))
            store.mark_seen("123")
            self.assertTrue(store.has_seen("123"))

            store.close()
