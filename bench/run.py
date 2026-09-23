"""Run one System One engine over the frozen benchmark set and save its raw answers.

  ../.venv/bin/python bench/run.py --name decider-2b --base-url http://127.0.0.1:8010 [--model jev-latest]

Uses the app's own questions (app/questions.py) and client (app/jev.py), so what is measured is exactly
what the job board would ask. By default only the 12 core evaluation questions + triage are asked
(the per-skill chip questions are skipped to keep slow local engines tractable); --full adds them.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import jev as jev_mod  # noqa: E402
from app import questions as Q  # noqa: E402
from app.pipeline import gap_vocab  # noqa: E402
from app.profile import build_profile, compact_candidate  # noqa: E402

BENCH = Path(__file__).parent
RESUME = ROOT.parent / "Devesh_Patel_Resume.tex"
BENCH_FIELDS = ["ai_ml_engineering", "applied_ai_products", "full_stack", "backend", "data_science"]


def candidate() -> dict:
    """A fixed candidate so every engine sees identical state (no engine-dependent profile step)."""
    p = build_profile(RESUME, None)
    p.update(level="entry", country="India",
             fields=[{"name": f, "p": None} for f in BENCH_FIELDS])
    p["keywords"] = p["keywords"][:30]
    return compact_candidate(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--model", default="jev-latest")
    ap.add_argument("--key", default="local")
    ap.add_argument("--full", action="store_true", help="also ask the per-skill has_/gap_ questions")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true", help="keep answers already in results/<name>.json")
    args = ap.parse_args()

    jev_mod.API_URL = args.base_url.rstrip("/") + "/v1/systemone"
    client = jev_mod.Jev(api_key=args.key, model=args.model, timeout=1800)
    s = json.loads((BENCH / "set.json").read_text())
    jobs = s["jobs"][: args.limit or None]
    titles = s["titles"][: (args.limit * 3) or None]
    cand = candidate()
    qs = Q.evaluate_questions(cand, gap_vocab(cand["skills"]))
    if not args.full:
        qs = {k: v for k, v in qs.items() if not k.startswith(("has_", "gap_"))}
    out = {"name": args.name, "model": args.model, "base_url": args.base_url, "full": args.full,
           "candidate": cand, "jobs": {}, "titles": {}, "timing": {"eval": [], "triage": []}, "errors": []}
    res_path = BENCH / "results" / f"{args.name}.json"
    if args.resume and res_path.exists():
        prev = json.loads(res_path.read_text())
        for k in ("jobs", "titles", "timing"):
            out[k] = prev[k]
        print(f"resuming: {len(out['jobs'])} jobs, {len(out['titles'])} titles already done", flush=True)

    # Triage: 60 titles per request, 2 questions each (same as the pipeline).
    tstate = {"candidate": {k: cand[k] for k in ("headline", "goal", "level", "fields")}}
    for i in range(0, len(titles), Q.TRIAGE_BATCH):
        batch = titles[i:i + Q.TRIAGE_BATCH]
        if all(t["id"] in out["titles"] for t in batch):
            continue
        t0 = time.time()
        try:
            ans = client.ask(tstate, Q.triage_questions(batch))
        except Exception as e:
            out["errors"].append(f"triage {i}: {e}")
            continue
        out["timing"]["triage"].append(time.time() - t0)
        for k, t in enumerate(batch):
            out["titles"][t["id"]] = {"field": ans[f"field_{k}"]["noul"], "above": ans[f"above_{k}"]["noul"]}
        print(f"triage {i + len(batch)}/{len(titles)}  {out['timing']['triage'][-1]:.1f}s", flush=True)

    for n, j in enumerate(jobs, 1):
        if j["id"] in out["jobs"]:
            continue
        t0 = time.time()
        try:
            ans = client.ask({"candidate": cand, "job": j["state"]}, qs)
        except Exception as e:
            out["errors"].append(f"{j['id']}: {e}")
            print(f"job {n}: ERROR {e}", flush=True)
            continue
        dt = time.time() - t0
        out["timing"]["eval"].append(dt)
        out["jobs"][j["id"]] = ans
        print(f"job {n}/{len(jobs)}  {dt:.1f}s  {j['state']['title'][:60]}", flush=True)
        res_path.write_text(json.dumps(out))  # checkpoint

    ev = out["timing"]["eval"]
    if ev:
        print(f"median eval {statistics.median(ev):.2f}s  ({len(qs)} questions/job)")
    res_path.write_text(json.dumps(out))


if __name__ == "__main__":
    main()
