"""Publish to GitHub, then keep this Mac's database in sync with the repo's encrypted `data` branch.

publish(): one-time setup (or joining an existing setup from another Mac with the same passphrase):
  code → main (Pages + the scheduled workflow), encrypted state → data, JOBBOARD_DATA_KEY and
  KAGGLE_KEY_<USER> → Actions secrets, Pages switched on.
sync_once(): your data (user.enc) merges both ways; the scheduled run's results (pipeline.enc) flow in.
While GitHub sync is on, the daily run happens on GitHub Actions, not on this Mac.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
from typing import Callable, Optional

from . import state, vault
from .db import DB, now
from .github import API, GitHub, GitHubError, publishable_files

DATA_README = b"""# Encrypted job-board data

Written by the job board (web app, Mac app, and the scheduled GitHub Action). Everything except
keys.json is AES-256-GCM ciphertext; keys.json holds the data key wrapped by your passphrase/passkeys.
"""


def secret_name(username: str) -> str:
    return "KAGGLE_KEY_" + re.sub(r"[^A-Z0-9_]", "_", username.upper())


class Sync:
    def __init__(self, db: DB, log: Callable[[str], None] = print):
        self.db, self.log = db, log
        self._lock = threading.Lock()

    @property
    def cfg(self) -> dict:
        return self.db.get("github", {})

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def gh(self) -> GitHub:
        c = self.cfg
        return GitHub(c["owner"], c["repo"], c["token"], c.get("api", API))

    def key(self) -> bytes:
        return vault.unb64(self.cfg["data_key"])

    def public(self) -> dict:
        c = self.cfg
        return {"enabled": self.enabled, "owner": c.get("owner"), "repo": c.get("repo"),
                "token_hint": ("••••" + c["token"][-4:]) if c.get("token") else "",
                "has_key": bool(c.get("data_key")),
                "pages_manual": c.get("pages_manual", False),
                "site": f"https://{c['owner']}.github.io/{c['repo']}/" if c.get("owner") else None,
                "last_sync": c.get("last_sync"), "last_error": c.get("last_error")}

    # -- one-time setup -----------------------------------------------------------------------
    def publish(self, owner: str, repo: str, token: str = "", passphrase: str = "", api: str = API) -> dict:
        """token/passphrase may be blank to reuse what an earlier attempt for this repo saved on this Mac."""
        saved = self.cfg if (self.cfg.get("owner"), self.cfg.get("repo")) == (owner, repo) else {}
        token = token or saved.get("token", "")
        if not token:
            raise ValueError("Enter your GitHub token.")
        if passphrase:
            bits = vault.passphrase_bits(passphrase)
            if bits < vault.MIN_PASSPHRASE_BITS:
                raise ValueError(f"Passphrase too weak (~{bits:.0f} bits). The encrypted files are public, so use "
                                 f"at least 5 random words, e.g. 'orbit tulip canyon ember velvet'.")
        gh = GitHub(owner, repo, token, api)
        existing = gh.file("keys.json")
        if existing:
            keys = json.loads(existing[1])
            key = vault.unlock(keys, passphrase) if passphrase else self._saved_key_for(gh, saved)
            if key is None:
                raise ValueError("This repo is already set up with a different passphrase." if passphrase else
                                 "Enter the passphrase this repo was set up with.")
            self.log("Joining the existing encrypted data on the repo")
        else:
            if not passphrase:
                raise ValueError("Choose a passphrase (use the generated one).")
            key = vault.new_data_key()
            keys = {"v": 1, "wraps": [vault.wrap_passphrase(key, passphrase)]}
        # Remember repo, token and data key now, so a retry after any later failure needs none of them again.
        # (Local database only — data/ is never published.)
        self.db.put("github", {**saved, "owner": owner, "repo": repo, "token": token, "api": api,
                               "data_key": vault.b64(key), "enabled": saved.get("enabled", False)})
        if gh.is_empty():
            self.log("Empty repository — creating its first commit")
            gh.seed_empty_repo()
        self.log("Uploading the app to main…")
        gh.commit_files("main", publishable_files(), "Job board app")
        if not existing:
            self.log("Creating the encrypted data branch…")
            user = self._export_user()
            gh.commit_files("data", {
                "keys.json": json.dumps(keys, indent=1).encode(), "README.md": DATA_README,
                "user.enc": vault.encrypt(key, "user", user),
                "pipeline.enc": vault.encrypt(key, "pipeline", state.export_pipeline(self.db)),
                "board.enc": vault.encrypt(key, "board", state.export_board(self.db)),
            }, "Encrypted job-board data", orphan=True)
        self.log("Saving secrets…")
        gh.set_secret("JOBBOARD_DATA_KEY", vault.b64(key))
        for a in self.db.get("kaggle_accounts", []):
            if a.get("key"):
                gh.set_secret(secret_name(a["username"]), a["key"])
        pages_ok = gh.enable_pages()
        if not pages_ok:
            self.log("  the token can't switch Pages on — do it once in Settings → Pages → Source: GitHub Actions")
        self.log("Deploying the web app and starting a run…")
        if pages_ok and not gh.dispatch_soon("pages.yml"):   # in case the push-triggered deploy ran before Pages was on
            self.log("  couldn't start the Pages deploy — re-run it from the repo's Actions tab")
        if not gh.dispatch_soon("daily.yml"):
            self.log("  couldn't start the run — it will start at the next hourly check")
        self.db.put("github", {"owner": owner, "repo": repo, "token": token, "api": api,
                               "data_key": vault.b64(key), "enabled": True, "pages_manual": not pages_ok})
        if existing:
            self.sync_once()
        self.db.put("github", {**self.cfg, "last_sync": now(), "last_error": None})
        return self.public()

    def _saved_key_for(self, gh: GitHub, saved: dict) -> Optional[bytes]:
        """The data key an earlier attempt stored here — only if it really opens this repo's data."""
        if not saved.get("data_key"):
            return None
        key = vault.unb64(saved["data_key"])
        f = gh.file("user.enc")
        try:
            if f:
                vault.decrypt(key, "user", f[1])
            return key
        except Exception:
            return None

    def disconnect(self) -> None:
        self.db.put("github", {**self.cfg, "enabled": False})

    def _export_user(self) -> dict:
        user = state.export_user(self.db)
        keys_set = {a["username"] for a in self.db.get("kaggle_accounts", []) if a.get("key")}
        for k in user.get("kaggle") or []:
            k["key_set"] = k["username"] in keys_set or k.get("key_set", False)
        return user

    # -- ongoing sync --------------------------------------------------------------------------------
    def sync_once(self) -> dict:
        if not self.enabled:
            return {"ok": False, "detail": "GitHub sync is off"}
        with self._lock:
            try:
                gh, key = self.gh(), self.key()
                changed = self._sync_user(gh, key)
                pulled = self._pull_pipeline(gh, key)
                self.db.put("github", {**self.cfg, "last_sync": now(), "last_error": None})
                return {"ok": True, "pushed": changed, "pulled_jobs": pulled}
            except Exception as e:
                traceback.print_exc()
                self.db.put("github", {**self.cfg, "last_error": str(e)[:300]})
                return {"ok": False, "detail": str(e)}

    def _sync_user(self, gh: GitHub, key: bytes) -> bool:
        for _ in range(3):
            remote = gh.file("user.enc")
            remote_user = vault.decrypt(key, "user", remote[1]) if remote else None
            merged = state.merge_user(self._export_user(), remote_user)
            state.apply_user(self.db, merged)
            same = remote_user and _canon(merged) == _canon(remote_user)
            if same:
                return False
            try:
                gh.put("user.enc", vault.encrypt(key, "user", merged), remote[0] if remote else None, "update from Mac app")
                return True
            except GitHubError as e:
                if not getattr(e, "conflict", False):
                    raise                                   # someone else saved in between: merge again
        raise GitHubError("user.enc kept changing; will retry next sync")

    def _pull_pipeline(self, gh: GitHub, key: bytes) -> int:
        f = gh.file("pipeline.enc")
        if not f or f[0] == self.cfg.get("pipeline_sha"):
            return 0
        pipe = vault.decrypt(key, "pipeline", f[1])
        state.import_pipeline(self.db, pipe)
        state.apply_user(self.db, state.export_user(self.db))   # re-apply swipes on top of imported statuses
        self.db.put("github", {**self.cfg, "pipeline_sha": f[0]})
        return len(pipe.get("jobs", []))

    def start_loop(self, every: float = 300) -> None:
        def loop():
            while True:
                time.sleep(every)
                if self.enabled:
                    r = self.sync_once()
                    if not r.get("ok"):
                        self.log(f"GitHub sync failed: {r.get('detail')}")
        threading.Thread(target=loop, daemon=True, name="github-sync").start()


def _canon(user: dict) -> str:
    """Order-insensitive fingerprint, so an unchanged state never causes a commit."""
    u = {k: v for k, v in user.items() if k != "exported_at"}
    u["applications"] = sorted(u.get("applications") or [], key=lambda r: r["uid"])
    u["kaggle"] = sorted(u.get("kaggle") or [], key=lambda k: k["username"])
    return json.dumps(u, sort_keys=True)
