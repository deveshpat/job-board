"""A stand-in for api.typesafe.ai used by tests: validates request shape the way the real API
documents it and returns correctly-typed, deterministic answers."""
import hashlib

from fastapi import FastAPI, HTTPException, Request

app = FastAPI()
CALLS = {"n": 0}


def _u(*parts) -> float:
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _dist(keys, seed):
    w = [_u(seed, k) ** 3 + 0.01 for k in keys]
    t = sum(w)
    return {k: x / t for k, x in zip(keys, w)}


def _conf(p):
    n = len(p)
    return max(0.0, min(1.0, (n * max(p.values()) - 1) / (n - 1)))


@app.get("/v1/models")
def models():
    return {"models": [{"name": "jev-latest"}]}


@app.post("/v1/systemone")
async def systemone(req: Request):
    if not req.headers.get("authorization", "").startswith("Bearer "):
        raise HTTPException(401, "missing key")
    return answer(await req.json())


def answer(body: dict) -> dict:
    """Deterministic, correctly-typed answers for a /v1/systemone request body."""
    for f in ("model", "state", "questions"):
        if f not in body:
            raise HTTPException(422, f"missing {f}")
    CALLS["n"] += 1
    state = hashlib.md5(str(body["state"]).encode()).hexdigest()
    answers = {}
    for qid, q in body["questions"].items():
        seed = (state, q["instructions"])
        if q["type"] == "noul":
            answers[qid] = {"type": "noul", "noul": round(_u(*seed), 4)}
        elif q["type"] == "choice":
            crit = q["criteria"]
            if not isinstance(crit, dict) or not crit or len(crit) > 255:
                raise HTTPException(422, f"{qid}: bad choice criteria")
            p = _dist(list(crit), seed)
            answers[qid] = {"type": "choice", "choice": max(p, key=p.get), "probabilities": p,
                            "confidence": _conf(p)}
        elif q["type"] == "score":
            crit = q["criteria"]
            if not isinstance(crit, list) or not 2 <= len(crit) <= 10:
                raise HTTPException(422, f"{qid}: bad score criteria")
            keys = [str(i) for i in range(len(crit))]
            p = _dist(keys, seed)
            answers[qid] = {"type": "score", "score": sum(int(k) * v for k, v in p.items()),
                            "legend": {str(i): c for i, c in enumerate(crit)}, "probabilities": p,
                            "confidence": _conf(p)}
        else:
            raise HTTPException(422, f"{qid}: unknown type")
    return {"model": "fake-jev", "answers": answers, "usage": {"input_tokens": len(str(body)) // 4, "output_tokens": 0}}
