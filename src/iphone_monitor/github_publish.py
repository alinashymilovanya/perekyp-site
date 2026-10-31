"""Publishes the dashboard snapshot to the GitHub repo that deploys the site."""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.github.com"
USER_AGENT = "iphone-monitor"
SITE_DEPLOY_WORKFLOW = "deploy-site.yml"


class GitHubPublishError(RuntimeError):
    """Raised when the snapshot could not be committed to the repository."""


def _open(request, *, opener, timeout):
    open_url = opener or urllib.request.urlopen
    return open_url(request, timeout=timeout)


def _read_json(response) -> dict:
    raw = response.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _current_sha(url: str, *, token: str, opener=None, timeout: int = 30) -> str | None:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with _open(request, opener=opener, timeout=timeout) as response:
            payload = _read_json(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise GitHubPublishError(_describe(exc, url)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubPublishError(f"Не удалось связаться с GitHub: {exc}") from exc
    return payload.get("sha")


def _describe(exc: urllib.error.HTTPError, url: str) -> str:
    if exc.code in (401, 403):
        return (
            "GitHub отклонил токен (HTTP "
            f"{exc.code}). Проверьте GITHUB_TOKEN и права на запись в репозиторий."
        )
    if exc.code == 404:
        return f"GitHub не нашёл репозиторий или файл: {url}"
    if exc.code == 409:
        return "GitHub сообщил о конфликте версий файла, повторите позже."
    return f"GitHub ответил HTTP {exc.code}"


def publish_snapshot(
    *,
    token: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str = "master",
    api_root: str = API_ROOT,
    opener=None,
    timeout: int = 30,
) -> dict:
    """Create or update one text file in the repository, returning the commit payload."""
    if not token:
        raise GitHubPublishError("GITHUB_TOKEN не задан")
    if not repo or "/" not in repo:
        raise GitHubPublishError("GITHUB_REPO должен выглядеть как владелец/репозиторий")
    if not path:
        raise GitHubPublishError("GITHUB_SITE_PATH не задан")
    url = f"{api_root}/repos/{repo}/contents/{path.lstrip('/')}"
    sha = _current_sha(url, token=token, opener=opener, timeout=timeout)
    body = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="PUT"
    )
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with _open(request, opener=opener, timeout=timeout) as response:
            return _read_json(response)
    except urllib.error.HTTPError as exc:
        raise GitHubPublishError(_describe(exc, url)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubPublishError(f"Не удалось связаться с GitHub: {exc}") from exc


DEPLOY_STATES = ("success", "failed", "in_progress", "queued", "not_found")


def describe_workflow_run(payload: dict) -> dict:
    """Turn one Actions run payload into the state the log view can show.

    A commit is not a deployment: only a completed run with conclusion
    "success" may be reported as a published site.
    """
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list) or not runs or not isinstance(runs[0], dict):
        return {
            "state": "not_found",
            "status": "",
            "conclusion": "",
            "url": "",
            "run_number": None,
            "head_sha": "",
        }
    run = runs[0]
    status = str(run.get("status") or "")
    conclusion = str(run.get("conclusion") or "")
    if status == "completed":
        state = "success" if conclusion == "success" else "failed"
    elif status in {"queued", "requested", "waiting", "pending"}:
        state = "queued"
    elif not status:
        state = "not_found"
    else:
        state = "in_progress"
    return {
        "state": state,
        "status": status,
        "conclusion": conclusion,
        "url": str(run.get("html_url") or ""),
        "run_number": run.get("run_number"),
        "head_sha": str(run.get("head_sha") or ""),
    }


def fetch_deploy_run(
    *,
    token: str,
    repo: str,
    branch: str = "master",
    head_sha: str | None = None,
    workflow: str = SITE_DEPLOY_WORKFLOW,
    api_root: str = API_ROOT,
    opener=None,
    timeout: int = 20,
) -> dict:
    """Ask GitHub Actions whether the site deployment has finished yet."""
    if not token:
        raise GitHubPublishError("GITHUB_TOKEN не задан")
    if not repo or "/" not in repo:
        raise GitHubPublishError("GITHUB_REPO должен выглядеть как владелец/репозиторий")
    query = {"per_page": "1"}
    if branch:
        query["branch"] = branch
    if head_sha:
        query["head_sha"] = head_sha
    url = (
        f"{api_root}/repos/{repo}/actions/workflows/{workflow}/runs?"
        + urllib.parse.urlencode(query)
    )
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", USER_AGENT)
    try:
        with _open(request, opener=opener, timeout=timeout) as response:
            payload = _read_json(response)
    except urllib.error.HTTPError as exc:
        raise GitHubPublishError(_describe(exc, url)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubPublishError(f"Не удалось связаться с GitHub: {exc}") from exc
    return describe_workflow_run(payload)
