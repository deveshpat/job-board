"""FastAPI app: JSON API + the static single-page UI."""
from __future__ import annotations

import os
import re
import threading
import traceback
from contextlib import nullcontext
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def _load_env(path: Path) -> None:
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


_load_env(ROOT / ".env")

from . import questions as Q  # noqa: E402  (after .env so JEV_* overrides apply)
from .db import DATA_DIR, DB, now  # noqa: E402
from .engine import LocalEngine  # noqa: E402
from .kaggle import Accounts, KaggleCLI  # noqa: E402
from .sync import Sync  # noqa: E402
from .jev import Jev, JevError  # noqa: E402
from .pipeline import Pipeline  # noqa: E402
from .profile import build_profile  # noqa: E402
from .urls import from_page, from_url  # noqa: E402

DEFAULT_RESUME = Path(os.environ.get("RESUME_PATH", ROOT.parent / "Devesh_Patel_Resume.tex"))
AVATAR = Path(os.environ.get("AVATAR_PATH", ROOT.parent / "DP.jpeg"))

ENGINE_LABEL = os.environ.get("ENGINE_LABEL") or ("Local engine" if "127.0.0.1" in os.environ.get("JEV_BASE_URL", "") else "Jev")

# Always-current tracker backup (a file to keep when reformatting the Mac); TRACKER_BACKUP= disables it.
_backup = os.environ.get("TRACKER_BACKUP", str(ROOT.parent / "tracker_backup.csv"))
db = DB(backup_csv=Path(_backup) if _backup else None)
jev = Jev(cache_path=Path(os.environ.get("JEV_CACHE", DATA_DIR / "jev_cache.db")))
engine = LocalEngine.from_env(ROOT)   # None when using hosted Jev
pipeline = Pipeline(db, jev, engine)
sync = Sync(db, log=pipeline.log)
pipeline.github_mode = lambda: sync.enabled      # daily runs move to GitHub Actions while sync is on
if sync.enabled and not os.environ.get("JOBBOARD_HEADLESS"):
    sync.start_loop(300)
    threading.Thread(target=sync.sync_once, daemon=True).start()
if os.environ.get("DAILY_SCHEDULER", "1") == "1" and not os.environ.get("JOBBOARD_HEADLESS"):
    pipeline.start_scheduler()
app = FastAPI(title="Job Board")
app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


_sync_timer = None


@app.middleware("http")
async def _sync_after_changes(request: Request, call_next):
    """Any change to your data (swipes, tracker, profile, settings) syncs to GitHub a few seconds later."""
    global _sync_timer
    response = await call_next(request)
    p = request.url.path
    if (sync.enabled and request.method in ("POST", "PATCH", "PUT", "DELETE") and p.startswith("/api/")
            and not p.startswith(("/api/github", "/api/pipeline")) and response.status_code < 400):
        if _sync_timer:
            _sync_timer.cancel()
        _sync_timer = threading.Timer(5, sync.sync_once)
        _sync_timer.daemon = True
        _sync_timer.start()
    return response


# -- GitHub publish & sync -------------------------------------------------------------------------
@app.get("/api/github")
def github_status():
    return {**sync.public(), "publish_error": db.get("github_publish_error")}


@app.post("/api/github/publish")
async def github_publish(request: Request):
    b = await request.json()
    owner, _, repo = (b.get("repo") or "").strip().strip("/").partition("/")
    if not owner or not repo:
        raise HTTPException(400, "Enter the repository as owner/name")
    passphrase = b.get("passphrase") or (_draft().get("phrase") if _draft().get("confirmed") else "")
    try:
        result = sync.publish(owner, repo, (b.get("token") or "").strip(), passphrase or "",
                              api=b.get("api") or "https://api.github.com")
        db.put("passphrase_draft", None)          # the Mac keeps only the data key from here on
        db.put("github_publish_error", None)
        return result
    except ValueError as e:
        db.put("github_publish_error", str(e))
        raise HTTPException(400, str(e))
    except Exception as e:
        traceback.print_exc()
        pipeline.log(f"✗ Publish failed: {e}")
        db.put("github_publish_error", f"GitHub: {e}"[:500])
        raise HTTPException(502, f"GitHub: {e}")


# The passphrase offered for Publish lives in the local database until publishing succeeds: generated (shown),
# then confirmed by retyping it from wherever you saved it (never sent back to the page after that).
def _draft() -> dict:
    return db.get("passphrase_draft") or {}


@app.get("/api/github/passphrase")
def github_new_passphrase(new: bool = False):
    from .vault import generate_passphrase, passphrase_bits
    d = _draft()
    if d.get("confirmed") and not new:
        return {"confirmed": True}
    if new or not d.get("phrase"):
        d = {"phrase": generate_passphrase(6), "confirmed": False}
        db.put("passphrase_draft", d)
    return {"passphrase": d["phrase"], "bits": round(passphrase_bits(d["phrase"])), "confirmed": False}


