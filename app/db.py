"""SQLite storage: profile, settings, jobs (the board), applications (the tracker), runs."""
from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = Path(os.environ.get("JOBBOARD_DB", DATA_DIR / "jobboard.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  source TEXT, title TEXT, company TEXT, location TEXT, url TEXT, posted_at TEXT,
  data TEXT NOT NULL,            -- normalized posting
  triage TEXT,                   -- Jev triage answers
  eval TEXT,                     -- Jev evaluation answers
  card TEXT,                     -- derived card info (match, chips, skills, reasons)
  match REAL,
  status TEXT NOT NULL,          -- raw | triaged_out | filtered | new | later | rejected | applied
  reason TEXT,
  scored_by TEXT,
  created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status, match DESC);
CREATE TABLE IF NOT EXISTS applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  company TEXT, role TEXT, url TEXT, location TEXT, source TEXT,
  applied_on TEXT, status TEXT, next_step TEXT, follow_up TEXT, notes TEXT,
  match REAL, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started TEXT, finished TEXT, stats TEXT, log TEXT
);
"""

APP_FIELDS = ["company", "role", "url", "location", "source", "applied_on", "status",
              "next_step", "follow_up", "notes", "match"]
# Column order of the tracker CSV (Export button and the auto-backup); scripts/import_applications.py reads it back.
CSV_COLUMNS = ["company", "role", "status", "applied_on", "next_step", "follow_up", "location", "source", "url",
               "notes", "match"]


def now() -> str:
    # UTC: timestamps sync between devices (and the browser, which stamps in UTC) and are compared as strings.
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path = DB_PATH, backup_csv: Optional[Path] = None):
        """backup_csv: if set, the tracker is rewritten there as CSV after every change to it."""
        self.backup_csv = backup_csv
        self.path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """uid: a stable id for tracker rows across devices (the integer id is per-database)."""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(applications)")}
        if "uid" not in cols:
            self.conn.execute("ALTER TABLE applications ADD COLUMN uid TEXT")
        for (rid,) in self.conn.execute("SELECT id FROM applications WHERE uid IS NULL").fetchall():
            self.conn.execute("UPDATE applications SET uid=? WHERE id=?", (uuid.uuid4().hex, rid))
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS applications_uid ON applications(uid)")
        self._migrate_to_utc()

    def _migrate_to_utc(self) -> None:
        """Timestamps used to be local time; sync compares them as UTC strings, so convert once."""
        if self.conn.execute("SELECT 1 FROM kv WHERE k='ts_utc'").fetchone():
            return
        from datetime import timedelta
        offset = datetime.now() - datetime.utcnow()
        shift = lambda t: (datetime.fromisoformat(t[:19]) - offset).isoformat(timespec="seconds") if t else t
        for table, cols in (("applications", ("created_at", "updated_at")), ("jobs", ("created_at", "updated_at")),
                            ("runs", ("started", "finished"))):
            for row in self.conn.execute(f"SELECT rowid, {', '.join(cols)} FROM {table}").fetchall():
                self.conn.execute(f"UPDATE {table} SET {', '.join(f'{c}=?' for c in cols)} WHERE rowid=?",
                                  (*[shift(row[c]) for c in cols], row[0]))
        for k, fix in (("decisions", lambda d: {j: {**v, "at": shift(v.get("at"))} for j, v in d.items()}),
                       ("deleted_apps", lambda d: {u: shift(t) for u, t in d.items()}),
                       ("user_meta", lambda d: {p: shift(t) for p, t in d.items()})):
            row = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            if row:
                self.conn.execute("UPDATE kv SET v=? WHERE k=?", (json.dumps(fix(json.loads(row["v"]))), k))
        self.conn.execute("INSERT OR REPLACE INTO kv VALUES ('ts_utc', 'true')")

    def touch(self, what: str) -> None:
        """Record when profile/settings/kaggle were last changed on this device (for sync merges)."""
        meta = self.get("user_meta", {})
        meta[what] = now()
        self.put("user_meta", meta)

    # -- key/value ------------------------------------------------------
    def get(self, k: str, default: Any = None) -> Any:
        with self.lock:
            row = self.conn.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return json.loads(row["v"]) if row else default

    def put(self, k: str, v: Any) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))
            self.conn.commit()

    # -- jobs -----------------------------------------------------------
    def known_ids(self) -> set:
        with self.lock:
            return {r[0] for r in self.conn.execute("SELECT id FROM jobs")}

    def insert_raw(self, jobs: List[dict]) -> None:
        t = now()
        with self.lock:
            self.conn.executemany(
                "INSERT OR IGNORE INTO jobs (id, source, title, company, location, url, posted_at, data,"
                " status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?, 'raw', ?, ?)",
                [(j["id"], j["source"], j["title"], j["company"], j["location"], j["url"], j["posted_at"],
                  json.dumps(j), t, t) for j in jobs])
            self.conn.commit()

    def refresh_text(self, jobs: List[dict], statuses=("new", "later", "applied", "rejected", "filtered")) -> int:
        """Postings seen again: update their stored text (e.g. a fuller description) without re-scoring."""
        by_id = {j["id"]: j for j in jobs}
        n = 0
        with self.lock:
            q = ",".join("?" * len(statuses))
            for row in self.conn.execute(f"SELECT id, data FROM jobs WHERE status IN ({q})", statuses).fetchall():
                j = by_id.get(row["id"])
                if j and json.loads(row["data"]).get("description") != j.get("description"):
                    self.conn.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(j), row["id"]))
                    n += 1
            self.conn.commit()
        return n

    def update_job(self, job_id: str, **fields) -> None:
        for k in ("triage", "eval", "card"):
            if k in fields and not isinstance(fields[k], (str, type(None))):
                fields[k] = json.dumps(fields[k])
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
            self.conn.commit()

    def jobs(self, statuses: List[str], limit: int = 500) -> List[dict]:
        q = ",".join("?" * len(statuses))
        with self.lock:
            rows = self.conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY match DESC, posted_at DESC LIMIT ?",
                (*statuses, limit)).fetchall()
        return [self._job(r) for r in rows]

    def job(self, job_id: str) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(r) if r else None

    def find_job_by_url(self, url: str) -> Optional[dict]:
        """The scraped posting behind a link the user pasted (loose URL match), if we have it."""
        from .urls import norm_url
        key = norm_url(url)
        if not key:
            return None
        with self.lock:
            rows = self.conn.execute("SELECT id, url FROM jobs WHERE url IS NOT NULL").fetchall()
        hit = next((r["id"] for r in rows if norm_url(r["url"]) == key), None)
        return self.job(hit) if hit else None

    def status_counts(self) -> Dict[str, int]:
        with self.lock:
            return {r[0]: r[1] for r in self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")}

    @staticmethod
    def _job(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("data", "triage", "eval", "card"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d

    # -- applications (tracker) ------------------------------------------
    def applications(self) -> List[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM applications ORDER BY COALESCE(applied_on, created_at) DESC, id DESC")
            return [dict(r) for r in rows]

    def applications_csv(self) -> str:
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(self.applications())
        return out.getvalue()

    def _backup(self) -> None:
        if not self.backup_csv:
            return
        tmp = self.backup_csv.with_suffix(".tmp")
        tmp.write_text(self.applications_csv(), encoding="utf-8")
        tmp.replace(self.backup_csv)  # atomic: a crash never leaves a half-written backup

    def add_application(self, data: dict) -> dict:
        t = now()
        vals = {k: data.get(k) for k in APP_FIELDS}
        vals["status"] = vals["status"] or "Applied"
        vals["applied_on"] = vals["applied_on"] or t[:10]
        with self.lock:
            if data.get("job_id"):
                row = self.conn.execute("SELECT id FROM applications WHERE job_id=?", (data["job_id"],)).fetchone()
                if row:
                    return self.application(row["id"])
            cur = self.conn.execute(
                f"INSERT INTO applications (uid, job_id, {', '.join(APP_FIELDS)}, created_at, updated_at)"
                f" VALUES (?, ?, {', '.join('?' * len(APP_FIELDS))}, ?, ?)",
                (data.get("uid") or uuid.uuid4().hex, data.get("job_id"), *vals.values(),
                 data.get("created_at") or t, data.get("updated_at") or t))
            self.conn.commit()
        self._backup()
        return self.application(cur.lastrowid)

    def set_application_job(self, app_id: int, job_id: Optional[str]) -> None:
        with self.lock:
            self.conn.execute("UPDATE applications SET job_id=?, updated_at=? WHERE id=?", (job_id, now(), app_id))
            self.conn.commit()
        self._backup()

    def application(self, app_id: int) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        return dict(r) if r else None

    def update_application(self, app_id: int, data: dict) -> Optional[dict]:
        fields = {k: v for k, v in data.items() if k in APP_FIELDS}
        if fields:
            fields["updated_at"] = now()
            cols = ", ".join(f"{k}=?" for k in fields)
            with self.lock:
                self.conn.execute(f"UPDATE applications SET {cols} WHERE id=?", (*fields.values(), app_id))
                self.conn.commit()
            self._backup()
        return self.application(app_id)

    def delete_application(self, app_id: int) -> None:
        with self.lock:
            row = self.conn.execute("SELECT uid FROM applications WHERE id=?", (app_id,)).fetchone()
            self.conn.execute("DELETE FROM applications WHERE id=?", (app_id,))
            self.conn.commit()
        if row:                                  # tombstone, so the deletion syncs to other devices
            dead = self.get("deleted_apps", {})
            dead[row["uid"]] = now()
            self.put("deleted_apps", dead)
        self._backup()

    # -- runs -----------------------------------------------------------
    def save_run(self, started: str, stats: dict, log: List[str]) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO runs (started, finished, stats, log) VALUES (?,?,?,?)",
                              (started, now(), json.dumps(stats), "\n".join(log)))
            self.conn.commit()

    def last_run(self) -> Optional[dict]:
        with self.lock:
            r = self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not r:
            return None
        d = dict(r)
        d["stats"] = json.loads(d["stats"] or "{}")
        return d
