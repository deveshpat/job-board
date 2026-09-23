"""EXTRACT: job sources. Each adapter returns jobs in one normalized shape.

Only public, unauthenticated feeds/APIs are used (no LinkedIn/Indeed/Naukri scraping,
which their terms forbid and which block bots anyway).

Normalized job:
  id, source, title, company, location, remote, url, description, salary,
  posted_at (ISO date), tags (list), employment_type, location_restrictions, logo
"""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (personal job-board; contact: local) JobBoard/1.0"}
TIMEOUT = 25

DEFAULT_COMPANIES = {
    "greenhouse": ["razorpaysoftwareprivatelimited", "groww", "hackerrank", "druva", "gitlab",
                   "mongodb", "databricks", "stripe", "coinbase", "elastic", "cloudflare",
                   "scaleai", "anthropic"],
    "ashby": ["sarvam", "openai", "deepgram", "supabase", "cursor", "replit", "ramp", "langchain",
              "elevenlabs", "posthog", "atlan", "composio", "perplexity", "notion", "cohere", "modal"],
    "lever": ["paytm", "cred", "zeta", "palantir"],
}


# Ashby/Lever only give the board slug; show the real names for the defaults.
COMPANY_NAMES = {"openai": "OpenAI", "elevenlabs": "ElevenLabs", "posthog": "PostHog", "langchain": "LangChain",
                 "cred": "CRED", "paytm": "Paytm", "sarvam": "Sarvam AI", "supabase": "Supabase"}


def _company_name(slug: str) -> str:
    return COMPANY_NAMES.get(slug, slug.replace("-", " ").title())


def html_to_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = html.unescape(s) if "&lt;" in s else s
    text = BeautifulSoup(s, "html.parser").get_text("\n")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _iso(v) -> Optional[str]:
    if v in (None, "", "None"):
        return None
    try:
        if isinstance(v, (int, float)) or str(v).isdigit():
            n = float(v)
            return datetime.fromtimestamp(n / 1000 if n > 1e12 else n, timezone.utc).date().isoformat()
        return str(v)[:10]
    except Exception:
        return None


def _job(source: str, source_id, **kw) -> dict:
    j = {"source": source, "title": "", "company": "", "location": "", "remote": None, "url": "",
         "description": "", "salary": "", "posted_at": None, "tags": [], "employment_type": "",
         "location_restrictions": "", "logo": ""}
    j.update({k: v for k, v in kw.items() if v is not None})
    j["id"] = hashlib.sha1(f"{source}:{source_id}".encode()).hexdigest()[:16]
    j["title"] = re.sub(r"\s+", " ", j["title"]).strip()
    return j


