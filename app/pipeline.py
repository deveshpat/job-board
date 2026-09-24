"""The ETL pipeline: EXTRACT (sources) -> TRANSFORM (Jev triage + evaluation + scoring) -> LOAD (board).

Jev makes every semantic decision; code does the arithmetic, dates, dedupe and gating,
as TypeSafe recommends. Questions and thresholds live in questions.py.
"""
from __future__ import annotations

import re
import threading
import traceback
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from . import questions as Q
from .db import DB, now
from .jev import Jev, JevError
from .kaggle import KAGGLE_KEV_RUN, KEV_COMMIT, KaggleError, KaggleRunner
from .profile import compact_candidate
from .sources import DEFAULT_COMPANIES, SOURCES, fetch_all
from .urls import norm_url as _norm_url

DEFAULT_SETTINGS = {
    "sources": list(SOURCES),
    "companies": DEFAULT_COMPANIES,
    "min_match": Q.DEFAULT_MIN_MATCH,
    "max_age_days": 45,
    "max_deep": 300,          # cap on deep evaluations per run (highest-triage first)
    "daily_at": "09:00",      # daily run time while the app is open ("" = off); missed runs catch up on start
    "engine_mode": "local",   # "kaggle": decisions run on Kaggle (GPU → CPU), falling back to local Kev
    "workers": 8,
}
DESC_CHARS = 9000             # keep state lean: Jev accuracy drops with irrelevant bulk

LEVEL_LABEL = {"internship": "Internship", "entry": "Entry level", "mid": "Mid level",
               "senior": "Senior", "staff_plus": "Staff+ / Manager"}
EXPERIENCE_LABEL = {"not_stated": "Experience not stated", "0_2": "0–2 yrs", "2_4": "2–4 yrs",
                    "4_7": "4–7 yrs", "7_plus": "7+ yrs"}
DEGREE_LABEL = {"none_stated": "No degree listed", "any_degree_or_equivalent": "Any degree / equivalent",
                "cs_engineering_degree": "CS/Eng degree", "advanced_degree": "Master's/PhD"}
MODE_LABEL = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "Onsite", "unclear": ""}
EMPLOYMENT_LABEL = {"full_time": "Full-time", "contract": "Contract", "part_time": "Part-time",
                    "internship": "Internship"}


def _tokens(s: str) -> set:
    return {t for t in re.split(r"[^a-z0-9+#]+", s.lower()) if t}


def gap_vocab(skills: List[str]) -> List[str]:
    have = [_tokens(s) for s in skills]
    return [g for g in Q.SKILL_VOCAB if not any(_tokens(g) & h for h in have)]


def job_state(job: dict) -> dict:
    return {
        "title": job["title"], "company": job["company"], "location": job["location"],
        "location_restrictions": job.get("location_restrictions") or "",
        "remote": job.get("remote"), "employment_type": job.get("employment_type") or "",
        "salary": job.get("salary") or "", "tags": job.get("tags")[:12] if job.get("tags") else [],
        "description": (job.get("description") or "")[:DESC_CHARS],
    }


# ---------------------------------------------------------------------------
# SCORE: answers -> card (pure; re-runnable without new Jev calls)
# ---------------------------------------------------------------------------

# Where a posting says it is, checked against where you are — a plain-text sanity check on Kev's location call.
CITIES = {"India": r"india|bengaluru|bangalore|mumbai|delhi|gurugram|gurgaon|noida|hyderabad|pune|chennai|kolkata|"
                   r"ahmedabad|jaipur|chandigarh|kochi|indore|coimbatore|thiruvananthapuram|dehradun"}
ANYWHERE = re.compile(r"\b(anywhere|worldwide|global(ly)?|any location|all locations|remote[- ]first|fully distributed)\b", re.I)
ELSEWHERE = re.compile(r"\b(usa?|u\.s\.a?\.?|united states|canada|uk|united kingdom|europe|eu|emea|germany|france|spain|poland|"
                       r"netherlands|ireland|portugal|brazil|mexico|latam|north america|americas|australia|singapore|japan|"
                       r"new york|san francisco|london|berlin|paris|amsterdam|toronto|seattle|austin|boston|nyc|sf)\b", re.I)


