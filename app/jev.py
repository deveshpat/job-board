"""Thin client for TypeSafe's Jev (System One) API.

POST https://api.typesafe.ai/v1/systemone  ->  {"answers": {...}, "usage": {...}}

Handles auth, retries with backoff on 429/529/5xx, splitting very large question
maps into several requests (the state is re-sent, answers are merged), and a
persistent response cache so re-running the pipeline never pays twice for the
same (state, questions) pair.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

API_URL = os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai").rstrip("/") + "/v1/systemone"
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
# 64k tokens covers state + all questions; stay well under it (≈4 chars/token).
MAX_QUESTION_CHARS = 90_000
MAX_QUESTIONS_PER_CALL = 120

_KEY_FILES = [Path.home() / ".config" / "typesafe" / "api_key"]


class JevError(RuntimeError):
    pass


def load_api_key() -> Optional[str]:
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key.strip()
    for f in _KEY_FILES:
        if f.exists():
            return f.read_text().strip() or None
    return None


def noul(instructions: Any, true: Any = None, false: Any = None) -> dict:
    q: Dict[str, Any] = {"type": "noul", "instructions": instructions}
    if true is not None or false is not None:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    return q


def choice(instructions: Any, criteria: Dict[str, Any]) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def score(instructions: Any, levels: list) -> dict:
    return {"type": "score", "instructions": instructions, "criteria": levels}


class Jev:
    def __init__(self, api_key: Optional[str] = None, cache_path: Optional[Path] = None,
                 model: str = MODEL, timeout: float = 60.0):
        self.api_key = api_key or load_api_key()
        self.model = model
        # Local engines are far slower than TypeSafe's GPUs (a 60-title triage batch takes ~70 s on an M4).
        self.timeout = max(timeout, 1800.0) if self.is_local else timeout
        self.session = requests.Session()
        self.usage = {"requests": 0, "cached": 0, "input_tokens": 0}
        self._lock = threading.Lock()
        self._cache: Optional[sqlite3.Connection] = None
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache = sqlite3.connect(str(cache_path), check_same_thread=False)
            self._cache.execute("CREATE TABLE IF NOT EXISTS jev_cache (k TEXT PRIMARY KEY, v TEXT)")

    @property
    def is_local(self) -> bool:
        """A self-hosted System One server (Kev, Decider, ...) on this machine: no key needed."""
        return bool(re.match(r"https?://(127\.0\.0\.1|localhost)[:/]", API_URL))

    @property
    def available(self) -> bool:
        return bool(self.api_key) or self.is_local

    # -- public ---------------------------------------------------------
    def ask(self, state: Any, questions: Dict[str, dict]) -> Dict[str, dict]:
        """Evaluate `questions` against `state`; returns {question_id: answer}."""
        if not self.available:
            raise JevError("No TypeSafe API key and no local engine. See README.")
        answers: Dict[str, dict] = {}
        for chunk in self._chunks(questions):
            answers.update(self._ask_one(state, chunk))
        return answers

    # -- internals ------------------------------------------------------
    @staticmethod
    def _chunks(questions: Dict[str, dict]):
        chunk: Dict[str, dict] = {}
        size = 0
        for qid, q in questions.items():
            qs = len(json.dumps(q))
            if chunk and (size + qs > MAX_QUESTION_CHARS or len(chunk) >= MAX_QUESTIONS_PER_CALL):
                yield chunk
                chunk, size = {}, 0
            chunk[qid] = q
            size += qs
        if chunk:
            yield chunk

    def _ask_one(self, state: Any, questions: Dict[str, dict]) -> Dict[str, dict]:
        body = {"model": self.model, "state": state, "questions": questions}
        key = hashlib.sha256((API_URL + json.dumps(body, sort_keys=True)).encode()).hexdigest()
        cached = self._cache_get(key)
        if cached is not None:
            with self._lock:
                self.usage["cached"] += 1
            return cached

        delay = 1.0
        for attempt in range(6):
            try:
                r = self.session.post(
                    API_URL, json=body, timeout=self.timeout,
                    headers={"Authorization": f"Bearer {self.api_key or 'local'}"},
                )
            except requests.RequestException as e:
                if attempt == 5:
                    raise JevError(f"Jev connection failed: {e}") from e
                time.sleep(delay)
                delay *= 2
                continue
            if r.status_code == 200:
                data = r.json()
                with self._lock:
                    self.usage["requests"] += 1
                    self.usage["input_tokens"] += data.get("usage", {}).get("input_tokens", 0)
                answers = data["answers"]
                self._cache_put(key, answers)
                return answers
            if r.status_code in (429, 500, 502, 503, 504, 529) and attempt < 5:
                retry_after = r.headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after else delay + random.random())
                delay *= 2
                continue
            raise JevError(f"Jev HTTP {r.status_code}: {r.text[:500]}")
        raise JevError("Jev: retries exhausted")

    def _cache_get(self, key: str) -> Optional[dict]:
        if not self._cache:
            return None
        with self._lock:
            row = self._cache.execute("SELECT v FROM jev_cache WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def _cache_put(self, key: str, answers: dict) -> None:
        if not self._cache:
            return
        with self._lock:
            self._cache.execute("INSERT OR REPLACE INTO jev_cache VALUES (?,?)", (key, json.dumps(answers)))
            self._cache.commit()
