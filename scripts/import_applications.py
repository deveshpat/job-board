"""Import an applications CSV into the Tracker (safe to re-run: rows whose URL is already tracked are skipped).

  ../.venv/bin/python scripts/import_applications.py ../application_tracker.csv

Understands columns: company, role, location, date_applied (dd/mm/yy or ISO), url, notes, outcome/status.
"""
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import DB  # noqa: E402

STATUS_MAP = {"": "Applied", "applied": "Applied", "screening": "Assessment", "assessment": "Assessment",
              "oa": "Assessment", "interview": "Interviewing", "interviewing": "Interviewing", "offer": "Offer",
              "rejected": "Rejected", "ghosted": "Ghosted", "withdrawn": "Withdrawn", "saved": "Saved"}
SOURCES = {"ashbyhq": "ashby", "lever.co": "lever", "greenhouse": "greenhouse", "ycombinator": "hackernews",
           "zohorecruit": "zoho", "myworkdayjobs": "workday", "polymer.co": "polymer", "bamboohr": "bamboohr",
           "smartrecruiters": "smartrecruiters", "eightfold": "eightfold", "breezy": "breezy", "jobicy": "jobicy"}
# Notes that mean the candidate stopped the process themselves.
WITHDRAWN = re.compile(r"not (gonna|going to) proceed|withdr[ae]w|declined", re.I)


def parse_date(s: str):
    s = (s or "").strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def source_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return next((v for k, v in SOURCES.items() if k in host), host.replace("www.", "") or "manual")


STATUSES = {"saved", "applied", "assessment", "interviewing", "offer", "rejected", "ghosted", "withdrawn"}


def main(path: str):
    db = DB()
    tracked = {a["url"] for a in db.applications() if a["url"]}
    added = skipped = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            g = lambda k: (row.get(k) or "").strip()
            url = g("url")
            if url and url in tracked:
                skipped += 1
                continue
            notes = g("notes")
            raw_status = (g("outcome") or g("status")).lower()
            status = raw_status.title() if raw_status in STATUSES else STATUS_MAP.get(raw_status, "Applied")
            if WITHDRAWN.search(notes) and raw_status not in STATUSES:
                status = "Withdrawn"
            match = g("match")
            # Our own export has a "source" column (kept as-is, blank included); other CSVs get it from the URL.
            source = (g("source") or None) if "source" in row else (source_of(url) if url else "manual")
            db.add_application({
                "company": g("company"), "role": g("role"), "location": g("location") or None, "url": url or None,
                "notes": notes or None, "applied_on": parse_date(g("date_applied") or g("applied_on")),
                "status": status, "next_step": g("next_step") or None, "follow_up": parse_date(g("follow_up")),
                "source": source, "match": float(match) if match else None,
            })
            tracked.add(url)
            added += 1
    print(f"imported {added}, skipped {skipped} already tracked")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2] / "application_tracker.csv"))