def listed_location(job: Optional[dict], country: str) -> Optional[str]:
    """'yours' if the posting lists your country (or a city in it), 'elsewhere' if it only lists other places,
    None if it says nothing useful (or 'anywhere')."""
    if not job or not country:
        return None
    text = " ".join(str(job.get(k) or "") for k in ("location", "location_restrictions"))
    if not text.strip():
        return None
    if re.search(rf"\b({CITIES.get(country, re.escape(country.lower()))})\b", text, re.I):
        return "yours"
    if ANYWHERE.search(text):
        return None
    return "elsewhere" if ELSEWHERE.search(text) else None


def score_answers(a: dict, profile: dict, skills: List[str], gaps: List[str], min_match: float,
                  job: Optional[dict] = None) -> dict:
    cand_level = profile.get("level") or "entry"
    country = profile.get("country") or "your country"
    lp = a["level"]["probabilities"]
    ep = a["experience"]["probabilities"]
    level_fit = sum(p * Q.LEVEL_FIT[cand_level].get(k, 0) for k, p in lp.items())
    exp_fit = sum(p * Q.EXPERIENCE_FIT[cand_level].get(k, 0) for k, p in ep.items())
    comp = {
        "skill_alignment": a["skill_alignment"]["score"] / 4,
        "interest_alignment": a["interest_alignment"]["score"] / 3,
        "should_apply": a["should_apply"]["noul"],
        "level_fit": level_fit,
        "experience_fit": exp_fit,
    }
    match = 100 * sum(Q.WEIGHTS[k] * v for k, v in comp.items())

    loc, deg = a["location"], a["degree"]
    lprob, dprob = loc["probabilities"], deg["probabilities"]
    if dprob.get("advanced_degree", 0) > 0.6:
        match -= Q.ADVANCED_DEGREE_PENALTY
    if lprob.get("unclear", 0) > 0.6:
        match -= Q.UNCLEAR_LOCATION_PENALTY
    match = max(0.0, min(100.0, match))

    gates = []
    if a["tech_role"]["noul"] < Q.GATE_TECH_ROLE_MIN:
        gates.append("Not a hands-on technical role")
    if a["field"]["probabilities"].get("non_engineering", 0) > Q.GATE_NON_ENGINEERING_MAX:
        gates.append("Non-engineering field")
    eligible = lprob.get("remote_open", 0) + lprob.get("local_office", 0) + 0.5 * lprob.get("unclear", 0)
    listed = listed_location(job, profile.get("country") or "")
    if listed == "yours":
        eligible = max(eligible, 1.0)            # the posting itself names your country/city
        if lprob.get("unclear", 0) > 0.6:
            match = min(100.0, match + Q.UNCLEAR_LOCATION_PENALTY)
    if eligible < Q.GATE_LOCATION_ELIGIBLE_MIN:
        gates.append(f"Not open to candidates in {country}")
    if level_fit < Q.GATE_LEVEL_FIT_MIN:
        gates.append("Level above yours")
    if exp_fit < Q.GATE_EXPERIENCE_FIT_MIN:
        gates.append("Asks for much more experience")
    if a["red_flags"]["noul"] > Q.GATE_RED_FLAGS_MAX:
        gates.append("Red flags (scam/unpaid/vague)")
    if match < min_match:
        gates.append(f"Match {match:.0f} below {min_match:.0f}")

    confs = [loc["confidence"], a["level"]["confidence"], a["skill_alignment"]["confidence"]]
    confidence = sum(confs) / len(confs)
    matched = [s for i, s in enumerate(skills) if a.get(f"has_{i}", {}).get("noul", 0) >= Q.SKILL_MATCH_MIN]
    missing = [s for i, s in enumerate(gaps) if a.get(f"gap_{i}", {}).get("noul", 0) >= Q.SKILL_MATCH_MIN]
    rank = {k["name"]: (k.get("core", False), k.get("used") or 0, k.get("market") or 0) for k in profile.get("keywords") or []}
    matched.sort(key=lambda s: rank.get(s, (False, 0, 0)), reverse=True)       # your core skills first
    lead = a.get("lead_project", {}).get("choice")

    loc_choice = loc["choice"]
    if listed == "yours" and loc_choice not in ("remote_open", "local_office"):
        loc_choice = "remote_open" if re.search(r"\bremote\b", str(job.get("location") or ""), re.I) else "local_office"
    loc_label = {"remote_open": f"Remote · open to {country}", "local_office": f"Office in {country}",
                 "restricted": "Location-restricted", "unclear": "Location unclear"}[loc_choice]
    sk, ia = a["skill_alignment"], a["interest_alignment"]
    reasons = [
        f"Skills: {sk['legend'][str(round(sk['score']))]} ({sk['score']:.1f}/4)",
        f"Goal fit: {ia['legend'][str(round(ia['score']))]}",
        f"Level: {LEVEL_LABEL[a['level']['choice']]} ({lp[a['level']['choice']]:.0%}) · asks {EXPERIENCE_LABEL[a['experience']['choice']]}",
        f"Location: {loc_label} ({lprob[loc['choice']]:.0%})",
        f"Verdict: {'worth applying' if comp['should_apply'] >= 0.5 else 'long shot'} ({comp['should_apply']:.0%})",
    ]
    tier = "strong" if match >= 75 else "good" if match >= 60 else "stretch"
    mode = MODE_LABEL[a["work_mode"]["choice"]]
    chips = [loc_label,
             mode if mode and not loc_label.startswith(mode) else "",          # "Remote · open to India" says it already
             LEVEL_LABEL[a["level"]["choice"]],
             EXPERIENCE_LABEL[a["experience"]["choice"]] if a["experience"]["choice"] != "not_stated" else "",
             EMPLOYMENT_LABEL[a["employment"]["choice"]] if a["employment"]["choice"] != "full_time" else "",
             DEGREE_LABEL[deg["choice"]] if deg["choice"] in ("cs_engineering_degree", "advanced_degree") else ""]
    return {
        "match": round(match), "tier": tier, "confidence": round(confidence, 3),
        "unsure": confidence < Q.LOW_CONFIDENCE,
        "chips": [c for c in chips if c],
        "location_ok": loc_choice in ("remote_open", "local_office") and listed != "elsewhere",
        "skills_matched": matched, "skills_gap": missing,
        "lead_project": None if lead in (None, "none") else lead,
        "take": kev_take(a, comp, cand_level, loc_choice, loc_label, matched, missing, confidence,
                         listed_elsewhere=(job or {}).get("location") if listed == "elsewhere" else None),
        "reasons": reasons, "gates": gates,
        "components": {k: round(v, 3) for k, v in comp.items()},
        "scored_by": "jev",
    }


