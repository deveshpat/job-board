"""End-to-end pipeline test against a local fake of the Jev API (no network, no credits)."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from app import jev as jev_mod
from app import pipeline as pl
from app.db import DB
from app.jev import Jev
from app.profile import build_profile

RESUME = Path(__file__).resolve().parents[2] / "Devesh_Patel_Resume.tex"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def fake_jev():
    from tests.fake_jev import app, CALLS
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline and t.is_alive(), "fake Jev server failed to start"
        time.sleep(0.05)
    jev_mod.API_URL = f"http://127.0.0.1:{port}/v1/systemone"
    yield CALLS
    server.should_exit = True


def _jobs():
    base = dict(location="Remote", remote=True, salary="", posted_at="2099-01-01", tags=[],
                employment_type="Full-time", location_restrictions="Worldwide", logo="", source="test")
    titles = ["Machine Learning Engineer", "AI Engineer (LLM agents)", "Account Executive",
              "Senior Staff Backend Engineer", "Full Stack Engineer (Next.js + FastAPI)"]
    return [dict(base, id=f"t{i}", title=t, company=f"Co{i}", url=f"https://example.com/{i}",
                 description=f"We are hiring a {t}. Python, PyTorch, FastAPI, React. " * 5)
            for i, t in enumerate(titles)]


def test_end_to_end(fake_jev, tmp_path, monkeypatch):
    jev = Jev(api_key="test-key", cache_path=tmp_path / "cache.db")
    db = DB(tmp_path / "db.sqlite")

    profile = build_profile(RESUME, jev)
    assert profile["scored_by"] == "jev"
    assert profile["projects"] and "EdgeRunner" in profile["projects"]
    assert any(k["keep"] for k in profile["keywords"])
    for t in profile["search_terms"]:
        t["keep"] = True                     # fake answers are random; keep every term
    db.put("profile", profile)
    db.put("settings", {"min_match": 0})     # load everything that passes the hard gates

    monkeypatch.setattr(pl, "fetch_all", lambda *a, **k: _jobs())
    monkeypatch.setattr(pl.Q, "TRIAGE_FIELD_MIN", 0.0)
    monkeypatch.setattr(pl.Q, "TRIAGE_ABOVE_MAX", 1.0)
    # A posting you already applied to elsewhere (tracked URL, different scheme/trailing slash) is skipped.
    db.add_application({"company": "Co0", "role": "x", "url": "http://www.example.com/0/"})
    p = pl.Pipeline(db, jev)
    stats = p.run()
    assert stats["fresh"] == 5 and stats["triaged_in"] == 4 and stats["evaluated"] == 4
    assert db.job("t0")["status"] == "applied"

    counts = db.status_counts()
    assert counts.get("new", 0) + counts.get("filtered", 0) == 4
    for row in db.jobs(["new", "filtered"]):
        card = row["card"]
        assert 0 <= card["match"] <= 100 and card["reasons"] and card["chips"]
        assert set(row["eval"]) == {"answers", "skills", "gaps"}

    # Second run: nothing new to fetch, and every Jev call is served from cache.
    calls_before = fake_jev["n"]
    stats2 = p.run()
    assert stats2["fresh"] == 0 and fake_jev["n"] == calls_before

    # Raising the bar re-scores from stored answers without calling Jev.
    db.put("settings", {"min_match": 101})
    assert p.rescore() == 0 and fake_jev["n"] == calls_before


def test_question_limits():
    from app import questions as Q
    qs = Q.evaluate_questions({"skills": ["Python"] * 60, "projects": {"A": "x"}, "country": "India"},
                              Q.SKILL_VOCAB)
    for q in qs.values():
        if q["type"] == "choice":
            assert 1 < len(q["criteria"]) <= 255
        if q["type"] == "score":
            assert 2 <= len(q["criteria"]) <= 10
    chunks = list(Jev._chunks(qs))
    assert sum(len(c) for c in chunks) == len(qs)


def test_url_matching():
    from app.pipeline import _norm_url
    same = ["https://www.example.com/jobs/1/", "http://example.com/jobs/1?utm_source=x", "example.com/jobs/1#apply"]
    assert len({_norm_url(u) for u in same}) == 1
    # Hacker News posts differ only by ?id= — they must not collapse into one.
    assert _norm_url("https://news.ycombinator.com/item?id=1") != _norm_url("https://news.ycombinator.com/item?id=2")


def test_link_helpers(tmp_path):
    from app.urls import from_url, parse_title
    assert from_url("https://job-boards.greenhouse.io/sigmoid/jobs/1") == {"company": "Sigmoid", "source": "greenhouse"}
    assert from_url("https://civicdatalab.zohorecruit.in/jobs/Careers/4")["company"] == "Civicdatalab"
    pt = lambda *a, **k: {k2: v for k2, v in parse_title(*a, **k).items() if v}
    # Real titles seen on Greenhouse, Ashby, Lever and Zoho pages:
    assert pt("Job Application for Full Stack SDE II at Sigmoid") == {"role": "Full Stack SDE II", "company": "Sigmoid"}
    assert pt("AI Solutions Engineer @ Bolna AI") == {"role": "AI Solutions Engineer", "company": "Bolna AI"}
    assert pt("Workloom - Ai Engg Intern", company="Epifi") == {"role": "Ai Engg Intern", "company": "Workloom"}
    assert pt("CivicDataLab - Junior AI Developer in New Delhi") == {
        "role": "Junior AI Developer", "company": "CivicDataLab", "location": "New Delhi"}
    assert pt("Backend Engineer | Acme Careers") == {"role": "Backend Engineer", "company": "Acme"}
    assert parse_title("Careers")["role"] is None
    assert parse_title("Current Openings")["role"] is None

    db = DB(tmp_path / "db.sqlite")
    job = dict(id="j1", source="greenhouse", title="AI Engineer", company="Acme", location="Remote",
               url="https://job-boards.greenhouse.io/acme/jobs/1", posted_at=None)
    db.insert_raw([job])
    assert db.find_job_by_url("http://job-boards.greenhouse.io/acme/jobs/1/?utm_source=x")["id"] == "j1"
    assert db.find_job_by_url("https://job-boards.greenhouse.io/acme/jobs/2") is None


def test_tracker_backup_round_trip(tmp_path, monkeypatch):
    """The auto-backup CSV restores into an empty tracker with every column intact."""
    backup = tmp_path / "tracker_backup.csv"
    db = DB(tmp_path / "a.sqlite", backup_csv=backup)
    db.add_application({"company": "Acme", "role": "AI Engineer", "url": "https://x.io/1", "location": "Remote",
                        "status": "Interviewing", "applied_on": "2026-09-01", "next_step": "Onsite 30/09",
                        "follow_up": "2026-09-28", "notes": "met CTO, said \"great\"", "source": "lever", "match": 71})
    row = db.add_application({"company": "Beta", "role": "SDE", "status": "Ghosted", "applied_on": "2026-08-01"})
    db.update_application(row["id"], {"notes": "no reply"})
    db.delete_application(db.add_application({"company": "Gone", "role": "x"})["id"])
    assert "Gone" not in backup.read_text() and "no reply" in backup.read_text()

    import importlib.util
    spec = importlib.util.spec_from_file_location("imp", Path(__file__).parents[1] / "scripts" / "import_applications.py")
    imp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(imp)
    fresh = DB(tmp_path / "b.sqlite")
    monkeypatch.setattr(imp, "DB", lambda: fresh)
    imp.main(str(backup))
    keep = ["company", "role", "url", "location", "status", "applied_on", "next_step", "follow_up", "notes", "source", "match"]
    strip = lambda rows: sorted(({k: r[k] for k in keep} for r in rows), key=lambda r: r["company"])
    assert strip(fresh.applications()) == strip(db.applications())


def test_local_engine_on_demand(tmp_path):
    """The engine starts on first use, is shared while in use, and unloads after going idle."""
    import sys
    from app.engine import LocalEngine
    port = _free_port()
    root = Path(__file__).resolve().parents[1]
    eng = LocalEngine([sys.executable, "-m", "uvicorn", "tests.fake_jev:app", "--port", str(port)],
                      cwd=root, port=port, log_path=tmp_path / "engine.log", idle_stop=1, ready_timeout=30)
    assert not eng.ping()
    with eng.use():
        assert eng.state == "ready" and eng.ping()
        pid = eng.proc.pid
        with eng.use():                      # nested user: same process, no restart
            assert eng.proc.pid == pid
    assert eng.ping()                        # still loaded during the idle grace period
    deadline = time.time() + 30
    while eng.state != "stopped" and time.time() < deadline:
        time.sleep(0.2)
    assert eng.state == "stopped" and not eng.ping() and eng.proc is None


def test_daily_schedule(tmp_path):
    from datetime import datetime
    db = DB(tmp_path / "db.sqlite")
    p = pl.Pipeline(db, Jev(api_key="x"))
    db.put("settings", {"daily_at": "09:00"})
    assert not p.due(datetime(2026, 9, 24, 10, 0))            # no profile yet
    db.put("profile", {"x": 1})
    assert not p.due(datetime(2026, 9, 24, 8, 59))            # before the scheduled time
    assert p.due(datetime(2026, 9, 24, 10, 0))                # after it, nothing run today (catch-up)
    db.save_run("2026-09-24T09:00:05", {}, [])
    assert not p.due(datetime(2026, 9, 24, 23, 0))            # already ran today
    assert p.due(datetime(2026, 9, 25, 9, 0))                 # next day
    db.put("settings", {"daily_at": ""})
    assert not p.due(datetime(2026, 9, 25, 9, 0))             # turned off


class FakeKaggle:
    """Stands in for the Kaggle CLI: per-account behaviour ('ok' | 'quota' | 'down'), answers from fake_jev."""
    behaviour = {}
    calls = []

    def __init__(self, username, key):
        self.username = username

    def run(self, *args, timeout=0):
        import json as _json
        from app.kaggle import CLI_Result
        from tests.fake_jev import answer
        FakeKaggle.calls.append((self.username, args[:2]))
        how = FakeKaggle.behaviour.get(self.username, "ok")
        if how == "down":
            return CLI_Result(1, "ConnectionError: could not reach kaggle.com")
        if args[:2] in (("datasets", "create"), ("datasets", "version")):
            FakeKaggle.spec = _json.loads((Path(args[args.index("-p") + 1]) / "spec.json").read_text())
            return CLI_Result(0, "Dataset version is being created")
        if args[:2] == ("datasets", "status"):
            return CLI_Result(0, "ready")
        if args[:2] == ("kernels", "push"):
            meta = _json.loads((Path(args[args.index("-p") + 1]) / "kernel-metadata.json").read_text())
            FakeKaggle.last_gpu = meta["enable_gpu"]
            if meta["enable_gpu"] and how == "quota":
                return CLI_Result(1, "400 Client Error: Maximum weekly GPU quota of 30.00 hours reached.")
            return CLI_Result(0, "Kernel version 1 successfully pushed.")
        if args[:2] == ("kernels", "status"):
            return CLI_Result(0, f'{self.username}/jobboard-runner has status "KernelWorkerStatus.COMPLETE"')
        if args[:2] == ("kernels", "output"):
            sp = FakeKaggle.spec
            tri = {jid: {"field": 0.9, "above": 0.1} for b in sp["triage"]["batches"] for jid in b["ids"]}
            ev = {jid: answer({"model": "x", "state": {"candidate": sp["candidate"], "job": st},
                               "questions": sp["eval_questions"]})["answers"]
                  for jid, st in list(sp["jobs"].items())[: sp["select"]["max_deep"]]}
            out = Path(args[args.index("-p") + 1])
            (out / "answers.json").write_text(_json.dumps({"triage": tri, "eval": ev, "meta": {
                "spec_id": sp["spec_id"], "device": ["GPU 0: Tesla T4"] if FakeKaggle.last_gpu else ["cpu"],
                "complete": True, "errors": []}}))
            return CLI_Result(0, "downloaded")
        return CLI_Result(0, "")


def test_kaggle_rotation_and_fallback(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from app import kaggle as kg
    db = DB(tmp_path / "db.sqlite")
    profile = build_profile(RESUME, None)
    db.put("profile", profile)
    db.put("settings", {"engine_mode": "kaggle", "min_match": 0})
    kg.Accounts(db).save([
        {"username": "acct_a", "key": "k" * 32, "enabled": True,
         "runs": [{"started": datetime.now(timezone.utc).isoformat(), "seconds": 7200, "accelerator": "gpu", "outcome": "complete"}]},
        {"username": "acct_b", "key": "k" * 32, "enabled": True},
        {"username": "acct_c", "key": "k" * 32, "enabled": False},
    ])
    monkeypatch.setattr(pl, "fetch_all", lambda *a, **k: _jobs())
    runner = kg.KaggleRunner(db, cli_factory=FakeKaggle, sleep=lambda s: None)
    p = pl.Pipeline(db, Jev(api_key="x"), kaggle=runner)
    runner.log = p.log

    # 1. b has less tracked usage than a, so it goes first; its GPU quota is gone → blocked until reset; a runs on GPU.
    FakeKaggle.behaviour, FakeKaggle.calls = {"acct_b": "quota"}, []
    stats = p.run()
    assert stats["engine"] == "kaggle" and stats["evaluated"] > 0 and FakeKaggle.last_gpu
    pushes = [u for u, a in FakeKaggle.calls if a == ("kernels", "push")]
    assert pushes == ["acct_b", "acct_a"] and "acct_c" not in {u for u, _ in FakeKaggle.calls}
    b = kg.Accounts(db).get("acct_b")
    assert b["gpu_blocked_until"] > datetime.now(timezone.utc).isoformat() and "quota" in b["quota_message"]
    assert db.status_counts().get("new", 0) + db.status_counts().get("filtered", 0) == stats["evaluated"]

    # 2. every GPU exhausted → Kaggle CPU.
    FakeKaggle.behaviour = {"acct_a": "quota", "acct_b": "quota"}
    kg.Accounts(db).update("acct_b", gpu_blocked_until=None)
    db.conn.execute("DELETE FROM jobs"); db.conn.commit()
    stats = p.run()
    assert stats["engine"] == "kaggle" and FakeKaggle.last_gpu is False

    # 3. Kaggle unreachable → local path (heuristic here, since this Jev client has no server).
    FakeKaggle.behaviour = {"acct_a": "down", "acct_b": "down"}
    db.conn.execute("DELETE FROM jobs"); db.conn.commit()
    monkeypatch.setattr(pl.Pipeline, "_run_local", lambda self, *a, **k: {"engine": "local"})
    assert p.run()["engine"] == "local"


def test_kaggle_week_window():
    from datetime import datetime, timezone
    from app.kaggle import next_reset, week_start
    wed = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)          # a Wednesday
    assert week_start(wed) == datetime(2026, 9, 19, tzinfo=timezone.utc)      # previous Saturday 00:00 UTC
    assert next_reset(wed) == datetime(2026, 9, 26, tzinfo=timezone.utc)
    sat = datetime(2026, 9, 26, 0, 30, tzinfo=timezone.utc)
    assert week_start(sat) == datetime(2026, 9, 26, tzinfo=timezone.utc)


def test_vault_round_trip_and_tamper():
    from app import vault as v
    k = v.new_data_key()
    blob = v.encrypt(k, "user", {"a": [1, "é"]})
    assert v.decrypt(k, "user", blob) == {"a": [1, "é"]}
    for bad in (lambda: v.decrypt(k, "board", blob),                 # renamed file
                lambda: v.decrypt(v.new_data_key(), "user", blob)):   # wrong key
        with pytest.raises(Exception):
            bad()
    keys = {"wraps": [v.wrap_passphrase(k, "correct horse battery staple", iterations=1000)]}
    assert v.unlock(keys, "correct horse battery staple") == k
    assert v.unlock(keys, "correct horse battery stapler") is None          # wrong word (case is ignored by design)
    assert v.passphrase_bits("correct horse battery staple") > 50 > v.passphrase_bits("hunter22")


def test_ci_run_scheduled_flow(tmp_path, monkeypatch):
    """GitHub Actions path: encrypted user state in → Kaggle (fake) → encrypted pipeline/board out."""
    import importlib.util
    from app import kaggle as kg, state, vault
    spec = importlib.util.spec_from_file_location("ci", Path(__file__).parents[1] / "scripts" / "ci_run.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)

    key = vault.new_data_key()
    folder = tmp_path / "state"
    folder.mkdir()
    dev = DB(tmp_path / "device.sqlite")                       # "a device" publishes its user state
    dev.put("profile", build_profile(RESUME, None)); dev.touch("profile")
    dev.put("settings", {"min_match": 0}); dev.touch("settings")
    dev.add_application({"company": "Acme", "role": "x", "url": "https://example.com/4"})   # applied elsewhere
    (folder / "user.enc").write_bytes(vault.encrypt(key, "user", state.export_user(dev)))

    monkeypatch.setenv("JOBBOARD_DATA_KEY", vault.b64(key))
    monkeypatch.setenv("KAGGLE_KEY_ACCT_A", "k" * 32)
    dev.put("kaggle_accounts", [{"username": "acct-a", "enabled": True}]); dev.touch("kaggle")   # hyphen → ACCT_A
    (folder / "user.enc").write_bytes(vault.encrypt(key, "user", state.export_user(dev)))
    monkeypatch.setattr(pl, "fetch_all", lambda *a, **k: _jobs())
    FakeKaggle.behaviour = {}
    factory = lambda db, log: kg.KaggleRunner(db, log=log, cli_factory=FakeKaggle, sleep=lambda s: None)

    assert ci.main(["--state", str(folder)], runner_factory=factory) == 0         # never run before → due
    board = vault.decrypt(key, "board", (folder / "board.enc").read_bytes())
    pipe = vault.decrypt(key, "pipeline", (folder / "pipeline.enc").read_bytes())
    by_url = {j["url"]: j for j in board["jobs"]}
    assert by_url["https://example.com/4"]["status"] == "applied" and not by_url["https://example.com/4"]["card"]
    assert all(j["card"] for j in board["jobs"] if j["status"] == "new")        # tracker link skipped, rest scored
    assert "k" * 32 not in (folder / "pipeline.enc").read_bytes().decode() and all("key" not in a for a in pipe["kaggle_accounts"])
    assert [a["username"] for a in pipe["kaggle_accounts"]] == ["acct-a"]      # secret matched despite the hyphen
    assert pipe["kaggle_accounts"][0]["runs"]                                 # GPU usage persisted across runs

    # Another device rejects a job; the next scheduled run keeps that decision and re-processes nothing.
    first = board["jobs"][0]["id"]
    dev.put("decisions", {first: {"status": "rejected", "at": "2099-01-01T00:00:00"}})
    (folder / "user.enc").write_bytes(vault.encrypt(key, "user", state.export_user(dev)))
    FakeKaggle.calls = []
    assert ci.main(["--state", str(folder)], runner_factory=factory) == 3         # already ran today → not due
    assert ci.main(["--state", str(folder), "--force"], runner_factory=factory) == 0
    board2 = vault.decrypt(key, "board", (folder / "board.enc").read_bytes())
    assert {j["id"]: j["status"] for j in board2["jobs"]}[first] == "rejected"
    assert not [c for c in FakeKaggle.calls if c[1] == ("kernels", "push")]   # nothing new → no Kaggle run


def test_merge_user_conflicts():
    from app.state import merge_user
    a = {"meta": {"profile": "2026-09-01", "settings": "2026-09-05"}, "profile": {"v": "a"}, "settings": {"x": 1},
         "decisions": {"j1": {"status": "later", "at": "2026-09-02"}},
         "applications": [{"uid": "u1", "company": "A", "updated_at": "2026-09-03"},
                          {"uid": "u2", "company": "B", "updated_at": "2026-09-01"}],
         "deleted_apps": {}, "kaggle": []}
    b = {"meta": {"profile": "2026-09-04", "settings": "2026-09-01"}, "profile": {"v": "b"}, "settings": {"x": 2},
         "decisions": {"j1": {"status": "applied", "at": "2026-09-06"}},
         "applications": [{"uid": "u1", "company": "A-old", "updated_at": "2026-09-02"}],
         "deleted_apps": {"u2": "2026-09-02"}, "kaggle": []}
    m = merge_user(a, b)
    assert m["profile"] == {"v": "b"} and m["settings"] == {"x": 1}          # newest side per part
    assert m["decisions"]["j1"]["status"] == "applied"                        # newest decision
    assert [r["company"] for r in m["applications"]] == ["A"]                 # newest edit kept; u2 deleted


@pytest.fixture()
def fake_github():
    from tests import fake_github as fg
    fg.reset()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(fg.app, port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    yield fg, f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_publish_and_two_device_sync(tmp_path, fake_github):
    from app import state, vault
    from app.sync import Sync
    fg, api = fake_github
    phrase = "orbit tulip canyon ember velvet"

    mac1 = DB(tmp_path / "mac1.sqlite")
    mac1.put("profile", {"name": "Test"}); mac1.touch("profile")
    mac1.add_application({"company": "Acme", "role": "AI Engineer", "url": "https://example.com/a"})
    mac1.put("kaggle_accounts", [{"username": "acct-a", "key": "k" * 32, "enabled": True}])
    s1 = Sync(mac1)
    with pytest.raises(ValueError):
        s1.publish("me", "job-board", "tok", "hunter22", api=api)             # too weak for public ciphertext
    s1.publish("me", "job-board", "tok", phrase, api=api)

    assert {"app/main.py", ".github/workflows/daily.yml", "web/static.js"} <= set(fg.BRANCHES["main"])
    assert not any(p.startswith(("data/", "engines/", "bench/engines/")) or p == ".env" for p in fg.BRANCHES["main"])
    assert set(fg.BRANCHES["data"]) == {"keys.json", "README.md", "user.enc", "pipeline.enc", "board.enc"}
    assert len(fg.RUNS) == 2                                                   # Pages deploy + first run started
    assert "README.md" in fg.BRANCHES["main"]                                  # empty repo was seeded first
    assert fg.SECRETS["JOBBOARD_DATA_KEY"] == mac1.get("github")["data_key"]
    assert fg.SECRETS["KAGGLE_KEY_ACCT_A"] == "k" * 32
    key = vault.unb64(fg.SECRETS["JOBBOARD_DATA_KEY"])
    assert b"k" * 32 not in fg.BRANCHES["data"]["user.enc"][1]                # keys never in the repo
    assert vault.unlock(json.loads(fg.BRANCHES["data"]["keys.json"][1]), phrase) == key

    # A second Mac joins with the same passphrase and gets the tracker — with a token lacking Pages permission.
    fg.PAGES_ALLOWED["ok"] = False
    mac2 = DB(tmp_path / "mac2.sqlite")
    s2 = Sync(mac2)
    with pytest.raises(ValueError):
        s2.publish("me", "job-board", "tok", "different words for this passphrase entirely", api=api)
    s2.publish("me", "job-board", "tok", phrase, api=api)
    assert [a["company"] for a in mac2.applications()] == ["Acme"]
    assert s2.public()["enabled"] and s2.public()["pages_manual"]             # publish completes, flags the click

    # Edits flow both ways; an unchanged sync makes no commit.
    a2 = mac2.applications()[0]
    mac2.update_application(a2["id"], {"status": "Interviewing"})
    assert s2.sync_once()["pushed"] is True
    s1.sync_once()
    assert mac1.applications()[0]["status"] == "Interviewing"
    assert s1.sync_once()["pushed"] is False

    # The scheduled run's results (pipeline.enc) reach the Macs.
    pipe = vault.decrypt(key, "pipeline", fg.BRANCHES["data"]["pipeline.enc"][1])
    pipe["jobs"].append({"id": "gh1", "status": "new", "title": "AI Engineer", "company": "Remote Co",
                         "url": "https://example.com/gh1", "data": json.dumps({"id": "gh1", "title": "AI Engineer"})})
    fg.BRANCHES["data"]["pipeline.enc"] = (fg.sha_of(b"x"), vault.encrypt(key, "pipeline", pipe))
    assert s1.sync_once()["pulled_jobs"] >= 1 and mac1.job("gh1")["status"] == "new"


def test_generated_passphrase():
    from app import vault
    words = set((Path(__file__).parents[1] / "app" / "wordlist.txt").read_text().split())
    p = vault.generate_passphrase()
    assert len(p.split()) == 6 and all(w in words for w in p.split())
    assert vault.passphrase_bits(p) >= 75 and len(words) == 7776
    assert vault.generate_passphrase() != vault.generate_passphrase()
    k = vault.new_data_key()
    keys = {"wraps": [vault.wrap_passphrase(k, p, iterations=1000)]}
    assert vault.unlock(keys, "  " + p.upper().replace(" ", "   ") + " ") == k     # case/spacing don't matter
    assert vault.unlock(keys, p.rsplit(" ", 1)[0]) is None                           # a missing word does


def test_publish_retry_reuses_saved_credentials(tmp_path, fake_github):
    """A publish that fails late (e.g. a network error) can be retried with no token or passphrase typed."""
    from app.sync import Sync
    from app import github as ghmod
    fg, api = fake_github
    db = DB(tmp_path / "mac.sqlite")
    db.put("profile", {"name": "Test"}); db.touch("profile")
    s = Sync(db)
    real = ghmod.GitHub.set_secret
    ghmod.GitHub.set_secret = lambda self, n, v: (_ for _ in ()).throw(ghmod.GitHubError("network down"))
    try:
        with pytest.raises(ghmod.GitHubError):
            s.publish("me", "job-board", "tok-123456", "orbit tulip canyon ember velvet", api=api)
    finally:
        ghmod.GitHub.set_secret = real
    pub = s.public()
    assert not pub["enabled"] and pub["token_hint"] == "••••3456" and pub["has_key"]    # remembered despite failure
    s.publish("me", "job-board", "", "", api=api)                                        # retry: nothing typed
    assert s.public()["enabled"] and "JOBBOARD_DATA_KEY" in fg.SECRETS
