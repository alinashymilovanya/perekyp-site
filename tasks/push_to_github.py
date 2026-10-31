"""Push the working tree to GitHub through the Git Data API (no git binary)."""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
SKIP_DIRS = {
    ".git",
    ".tools",
    ".venv",
    "backup",
    "data",
    "node_modules",
    "preview",
    "__pycache__",
    ".pytest_cache",
}
SKIP_FILES = {".env", "site/site-data.json"}
SKIP_PREFIXES = (".github/workflows/",)


def env_value(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def call(method: str, url: str, token: str, payload=None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "iphone-monitor-push")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"{method} {url} -> HTTP {exc.code}: {detail}")
    return json.loads(body) if body else {}


REQUIRED_PATHS = (".github/workflows/deploy-site.yml",)


def missing_required(tree_sha: str, token: str, repo: str) -> list[str]:
    """Return required files that the new tree would silently drop."""
    url = f"{API}/repos/{repo}/git/trees/{tree_sha}?recursive=1"
    try:
        tree = call("GET", url, token)
    except SystemExit as exc:
        print(f"warning: could not verify required files: {exc}")
        return []
    present = {item.get("path") for item in tree.get("tree", [])}
    return [path for path in REQUIRED_PATHS if path not in present]


def collect() -> list[Path]:
    files = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        parts = set(path.relative_to(ROOT).parts)
        if parts & SKIP_DIRS:
            continue
        if rel in SKIP_FILES or rel.startswith(SKIP_PREFIXES):
            continue
        if rel.endswith((".pyc", ".session", ".session-journal")):
            continue
        files.append(path)
    return files


def main() -> int:
    token = env_value("GITHUB_TOKEN")
    repo = env_value("GITHUB_REPO")
    branch = env_value("GITHUB_BRANCH") or "master"
    message = sys.argv[1] if len(sys.argv) > 1 else "chore: sync working tree"
    if not token or "/" not in repo:
        raise SystemExit("GITHUB_TOKEN and GITHUB_REPO are required")

    head = call("GET", f"{API}/repos/{repo}/git/ref/heads/{branch}", token)["object"]["sha"]
    commit = call("GET", f"{API}/repos/{repo}/git/commits/{head}", token)
    base_tree = commit["tree"]["sha"]

    entries = []
    files = collect()
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
            blob = call(
                "POST",
                f"{API}/repos/{repo}/git/blobs",
                token,
                {"content": text, "encoding": "utf-8"},
            )
        except UnicodeDecodeError:
            blob = call(
                "POST",
                f"{API}/repos/{repo}/git/blobs",
                token,
                {
                    "content": base64.b64encode(path.read_bytes()).decode("ascii"),
                    "encoding": "base64",
                },
            )
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})

    tree = call(
        "POST",
        f"{API}/repos/{repo}/git/trees",
        token,
        {"base_tree": base_tree, "tree": entries},
    )
    new_commit = call(
        "POST",
        f"{API}/repos/{repo}/git/commits",
        token,
        {"message": message, "tree": tree["sha"], "parents": [head]},
    )
    missing = missing_required(new_commit["tree"]["sha"], token, repo)
    if missing:
        raise SystemExit(
            "refusing to move the branch: these files would disappear from the repo: "
            + ", ".join(missing)
            + " (a workflow file needs the 'workflow' token scope; restore it in the web UI)"
        )
    call(
        "PATCH",
        f"{API}/repos/{repo}/git/refs/heads/{branch}",
        token,
        {"sha": new_commit["sha"], "force": False},
    )
    print(f"pushed {len(entries)} files as {new_commit['sha'][:8]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
