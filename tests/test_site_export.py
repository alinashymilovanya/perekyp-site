import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor.main import publish_site_data
from iphone_monitor.site_export import build_site_data, upload_site_data, write_site_data
from iphone_monitor.store import SeenStore


class SiteExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SeenStore(self.root / "seen.sqlite3")
        self.status_path = self.root / "runtime-status.json"
        self.store.record_notified(
            item_id="8499243676",
            title="iPhone 17, 256 ГБ, eSIM",
            price_rub=58_990,
            market_price_rub=79_000,
            margin_rub=18_110,
            url="https://www.avito.ru/lyubertsy/telefony/iphone_17_256_gb_esim_8499243676",
            notified_at="2026-10-30T15:00:00+00:00",
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _write_status(self, heartbeat_at, monitoring=True, failures=0):
        self.status_path.write_text(
            json.dumps(
                {
                    "heartbeat_at": heartbeat_at,
                    "source_failure_count": failures,
                    "monitoring_enabled": monitoring,
                }
            ),
            encoding="utf-8",
        )

    def test_payload_marks_a_fresh_heartbeat_healthy(self):
        self._write_status(1_000.0)
        payload = build_site_data(self.store, status_path=self.status_path, now=1_200.0)
        self.assertTrue(payload["status"]["healthy"])
        self.assertTrue(payload["status"]["live"])
        self.assertTrue(payload["status"]["monitoring_enabled"])
        self.assertEqual(payload["items"][0]["item_id"], "8499243676")
        self.assertEqual(payload["items"][0]["margin_rub"], 18_110)

    def test_payload_marks_a_stale_heartbeat_unhealthy(self):
        self._write_status(1_000.0)
        payload = build_site_data(self.store, status_path=self.status_path, now=5_000.0)
        self.assertFalse(payload["status"]["healthy"])

    def test_missing_status_file_still_returns_items(self):
        payload = build_site_data(
            self.store, status_path=self.root / "absent.json", now=1_000.0
        )
        self.assertFalse(payload["status"]["healthy"])
        self.assertIn("не передавал", payload["status"]["error"])
        self.assertEqual(len(payload["items"]), 1)

    def test_write_site_data_is_readable_json(self):
        self._write_status(1_000.0)
        payload = build_site_data(self.store, status_path=self.status_path, now=1_100.0)
        path = write_site_data(self.root / "site-data.json", payload)
        reloaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["items"][0]["title"], "iPhone 17, 256 ГБ, eSIM")

    def test_publish_is_throttled_by_the_interval(self):
        self._write_status(1_000.0)
        settings = SimpleNamespace(
            status_path=str(self.status_path),
            site_export_path=str(self.root / "site-data.json"),
            site_export_interval_seconds=3600,
            site_ftp_host="",
            site_ftp_user="",
            site_ftp_password="",
            site_ftp_dir="/htdocs",
            github_token="",
            github_repo="",
            github_site_path="site/site-data.json",
            github_branch="master",
        )
        self.assertIsNotNone(publish_site_data(self.store, settings, now=1_000.0))
        self.assertIsNone(publish_site_data(self.store, settings, now=1_500.0))
        self.assertIsNotNone(publish_site_data(self.store, settings, now=5_000.0))

    def test_publish_uploads_when_ftp_is_configured(self):
        self._write_status(1_000.0)
        settings = SimpleNamespace(
            status_path=str(self.status_path),
            site_export_path=str(self.root / "site-data.json"),
            site_export_interval_seconds=0,
            site_ftp_host="ftp.example.com",
            site_ftp_user="user",
            site_ftp_password="secret",
            site_ftp_dir="/htdocs",
            github_token="",
            github_repo="",
            github_site_path="site/site-data.json",
            github_branch="master",
        )
        with mock.patch("iphone_monitor.main.upload_site_data") as upload:
            publish_site_data(self.store, settings, now=1_000.0)
        upload.assert_called_once()
        self.assertEqual(upload.call_args.kwargs["host"], "ftp.example.com")

    def test_upload_rejects_a_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            upload_site_data(
                self.root / "absent.json",
                host="ftp.example.com",
                username="user",
                password="secret",
            )


if __name__ == "__main__":
    unittest.main()
