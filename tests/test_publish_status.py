"""The snapshot, the upload and the deployment are three different stages."""

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from iphone_monitor.github_publish import GitHubPublishError, describe_workflow_run
from iphone_monitor.main import (
    SITE_PUBLICATION_METADATA_KEY,
    new_cycle_id,
    publish_site_data,
)
from iphone_monitor.store import SeenStore


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[dict] = []

    def emit(self, record):
        self.records.append(json.loads(record.getMessage()))

    def events(self, name: str) -> list[dict]:
        return [record for record in self.records if record.get("event") == name]


class PublishStatusTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = SeenStore(self.root / "seen.sqlite3")
        self.status_path = self.root / "runtime-status.json"
        self.status_path.write_text(
            json.dumps({"heartbeat_at": 1_000.0, "monitoring_enabled": True}),
            encoding="utf-8",
        )
        self.logger = logging.getLogger("iphone_monitor")
        self.capture = _Capture()
        self.logger.addHandler(self.capture)
        self.logger.setLevel(logging.INFO)
        self._env = mock.patch.dict("os.environ", {}, clear=False)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.logger.removeHandler(self.capture)
        self.store.close()
        self._tmp.cleanup()

    def settings(self, **overrides):
        base = dict(
            status_path=self.status_path,
            site_export_path=self.root / "site-data.json",
            site_export_interval_seconds=3_600,
            github_token="",
            github_repo="",
            github_site_path="site/site-data.json",
            github_branch="master",
            site_ftp_host="",
            site_ftp_user="",
            site_ftp_password="",
            site_ftp_dir="/htdocs",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def _event(self, name: str) -> dict:
        found = self.capture.events(name)
        self.assertTrue(found, f"no {name} event: {[r['event'] for r in self.capture.records]}")
        return found[-1]

    def publication(self) -> dict:
        raw = self.store.get_metadata(SITE_PUBLICATION_METADATA_KEY)
        return json.loads(raw)

    def test_local_snapshot_is_not_a_publication(self):
        publication = publish_site_data(self.store, self.settings(), force=True)
        self.assertEqual(publication["stage"], "local_only")
        self.assertFalse(publication["deploy_confirmed"])
        self.assertEqual(self._event("site_snapshot_local_only")["status"], "skipped")
        self.assertFalse(self.publication()["deploy_confirmed"])

    def test_github_commit_builds_a_snapshot_but_does_not_confirm_the_deploy(self):
        result = {"commit": {"sha": "62c7131f9a" * 4}}
        with mock.patch(
            "iphone_monitor.main.publish_snapshot", return_value=result
        ) as publish:
            publication = publish_site_data(
                self.store, self.settings(github_token="token", github_repo="owner/repo"), force=True
            )
        self.assertEqual(publish.call_count, 1)
        self.assertEqual(publication["stage"], "awaiting_deploy")
        self.assertFalse(publication["deploy_confirmed"])
        self.assertEqual(publication["commit"], "62c7131f")
        committed = self._event("site_snapshot_committed")
        self.assertFalse(committed["details"]["deploy_confirmed"])
        self.assertEqual(committed["details"]["stage"], "awaiting_deploy")
        state = self._event("site_publication_state")
        self.assertEqual(state["status"], "queued")
        self.assertFalse(state["details"]["deploy_confirmed"])
        self.assertFalse(self.publication()["deploy_confirmed"])

    def test_failed_github_publish_does_not_look_published(self):
        fake_token = "ghp_" + "a1b2c3d4e5" * 3 + "abcd"
        with mock.patch(
            "iphone_monitor.main.publish_snapshot",
            side_effect=GitHubPublishError(f"401: bad token {fake_token}"),
        ):
            publication = publish_site_data(
                self.store, self.settings(github_token="t", github_repo="owner/repo"), force=True
            )
        self.assertEqual(publication["stage"], "snapshot")
        self.assertFalse(publication["deploy_confirmed"])
        failed = self._event("site_snapshot_publish_failed")
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn(fake_token, json.dumps(failed, ensure_ascii=False))

    def test_ftp_upload_is_a_confirmed_publication(self):
        with mock.patch("iphone_monitor.main.upload_site_data") as upload:
            publication = publish_site_data(
                self.store,
                self.settings(
                    site_ftp_host="ftp.example", site_ftp_user="u", site_ftp_password="p"
                ),
                force=True,
            )
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(publication["stage"], "published")
        self.assertTrue(publication["deploy_confirmed"])
        self.assertEqual(self._event("site_snapshot_uploaded")["status"], "success")
        self.assertEqual(self._event("site_publication_state")["status"], "success")

    def test_ftp_failure_raises_and_stays_awaiting_upload(self):
        with mock.patch(
            "iphone_monitor.main.upload_site_data", side_effect=OSError("530 login incorrect")
        ):
            with self.assertRaises(OSError):
                publish_site_data(
                    self.store,
                    self.settings(
                        site_ftp_host="ftp.example", site_ftp_user="u", site_ftp_password="p"
                    ),
                    force=True,
                )
        failed = self._event("site_snapshot_upload_failed")
        self.assertEqual(failed["status"], "failed")

    def test_throttled_call_returns_none(self):
        self.settings()
        first = publish_site_data(self.store, self.settings(), now=1_000.0, force=True)
        self.assertIsNotNone(first)
        second = publish_site_data(self.store, self.settings(), now=1_010.0)
        self.assertIsNone(second)

    def test_deploy_check_marks_the_publication_confirmed(self):
        run = {
            "state": "success",
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.com/owner/repo/actions/runs/1",
            "run_number": 1,
            "head_sha": "62c7131f",
        }
        with mock.patch.dict("os.environ", {"GITHUB_DEPLOY_CHECK": "1"}):
            with mock.patch(
                "iphone_monitor.main.publish_snapshot",
                return_value={"commit": {"sha": "62c7131f9a" * 4}},
            ), mock.patch("iphone_monitor.main.fetch_deploy_run", return_value=run) as deploy:
                publication = publish_site_data(
                    self.store,
                    self.settings(github_token="t", github_repo="owner/repo"),
                    force=True,
                )
        self.assertEqual(deploy.call_args.kwargs["head_sha"], "62c7131f9a" * 4)
        self.assertEqual(publication["stage"], "published")
        self.assertTrue(publication["deploy_confirmed"])
        self.assertEqual(self._event("site_deploy_confirmed")["status"], "success")

    def test_deploy_check_pending_keeps_the_site_unconfirmed(self):
        run = {
            "state": "queued",
            "status": "queued",
            "conclusion": None,
            "url": "https://github.com/owner/repo/actions/runs/2",
            "run_number": 2,
            "head_sha": "62c7131f",
        }
        with mock.patch.dict("os.environ", {"GITHUB_DEPLOY_CHECK": "1"}):
            with mock.patch(
                "iphone_monitor.main.publish_snapshot",
                return_value={"commit": {"sha": "62c7131f9a" * 4}},
            ), mock.patch("iphone_monitor.main.fetch_deploy_run", return_value=run):
                publication = publish_site_data(
                    self.store,
                    self.settings(github_token="t", github_repo="owner/repo"),
                    force=True,
                )
        self.assertEqual(publication["stage"], "awaiting_deploy")
        self.assertFalse(publication["deploy_confirmed"])
        pending = self._event("site_deploy_pending")
        self.assertEqual(pending["status"], "queued")

    def test_failed_workflow_is_shown_as_retried(self):
        run = {
            "state": "failed",
            "status": "completed",
            "conclusion": "failure",
            "url": "https://github.com/owner/repo/actions/runs/3",
            "run_number": 3,
            "head_sha": "62c7131f",
        }
        with mock.patch.dict("os.environ", {"GITHUB_DEPLOY_CHECK": "1"}):
            with mock.patch(
                "iphone_monitor.main.publish_snapshot",
                return_value={"commit": {"sha": "62c7131f9a" * 4}},
            ), mock.patch("iphone_monitor.main.fetch_deploy_run", return_value=run):
                publication = publish_site_data(
                    self.store,
                    self.settings(github_token="t", github_repo="owner/repo"),
                    force=True,
                )
        self.assertFalse(publication["deploy_confirmed"])
        self.assertEqual(self._event("site_deploy_pending")["status"], "retried")

    def test_unknown_deploy_state_is_a_failure_not_a_confirmation(self):
        with mock.patch.dict("os.environ", {"GITHUB_DEPLOY_CHECK": "1"}):
            with mock.patch(
                "iphone_monitor.main.publish_snapshot",
                return_value={"commit": {"sha": "62c7131f9a" * 4}},
            ), mock.patch(
                "iphone_monitor.main.fetch_deploy_run",
                side_effect=GitHubPublishError("network down"),
            ):
                publication = publish_site_data(
                    self.store,
                    self.settings(github_token="t", github_repo="owner/repo"),
                    force=True,
                )
        self.assertFalse(publication["deploy_confirmed"])
        self.assertEqual(self._event("site_deploy_status_unknown")["status"], "failed")

    def test_deploy_check_is_off_by_default(self):
        with mock.patch(
            "iphone_monitor.main.publish_snapshot",
            return_value={"commit": {"sha": "62c7131f9a" * 4}},
        ), mock.patch("iphone_monitor.main.fetch_deploy_run") as deploy:
            publish_site_data(
                self.store, self.settings(github_token="t", github_repo="owner/repo"), force=True
            )
        deploy.assert_not_called()


class WorkflowRunTests(unittest.TestCase):
    def test_successful_run(self):
        state = describe_workflow_run(
            {"workflow_runs": [{"status": "completed", "conclusion": "success", "html_url": "u", "run_number": 7}]}
        )
        self.assertEqual(state["state"], "success")

    def test_failed_run(self):
        state = describe_workflow_run(
            {"workflow_runs": [{"status": "completed", "conclusion": "failure"}]}
        )
        self.assertEqual(state["state"], "failed")

    def test_in_progress_and_queued_runs(self):
        for status, expected in (
            ("in_progress", "in_progress"),
            ("queued", "queued"),
            ("requested", "queued"),
            ("waiting", "queued"),
        ):
            state = describe_workflow_run(
                {"workflow_runs": [{"status": status, "conclusion": None}]}
            )
            self.assertEqual(state["state"], expected, status)

    def test_no_run_is_not_found(self):
        self.assertEqual(describe_workflow_run({"workflow_runs": []})["state"], "not_found")
        self.assertEqual(describe_workflow_run({})["state"], "not_found")


class CycleIdTests(unittest.TestCase):
    def test_cycle_id_is_unique_and_sortable(self):
        first = new_cycle_id(1_700_000_000.0)
        second = new_cycle_id(1_700_000_060.0)
        self.assertTrue(first.startswith("20231114T221320Z-"))
        self.assertLess(first, second)
        self.assertNotEqual(first, new_cycle_id(1_700_000_000.0))


if __name__ == "__main__":
    unittest.main()
