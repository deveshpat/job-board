"""The scheduled run, as GitHub Actions executes it (.github/workflows/daily.yml).

  JOBBOARD_DATA_KEY=<base64> KAGGLE_KEY_1=<key> ... KAGGLE_KEY_5=<key> python scripts/ci_run.py --state state/ [--force]

The workflow fires every hour; this exits quietly unless settings.daily_at (in settings.tz_offset_min, your
timezone) has passed today and no run happened yet today — so the web app can change the time.

Reads the encrypted state from --state (a checkout of the `data` branch), runs the pipeline with every
decision on Kaggle (GPU → CPU across accounts; no Mac to fall back to here), and writes pipeline.enc and
board.enc back. user.enc is only read: it belongs to your devices.
"""
import argparse
import json
from datetime import datetime, timezone
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import state, vault  # noqa: E402
from app.db import DB  # noqa: E402
from app.jev import Jev  # noqa: E402
from app.kaggle import Accounts, KaggleError, KaggleRunner  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402


def read(key: bytes, folder: Path, name: str):
    f = folder / f"{name}.enc"
    return vault.decrypt(key, name, f.read_bytes()) if f.exists() else None


def main(argv=None, runner_factory=KaggleRunner) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True, type=Path)
    ap.add_argument("--force", action="store_true", help="run now even if not due (manual dispatch)")
    args = ap.parse_args(argv)
    key = vault.unb64(os.environ["JOBBOARD_DATA_KEY"])

    with tempfile.TemporaryDirectory() as tmp:
        db = DB(Path(tmp) / "run.sqlite")
        state.import_pipeline(db, read(key, args.state, "pipeline"))
        user = read(key, args.state, "user")
        if not user or not user.get("profile"):
            print("No profile in user.enc yet — publish from the Mac app or the web app first.")
            return 1
        state.apply_user(db, user)
        if not args.force and not _due(db):
            print("Not due yet — nothing to do.")
            return 3
        # Keys come only from the KAGGLE_KEY_<slot> secrets; user.enc says which account is in which slot.
        accts = {a["username"]: a for a in db.get("kaggle_accounts", [])}
        for k in user.get("kaggle") or []:
            kaggle_key = os.environ.get(f"KAGGLE_KEY_{k.get('slot')}") if k.get("slot") else None
            if kaggle_key:
                accts[k["username"]] = {**accts.get(k["username"], {}), "username": k["username"], "key": kaggle_key,
                                        "enabled": k.get("enabled", True)}
        Accounts(db).save([a for a in accts.values() if a.get("key")])
        db.put("settings", {**db.get("settings", {}), "engine_mode": "kaggle", "local_fallback": False})

        pipe = Pipeline(db, Jev(api_key="kaggle"), engine=None)
        pipe.kaggle = runner_factory(db, log=pipe.log)
        pipe.rescore()                    # cards follow your latest profile/settings (and card format) — no model calls
        code, started = 0, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")  # UTC, see _due
        stats = {}
        try:
            stats = pipe.run()
            pipe.log(f"stats: {json.dumps(stats)}")
        except KaggleError as e:
            pipe.log(f"✗ Kaggle unavailable today: {e}")
            code = 2
        finally:
            db.save_run(started, {**stats, "engine": "kaggle"}, pipe.state["log"])
            print("\n".join(pipe.state["log"]))
        (args.state / "pipeline.enc").write_bytes(vault.encrypt(key, "pipeline", state.export_pipeline(db)))
        (args.state / "board.enc").write_bytes(vault.encrypt(key, "board", state.export_board(db)))
    return code


def _due(db: DB) -> bool:
    """Is today's run (in the user's own timezone) due and not yet done?"""
    from datetime import datetime, timedelta, timezone
    cfg = db.get("settings", {})
    at = (cfg.get("daily_at") or "09:00").strip()
    if not at:
        return False
    local = datetime.now(timezone.utc) + timedelta(minutes=int(cfg.get("tz_offset_min", 330)))
    hh, mm = (int(x) for x in at.split(":"))
    if (local.hour, local.minute) < (hh, mm):
        return False
    last = db.last_run()
    if not last:
        return True
    # Runs are stamped in the runner's clock (UTC on GitHub); compare dates in the user's timezone.
    started_local = datetime.fromisoformat(last["started"][:19]) + timedelta(minutes=int(cfg.get("tz_offset_min", 330)))
    return started_local.date() < local.date()


if __name__ == "__main__":
    sys.exit(main())