@app.post("/api/github/passphrase/confirm")
async def github_confirm_passphrase(request: Request):
    from .vault import normalize_passphrase
    typed = (await request.json()).get("passphrase") or ""
    d = _draft()
    if not d.get("phrase") or normalize_passphrase(typed) != normalize_passphrase(d["phrase"]):
        raise HTTPException(400, "That doesn't match the generated passphrase")
    db.put("passphrase_draft", {**d, "confirmed": True})
    return {"confirmed": True}


@app.post("/api/github/sync")
def github_sync():
    return sync.sync_once()


@app.post("/api/github/disconnect")
def github_disconnect():
    sync.disconnect()
    return sync.public()


@app.on_event("shutdown")
def _stop_engine():
    if engine:
        engine.stop()


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/api/avatar")
def avatar():
    if not AVATAR.exists():
        raise HTTPException(404)
    return FileResponse(AVATAR)


# -- status ---------------------------------------------------------------
@app.get("/api/status")
def status():
    return {
        "jev": {"available": jev.available, "model": jev.model, "usage": jev.usage,
                "engine": ENGINE_LABEL, "local": jev.is_local,
                "engine_state": engine.state if engine else None},
        "counts": db.status_counts(),
        "pipeline": pipeline.state,
        "last_run": db.last_run(),
        "has_profile": db.get("profile") is not None,
        "github": sync.public(),
    }


# -- profile ----------------------------------------------------------------
def _public_profile(p: Optional[dict]) -> Optional[dict]:
    if not p:
        return None
    return {k: v for k, v in p.items() if k != "resume_text"} | {
        "field_labels": Q.FIELDS, "level_labels": Q.LEVELS, "countries": Q.COUNTRIES}


@app.get("/api/profile")
def get_profile():
    return _public_profile(db.get("profile"))


@app.post("/api/profile/build")
async def build(request: Request, filename: Optional[str] = None):
    """Build (or rebuild) the profile. Body = uploaded resume bytes, or empty to use the default file."""
    body = await request.body()
    if body:
        suffix = Path(filename or "resume.pdf").suffix.lower()
        if suffix not in (".pdf", ".tex", ".txt", ".md"):
            raise HTTPException(400, "Upload a .pdf, .tex, .txt or .md resume")
        path = DATA_DIR / f"resume{suffix}"
        path.write_bytes(body)
    else:
        prev = db.get("profile")
        path = DATA_DIR / prev["resume_file"] if prev and (DATA_DIR / prev["resume_file"]).exists() else DEFAULT_RESUME
    if not path.exists():
        raise HTTPException(400, f"Resume not found at {path}")
    try:
        with engine.use() if engine else nullcontext():
            profile = build_profile(path, jev)
    except (JevError, RuntimeError) as e:
        raise HTTPException(502, str(e))
    db.put("profile", profile)
    db.touch("profile")
    return _public_profile(profile)


@app.patch("/api/profile")
async def patch_profile(request: Request):
    """User overrides of Jev's picks: keyword/search-term toggles, added terms, level, country, goal."""
    p = db.get("profile")
    if not p:
        raise HTTPException(400, "Build a profile first")
    patch = await request.json()
    for k in ("keywords", "search_terms", "fields", "projects"):
        if k in patch:
            p[k] = patch[k]
    for k in ("level", "country", "goal", "location", "headline"):
        if k in patch:
            p[k] = patch[k]
    db.put("profile", p)
    db.touch("profile")
    return _public_profile(p)


# -- settings -------------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    return pipeline.settings()


@app.patch("/api/settings")
async def patch_settings(request: Request):
    patch = await request.json()
    s = pipeline.settings()
    old_min = s["min_match"]
    s.update({k: v for k, v in patch.items() if k in s})
    db.put("settings", s)
    db.touch("settings")
    rescored = pipeline.rescore() if s["min_match"] != old_min and db.get("profile") else None
    return {**s, "rescored_on_board": rescored}


# -- Kaggle accounts ----------------------------------------------------------
kaggle_accounts = Accounts(db)


@app.get("/api/kaggle/accounts")
def get_kaggle_accounts():
    return kaggle_accounts.public()


@app.put("/api/kaggle/accounts")
async def put_kaggle_accounts(request: Request):
    """Replace the account list. A blank key keeps the stored one (the browser never has it)."""
    incoming = await request.json()
    old = {a["username"]: a for a in kaggle_accounts.all()}
    saved, seen = [], set()
    for a in incoming:
        user = (a.get("username") or "").strip()
        if not user or user in seen:
            continue
        seen.add(user)
        prev = old.get(user, {})
        key = (a.get("key") or "").strip() or prev.get("key", "")
        if key and not re.fullmatch(r"[A-Za-z0-9_\-]{16,128}", key):
            raise HTTPException(400, f"That doesn't look like a Kaggle API key for {user}")
        saved.append({**prev, "username": user, "key": key, "enabled": bool(a.get("enabled", True)),
                      "verified": prev.get("verified") if key == prev.get("key") else None})
    kaggle_accounts.save(saved)
    db.touch("kaggle")
    return kaggle_accounts.public()


