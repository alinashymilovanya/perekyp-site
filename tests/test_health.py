import tempfile
import unittest
from pathlib import Path

from iphone_monitor.health import is_healthy, read_status, write_status


class HealthTests(unittest.TestCase):
    def test_writes_and_reads_fresh_process_status(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-status.json"
            write_status(path, heartbeat_at=1_000, source_failure_count=0, monitoring_enabled=True)

            status = read_status(path)

            self.assertTrue(is_healthy(status, now=1_030, max_age_seconds=60))

    def test_stale_heartbeat_is_unhealthy(self):
        status = {"heartbeat_at": 1_000}

        self.assertFalse(is_healthy(status, now=1_061, max_age_seconds=60))
