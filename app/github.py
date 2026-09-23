"""Minimal GitHub REST client for publishing the app and syncing its encrypted state (no git needed).

Token: a fine-grained personal access token for the one repository, with read/write on
Contents, Actions, Secrets, Workflows and Pages (Metadata read is implied).
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from nacl.public import PublicKey, SealedBox

API = "https://api.github.com"


class GitHubError(RuntimeError):
    pass


class GitHub:
    def __init__(self, owner: str, repo: str, token: str, api: str = API):
        self.owner, self.repo, self.api = owner, repo, api.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                               "X-GitHub-Api-Version": "2022-11-28"})

    def _url(self, p: str) -> str:
        return f"{self.api}/repos/{self.owner}/{self.repo}{p}"

    def _req(self, method: str, p: str, ok=(200, 201, 204), **kw) -> requests.Response:
        r = self.s.request(method, self._url(p), timeout=60, **kw)
        if r.status_code not in ok:
            raise GitHubError(f"GitHub {r.status_code} {method} {p}: {r.text[:300]}")
        return r

    # -- files on a branch --------------------------------------------------------------------
    def file(self, path: str, branch: str = "data") -> Optional[Tuple[str, bytes]]:
        r = self._req("GET", f"/contents/{path}", ok=(200, 404), params={"ref": branch})
        if r.status_code == 404:
            return None
        meta = r.json()
        if meta.get("encoding") == "base64" and meta.get("content"):
            return meta["sha"], base64.b64decode(meta["content"])
        raw = self._req("GET", f"/contents/{path}", params={"ref": branch},
                        headers={"Accept": "application/vnd.github.raw"})
        return meta["sha"], raw.content

    def put(self, path: str, data: bytes, sha: Optional[str], message: str, branch: str = "data") -> str:
        """Returns the new sha; raises GitHubError with .conflict if someone else changed the file first."""
        r = self.s.put(self._url(f"/contents/{path}"), timeout=60, json={
            "message": message, "content": base64.b64encode(data).decode(), "branch": branch,
            **({"sha": sha} if sha else {})})
        if r.status_code in (409, 422):
            e = GitHubError("conflict")
            e.conflict = True
            raise e
        if r.status_code not in (200, 201):
            raise GitHubError(f"GitHub {r.status_code} saving {path}: {r.text[:300]}")
        return r.json()["content"]["sha"]

    def is_empty(self) -> bool:
        """GitHub answers 409 "Git Repository is empty." to ref lookups until the first commit exists."""
        return self._req("GET", "/git/ref/heads/main", ok=(200, 404, 409)).status_code == 409

    def seed_empty_repo(self) -> None:
        """The git data API refuses to work on an empty repo; the contents API can make its first commit."""
        self.put("README.md", b"# Job board\n\nSetting up\u2026\n", None, "Initial commit", branch="main")

    def branch_head(self, branch: str) -> Optional[str]:
        r = self._req("GET", f"/git/ref/heads/{branch}", ok=(200, 404, 409))
        return r.json()["object"]["sha"] if r.status_code == 200 else None

    def commit_files(self, branch: str, files: Dict[str, bytes], message: str, orphan: bool = False) -> None:
        """One commit with all `files` (added/replaced) on `branch`, creating the branch if needed."""
        head = None if orphan else self.branch_head(branch)
        tree = []
        for path, data in files.items():
            blob = self._req("POST", "/git/blobs", json={"content": base64.b64encode(data).decode(),
                                                         "encoding": "base64"}).json()["sha"]
            tree.append({"path": path, "mode": "100755" if path.endswith(".sh") else "100644", "type": "blob", "sha": blob})
        body = {"tree": tree}
        if head:
            body["base_tree"] = self._req("GET", f"/git/commits/{head}").json()["tree"]["sha"]
        tree_sha = self._req("POST", "/git/trees", json=body).json()["sha"]
        commit = self._req("POST", "/git/commits", json={"message": message, "tree": tree_sha,
                                                         "parents": [head] if head else []}).json()["sha"]
        if head:
            self._req("PATCH", f"/git/refs/heads/{branch}", json={"sha": commit, "force": False})
        else:
            self._req("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": commit})

    # -- secrets, Pages, Actions -----------------------------------------------------------------
    def set_secret(self, name: str, value: str) -> None:
        pk = self._req("GET", "/actions/secrets/public-key").json()
        sealed = SealedBox(PublicKey(base64.b64decode(pk["key"]))).encrypt(value.encode())
        self._req("PUT", f"/actions/secrets/{name}", json={"encrypted_value": base64.b64encode(sealed).decode(),
                                                           "key_id": pk["key_id"]})

    def delete_secret(self, name: str) -> None:
        self._req("DELETE", f"/actions/secrets/{name}", ok=(204, 404))

    def enable_pages(self) -> bool:
        """True if Pages is on (or was already). False if the token may not do it (no Pages permission):
        the user then flips it once in Settings → Pages → Source: GitHub Actions."""
        r = self._req("POST", "/pages", ok=(201, 403, 409, 422), json={"build_type": "workflow"})
        return r.status_code != 403

    def dispatch(self, workflow: str = "daily.yml", inputs: Optional[dict] = None) -> None:
        body = {"ref": "main"}
        if workflow == "daily.yml":
            body["inputs"] = inputs or {"force": True}
        self._req("POST", f"/actions/workflows/{workflow}/dispatches", json=body)

    def dispatch_soon(self, workflow: str, tries: int = 6, wait: float = 5) -> bool:
        """Workflows pushed a moment ago take a few seconds to register; retry briefly."""
        import time
        for _ in range(tries):
            try:
                self.dispatch(workflow)
                return True
            except GitHubError:
                time.sleep(wait)
        return False

    def latest_run(self) -> Optional[dict]:
        runs = self._req("GET", "/actions/workflows/daily.yml/runs", params={"per_page": 5}).json().get("workflow_runs", [])
        return next((r for r in runs if r["status"] != "completed"), runs[0] if runs else None)


# -- what gets published to the public repo ------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
PUBLISH = ["app", "web", "scripts", "tests", "kaggle_kernel", ".github", "README.md", "requirements.txt",
           "run.sh", ".env.example", ".gitignore",
           "bench/make_set.py", "bench/run.py", "bench/report.py", "bench/run_all.sh", "bench/set.json",
           "bench/gold.json", "bench/gold_raw.py", "bench/results/report.md"]
NEVER = ("__pycache__", ".pyc", ".DS_Store")


def publishable_files() -> Dict[str, bytes]:
    """The code, never data: no data/, .env, engines, local results or keys."""
    out: Dict[str, bytes] = {}
    for entry in PUBLISH:
        p = ROOT / entry
        paths: List[Path] = [p] if p.is_file() else sorted(x for x in p.rglob("*") if x.is_file()) if p.exists() else []
        for f in paths:
            rel = f.relative_to(ROOT).as_posix()
            if not any(n in rel for n in NEVER):
                out[rel] = f.read_bytes()
    return out
