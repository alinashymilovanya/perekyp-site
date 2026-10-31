"""Публикует сайт в GitHub через API (git в песочнице не может писать на диск).

Создаёт один коммит поверх master: обновляет файлы сайта, добавляет workflow,
удаляет файлы старого дашборда. Push-событие запускает deploy-site.yml.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "alinashymilovanya/perekyp-site"
BRANCH = "master"
ROOT = r"D:\tg"

# файл на диске -> путь в репозитории (None, если путь совпадает)
UPDATE = [
    ("site/index.html", None),
    ("site/cabinet.html", None),
("site/legal.html", None),
    ("site/favicon.svg", None),
    ("site/README.md", None),
    ("site/server.py", None),
    ("site/.htaccess", None),
    (".gitignore", None),
]

DELETE = [
    "site/app.js",
    "site/styles.css",
    "site/package.json",
    "site/site-data.json",
    "site/tests/app.test.js",
]

MESSAGE = """fix(site): HTML больше не кешируется на 30 дней\n\nInfinityFree отдавал .html с cache-control: max-age=2592000, из-за чего\nпосле деплоя браузер показывал старую версию. Теперь html отдаётся\nс no-cache и ревалидацией по ETag.\n\nfeat(site): новый лендинг VISHIT и кабинет

- лендинг: hero-терминал, находки с колонкой «Доставка», расчёт маржи, тарифы, FAQ
- кабинет: фильтры, KPI, график маржи, таблица находок, статус бота, подписка Pro
- маршрут: главная -> кабинет -> бот, все кнопки ведут в кабинет
- удалён старый светлый дашборд (monitor.html, app.js, styles.css, package.json)
- tests.yml: job site больше не зависит от node
"""

TOKEN = os.environ.get("GH_TOKEN") or ""
if not TOKEN:
    sys.exit("GH_TOKEN is not set")


def api(method: str, path: str, payload: dict | None = None) -> dict:
    url = f"https://api.github.com{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        detail = exc.read().decode("utf-8", "replace")
        sys.exit(f"{method} {path} -> {exc.code}: {detail}")


def blob_sha(rel: str) -> str:
    raw = open(os.path.join(ROOT, rel), "rb").read()
    content = base64.b64encode(raw).decode("ascii")
    blob = api("POST", f"/repos/{REPO}/git/blobs", {"content": content, "encoding": "base64"})
    return blob["sha"]


head = api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}")
parent = head["object"]["sha"]
commit = api("GET", f"/repos/{REPO}/git/commits/{parent}")
base_tree = commit["tree"]["sha"]
print(f"parent {parent[:7]} tree {base_tree[:7]}")

entries = []
for rel, _ in UPDATE:
    entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob_sha(rel)})
    print(f"  + {rel}")
tree_data = api("GET", f"/repos/{REPO}/git/trees/{base_tree}?recursive=1")
existing = {item["path"] for item in tree_data.get("tree", [])}
for rel in DELETE:
    if rel in existing:
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": None})
        print(f"  - {rel}")

tree = api("POST", f"/repos/{REPO}/git/trees", {"base_tree": base_tree, "tree": entries})
new_commit = api(
    "POST",
    f"/repos/{REPO}/git/commits",
    {"message": MESSAGE, "tree": tree["sha"], "parents": [parent]},
)
print(f"commit {new_commit['sha'][:7]}")

api(
    "PATCH",
    f"/repos/{REPO}/git/refs/heads/{BRANCH}",
    {"sha": new_commit["sha"], "force": False},
)
print("pushed to", BRANCH)