def _list(xs: List[str], n: int = 4) -> str:
    return ", ".join(xs[:n]) + (f" +{len(xs) - n}" if len(xs) > n else "")


def kev_take(a: dict, comp: dict, cand_level: str, loc_choice: str, loc_label: str,
             matched: List[str], missing: List[str], confidence: float, listed_elsewhere: Optional[str] = None) -> dict:
    """Kev's answers as a short verdict plus the few points that matter — no probabilities or rubric text."""
    pros, cons = [], []
    sk = comp["skill_alignment"]
    if sk >= 0.7:
        pros.append(f"Strong skill overlap{': ' + _list(matched) if matched else ''}")
    elif sk >= 0.45:
        pros.append(f"Some skill overlap{': ' + _list(matched) if matched else ''}")
    else:
        cons.append("Few of your skills are what they need")
    if missing:
        cons.append(f"They also want {_list(missing, 3)}")
    if comp["interest_alignment"] >= 0.8:
        pros.append("The kind of role you're after")
    elif comp["interest_alignment"] < 0.5:
        cons.append("Off your stated goal")
    if listed_elsewhere and loc_choice != "restricted":
        cons.append(f"Listed for {listed_elsewhere.strip()[:60]} — check they hire where you are")
    elif loc_choice == "restricted":
        cons.append("Probably limited to other countries — check before applying")
    elif loc_choice == "unclear":
        cons.append("Location unclear — check before applying")
    # (an open location is already the first chip on the card)
    job_level = a["level"]["choice"]
    if comp["level_fit"] >= 0.75:
        pros.append(f"Right level ({LEVEL_LABEL[job_level].lower()})")
    elif comp["level_fit"] < 0.6:
        cons.append(f"Pitched at {LEVEL_LABEL[job_level].lower()} — a stretch from {LEVEL_LABEL.get(cand_level, cand_level).lower()}")
    if comp["experience_fit"] < 0.6 and a["experience"]["choice"] != "not_stated":
        cons.append(f"Asks for {EXPERIENCE_LABEL[a['experience']['choice']]} of experience")
    if a["degree"]["probabilities"].get("advanced_degree", 0) > 0.5:
        cons.append("Wants a Master's or PhD")
    if a["red_flags"]["noul"] > 0.3:
        cons.append("Some red flags in the posting")
    sa = comp["should_apply"]
    verdict = "Worth applying" if sa >= 0.75 else "Worth a look" if sa >= 0.5 else "Long shot"
    lead = pros[0].split(":")[0].lower() if pros else ""
    summary = verdict + (f" — {lead}" if lead else "") + (f", but {cons[0][0].lower() + cons[0][1:]}" if cons and sa < 0.75 else "")
    return {"verdict": verdict, "summary": summary + ".", "pros": pros[:4], "cons": cons[:3],
            "unsure": confidence < Q.LOW_CONFIDENCE}