@app.post("/api/kaggle/accounts/{username}/verify")
def verify_kaggle_account(username: str):
    a = kaggle_accounts.get(username)
    if not a or not a.get("key"):
        raise HTTPException(404, "No key saved for that account")
    r = KaggleCLI(username, a["key"]).verify()
    ok = r.code == 0
    kaggle_accounts.update(username, verified=ok, last_error=None if ok else r.out[-300:])
    return {"ok": ok, "detail": None if ok else r.out[-300:], "accounts": kaggle_accounts.public()}


# -- pipeline -------------------------------------------------------------
@app.post("/api/pipeline/run")
async def run_pipeline(request: Request):
    body = await request.json() if (await request.body()) else {}
    if not db.get("profile"):
        raise HTTPException(400, "Build your profile first")
    if sync.enabled:                                  # runs live on GitHub Actions now
        try:
            sync.gh().dispatch()
        except Exception as e:
            raise HTTPException(502, f"Couldn't start the GitHub run: {e}")
        return {"running": False, "stage": "Started on GitHub Actions — results sync here when it finishes",
                "done": 0, "total": 0, "log": [], "error": None}
    if not pipeline.start(reevaluate=bool(body.get("reevaluate"))):
        raise HTTPException(409, "Pipeline already running")
    return pipeline.state


@app.get("/api/pipeline/status")
def pipeline_status():
    return pipeline.state


# -- jobs (the swipe deck) ------------------------------------------------
@app.get("/api/jobs")
def list_jobs(status: str = "new", limit: int = 200):
    rows = db.jobs(status.split(","), limit=limit)
    return [{**r["data"], "status": r["status"], "match": r["match"], "card": r["card"],
             "reason": r["reason"], "description": (r["data"].get("description") or "")[:6000]}
            for r in rows]


@app.post("/api/jobs/{job_id}/decision")
async def decide(job_id: str, request: Request):
    action = (await request.json()).get("action")
    row = db.job(job_id)
    if not row:
        raise HTTPException(404)
    status = {"apply": "applied", "reject": "rejected", "later": "later", "undo": "new"}.get(action)
    if not status:
        raise HTTPException(400, "action must be apply | reject | later | undo")
    db.update_job(job_id, status=status)
    decisions = db.get("decisions", {})
    decisions[job_id] = {"status": status, "at": now()}     # synced to other devices via user.enc
    db.put("decisions", decisions)
    app_row = None
    if action == "apply":
        d = row["data"]
        app_row = db.add_application({
            "job_id": job_id, "company": d["company"], "role": d["title"], "url": d["url"],
            "location": d["location"],
            "source": d["source"], "status": "Applied", "match": row["match"]})
    return {"status": status, "application": app_row}


# -- applications (tracker) -----------------------------------------------
@app.get("/api/applications")
def apps():
    return db.applications()


def _link(row: dict) -> dict:
    """Tie a tracked application to the scraped posting with the same link (if any): the posting is
    marked applied so it leaves the swipe deck, and the row gets its match score."""
    job = db.find_job_by_url(row["url"]) if row.get("url") else None
    if not job:
        if row.get("job_id") and row.get("url"):
            db.set_application_job(row["id"], None)
        return {**db.application(row["id"]), "linked": None}
    db.set_application_job(row["id"], job["id"])
    if job["status"] != "applied":
        db.update_job(job["id"], status="applied", reason="in Tracker")
    if row.get("match") is None and job["match"] is not None:
        db.update_application(row["id"], {"match": job["match"]})
    return {**db.application(row["id"]), "linked": {"title": job["title"], "company": job["company"]}}


@app.get("/api/applications/lookup")
def lookup_application(url: str):
    """Pre-fill the add form from a pasted job link: our scraped posting if we have it, else the URL
    pattern (company) and the page title (role)."""
    if not re.match(r"https?://", url):
        raise HTTPException(400, "Paste a full http(s) link")
    job = db.find_job_by_url(url)
    if job:
        d = job["data"]
        return {"company": d["company"], "role": d["title"], "location": d["location"], "source": d["source"],
                "match": job["match"], "from": "board"}
    guess = from_url(url)
    page = from_page(url, guess["company"])
    return {"company": page.get("company") or guess["company"], "role": page.get("role"), "location": page.get("location"),
            "source": guess["source"], "match": None, "from": "page" if page.get("role") else "url"}


@app.post("/api/applications")
async def add_app(request: Request):
    return _link(db.add_application(await request.json()))


@app.patch("/api/applications/{app_id}")
async def edit_app(app_id: int, request: Request):
    patch = await request.json()
    row = db.update_application(app_id, patch)
    if not row:
        raise HTTPException(404)
    return _link(row) if "url" in patch else row


@app.delete("/api/applications/{app_id}")
def del_app(app_id: int):
    db.delete_application(app_id)
    return {"ok": True}


@app.get("/api/applications.csv")
def export_csv():
    return Response(db.applications_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=applications.csv"})


@app.exception_handler(JevError)
def jev_error(_, exc: JevError):
    return JSONResponse({"detail": str(exc)}, status_code=502)
