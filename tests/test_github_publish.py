import json
import unittest
import urllib.error

from iphone_monitor.github_publish import GitHubPublishError, publish_snapshot


class FakeResponse:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


class FakeGitHub:
    def __init__(self, *, existing_sha: str | None = None, status: int = 200):
        self.existing_sha = existing_sha
        self.status = status
        self.requests: list = []

    def __call__(self, request, timeout=30):
        self.requests.append(request)
        if self.status != 200:
            raise urllib.error.HTTPError(
                request.full_url, self.status, "error", {}, None
            )
        if request.get_method() == "GET":
            if self.existing_sha is None:
                raise urllib.error.HTTPError(
                    request.full_url, 404, "not found", {}, None
                )
            return FakeResponse({"sha": self.existing_sha})
        return FakeResponse({"commit": {"sha": "abc1234567890"}})


class GitHubPublishTests(unittest.TestCase):
    def test_new_file_is_created_without_sha(self):
        fake = FakeGitHub(existing_sha=None)

        result = publish_snapshot(
            token="t",
            repo="owner/repo",
            path="site/site-data.json",
            content='{"items": []}',
            message="snapshot",
            opener=fake,
        )

        self.assertEqual(result["commit"]["sha"], "abc1234567890")
        put = fake.requests[-1]
        self.assertEqual(put.get_method(), "PUT")
        self.assertEqual(
            put.get_full_url(),
            "https://api.github.com/repos/owner/repo/contents/site/site-data.json",
        )
        self.assertNotIn("sha", json.loads(put.data.decode("utf-8")))

    def test_existing_file_is_updated_with_sha(self):
        fake = FakeGitHub(existing_sha="deadbeef")

        publish_snapshot(
            token="t",
            repo="owner/repo",
            path="site/site-data.json",
            content="{}",
            message="snapshot",
            opener=fake,
        )

        body = json.loads(fake.requests[-1].data.decode("utf-8"))
        self.assertEqual(body["sha"], "deadbeef")
        self.assertEqual(body["branch"], "master")
        self.assertEqual(body["content"], "e30=")

    def test_rejected_token_is_explained(self):
        fake = FakeGitHub(status=401)

        with self.assertRaises(GitHubPublishError) as ctx:
            publish_snapshot(
                token="t",
                repo="owner/repo",
                path="site/site-data.json",
                content="{}",
                message="snapshot",
                opener=fake,
            )

        self.assertIn("GITHUB_TOKEN", str(ctx.exception))

    def test_missing_token_is_rejected_before_any_request(self):
        fake = FakeGitHub()

        with self.assertRaises(GitHubPublishError):
            publish_snapshot(
                token="",
                repo="owner/repo",
                path="site/site-data.json",
                content="{}",
                message="snapshot",
                opener=fake,
            )

        self.assertEqual(fake.requests, [])

    def test_custom_api_root_and_branch(self):
        fake = FakeGitHub(existing_sha=None)

        publish_snapshot(
            token="t",
            repo="owner/repo",
            path="/site/site-data.json",
            content="{}",
            message="snapshot",
            branch="main",
            api_root="https://example.test",
            opener=fake,
        )

        body = json.loads(fake.requests[-1].data.decode("utf-8"))
        self.assertEqual(body["branch"], "main")
        self.assertTrue(
            fake.requests[-1].get_full_url().startswith(
                "https://example.test/repos/owner/repo/contents/site/site-data.json"
            )
        )
