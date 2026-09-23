"""Run the pipeline's decision work on Kaggle (Kev on GPU T4 ×2, or Kaggle CPU) with your accounts.

Order for each run: every enabled account with GPU quota (least tracked usage first) → Kaggle CPU on any
account → the caller falls back to local Kev. An account that reports exhausted GPU quota is skipped
until Kaggle's weekly reset (Saturday 00:00 UTC).

Kaggle's API does not expose remaining GPU quota, so usage is tracked from this app's own runs; hours
used elsewhere on the same account (e.g. training notebooks) are invisible here until Kaggle refuses.
Credentials live only in the local database (data/, git-ignored) and are passed to the Kaggle CLI through
environment variables; the browser only ever sees the last four characters of a key.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
CLI = Path(os.environ.get("KAGGLE_CLI") or ROOT / "engines" / "kaggle-cli" / "bin" / "kaggle")
RUNNER = ROOT / "kaggle_kernel" / "runner.py"
KEV_COMMIT = "557598fced1dada75dfbf36ed144dce309ac6ceb"   # the commit benchmarked in bench/
KAGGLE_KEV_RUN = "jaredpalmer/kev-4b"
DATASET_SLUG = "jobboard-batch"
KERNEL_SLUG = "jobboard-runner"
# Stop starting new work after this (Kaggle's session cap is higher). GitHub Actions jobs end at 6 h, so
# scheduled runs use a shorter budget (JOBBOARD_KAGGLE_BUDGET_H); unfinished work carries to the next run.
_BUDGET_H = os.environ.get("JOBBOARD_KAGGLE_BUDGET_H")
GPU_BUDGET_S = int(float(_BUDGET_H) * 3600) if _BUDGET_H else 8 * 3600
CPU_BUDGET_S = int(float(_BUDGET_H) * 3600) if _BUDGET_H else 11 * 3600
POLL_S = 60
# Kaggle's wording for "no GPU hours left" isn't documented; match broadly, keep the raw text for review.
QUOTA_RE = re.compile(r"quota|exceed|limit reached|maximum .*hours|out of .*hours|no .*hours (left|remaining)", re.I)


class KaggleError(RuntimeError):
    """quota: this account's GPU hours are used up · run: the kernel itself failed (same on every account)."""
    def __init__(self, msg: str, quota: bool = False, run: bool = False):
        super().__init__(msg)
        self.quota, self.run = quota, run


