# Job-board runner for Kaggle (pushed by app/kaggle.py; not run locally).
#
# Reads spec.json from the attached private dataset, serves Kev on every GPU (or the CPU), and answers:
#   1. triage questions for every new title,
#   2. picks candidates with the same thresholds the local pipeline uses,
#   3. evaluation questions for the best of them (up to max_deep),
# writing /kaggle/working/answers.json after every step so a timeout still returns partial work.
import glob
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

T0 = time.time()
OUT = "/kaggle/working/answers.json"
LOG = []


def log(msg):
    line = f"[{time.time() - T0:7.1f}s] {msg}"
    print(line, flush=True)
    LOG.append(line)


def sh(cmd, check=True, env=None):
    log(f"$ {cmd}")
    r = subprocess.run(cmd, shell=True, text=True, capture_output=True, env=env)
    if r.returncode and check:
        print(r.stdout[-3000:], r.stderr[-3000:], flush=True)
        raise RuntimeError(f"command failed ({r.returncode}): {cmd}")
    return r.stdout


spec_path = glob.glob("/kaggle/input/**/spec.json", recursive=True)
SPEC = json.load(open(spec_path[0]))
BUDGET = SPEC["time_budget_s"]
RESULT = {"triage": {}, "eval": {}, "meta": {"spec_id": SPEC["spec_id"], "log": LOG, "errors": []}}
LOCK = threading.Lock()   # both GPU workers write RESULT and save it; serialise (the 2nd real run crashed here)


def record(section, key, value):
    with LOCK:
        RESULT[section][key] = value


def error(msg):
    with LOCK:
        RESULT["meta"]["errors"].append(msg)


def save():
    with LOCK:
        RESULT["meta"]["elapsed_s"] = round(time.time() - T0)
        text = json.dumps(RESULT)
    tmp = OUT + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, OUT)


# -- 1. environment: Kev at the benchmarked commit, in its own Python 3.13 env -------------------------
gpus = [l for l in sh("nvidia-smi -L", check=False).splitlines() if l.startswith("GPU")]
RESULT["meta"]["device"] = gpus or ["cpu"]
log(f"devices: {gpus or 'cpu only'}")
sh("pip install -q uv")
sh(f"git clone -q https://github.com/jaredpalmer/kev /tmp/kev && cd /tmp/kev && git checkout -q {SPEC['kev_commit']}")
sh("cd /tmp/kev && uv sync -q --extra serve")
PY = "/tmp/kev/.venv/bin/python"
fla = bool(gpus) and SPEC.get("try_fla", True)
if fla:
    r = subprocess.run(f"cd /tmp/kev && uv pip install -q -p {PY} flash-linear-attention 'triton>=3.7.1'",
                       shell=True, capture_output=True, text=True)
    fla = r.returncode == 0
log(f"flash-linear-attention: {'installed' if fla else 'not used'}")

# -- 2. one Kev server per GPU (or one on CPU) ----------------------------------------------------------
PROBE = {"model": "jev-latest", "state": "Payouts failing for 3 days.",
         "questions": {"u": {"type": "noul", "instructions": "Is this urgent?"}}}


def post(port, body, timeout=3600):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/systemone", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["answers"]


