"""The three encrypted state files that live on the repo's `data` branch, and how they merge.

  user.enc      written by your devices (web app, Mac app): profile, settings, swipe decisions,
                tracker rows (+ deletion tombstones), which Kaggle accounts are enabled, your resume
                source and photo. The resume Action also writes it (a quick profile refresh), merging the same way.
  resume_pdf.enc  the compiled resume PDF for the current resume rev (Mac app or the resume Action)
  pipeline.enc  written only by the scheduled GitHub Action: what it has already seen, jobs waiting
                for evaluation, stored answers (for re-scoring), Kaggle GPU usage per account
  board.enc     written only by the Action: what the web app shows

One writer per file, so the Action and your devices never overwrite each other. Devices can race on
user.enc; merge_user() resolves that record by record, newest change wins, deletions win over older edits.
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import questions as Q
from .db import APP_FIELDS, DB, now

BOARD_STATUSES = ("new", "later", "applied", "rejected")
DESC_CHARS = 30000           # whole descriptions (the longest seen are ~17k); board.enc is gzipped


# -- user.enc -----------------------------------------------------------------------------------

def export_user(db: DB) -> Dict[str, Any]:
    apps = [{k: a[k] for k in ("uid", "job_id", *APP_FIELDS, "created_at", "updated_at")} for a in db.applications()]
    accounts = [{"username": a["username"], "enabled": a.get("enabled", True), "slot": a.get("slot"),
                 "key_set": bool(a.get("key") or a.get("key_set"))} for a in db.get("kaggle_accounts", [])]
    return {
        "schema": 1, "exported_at": now(), "meta": db.get("user_meta", {}),
        "profile": db.get("profile"), "settings": db.get("settings", {}),
        "decisions": db.get("decisions", {}), "applications": apps,
        "deleted_apps": db.get("deleted_apps", {}), "kaggle": accounts,
        "resume": _public_resume(db.get("resume")), "photo": db.get("photo"),
    }


def _public_resume(res):
    """The synced part of the resume record (the Mac's local file path stays local)."""
    return {k: v for k, v in res.items() if k != "source_path"} if res else None


def _newer(a: Dict, b: Dict, part: str) -> Dict:
    """a = this device, b = the repo. On an exact tie the repo wins, so devices converge instead of
    taking turns overwriting each other."""
    return a if a.get("meta", {}).get(part, "") > b.get("meta", {}).get(part, "") else b


def merge_user(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Combine two copies of user state (e.g. this device's and the repo's)."""
    if not a:
        return b
    if not b:
        return a
    out = {"schema": 1, "exported_at": now(), "meta": {}}
    for part, keys in (("profile", ("profile",)), ("settings", ("settings",)), ("kaggle", ("kaggle",)),
                       ("resume", ("resume",)), ("photo", ("photo",))):
        src = _newer(a, b, part)
        for k in keys:
            out[k] = src.get(k)
        out["meta"][part] = src.get("meta", {}).get(part, "")
    dec = dict(a.get("decisions", {}))
    for jid, d in b.get("decisions", {}).items():
        if jid not in dec or d.get("at", "") > dec[jid].get("at", ""):
            dec[jid] = d
    out["decisions"] = dec
    dead = dict(a.get("deleted_apps", {}))
    for uid, at in b.get("deleted_apps", {}).items():
        dead[uid] = max(at, dead.get(uid, ""))
    apps: Dict[str, Dict] = {}
    for row in a.get("applications", []) + b.get("applications", []):
        cur = apps.get(row["uid"])
        if not cur or (row.get("updated_at") or "") > (cur.get("updated_at") or ""):
            apps[row["uid"]] = row
    out["applications"] = [r for uid, r in apps.items() if dead.get(uid, "") < (r.get("updated_at") or "")]
    out["deleted_apps"] = dead
    return out


def apply_user(db: DB, user: Dict[str, Any]) -> None:
    """Make this database match `user` (call with the merged state)."""
    if not user:
        return
    if user.get("profile") is not None:
        db.put("profile", user["profile"])
    db.put("settings", user.get("settings") or {})
    if user.get("resume"):
        db.put("resume", {**(db.get("resume") or {}), **user["resume"]})
    if user.get("photo"):
        db.put("photo", user["photo"])
    db.put("decisions", user.get("decisions", {}))
    db.put("deleted_apps", user.get("deleted_apps", {}))
    db.put("user_meta", user.get("meta", {}))
    remote = {k["username"]: k for k in user.get("kaggle") or []}
    accts = [a for a in db.get("kaggle_accounts", []) if a["username"] in remote or not user.get("kaggle")]
    known = {a["username"] for a in accts}
    for a in accts:
        if a["username"] in remote:
            r = remote[a["username"]]
            a.update(enabled=r.get("enabled", True), slot=r.get("slot") or a.get("slot"),
                     key_set=r.get("key_set", False) or bool(a.get("key")))
    accts += [{"username": u, "enabled": r.get("enabled", True), "slot": r.get("slot"), "key_set": r.get("key_set", False)}
              for u, r in remote.items() if u not in known]
    db.put("kaggle_accounts", accts)
    # Tracker: upsert by uid, drop tombstoned rows.
    mine = {a["uid"]: a for a in db.applications()}
    want = {r["uid"]: r for r in user.get("applications", [])}
    with db.lock:
        for uid, a in mine.items():
            if uid not in want:
                db.conn.execute("DELETE FROM applications WHERE uid=?", (uid,))
        for uid, r in want.items():
            vals = {k: r.get(k) for k in ("job_id", *APP_FIELDS, "created_at", "updated_at")}
            if uid in mine:
                if (mine[uid]["updated_at"] or "") != (r.get("updated_at") or ""):
                    cols = ", ".join(f"{k}=?" for k in vals)
                    db.conn.execute(f"UPDATE applications SET {cols} WHERE uid=?", (*vals.values(), uid))
            else:
                db.conn.execute(f"INSERT INTO applications (uid, {', '.join(vals)}) VALUES (?, {', '.join('?' * len(vals))})",
                                (uid, *vals.values()))
        db.conn.commit()
    db._backup()
    # Swipe decisions override whatever status the pipeline gave a job.
    for jid, d in user.get("decisions", {}).items():
        row = db.job(jid)
        if row and row["status"] != d["status"]:
            db.update_job(jid, status=d["status"])


# -- pipeline.enc -------------------------------------------------------------------------------

def export_pipeline(db: DB) -> Dict[str, Any]:
    """Everything the Action needs next time, minus bulk: rejected-at-triage postings keep only their id."""
    rows = []
    with db.lock:
        for r in db.conn.execute("SELECT * FROM jobs").fetchall():
            r = dict(r)
            if r["status"] == "triaged_out":
                rows.append({"id": r["id"], "status": "triaged_out", "posted_at": r["posted_at"]})
                continue
            rows.append(r)
    accts = [{k: v for k, v in a.items() if k != "key"} for a in db.get("kaggle_accounts", [])]  # keys stay in secrets
    return {"schema": 1, "jobs": rows, "kaggle_accounts": accts, "runs": _runs(db, 30)}


def import_pipeline(db: DB, state: Dict[str, Any]) -> None:
    if not state:
        return
    with db.lock:
        for r in state.get("jobs", []):
            r = {"source": None, "title": None, "company": None, "location": None, "url": None, "data": "{}",
                 "triage": None, "eval": None, "card": None, "match": None, "reason": None, "scored_by": None,
                 "created_at": None, "updated_at": None, **r}
            cols = list(r)
            db.conn.execute(f"INSERT OR REPLACE INTO jobs ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [r[c] for c in cols])
        have = {r[0] for r in db.conn.execute("SELECT started FROM runs")}
        for run in state.get("runs", []):
            if run["started"] not in have:           # importing again (each sync) must not duplicate history
                db.conn.execute("INSERT INTO runs (started, finished, stats, log) VALUES (?,?,?,?)",
                                (run["started"], run["finished"], run["stats"], run["log"]))
        db.conn.commit()
    if state.get("kaggle_accounts"):
        # Take usage/quota fields from the Action's copy but keep anything local (keys on the Mac).
        mine = {a["username"]: a for a in db.get("kaggle_accounts", [])}
        for a in state["kaggle_accounts"]:
            mine[a["username"]] = {**mine.get(a["username"], {}), **{k: v for k, v in a.items() if k != "key"}}
        db.put("kaggle_accounts", list(mine.values()))


def _runs(db: DB, n: int) -> List[Dict]:
    with db.lock:
        return [dict(r) for r in db.conn.execute(
            "SELECT started, finished, stats, log FROM runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()][::-1]


# -- board.enc ----------------------------------------------------------------------------------

def export_board(db: DB) -> Dict[str, Any]:
    jobs = []
    for r in db.jobs(list(BOARD_STATUSES), limit=100_000):
        d = dict(r["data"])
        d["description"] = (d.get("description") or "")[:DESC_CHARS]
        jobs.append({**d, "status": r["status"], "match": r["match"], "card": r["card"], "reason": r["reason"]})
    last = db.last_run()
    from .kaggle import Accounts                                   # local import: avoids a cycle
    from .pipeline import DEFAULT_SETTINGS
    return {"schema": 1, "exported_at": now(), "jobs": jobs, "counts": db.status_counts(),
            "last_run": {k: last[k] for k in ("started", "finished", "stats")} if last else None,
            "last_log": (last or {}).get("log", "").splitlines()[-40:],
            "kaggle": Accounts(db).public(),                       # usage/blocks only — never keys
            "defaults": {k: v for k, v in DEFAULT_SETTINGS.items() if k != "companies"},
            "labels": {"field_labels": Q.FIELDS, "level_labels": Q.LEVELS, "countries": Q.COUNTRIES}}