def _get(session: requests.Session, url: str, **params):
    r = session.get(url, params=params or None, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------------------
# Search-based sources (use the Jev-selected search terms)
# ---------------------------------------------------------------------------

def remotive(s, terms, companies, log):
    out = []
    for t in terms:
        for j in _get(s, "https://remotive.com/api/remote-jobs", search=t, limit=100).json().get("jobs", []):
            out.append(_job("remotive", j["id"], title=j["title"], company=j["company_name"],
                            location=j.get("candidate_required_location", ""), remote=True, url=j["url"],
                            description=html_to_text(j.get("description")), salary=j.get("salary") or "",
                            posted_at=_iso(j.get("publication_date")), tags=j.get("tags") or [],
                            employment_type=j.get("job_type", ""), logo=j.get("company_logo") or "",
                            location_restrictions=j.get("candidate_required_location", "")))
    return out


def himalayas(s, terms, companies, log, pages=3):
    out = []
    for t in terms:
        cursor = None
        for _ in range(pages):
            params = {"q": t, "limit": 20}
            if cursor:
                params["cursor"] = cursor
            d = _get(s, "https://himalayas.app/jobs/api/search", **params).json()
            for j in d.get("jobs", []):
                restr = ", ".join(j.get("locationRestrictions") or []) or "Worldwide"
                sal = ""
                if j.get("minSalary"):
                    sal = f"{j.get('currency', '')} {j['minSalary']:,}–{j.get('maxSalary') or ''} / {j.get('salaryPeriod', 'year')}"
                out.append(_job("himalayas", j["guid"], title=j["title"], company=j["companyName"],
                                location=restr[:200], remote=True, url=j["applicationLink"],
                                description=html_to_text(j.get("description")), salary=sal,
                                posted_at=_iso(j.get("pubDate")), tags=j.get("categories") or [],
                                employment_type=j.get("employmentType", ""), logo=j.get("companyLogo") or "",
                                location_restrictions=restr))
            cursor = d.get("nextCursor")
            if not cursor:
                break
    return out


def jobicy(s, terms, companies, log):
    out = []
    for t in terms:
        try:
            jobs = _get(s, "https://jobicy.com/api/v2/remote-jobs", count=50, tag=t).json().get("jobs", [])
        except requests.HTTPError:
            continue
        for j in jobs:
            sal = ""
            if j.get("annualSalaryMin"):
                sal = f"{j.get('salaryCurrency', '')} {j['annualSalaryMin']}–{j.get('annualSalaryMax', '')} / year"
            out.append(_job("jobicy", j["id"], title=html.unescape(j["jobTitle"]), company=j["companyName"],
                            location=j.get("jobGeo", ""), remote=True, url=j["url"],
                            description=html_to_text(j.get("jobDescription")), salary=sal,
                            posted_at=_iso(j.get("pubDate")), tags=j.get("jobIndustry") or [],
                            employment_type=", ".join(j.get("jobType") or []), logo=j.get("companyLogo") or "",
                            location_restrictions=j.get("jobGeo", "")))
    return out


# ---------------------------------------------------------------------------
# Feed sources (no search; Jev title triage filters them)
# ---------------------------------------------------------------------------

def arbeitnow(s, terms, companies, log, pages=2):
    out = []
    for page in range(1, pages + 1):
        for j in _get(s, "https://www.arbeitnow.com/api/job-board-api", page=page).json().get("data", []):
            out.append(_job("arbeitnow", j["slug"], title=j["title"], company=j["company_name"],
                            location=j.get("location", ""), remote=j.get("remote"), url=j["url"],
                            description=html_to_text(j.get("description")),
                            posted_at=_iso(j.get("created_at")), tags=j.get("tags") or [],
                            employment_type=", ".join(j.get("job_types") or [])))
    return out


WWR_CATEGORIES = ["remote-full-stack-programming-jobs", "remote-back-end-programming-jobs",
                  "remote-front-end-programming-jobs", "remote-devops-sysadmin-jobs", "all-other-remote-jobs"]


def weworkremotely(s, terms, companies, log):
    out = []
    for cat in WWR_CATEGORIES:
        root = ET.fromstring(_get(s, f"https://weworkremotely.com/categories/{cat}.rss").content)
        for it in root.iter("item"):
            g = lambda tag: (it.findtext(tag) or "").strip()
            company, _, title = g("title").partition(": ")
            region = g("region")
            countries = g("country")
            out.append(_job("weworkremotely", g("guid") or g("link"), title=title or company,
                            company=company if title else "", location=region, remote=True, url=g("link"),
                            description=html_to_text(g("description")), posted_at=_iso(_rfc822(g("pubDate"))),
                            tags=[x.strip() for x in g("skills").replace(" and ", ", ").split(",") if x.strip()],
                            employment_type=g("type"),
                            location_restrictions=f"{region}; {countries}"[:600] if countries else region))
    return out


def _rfc822(v: str) -> Optional[str]:
    try:
        return datetime.strptime(v[:25], "%a, %d %b %Y %H:%M:%S").date().isoformat()
    except Exception:
        return None


def hackernews(s, terms, companies, log):
    """Latest 'Ask HN: Who is hiring?' thread; each top-level comment is one posting."""
    hits = _get(s, "https://hn.algolia.com/api/v1/search_by_date",
                tags="story,author_whoishiring", hitsPerPage=6).json()["hits"]
    story = next((h for h in hits if "who is hiring" in h["title"].lower()), None)
    if not story:
        return []
    item = _get(s, f"https://hn.algolia.com/api/v1/items/{story['objectID']}").json()
    out = []
    for c in item.get("children", []):
        text = html_to_text(c.get("text"))
        if not text or len(text) < 80:
            continue
        first = text.split("\n", 1)[0]
        parts = [p.strip() for p in first.split("|")]
        remote = bool(re.search(r"\bremote\b", first, re.I))
        out.append(_job("hackernews", c["id"], title=first[:160], company=parts[0][:80],
                        location=" | ".join(parts[1:])[:200], remote=remote,
                        url=f"https://news.ycombinator.com/item?id={c['id']}", description=text,
                        posted_at=_iso(c.get("created_at"))))
    return out


# ---------------------------------------------------------------------------
# Company applicant-tracking boards (Greenhouse / Ashby / Lever)
# ---------------------------------------------------------------------------

def _per_company(fn, companies, log):
    out = []
    with ThreadPoolExecutor(8) as ex:
        for co, res in zip(companies, ex.map(lambda c: _safe(fn, c), companies)):
            if isinstance(res, Exception):
                log(f"  {fn.__name__}:{co} failed: {res}")
            else:
                out.extend(res)
    return out


def _safe(fn, c):
    try:
        return fn(c)
    except Exception as e:  # one dead board shouldn't sink the source
        return e


def greenhouse(s, terms, companies, log):
    def one(co):
        d = _get(s, f"https://boards-api.greenhouse.io/v1/boards/{co}/jobs", content="true").json()
        res = []
        for j in d.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            res.append(_job("greenhouse", j["id"], title=j["title"], company=j.get("company_name") or co,
                            location=loc, remote="remote" in loc.lower(), url=j["absolute_url"],
                            description=html_to_text(j.get("content")),
                            posted_at=_iso(j.get("first_published") or j.get("updated_at")),
                            tags=[x["name"] for x in j.get("departments", [])]))
        return res
    return _per_company(one, companies.get("greenhouse", []), log)


def ashby(s, terms, companies, log):
    def one(co):
        d = _get(s, f"https://api.ashbyhq.com/posting-api/job-board/{co}", includeCompensation="true").json()
        res = []
        for j in d.get("jobs", []):
            if not j.get("isListed", True):
                continue
            locs = [j.get("location", "")] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
            comp = (j.get("compensation") or {}).get("scrapeableCompensationSalarySummary") or ""
            res.append(_job("ashby", j["id"], title=j["title"], company=_company_name(co),
                            location=" / ".join(l for l in locs if l), remote=j.get("isRemote"),
                            url=j["jobUrl"], description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                            salary=comp, posted_at=_iso(j.get("publishedAt")),
                            tags=[x for x in (j.get("department"), j.get("team")) if x],
                            employment_type=j.get("employmentType", ""),
                            location_restrictions=j.get("workplaceType", "")))
        return res
    return _per_company(one, companies.get("ashby", []), log)


def lever(s, terms, companies, log):
    def one(co):
        res = []
        for j in _get(s, f"https://api.lever.co/v0/postings/{co}", mode="json").json():
            cat = j.get("categories") or {}
            loc = cat.get("location") or ", ".join(cat.get("allLocations") or [])
            desc = (j.get("descriptionPlain") or "") + "\n\n" + "\n\n".join(
                f"{l.get('text', '')}\n{html_to_text(l.get('content'))}" for l in j.get("lists", []))
            res.append(_job("lever", j["id"], title=j["text"], company=_company_name(co), location=loc,
                            remote=j.get("workplaceType") == "remote", url=j["hostedUrl"],
                            description=desc.strip(), posted_at=_iso(j.get("createdAt")),
                            tags=[x for x in (cat.get("team"), cat.get("department")) if x],
                            employment_type=cat.get("commitment", ""),
                            location_restrictions=j.get("workplaceType", "")))
        return res
    return _per_company(one, companies.get("lever", []), log)


SOURCES: Dict[str, Callable] = {
    "remotive": remotive, "himalayas": himalayas, "jobicy": jobicy, "weworkremotely": weworkremotely,
    "arbeitnow": arbeitnow, "hackernews": hackernews, "greenhouse": greenhouse, "ashby": ashby,
    "lever": lever,
}


def fetch_all(enabled: List[str], terms: List[str], companies: Dict[str, List[str]], log) -> List[dict]:
    session = requests.Session()
    jobs: List[dict] = []

    def run(name):
        try:
            return name, SOURCES[name](session, terms, companies, log)
        except Exception as e:
            return name, e

    with ThreadPoolExecutor(6) as ex:
        for name, res in ex.map(run, [n for n in enabled if n in SOURCES]):
            if isinstance(res, Exception):
                log(f"✗ {name}: {res}")
            else:
                log(f"✓ {name}: {len(res)} postings")
                jobs.extend(res)
    # Dedupe the same posting seen through several sources/terms.
    seen, unique = set(), []
    for j in jobs:
        key = (j["id"],)
        soft = (re.sub(r"\W", "", j["company"].lower()), re.sub(r"\W", "", j["title"].lower()))
        if key in seen or soft in seen:
            continue
        seen.update({key, soft})
        unique.append(j)
    return unique
