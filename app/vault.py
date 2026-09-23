"""Encrypted state for the public GitHub repo. The browser (web/vault.js) implements the same format.

Everything personal (profile, tracker, swipes, board) is stored as AES-256-GCM ciphertext; only the
encryption *format* is public. One random 256-bit data key encrypts all files. That key is:
  - in the GitHub Actions secret JOBBOARD_DATA_KEY (so scheduled runs can read and write state), and
  - "wrapped" in keys.json under your passphrase (PBKDF2-SHA256) and/or passkeys (WebAuthn PRF),
    so any browser can unlock it without the key ever being stored in the clear.

Blob: {"v": 1, "iv": b64, "ct": b64}; plaintext = gzip(JSON); AAD = "jobboard:<name>" so files
can't be swapped for one another.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PBKDF2_ITERATIONS = 600_000        # OWASP 2023+ guidance for PBKDF2-SHA256
MIN_PASSPHRASE_BITS = 60           # the ciphertext is public, so offline guessing is the threat


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def unb64(s: str) -> bytes:
    return base64.b64decode(s)


def new_data_key() -> bytes:
    return os.urandom(32)


def encrypt(key: bytes, name: str, obj: Any) -> bytes:
    iv = os.urandom(12)
    plain = gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), mtime=0)
    ct = AESGCM(key).encrypt(iv, plain, f"jobboard:{name}".encode())
    return json.dumps({"v": 1, "iv": b64(iv), "ct": b64(ct)}).encode()


def decrypt(key: bytes, name: str, blob: bytes) -> Any:
    d = json.loads(blob)
    plain = AESGCM(key).decrypt(unb64(d["iv"]), unb64(d["ct"]), f"jobboard:{name}".encode())
    return json.loads(gzip.decompress(plain))


# -- keys.json: the data key wrapped by each unlock method ----------------------------------------

def normalize_passphrase(p: str) -> str:
    """Case and spacing don't matter when typing it back (web/vault.js does the same)."""
    return " ".join(p.lower().split())


def generate_passphrase(words: int = 6) -> str:
    """Diceware: `words` picks from the EFF large wordlist (7776 words ≈ 12.9 bits each)."""
    import secrets
    from pathlib import Path
    wordlist = (Path(__file__).parent / "wordlist.txt").read_text().split()
    return " ".join(secrets.choice(wordlist) for _ in range(words))


def _kek_from_passphrase(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", normalize_passphrase(passphrase).encode(), salt, iterations, dklen=32)


def wrap_passphrase(data_key: bytes, passphrase: str, iterations: int = PBKDF2_ITERATIONS) -> Dict[str, Any]:
    salt, iv = os.urandom(16), os.urandom(12)
    kek = _kek_from_passphrase(passphrase, salt, iterations)
    ct = AESGCM(kek).encrypt(iv, data_key, b"jobboard:wrap:passphrase")
    return {"kind": "passphrase", "kdf": "PBKDF2-SHA256", "iterations": iterations,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def unwrap_passphrase(wrap: Dict[str, Any], passphrase: str) -> bytes:
    kek = _kek_from_passphrase(passphrase, unb64(wrap["salt"]), wrap["iterations"])
    return AESGCM(kek).decrypt(unb64(wrap["iv"]), unb64(wrap["ct"]), b"jobboard:wrap:passphrase")


def unlock(keys: Dict[str, Any], passphrase: str) -> Optional[bytes]:
    for w in keys.get("wraps", []):
        if w["kind"] == "passphrase":
            try:
                return unwrap_passphrase(w, passphrase)
            except Exception:
                continue
    return None


def passphrase_bits(p: str) -> float:
    """Rough strength estimate: words for diceware-style phrases, character classes otherwise."""
    import math
    words = [w for w in p.replace("-", " ").split() if w]
    if len(words) >= 4 and all(w.isalpha() for w in words):
        return len(words) * math.log2(7776)          # diceware-sized wordlist assumption
    pool = sum(n for cond, n in ((any(c.islower() for c in p), 26), (any(c.isupper() for c in p), 26),
                                  (any(c.isdigit() for c in p), 10), (any(not c.isalnum() for c in p), 33)) if cond)
    return len(p) * math.log2(pool) if pool else 0.0
