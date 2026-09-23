"""Score every results/*.json against gold.json and print a comparison table.

  ../.venv/bin/python bench/report.py            # markdown table to stdout, also results/report.md
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import questions as Q  # noqa: E402
from app.pipeline import score_answers  # noqa: E402

BENCH = Path(__file__).parent
CHOICES = ["field", "level", "experience", "work_mode", "employment", "degree"]


def acc(pairs):
    pairs = list(pairs)
    return (sum(a == b for a, b in pairs) / len(pairs), len(pairs)) if pairs else (None, 0)


def brier(pairs):
    pairs = list(pairs)
    return sum((p - float(y)) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def ece(conf_correct, bins=10):
    if not conf_correct:
        return None
    total, err = len(conf_correct), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        cell = [(c, k) for c, k in conf_correct if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if cell:
            err += len(cell) / total * abs(sum(c for c, _ in cell) / len(cell) - sum(k for _, k in cell) / len(cell))
    return err


def evaluate(res, gold):
    r = {"name": res["name"]}
    G, A = gold["jobs"], res["jobs"]
    ids = [i for i in G if i in A]
    r["jobs_answered"] = f"{len(ids)}/{len(G)}"
    r["tech_acc"], _ = acc((A[i]["tech_role"]["noul"] >= 0.5, G[i]["tech"]) for i in ids if "tech" in G[i])
    r["tech_brier"] = brier((A[i]["tech_role"]["noul"], G[i]["tech"]) for i in ids if "tech" in G[i])
    r["eligible_acc"], _ = acc((A[i]["location"]["choice"] in ("remote_open", "local_office"), G[i]["eligible"])
                               for i in ids if "eligible" in G[i])
    r["location_acc"], _ = acc((A[i]["location"]["choice"], G[i]["location"]) for i in ids if "location" in G[i])
    cc = []
    for c in CHOICES:
        key = {"field": "field", "level": "level", "experience": "experience", "work_mode": "work_mode",
               "employment": "employment", "degree": "degree"}[c]
        pairs = [(A[i][key]["choice"], G[i][c]) for i in ids if c in G[i]]
        r[f"{c}_acc"], r[f"{c}_n"] = acc(pairs)
        cc += [(A[i][key]["confidence"], A[i][key]["choice"] == G[i][c]) for i in ids if c in G[i]]
    cc += [(A[i]["location"]["confidence"], A[i]["location"]["choice"] == G[i]["location"]) for i in ids if "location" in G[i]]
    r["choice_ece"] = ece(cc)
    r["red_flag_acc"], _ = acc((A[i]["red_flags"]["noul"] >= 0.5, G[i]["red_flags"]) for i in ids if "red_flags" in G[i])
    sk = [(A[i]["skill_alignment"]["score"], G[i]["skill"]) for i in ids if "skill" in G[i]]
    r["skill_mae"] = sum(abs(a - b) for a, b in sk) / len(sk) if sk else None

    # The number that matters: which jobs would reach the board (pipeline scoring, default threshold).
    prof = {"level": "entry", "country": "India"}
    tp = fp = fn = tn = 0
    for i in ids:
        if "relevant" not in G[i]:
            continue
        card = score_answers(A[i], prof, [], [], Q.DEFAULT_MIN_MATCH)
        shown = not card["gates"]
        tp += shown and G[i]["relevant"]
        fp += shown and not G[i]["relevant"]
        fn += (not shown) and G[i]["relevant"]
        tn += (not shown) and not G[i]["relevant"]
    r["board"] = f"TP {tp} · FP {fp} · FN {fn} · TN {tn}"
    r["board_correct"] = (tp + tn) / max(1, tp + fp + fn + tn)

    GT, AT = gold["titles"], res["titles"]
    tid = [i for i in GT if i in AT]
    r["triage_field_acc"], _ = acc((AT[i]["field"] >= 0.5, GT[i]["field"]) for i in tid if "field" in GT[i])
    r["triage_above_acc"], _ = acc((AT[i]["above"] >= 0.5, GT[i]["above"]) for i in tid if "above" in GT[i])
    r["triage_field_brier"] = brier((AT[i]["field"], GT[i]["field"]) for i in tid if "field" in GT[i])
    # Triage as used: would it wrongly drop a good title? (recall on in-field, not-above titles)
    keep = [(AT[i]["field"] >= Q.TRIAGE_FIELD_MIN and AT[i]["above"] <= Q.TRIAGE_ABOVE_MAX)
            for i in tid if GT[i].get("field") and GT[i].get("above") is False]
    r["triage_keep_recall"] = sum(keep) / len(keep) if keep else None

    ev, tr = res["timing"]["eval"], res["timing"]["triage"]
    r["sec_per_job"] = statistics.median(ev) if ev else None
    r["sec_per_60_titles"] = statistics.median(tr) if tr else None
    r["errors"] = len(res["errors"])
    return r


def fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def main():
    gold = json.loads((BENCH / "gold.json").read_text())
    rows = [evaluate(json.loads(p.read_text()), gold) for p in sorted((BENCH / "results").glob("*.json"))]
    if not rows:
        print("no results yet")
        return
    keys = ["jobs_answered", "board", "board_correct", "tech_acc", "tech_brier", "eligible_acc", "location_acc",
            "level_acc", "experience_acc", "work_mode_acc", "field_acc", "employment_acc", "degree_acc",
            "red_flag_acc", "skill_mae", "choice_ece", "triage_field_acc", "triage_above_acc",
            "triage_field_brier", "triage_keep_recall", "sec_per_job", "sec_per_60_titles", "errors"]
    lines = ["| metric | " + " | ".join(r["name"] for r in rows) + " |", "|---|" + "---|" * len(rows)]
    lines += [f"| {k} | " + " | ".join(fmt(r.get(k)) for r in rows) + " |" for k in keys]
    md = "\n".join(lines)
    print(md)
    (BENCH / "results" / "report.md").write_text(md + "\n")


if __name__ == "__main__":
    main()