def heuristic_card(job: dict, profile: dict, skills: List[str], min_match: float) -> dict:
    """Fallback when no Jev key is configured: plain keyword overlap. Clearly labelled on the card."""
    text = f"{job['title']}\n{job.get('description', '')}".lower()
    hit = [s for s in skills if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", text)]
    loc = f"{job.get('location', '')} {job.get('location_restrictions', '')}".lower()
    country = (profile.get("country") or "").lower()
    loc_ok = bool(country and country in loc) or bool(re.search(r"anywhere|worldwide|global", loc))
    senior = bool(re.search(r"\b(senior|sr\.?|staff|principal|lead|head|director|manager)\b", job["title"], re.I))
    match = min(100, 12 * len(hit)) * (0.5 if senior else 1.0) * (1.0 if loc_ok else 0.7)
    gates = [] if match >= min_match else [f"Match {match:.0f} below {min_match:.0f}"]
    return {
        "match": round(match), "tier": "strong" if match >= 75 else "good" if match >= 60 else "stretch",
        "confidence": None, "unsure": True, "chips": ["Heuristic score (no Jev key)"],
        "location_ok": loc_ok, "skills_matched": hit[:12], "skills_gap": [], "lead_project": None,
        "reasons": [f"{len(hit)} of your skills appear in the posting", "Add a Jev key for real scoring"],
        "gates": gates, "components": {}, "scored_by": "heuristic",
    }


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

class Pipeline:
    def __init__(self, db: DB, jev: Jev, engine=None, kaggle: Optional[KaggleRunner] = None):
        self.db, self.jev, self.engine = db, jev, engine
        self.kaggle = kaggle or KaggleRunner(db, log=self.log)
        self.github_mode = lambda: False          # main.py: True while GitHub sync is on
        self.state = {"running": False, "stage": "idle", "done": 0, "total": 0, "log": [], "error": None}
        self._thread: Optional[threading.Thread] = None

    def settings(self) -> dict:
        return {**DEFAULT_SETTINGS, **(self.db.get("settings") or {})}

    # -- daily routine ------------------------------------------------------------
    def due(self, now) -> bool:
        """True when today's scheduled run hasn't happened yet and its time has passed."""
        at = (self.settings().get("daily_at") or "").strip()
        if not at or self.state["running"] or not self.db.get("profile") or self.github_mode():
            return False
        hh, mm = (int(x) for x in at.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return False
        last = self.db.last_run()
        if not last:
            return True
        # runs are stamped in UTC; compare calendar days in local time
        started = datetime.fromisoformat(last["started"][:19]) + (datetime.now() - datetime.utcnow())
        return started.date() < now.date()

    def start_scheduler(self, check_every: float = 300) -> None:
        def loop():
            from datetime import datetime
            import time as _t
            _t.sleep(60)                      # let the app finish starting before a catch-up run
            while True:
                try:
                    if self.due(datetime.now()):
                        self.log("Daily run (scheduled)")
                        self.start()
                except Exception:
                    traceback.print_exc()
                _t.sleep(check_every)
        threading.Thread(target=loop, daemon=True, name="daily-run").start()

    def _workers(self) -> int:
        # A local engine answers one request at a time; parallel requests would only queue (and time out).
        return 1 if self.jev.is_local else self.settings()["workers"]

    def log(self, msg: str) -> None:
        self.state["log"].append(f"{datetime.now():%H:%M:%S}  {msg}")    # local time for reading
        self.state["log"] = self.state["log"][-300:]

    def _stage(self, name: str, total: int = 0) -> None:
        self.state.update(stage=name, done=0, total=total)
        self.log(f"— {name}")

    def start(self, reevaluate: bool = False) -> bool:
        if self.state["running"]:
            return False
        self.state = {"running": True, "stage": "starting", "done": 0, "total": 0, "log": [], "error": None}
        self._thread = threading.Thread(target=self._run_safe, args=(reevaluate,), daemon=True)
        self._thread.start()
        return True

    def _run_safe(self, reevaluate: bool) -> None:
        started = now()
        stats: Dict[str, int] = {}
        try:
            stats = self.run(reevaluate)
        except Exception as e:
            self.state["error"] = str(e)
            self.log(f"✗ {e}")
            traceback.print_exc()
        finally:
            self.state.update(running=False, stage="done" if not self.state["error"] else "failed")
            self.db.save_run(started, {**stats, "jev": dict(self.jev.usage)}, self.state["log"])

    # -- the run ----------------------------------------------------------
    def run(self, reevaluate: bool = False, retriage: bool = False) -> Dict[str, int]:
        cfg = self.settings()
        profile = self.db.get("profile")
        if not profile:
            raise RuntimeError("No profile yet — upload or build your resume profile first.")
        use_jev = self.jev.available
        self.log(f"Decision engine {'ready' if use_jev else 'NOT configured — using heuristic fallback'}"
                 f"{' (local)' if self.jev.is_local else ''}")

        terms = [t["name"] for t in profile["search_terms"] if t["keep"]]
        self._stage(f"Extract: {len(cfg['sources'])} sources · terms: {', '.join(terms)}")
        jobs = fetch_all(cfg["sources"], terms, cfg["companies"], self.log)
        cutoff = (date.today() - timedelta(days=cfg["max_age_days"])).isoformat()
        known = self.db.known_ids()
        fresh = [j for j in jobs if j["id"] not in known and (not j["posted_at"] or j["posted_at"] >= cutoff)]
        self.log(f"{len(jobs)} postings · {len(fresh)} new & posted within {cfg['max_age_days']} days")
        self.db.insert_raw(fresh)
        if retriage:          # a better engine arrived: give titles the old one turned away another look
            n = self.db.retriage([j for j in jobs if j["id"] in known and (not j["posted_at"] or j["posted_at"] >= cutoff)])
            self.log(f"{n} postings turned away at triage earlier get another look")
        refreshed = self.db.refresh_text([j for j in jobs if j["id"] in known])
        if refreshed:
            self.log(f"Updated the text of {refreshed} postings already on your board")
        # Postings already in the Tracker (e.g. applied elsewhere) skip triage and never reach the deck.
        tracked = {_norm_url(a["url"]) for a in self.db.applications() if a["url"]}
        already = [j for j in fresh if _norm_url(j["url"]) in tracked]
        for j in already:
            self.db.update_job(j["id"], status="applied", reason="already in Tracker")
        if already:
            self.log(f"{len(already)} postings already in your Tracker — skipped")

        skills = [k["name"] for k in profile["keywords"] if k["keep"]]
        candidate = compact_candidate(profile)
        raw = self.db.jobs(["raw"], limit=100_000)
        to_triage = [r["data"] for r in raw if not r["triage"]]

        args = (profile, candidate, skills, cfg)
        # 1. Hosted Jev (TypeSafe): fastest and most accurate (bench/results/report.md). Kev on Kaggle if it fails.
        if use_jev and not self.jev.is_local:
            try:
                return self._run_local(to_triage, raw, profile, candidate, skills, cfg, terms, use_jev, reevaluate,
                                       len(jobs), len(fresh))
            except JevError as e:
                if not self.kaggle.configured:
                    raise
                self.log(f"Jev unavailable ({str(e)[:160]}) — falling back to Kev on Kaggle")
                raw = self.db.jobs(["raw"], limit=100_000)
                to_triage = [r["data"] for r in raw if not r["triage"]]
                return self._run_remote(to_triage, raw, *args, len(jobs), len(fresh))
        # 2. Kev on Kaggle (GPU → CPU across your accounts); local Kev if that fails.
        if cfg.get("engine_mode") == "kaggle" and self.kaggle.configured and use_jev and not reevaluate:
            self._stage("Running on Kaggle")
            try:
                return self._run_remote(to_triage, raw, *args, len(jobs), len(fresh))
            except KaggleError as e:
                if not cfg.get("local_fallback", True):
                    raise
                self.log(f"Kaggle unavailable ({str(e)[:160]}) — running on this Mac instead")
        # 3. Kev on this Mac (or the keyword heuristic when there is no engine at all).
        if self.engine and use_jev:
            self._stage("Loading the local engine (about a minute)")
        with self.engine.use() if (self.engine and use_jev) else nullcontext():
            return self._run_local(to_triage, raw, profile, candidate, skills, cfg, terms, use_jev, reevaluate,
                                   len(jobs), len(fresh))

    def _run_local(self, to_triage, raw, profile, candidate, skills, cfg, terms, use_jev, reevaluate,
                   n_fetched, n_fresh) -> Dict[str, int]:
        # TRIAGE (new postings + any left untriaged by an earlier, interrupted run) --------
        self._stage("Triage titles" if use_jev else "Triage titles (heuristic)", len(to_triage))
        passed = self._triage(to_triage, candidate) if use_jev else self._triage_heuristic(to_triage, terms)
        self.log(f"{len(passed)} of {len(to_triage)} passed triage")
        # Postings that passed triage earlier but were over the max_deep cap.
        seen = {j["id"] for j in passed}
        backlog = sorted((r for r in raw if r["triage"] and not r["eval"] and r["id"] not in seen),
                         key=lambda r: -(r["triage"]["field"] - 0.5 * r["triage"]["above"]))
        passed += [r["data"] for r in backlog]

        # EVALUATE -------------------------------------------------------
        cap = cfg["max_deep"] if self.jev.is_local else max(cfg["max_deep"], 2000)   # hosted Jev: ~0.4 s and a fraction of a cent per job
        todo = passed[:cap]
        if len(passed) > len(todo):
            self.log(f"{len(passed) - len(todo)} triaged jobs over the cap of {cap}; next run picks them up")
        if reevaluate:
            seen = {j["id"] for j in todo}
            todo += [r["data"] for r in self.db.jobs(["new", "later", "filtered"], limit=100_000 if not self.jev.is_local else cap)
                     if r["id"] not in seen]
        gaps = gap_vocab(skills)
        self._stage("Evaluate postings" if use_jev else "Score (heuristic)", len(todo))
        loaded = self._evaluate(todo, profile, candidate, skills, gaps, cfg, use_jev)

        stats = {"fetched": n_fetched, "fresh": n_fresh, "triaged_in": len(passed),
                 "evaluated": len(todo), "loaded": loaded, "engine": "local" if self.jev.is_local else "jev"}
        self.log(f"✓ Loaded {loaded} jobs onto the board · Jev: {self.jev.usage['requests']} requests, "
                 f"{self.jev.usage['input_tokens']:,} input tokens, {self.jev.usage['cached']} cached")
        return stats

    def _triage(self, jobs: List[dict], candidate: dict) -> List[dict]:
        state = {"candidate": {k: candidate[k] for k in ("headline", "goal", "level", "fields")}}
        scored = []
        batches = [jobs[i:i + Q.TRIAGE_BATCH] for i in range(0, len(jobs), Q.TRIAGE_BATCH)]

        def run(batch):
            return batch, self.jev.ask(state, Q.triage_questions(batch))

        with ThreadPoolExecutor(self._workers()) as ex:
            for fut in as_completed([ex.submit(run, b) for b in batches]):
                batch, ans = fut.result()
                for i, j in enumerate(batch):
                    field, above = ans[f"field_{i}"]["noul"], ans[f"above_{i}"]["noul"]
                    if self._store_triage(j["id"], field, above):
                        scored.append((field - 0.5 * above, j))
                self.state["done"] += len(batch)
        scored.sort(key=lambda x: -x[0])
        return [j for _, j in scored]

    def _store_triage(self, job_id: str, field: float, above: float) -> bool:
        ok = field >= Q.TRIAGE_FIELD_MIN and above <= Q.TRIAGE_ABOVE_MAX
        self.db.update_job(job_id, triage={"field": field, "above": above},
                           status="raw" if ok else "triaged_out",
                           reason=None if ok else ("off-field title" if field < Q.TRIAGE_FIELD_MIN else "title above level"))
        return ok

    def _store_eval(self, job_id: str, ans: Optional[dict], card: dict, skills, gaps) -> bool:
        """Save one evaluation; returns True if the job lands on the board."""
        row = self.db.job(job_id)
        keep_status = row and row["status"] in ("later", "rejected", "applied")
        status = row["status"] if keep_status else ("filtered" if card["gates"] else "new")
        ev = {"answers": ans, "skills": skills, "gaps": gaps} if ans else None
        self.db.update_job(job_id, eval=ev, card=card, match=card["match"], status=status,
                           reason="; ".join(card["gates"]) or None, scored_by=card["scored_by"])
        return status == "new"

    def _run_remote(self, to_triage, raw, profile, candidate, skills, cfg, n_fetched, n_fresh) -> Dict[str, int]:
        gaps = gap_vocab(skills)
        backlog = sorted((r for r in raw if r["triage"] and not r["eval"]),
                         key=lambda r: -(r["triage"]["field"] - 0.5 * r["triage"]["above"]))
        if not to_triage and not backlog:
            self.log("Nothing new to evaluate — no Kaggle run needed")
            return {"fetched": n_fetched, "fresh": n_fresh, "triaged_in": 0, "evaluated": 0, "loaded": 0,
                    "engine": "kaggle"}
        jobs = {j["id"]: j for j in to_triage} | {r["id"]: r["data"] for r in backlog}
        tstate = {"candidate": {k: candidate[k] for k in ("headline", "goal", "level", "fields")}}
        batches = [to_triage[i:i + Q.TRIAGE_BATCH] for i in range(0, len(to_triage), Q.TRIAGE_BATCH)]
        spec = {
            "kev_run": KAGGLE_KEV_RUN, "kev_commit": KEV_COMMIT, "try_fla": True,
            "candidate": candidate, "eval_questions": Q.evaluate_questions(candidate, gaps),
            "triage": {"state": tstate, "batches": [{"ids": [j["id"] for j in b], "questions": Q.triage_questions(b)}
                                                   for b in batches]},
            "jobs": {jid: job_state(j) for jid, j in jobs.items()},
            "backlog": [r["id"] for r in backlog],
            "select": {"field_min": Q.TRIAGE_FIELD_MIN, "above_max": Q.TRIAGE_ABOVE_MAX, "max_deep": cfg["max_deep"]},
        }
        self.log(f"{len(to_triage)} titles to triage, {len(backlog)} triaged earlier and waiting — sending to Kaggle")
        answers = self.kaggle.run(spec)
        passed = sum(self._store_triage(jid, v["field"], v["above"]) for jid, v in answers["triage"].items()
                     if jid in jobs)
        loaded = 0
        for jid, ans in answers["eval"].items():
            if jid in jobs:
                card = score_answers(ans, profile, skills, gaps, cfg["min_match"], jobs[jid])
                loaded += self._store_eval(jid, ans, card, skills, gaps)
        self.log(f"✓ Loaded {loaded} jobs onto the board (Kaggle)")
        return {"fetched": n_fetched, "fresh": n_fresh, "triaged_in": passed, "evaluated": len(answers["eval"]),
                "loaded": loaded, "engine": "kaggle"}

    def _triage_heuristic(self, jobs: List[dict], terms: List[str]) -> List[dict]:
        words = {w.lower() for t in terms for w in t.split()} - {"engineer", "developer"}
        words |= {"ml", "ai", "llm", "python", "backend", "full-stack", "fullstack", "software", "data"}
        out = []
        for j in jobs:
            ok = bool(_tokens(j["title"]) & words)
            self.db.update_job(j["id"], status="raw" if ok else "triaged_out",
                               reason=None if ok else "off-field title (heuristic)")
            if ok:
                out.append(j)
            self.state["done"] += 1
        return out

    def _evaluate(self, jobs, profile, candidate, skills, gaps, cfg, use_jev) -> int:
        questions = Q.evaluate_questions(candidate, gaps) if use_jev else None
        loaded = 0

        def run(job):
            if not use_jev:
                return job, None, heuristic_card(job, profile, skills, cfg["min_match"])
            ans = self.jev.ask({"candidate": candidate, "job": job_state(job)}, questions)
            return job, ans, score_answers(ans, profile, skills, gaps, cfg["min_match"], job)

        with ThreadPoolExecutor(self._workers()) as ex:
            futs = [ex.submit(run, j) for j in jobs]
            for fut in as_completed(futs):
                try:
                    job, ans, card = fut.result()
                except JevError as e:
                    self.log(f"✗ Jev: {e}")
                    self.state["done"] += 1
                    continue
                loaded += self._store_eval(job["id"], ans, card, skills, gaps)
                self.state["done"] += 1
        return loaded

    # -- cheap re-scoring after threshold/weight changes (no Jev calls) --------
    def rescore(self) -> int:
        cfg, profile = self.settings(), self.db.get("profile")
        skills = [k["name"] for k in profile["keywords"] if k["keep"]]
        gaps = gap_vocab(skills)
        n = 0
        for row in self.db.jobs(["new", "filtered"], limit=100_000):
            if row["eval"]:
                ev = row["eval"]
                card = score_answers(ev["answers"], profile, ev["skills"], ev["gaps"], cfg["min_match"], row["data"])
            else:
                card = heuristic_card(row["data"], profile, skills, cfg["min_match"])
            status = "filtered" if card["gates"] else "new"
            n += status == "new"
            self.db.update_job(row["id"], card=card, match=card["match"], status=status,
                               reason="; ".join(card["gates"]) or None)
        return n
