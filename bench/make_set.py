"""Build a frozen benchmark set from live postings: 200 titles (triage) + 60 full jobs (evaluation).

Stratified with crude keyword buckets so the set covers the cases that matter
(India-based, remote-worldwide, US/EU-only, senior, non-engineering, ML/AI). The buckets are
only for sampling; the gold labels are written by hand in gold.json.
"""
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.pipeline import job_state  # noqa: E402
from app.sources import DEFAULT_COMPANIES, SOURCES, fetch_all  # noqa: E402

OUT = Path(__file__).parent / "set.json"
TERMS = ["Machine Learning Engineer", "AI Engineer", "Full Stack Engineer", "Python Developer", "Backend Engineer"]

BUCKETS = {
    "india": lambda j: re.search(r"india|bengaluru|bangalore|delhi|gurgaon|gurugram|noida|mumbai|pune|hyderabad|chennai", f"{j['location']} {j['location_restrictions']}", re.I),
    "worldwide": lambda j: re.search(r"anywhere|worldwide|global", f"{j['location']} {j['location_restrictions']}", re.I),
    "us_eu_only": lambda j: re.search(r"^(usa|us|united states|canada|europe|eu|uk)\b", j["location"].strip(), re.I),
    "senior": lambda j: re.search(r"\b(senior|staff|principal|lead|director|head)\b", j["title"], re.I),
    "ml_ai": lambda j: re.search(r"\b(ml|ai|machine learning|llm|data scien|nlp|research)\b", j["title"], re.I),
    "fullstack_backend": lambda j: re.search(r"full.?stack|backend|back-end|python|software engineer", j["title"], re.I),
    "non_eng": lambda j: re.search(r"sales|account|marketing|recruit|legal|finance|customer|support|manager|designer", j["title"], re.I),
}
QUOTA = {"india": 12, "worldwide": 8, "us_eu_only": 8, "senior": 6, "ml_ai": 12, "fullstack_backend": 8, "non_eng": 6}


def main():
    random.seed(7)
    jobs = fetch_all(list(SOURCES), TERMS, DEFAULT_COMPANIES, print)
    jobs = [j for j in jobs if len(j.get("description") or "") > 400]
    random.shuffle(jobs)
    picked, seen = [], set()
    for b, n in QUOTA.items():
        for j in [j for j in jobs if BUCKETS[b](j) and j["id"] not in seen][:n]:
            seen.add(j["id"])
            picked.append({"id": j["id"], "bucket": b, "source": j["source"], "url": j["url"], "state": job_state(j)})
    rest = [j for j in jobs if j["id"] not in seen]
    titles = [{"id": j["id"], "title": j["title"], "company": j["company"]} for j in rest[:200]]
    OUT.write_text(json.dumps({"jobs": picked, "titles": titles}, indent=1))
    print(f"{len(picked)} jobs, {len(titles)} titles -> {OUT}")


if __name__ == "__main__":
    main()