def week_start(now: datetime) -> datetime:
    """Kaggle's quota week begins Saturday 00:00 UTC."""
    now = now.astimezone(timezone.utc)
    start = (now - timedelta(days=(now.weekday() - 5) % 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start


def next_reset(now: datetime) -> datetime:
    return week_start(now) + timedelta(days=7)


# ---------------------------------------------------------------------------
# Accounts (stored in the app database under kv "kaggle_accounts")
# ---------------------------------------------------------------------------

class Accounts:
    def __init__(self, db):
        self.db = db

    def all(self) -> List[dict]:
        return self.db.get("kaggle_accounts", [])

    def save(self, accounts: List[dict]) -> None:
        self.db.put("kaggle_accounts", accounts)

    def get(self, username: str) -> Optional[dict]:
        return next((a for a in self.all() if a["username"] == username), None)

    def update(self, username: str, **fields) -> None:
        accts = self.all()
        for a in accts:
            if a["username"] == username:
                a.update(fields)
        self.save(accts)

    def gpu_seconds_this_week(self, a: dict, now: Optional[datetime] = None) -> float:
        start = week_start(now or datetime.now(timezone.utc)).isoformat()
        return sum(r["seconds"] for r in a.get("runs", []) if r["accelerator"] == "gpu" and r["started"] >= start)

    def gpu_blocked(self, a: dict, now: Optional[datetime] = None) -> bool:
        until = a.get("gpu_blocked_until")
        return bool(until) and (now or datetime.now(timezone.utc)).isoformat() < until

    def gpu_order(self, now: Optional[datetime] = None) -> List[dict]:
        ok = [a for a in self.all() if a.get("enabled", True) and a.get("key") and not self.gpu_blocked(a, now)]
        return sorted(ok, key=lambda a: self.gpu_seconds_this_week(a, now))

    def cpu_order(self) -> List[dict]:
        return [a for a in self.all() if a.get("enabled", True) and a.get("key")]

    def record_run(self, username: str, accelerator: str, started: datetime, seconds: float, outcome: str) -> None:
        accts = self.all()
        for a in accts:
            if a["username"] == username:
                a.setdefault("runs", []).append({"started": started.isoformat(), "seconds": round(seconds),
                                                 "accelerator": accelerator, "outcome": outcome})
                a["runs"] = a["runs"][-200:]
        self.save(accts)

    def public(self, now: Optional[datetime] = None) -> List[dict]:
        """What the browser may see: never the key itself."""
        now = now or datetime.now(timezone.utc)
        out = []
        for a in self.all():
            out.append({
                "username": a["username"], "enabled": a.get("enabled", True),
                "key_hint": ("••••" + a["key"][-4:]) if a.get("key") else "",
                "verified": a.get("verified"), "last_error": a.get("last_error"),
                "gpu_hours_this_week": round(self.gpu_seconds_this_week(a, now) / 3600, 2),
                "gpu_blocked_until": a.get("gpu_blocked_until") if self.gpu_blocked(a, now) else None,
                "quota_message": a.get("quota_message"),
                "last_run": (a.get("runs") or [None])[-1],
            })
        return out


# ---------------------------------------------------------------------------
# Kaggle CLI wrapper (one account per call, credentials via env)
# ---------------------------------------------------------------------------

@dataclass
class CLI_Result:
    code: int
    out: str


class KaggleCLI:
    def __init__(self, username: str, key: str, cli: Path = CLI):
        self.username, self.key, self.cli = username, key, cli

    def run(self, *args: str, timeout: float = 600) -> CLI_Result:
        home = tempfile.mkdtemp(prefix="kaggle-home-")   # isolate from any ~/.kaggle/kaggle.json
        env = {k: v for k, v in os.environ.items() if not k.startswith("KAGGLE_")}
        env.update(HOME=home, KAGGLE_CONFIG_DIR=home)
        if self.key.startswith("KGAT_"):          # new-style token from "Generate New Token"
            env["KAGGLE_API_TOKEN"] = self.key
        else:                                     # legacy kaggle.json username + key
            env.update(KAGGLE_USERNAME=self.username, KAGGLE_KEY=self.key)
        try:
            r = subprocess.run([str(self.cli), *args], capture_output=True, text=True, env=env, timeout=timeout)
            return CLI_Result(r.returncode, (r.stdout + "\n" + r.stderr).strip())
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def verify(self) -> CLI_Result:
        return self.run("kernels", "list", "--user", self.username, "--page-size", "1")


# ---------------------------------------------------------------------------
# One remote run
# ---------------------------------------------------------------------------

class KaggleRunner:
    def __init__(self, db, log: Callable[[str], None] = print, cli_factory=KaggleCLI, sleep=time.sleep):
        self.accounts = Accounts(db)
        self.log, self.cli_factory, self.sleep = log, cli_factory, sleep

    @property
    def configured(self) -> bool:
        return bool(self.accounts.cpu_order())

    def run(self, spec: dict) -> dict:
        """Answers for `spec` from the first account/accelerator that works. Raises KaggleError if none did."""
        errors = []
        for a in self.accounts.gpu_order():
            try:
                return self._attempt(a, spec, gpu=True)
            except KaggleError as e:
                errors.append(f"{a['username']} GPU: {e}")
                if e.quota:
                    until = next_reset(datetime.now(timezone.utc)).isoformat()
                    self.accounts.update(a["username"], gpu_blocked_until=until, quota_message=str(e)[:500])
                    self.log(f"  {a['username']}: GPU quota used up — skipping it until {until[:16]} UTC")
                elif e.run:          # the kernel ran and failed: every account would fail the same way
                    self.log(f"  {a['username']}: the Kaggle run itself failed — not retrying on other accounts")
                    raise
                else:
                    self.log(f"  {a['username']}: GPU run failed — {str(e)[:200]}")
        for a in self.accounts.cpu_order():
            try:
                self.log(f"No GPU account available — using Kaggle CPU on {a['username']} (slower)")
                return self._attempt(a, spec, gpu=False)
            except KaggleError as e:
                errors.append(f"{a['username']} CPU: {e}")
                self.log(f"  {a['username']}: CPU run failed — {str(e)[:200]}")
        raise KaggleError("; ".join(errors) or "no Kaggle account configured")

    def _attempt(self, account: dict, spec: dict, gpu: bool) -> dict:
        user = account["username"]
        cli = self.cli_factory(user, account["key"])
        accel = "gpu" if gpu else "cpu"
        spec = {**spec, "time_budget_s": GPU_BUDGET_S if gpu else CPU_BUDGET_S, "spec_id": uuid.uuid4().hex}
        started = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._upload_dataset(cli, user, spec, tmp / "data")
            kdir = tmp / "kernel"
            kdir.mkdir()
            shutil.copy(RUNNER, kdir / "runner.py")
            meta = {"id": f"{user}/{KERNEL_SLUG}", "title": KERNEL_SLUG, "code_file": "runner.py",
                    "language": "python", "kernel_type": "script", "is_private": True,
                    "enable_gpu": gpu, "enable_internet": True, "dataset_sources": [f"{user}/{DATASET_SLUG}"]}
            if gpu:
                meta["machine_shape"] = "NvidiaTeslaT4"
            (kdir / "kernel-metadata.json").write_text(json.dumps(meta))
            args = ["kernels", "push", "-p", str(kdir), "-t", str((GPU_BUDGET_S if gpu else CPU_BUDGET_S) + 1800)]
            if gpu:
                args += ["--accelerator", "NvidiaTeslaT4"]
            self.log(f"Starting Kaggle {'GPU T4 ×2' if gpu else 'CPU'} run on {user}")
            r = cli.run(*args)
            if r.code or re.search(r"\berror\b", r.out, re.I):
                self.accounts.record_run(user, accel, started, 0, "push failed")
                raise KaggleError(f"push: {r.out[-400:]}", quota=bool(QUOTA_RE.search(r.out)))
            status = self._wait(cli, f"{user}/{KERNEL_SLUG}")
            seconds = (datetime.now(timezone.utc) - started).total_seconds()
            out_dir = tmp / "out"
            out_dir.mkdir()
            cli.run("kernels", "output", f"{user}/{KERNEL_SLUG}", "-p", str(out_dir), "-o", timeout=900)
            answers_file = out_dir / "answers.json"
            answers = json.loads(answers_file.read_text()) if answers_file.exists() else None
            logs = " ".join(p.read_text(errors="ignore")[-4000:] for p in out_dir.glob("*.log"))
            self.accounts.record_run(user, accel, started, seconds, status)
            if not answers or answers.get("meta", {}).get("spec_id") != spec["spec_id"]:
                quota = bool(QUOTA_RE.search(logs)) and status != "complete"
                raise KaggleError(f"run {status} without results. {logs[-400:]}", quota=quota, run=not quota)
            if not answers.get("triage") and answers.get("meta", {}).get("errors"):
                raise KaggleError(f"kernel failed: {answers['meta']['errors'][:3]}", run=True)
            m = answers["meta"]
            self.log(f"Kaggle run {status} in {seconds / 60:.0f} min on {', '.join(m.get('device', []))}: "
                     f"{len(answers['triage'])} titles, {len(answers['eval'])} evaluations"
                     f"{'' if m.get('complete') else ' (time budget reached — rest next run)'}")
            for e in m.get("errors", [])[:5]:
                self.log(f"  kernel warning: {e[:200]}")
            return answers

    def _upload_dataset(self, cli: KaggleCLI, user: str, spec: dict, folder: Path) -> None:
        folder.mkdir()
        (folder / "spec.json").write_text(json.dumps(spec))
        (folder / "dataset-metadata.json").write_text(json.dumps(
            {"title": DATASET_SLUG, "id": f"{user}/{DATASET_SLUG}", "licenses": [{"name": "CC0-1.0"}]}))
        exists = cli.run("datasets", "status", f"{user}/{DATASET_SLUG}").code == 0
        r = (cli.run("datasets", "version", "-p", str(folder), "-m", "job board batch", "-q", "-d", timeout=900)
             if exists else cli.run("datasets", "create", "-p", str(folder), "-q", timeout=900))
        if r.code:
            raise KaggleError(f"dataset upload: {r.out[-400:]}")
        denied = 0
        for _ in range(60):                      # Kaggle processes a new version before kernels can read it
            s = cli.run("datasets", "status", f"{user}/{DATASET_SLUG}").out.lower()
            if "ready" in s:
                return
            if "client error" in s and denied < 6:   # a just-created dataset answers 403/404 for a few seconds
                denied += 1                          # (an account's first run failed on this)
            elif "error" in s:
                raise KaggleError(f"dataset processing: {s[-300:]}")
            self.sleep(10)
        raise KaggleError("dataset was not ready after 10 minutes")

    def _wait(self, cli: KaggleCLI, kernel: str) -> str:
        last = None
        while True:
            out = cli.run("kernels", "status", kernel).out
            m = re.search(r'status\s+"?([\w.]+)"?', out)          # e.g. has status "KernelWorkerStatus.COMPLETE"
            status = (m.group(1).split(".")[-1] if m else out.strip()[-40:]).lower()
            if status != last:
                self.log(f"  Kaggle: {status}")
                last = status
            if status in ("complete", "error", "cancelacknowledged", "cancelled"):
                return status
            self.sleep(POLL_S)
