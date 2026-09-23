"""On-demand local decision engine (Kev): loaded only while something needs it.

The pipeline and the profile builder call `use()`; the first user starts the server, the last one
leaving schedules a stop after `idle_stop` seconds. Kev-4B holds ~8 GB, so it shouldn't sit in memory
while you're only swiping or editing the tracker. Only one instance ever runs: two 4B models on one
GPU crash with a Metal timeout.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

import requests


class LocalEngine:
    def __init__(self, cmd: List[str], cwd: Path, port: int, log_path: Path,
                 idle_stop: float = 300, ready_timeout: float = 300):
        self.cmd, self.cwd, self.port, self.log_path = cmd, cwd, port, log_path
        self.idle_stop, self.ready_timeout = idle_stop, ready_timeout
        self.proc: Optional[subprocess.Popen] = None
        self.users = 0
        self.state = "stopped"          # stopped | loading | ready | stopping | error
        self.error: Optional[str] = None
        self._lock = threading.RLock()
        self._timer: Optional[threading.Timer] = None

    @classmethod
    def from_env(cls, root: Path) -> Optional["LocalEngine"]:
        if os.environ.get("LOCAL_ENGINE") != "kev":
            return None
        kev_dir = (root / os.environ.get("KEV_DIR", "bench/engines/kev")).resolve()
        port = int(os.environ.get("JEV_BASE_URL", "http://127.0.0.1:8011").rsplit(":", 1)[-1].split("/")[0])
        cmd = [str(kev_dir / ".venv/bin/python"), "-m", "kev.serve",
               "--run", os.environ.get("KEV_RUN", "jaredpalmer/kev-4b"), "--port", str(port)]
        return cls(cmd, kev_dir, port, root / "data" / "kev.log",
                   idle_stop=float(os.environ.get("KEV_IDLE_STOP", 300)))

    # -- public -----------------------------------------------------------
    @contextmanager
    def use(self):
        """Hold the engine loaded for the duration of the block."""
        with self._lock:
            self.users += 1
            if self._timer:
                self._timer.cancel()
                self._timer = None
        try:
            self._ensure()
            yield
        finally:
            with self._lock:
                self.users -= 1
                if self.users == 0 and self.proc:
                    self._timer = threading.Timer(self.idle_stop, self._idle)
                    self._timer.daemon = True
                    self._timer.start()

    def stop(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            p, self.proc = self.proc, None
        if p and p.poll() is None:
            if self.state != "error":
                self.state = "stopping"
            p.terminate()                       # Kev finishes its in-flight request, then exits
            try:
                p.wait(timeout=120)
            except subprocess.TimeoutExpired:
                p.kill()
        if self.state != "error":
            self.state = "stopped"

    def ping(self) -> bool:
        try:
            return requests.get(f"http://127.0.0.1:{self.port}/v1/models", timeout=3).ok
        except requests.RequestException:
            return False

    # -- internals ------------------------------------------------------------
    def _idle(self) -> None:
        with self._lock:
            if self.users:
                return
        self.stop()

    def _port_busy_by_other(self) -> bool:
        """Another Kev on our port that we didn't start (e.g. one still finishing a request)."""
        r = subprocess.run(["pgrep", "-f", f"kev.serve.*--port {self.port}"], capture_output=True, text=True)
        mine = {str(self.proc.pid)} if self.proc else set()
        return bool(set(r.stdout.split()) - mine)

    def _ensure(self) -> None:
        deadline = time.time() + 180
        while self.state == "stopping" and time.time() < deadline:
            time.sleep(1)                           # an unload is in progress: don't grab the dying server
        with self._lock:
            if self.proc and self.proc.poll() is None and self.ping():
                self.state = "ready"
                return
            if not self.proc and self.ping():       # someone else's instance is serving: just use it
                self.state = "ready"
                return
            for attempt in (1, 2):
                deadline = time.time() + 180
                while self._port_busy_by_other() and time.time() < deadline:
                    time.sleep(2)                   # wait for a previous instance to finish and exit
                self.state, self.error = "loading", None
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                log = open(self.log_path, "a")
                self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdout=log, stderr=subprocess.STDOUT)
                end = time.time() + self.ready_timeout
                while time.time() < end and self.proc.poll() is None:
                    if self.ping():
                        self.state = "ready"
                        return
                    time.sleep(2)
                self.stop()
                time.sleep(5)
            self.state, self.error = "error", f"engine failed to start; see {self.log_path}"
            raise RuntimeError(self.error)