def start_servers():
    procs, ports = [], []
    slots = [str(i) for i in range(len(gpus))] or [""]
    for i, dev in enumerate(slots):
        # KEV_MERGE=0: load straight in fp16. Kev's default merges its LoRA in fp32 first, which needs ~16 GB
        # for the 4B model — more than a T4's 14.5 GB (the first real run died with CUDA out-of-memory).
        env = dict(os.environ, CUDA_VISIBLE_DEVICES=dev, KEV_DTYPE="fp16" if gpus else "fp32", KEV_MERGE="0",
                   PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True")
        port = 8011 + i
        f = open(f"/kaggle/working/kev-{i}.log", "w")
        procs.append(subprocess.Popen([PY, "-m", "kev.serve", "--run", SPEC["kev_run"], "--port", str(port)],
                                      cwd="/tmp/kev", env=env, stdout=f, stderr=subprocess.STDOUT))
        ports.append(port)
        if i == 0:
            time.sleep(20)          # let the first one populate the HF cache before the others read it
    ready = []
    for p, port in zip(procs, ports):
        for _ in range(900):
            if p.poll() is not None:
                break
            try:
                post(port, PROBE, timeout=600)
                ready.append(port)
                break
            except Exception:
                time.sleep(2)
    return procs, ready


procs, PORTS = start_servers()
if not PORTS and fla:
    log("Kev failed with flash-linear-attention; retrying without it")
    for p in procs:
        p.kill()
    sh(f"cd /tmp/kev && uv pip uninstall -q -p {PY} flash-linear-attention", check=False)
    procs, PORTS = start_servers()
if not PORTS:
    error("no Kev server became ready")
    save()
    sys.exit(1)
log(f"Kev ready on ports {PORTS}")
RESULT["meta"]["servers"] = len(PORTS)


def ask(port, state, questions):
    answers, chunk = {}, {}
    for qid, q in questions.items():           # same 120-question chunking as the app's client
        chunk[qid] = q
        if len(chunk) == 120:
            answers.update(post(port, {"model": "jev-latest", "state": state, "questions": chunk}))
            chunk = {}
    if chunk:
        answers.update(post(port, {"model": "jev-latest", "state": state, "questions": chunk}))
    return answers


def run_parallel(items, fn, label):
    """items are processed across all servers; stops starting new work when the time budget is spent."""
    done = 0

    def worker(k):
        nonlocal done
        port = PORTS[k % len(PORTS)]
        for j in range(k, len(items), len(PORTS)):
            if time.time() - T0 > BUDGET:
                return
            try:
                fn(port, items[j])
            except Exception as e:           # one bad item must not sink the run
                error(f"{label} {j}: {e}")
            with LOCK:
                done += 1
                due = done % 10 == 0
            if due:
                save()
    with ThreadPoolExecutor(len(PORTS)) as ex:
        list(ex.map(worker, range(len(PORTS))))
    save()


# -- 3. triage ------------------------------------------------------------------------------------------
t = time.time()
tri = SPEC["triage"]


def do_triage(port, batch):
    ans = ask(port, tri["state"], batch["questions"])
    for k, jid in enumerate(batch["ids"]):
        record("triage", jid, {"field": ans[f"field_{k}"]["noul"], "above": ans[f"above_{k}"]["noul"]})


run_parallel(tri["batches"], do_triage, "triage")
RESULT["meta"]["triage_s"] = round(time.time() - t)
log(f"triaged {len(RESULT['triage'])} titles in {time.time() - t:.0f}s")

# -- 4. choose candidates exactly like app/pipeline.py -----------------------------------------------------
cfg = SPEC["select"]
scored = sorted(((v["field"] - 0.5 * v["above"], jid) for jid, v in RESULT["triage"].items()
                 if v["field"] >= cfg["field_min"] and v["above"] <= cfg["above_max"]), reverse=True)
candidates = [jid for _, jid in scored] + [j for j in SPEC["backlog"] if j not in RESULT["triage"]]
candidates = [j for j in candidates if j in SPEC["jobs"]][: cfg["max_deep"]]
log(f"{len(candidates)} candidates for evaluation")

# -- 5. evaluate ----------------------------------------------------------------------------------------------
t = time.time()


def do_eval(port, jid):
    record("eval", jid, ask(port, {"candidate": SPEC["candidate"], "job": SPEC["jobs"][jid]}, SPEC["eval_questions"]))


run_parallel(candidates, do_eval, "eval")
RESULT["meta"]["eval_s"] = round(time.time() - t)
RESULT["meta"]["complete"] = len(RESULT["eval"]) == len(candidates)
log(f"evaluated {len(RESULT['eval'])} jobs in {time.time() - t:.0f}s")
for p in procs:
    p.terminate()
save()
