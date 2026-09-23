"""Job-posting URL helpers: loose matching, and guessing company/role from a link the user pastes."""
from __future__ import annotations

import re
from typing import Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests
from bs4 import BeautifulSoup

_TRACKING = re.compile(r"^(utm_.*|ref|source|src|gh_src|lever-source|lever-origin|fbclid|gclid)$")


def norm_url(u: str) -> str:
    """Compare posting URLs loosely: ignore scheme, 'www.', fragment, trailing slash and tracking
    parameters — but keep identifying ones (news.ycombinator.com/item?id=... is a different post per id)."""
    parts = urlsplit((u or "").strip().lower())
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    host = re.sub(r"^www\.", "", parts.netloc)
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


# Applicant-tracking systems put the company in the URL; (regex on host+path, source name).
_ATS = [
    (r"(?:job-)?boards(?:-api)?\.greenhouse\.io/([^/]+)", "greenhouse"),
    (r"jobs\.(?:eu\.)?lever\.co/([^/]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([^/]+)", "ashby"),
    (r"apply\.workable\.com/([^/]+)", "workable"),
    (r"jobs\.smartrecruiters\.com/([^/]+)", "smartrecruiters"),
    (r"jobs\.polymer\.co/([^/]+)", "polymer"),
    (r"([^./]+)\.zohorecruit\.(?:com|in)", "zoho"),
    (r"([^./]+)\.bamboohr\.com", "bamboohr"),
    (r"([^./]+)\.wd\d+\.myworkdayjobs\.com", "workday"),
    (r"([^./]+)\.breezy\.hr", "breezy"),
    (r"([^./]+)\.eightfold\.ai", "eightfold"),
    (r"([^./]+)\.recruitee\.com", "recruitee"),
    (r"([^./]+)\.teamtailor\.com", "teamtailor"),
]


def _pretty(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def from_url(url: str) -> Dict[str, Optional[str]]:
    """Company + source from the URL alone (no network)."""
    parts = urlsplit(url.strip())
    hp = parts.netloc.lower() + parts.path
    for pat, source in _ATS:
        m = re.search(pat, hp)
        if m:
            return {"company": _pretty(m.group(1)), "source": source}
    host = re.sub(r"^(www|jobs|careers|apply)\.", "", parts.netloc.lower())
    if "ycombinator.com" in host:
        return {"company": None, "source": "hackernews"}
    return {"company": None, "source": host or None}


def from_page(url: str, company: Optional[str]) -> Dict[str, Optional[str]]:
    """Role (and company, if unknown) from the page's og:title/<title>. Best effort: many ATS pages
    render with JavaScript and only expose a generic title, in which case nothing is returned."""
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0 JobBoard/1.0"})
        r.raise_for_status()
    except requests.RequestException:
        return {}
    soup = BeautifulSoup(r.text[:300_000], "html.parser")
    og = soup.find("meta", property="og:title") or soup.find("meta", attrs={"name": "og:title"})
    site = soup.find("meta", property="og:site_name")
    titles = [og.get("content") if og else None, soup.title.string if soup.title else None]
    parsed = [parse_title(t, (site.get("content") if site else "") or "", company) for t in titles if t]
    if not parsed:
        return {}
    # Prefer the reading that found a job-title-looking role, then the one that also names the company.
    best = max(parsed, key=lambda p: (bool(p["role"] and _ROLE_WORDS.search(p["role"])), p["company"] != company))
    named = next((p["company"] for p in parsed if p["company"] and p["company"] != company), None)
    return {**best, "company": named or best["company"]}


_ROLE_WORDS = re.compile(
    r"\b(engineer\w*|engg|developer|dev|intern(ship)?|scientist|analyst|manager|designer|lead|architect|"
    r"specialist|associate|researcher|sde|consultant|programmer|administrator|devops|sre|writer|recruiter|"
    r"executive|officer|director|head|fellow|trainee|technician|advocate)\b", re.I)
_CAREERS = re.compile(r"^(careers?|jobs?|job board|hiring|(current |job )?open(ing|ings| positions)|join us|apply)$", re.I)


def parse_title(title: str, site: str = "", company: Optional[str] = None) -> Dict[str, Optional[str]]:
    """Split a posting page title into role / company / location. Handles "Role at Company",
    "Role @ Company", "Company - Role", "Role | Company Careers" and a trailing " in <City>"."""
    t = re.sub(r"^(job application for|apply for|careers?:?)\s+", "", (title or "").strip(), flags=re.I)
    role = co = None
    m = re.match(r"(.+?)\s+(?:at|@)\s+(.+)$", t)
    if m:
        role, co = m.group(1), m.group(2)
    else:
        bits = [re.sub(r"\s+(careers?|jobs?)$", "", b.strip(), flags=re.I)
                for b in re.split(r"\s+[|–—-]\s+", t) if b.strip()]
        bits = [b for b in bits if b and not _CAREERS.match(b)]
        if len(bits) == 1:
            role = bits[0]
        elif bits:
            roley = [b for b in bits if _ROLE_WORDS.search(b)]
            role = roley[0] if roley else bits[0]
            others = [b for b in bits if b != role]
            hint = [b for b in others if company and b.lower() == company.lower()]
            co = (hint or others or [None])[0]
    location = None
    if role:
        m = re.match(r"(.+?)\s+in\s+([A-Z][\w .,-]+)$", role)
        if m and not _ROLE_WORDS.search(m.group(2)):
            role, location = m.group(1), m.group(2)
    generic = not role or len(role) > 120 or _CAREERS.match(role)
    return {"role": None if generic else role.strip(), "company": co or company or site or None,
            "location": location}
