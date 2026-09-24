"""Resume -> candidate profile.

Code extracts *candidates* (keywords, projects, location, goal sentence); Jev decides
which ones matter (see questions.profile_questions). Without a Jev key the profile
still builds, using every extracted keyword and a keyword-overlap guess for roles.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from . import questions as Q
from .jev import Jev

# Terms we recognise anywhere in a resume, so PDFs without a clean skills table still work.
TECH_LEXICON = [
    "Python", "SQL", "JavaScript", "TypeScript", "HTML", "CSS", "Java", "Go", "Rust", "C++",
    "PyTorch", "TensorFlow", "HuggingFace Transformers", "PEFT/LoRA", "LoRA", "llama.cpp",
    "LangChain", "OpenAI API", "prompt engineering", "evals", "quantization", "RAG",
    "FastAPI", "Django", "Flask", "REST", "SSE streaming", "PostgreSQL", "MySQL", "MongoDB",
    "Redis", "Next.js", "React", "Tailwind CSS", "Node.js", "BeautifulSoup", "Selenium",
    "numpy", "pandas", "matplotlib", "scikit-learn", "ETL Pipelines", "CI/CD", "Git", "Docker",
    "Kubernetes", "GitHub Actions", "Linux", "Azure", "AWS", "GCP", "Weights & Biases",
    "Cloudflare Tunnel", "HuggingFace Hub", "Software Architecture", "LaTeX", "pytest",
]


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _brace_args(tex: str, pos: int, n: int):
    """Read `n` consecutive {...} groups starting at `pos` (balanced braces). Returns (args, end)."""
    args = []
    for _ in range(n):
        while pos < len(tex) and tex[pos] in " \t\n":
            pos += 1
        if pos >= len(tex) or tex[pos] != "{":
            break
        depth, start = 0, pos
        while pos < len(tex):
            depth += {"{": 1, "}": -1}.get(tex[pos], 0) if tex[pos - 1] != "\\" else 0
            pos += 1
            if depth == 0:
                break
        args.append(tex[start + 1:pos - 1])
    return args, pos


def latex_to_text(tex: str) -> str:
    tex = re.sub(r"(?<!\\)%.*", "", tex)
    tex = tex.split(r"\begin{document}", 1)[-1].split(r"\end{document}", 1)[0]
    tex = re.sub(r"\\(begin|end)\{[^}]*\}(\{[^}]*\})*", "\n", tex)
    tex = re.sub(r"\\href\{[^}]*\}", "", tex)
    tex = re.sub(r"\\(includegraphics|vspace|hspace|renewcommand|arraystretch|par)\*?(\[[^\]]*\])?(\{[^}]*\})*", " ", tex)
    tex = tex.replace(r"$\cdot$", " · ").replace(r"\&", "&").replace(r"\,", " ")
    tex = re.sub(r"\\\\(\[[^\]]*\])?", "\n", tex)
    tex = re.sub(r"\\(section|subsection)\*?\{([^}]*)\}", r"\n\n\2\n", tex)
    tex = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", tex)
    tex = re.sub(r"@\{\}|\{@\}|[{}$]", "", tex)
    tex = re.sub(r"(?m)^\s*(@?l X@?|-?\d+(\.\d+)?(pt|in|em)?|\d+(\.\d+)?)\s*$", "", tex)
    tex = re.sub(r"[ \t]+", " ", tex)
    tex = re.sub(r"(?m)^ | $", "", tex)
    return re.sub(r"\n\s*\n+", "\n\n", tex).strip()


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".tex":
        return latex_to_text(path.read_text(errors="ignore"))
    if suffix == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    return path.read_text(errors="ignore")


# ---------------------------------------------------------------------------
# Candidate extraction (deterministic)
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", s.lower())


def _tokens(s: str) -> frozenset:
    return frozenset(t for t in re.split(r"[^a-z0-9+#]+", s.lower()) if t)


def extract_keywords(raw: str, text: str, headline: str = "") -> List[str]:
    found: Dict[str, str] = {}
    token_sets: List[frozenset] = []

    def add(kw: str) -> None:
        toks = _tokens(kw)
        # "LoRA" vs "PEFT/LoRA", "Tailwind" vs "Tailwind CSS": keep the first-seen variant.
        for t, prev in zip(token_sets, found.values()):
            if toks <= t or (t <= toks and kw.lower().startswith(prev.lower())):
                return
        if not toks:
            return
        token_sets.append(toks)
        found[_norm(kw)] = kw

    # Skills table rows ("Label & a, b, c") in LaTeX, or "Label: a, b, c" in plain text.
    for m in re.finditer(r"\\textbf\{[^}]+\}\s*&\s*(.+?)\\\\", raw):
        for kw in m.group(1).split(","):
            kw = kw.replace(r"\&", "&").strip(" .")
            if 1 < len(kw) < 40:
                add(kw)
    # Tech stacks on project lines ("PyTorch · HuggingFace · ...").
    for line in text.splitlines():
        if line.count("·") >= 2 and line.strip() != headline:
            for kw in line.split("·"):
                kw = kw.strip(" .")
                if 1 < len(kw) < 40:
                    add(kw)
    low = text.lower()
    for kw in TECH_LEXICON:
        if re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", low):
            add(kw)
    return list(found.values())


def extract_projects(raw: str, text: str) -> Dict[str, Optional[str]]:
    """Project name -> one-line description, from the LaTeX Projects/Experience entries."""
    projects: Dict[str, Optional[str]] = {}
    body = raw.split(r"\section*{Projects}", 1)
    if len(body) == 2:
        section = re.split(r"\\section\*", body[1], maxsplit=1)[0]
        for m in re.finditer(r"\\entryheader", section):
            (head, *_), _ = _brace_args(section, m.end(), 1)
            name, _, desc = latex_to_text(head).partition(" - ")
            if name.strip():
                projects[name.strip()] = desc.strip() or None
    # Named items inside experience bullets:  \textbf{\href{..}{\textit{Name}}}: description
    for m in re.finditer(r"\\textbf\{\\href\{[^}]*\}\{\\textit\{([^}]+)\}\}\}:\s*([^\n]+)", raw):
        projects.setdefault(m.group(1).strip(), latex_to_text(m.group(2))[:160])
    if not projects:  # plain-text / PDF resumes: first phrase of lines under "Projects"
        section = re.split(r"(?im)^\s*projects\s*$", text, maxsplit=1)
        if len(section) == 2:
            for line in section[1].splitlines()[:40]:
                if re.match(r"(?i)^\s*(education|certificat|skills|experience)", line):
                    break
                m = re.match(r"^([A-Z][\w.+-]{2,30})\s*[-–|]\s+(.{10,}?)(\s*[A-Z][a-z]{2}\s*[’']\d{2}\b.*)?$",
                             line.strip())
                if m:
                    projects[m.group(1)] = m.group(2).strip()[:160]
    return projects


def extract_header(text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    name = lines[0] if lines else ""
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
    loc = re.search(r"([A-Z][a-zA-Z .]+,\s*[A-Z][a-zA-Z ]+)\s*(\||\n|·)", text[:600])
    if not loc:   # a single place on the contact line, e.g. "India |"
        loc = next((m for l in lines[1:8] for m in [re.match(r"^([A-Z][A-Za-z .'-]{1,40}?)\s*[|·]\s*$", l)] if m), None)
    headline = lines[1] if len(lines) > 1 and len(lines[1]) < 120 else ""
    goal = re.search(r"(Looking for[^.]+\.)", text)
    summary = next((l for l in lines[2:8] if len(l) > 150), "")
    return {
        "name": name,
        "email": email.group(0) if email else "",
        "location": loc.group(1).strip() if loc else "",
        "headline": headline,
        "goal": goal.group(1) if goal else headline,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Profile building
# ---------------------------------------------------------------------------

def _top(ans: dict) -> str:
    return ans["choice"]


def build_profile(resume_path: Path, jev: Optional[Jev], display_name: str = "") -> dict:
    raw = resume_path.read_text(errors="ignore") if resume_path.suffix.lower() == ".tex" else ""
    text = extract_text(resume_path)
    header = extract_header(text)
    keywords = extract_keywords(raw, text, header["headline"])
    projects = extract_projects(raw, text)

    profile = {
        **header,
        "resume_file": display_name or resume_path.name,
        "resume_text": text,
        "projects": projects,
        "scored_by": "heuristic",
        "keywords": [],          # [{name, used, market, keep, core}]
        "fields": [],            # [{name, p}]
        "level": "entry",
        "level_confidence": None,
        "country": country_of(header["location"]),
        "search_terms": [],      # [{name, p, keep}]
    }
    profile["languages"] = default_languages(profile["country"])      # the languages you work in (editable)

    if jev and jev.available:
        ans = jev.ask(text, Q.profile_questions(keywords, list(projects)))
        profile["scored_by"] = "jev"
        for i, kw in enumerate(keywords):
            used, market = ans[f"kw_used_{i}"]["noul"], ans[f"kw_market_{i}"]["noul"]
            keep = used >= Q.KEYWORD_KEEP_USED or market >= Q.KEYWORD_KEEP_MARKET
            profile["keywords"].append({"name": kw, "used": round(used, 3), "market": round(market, 3),
                                        "keep": keep, "core": used >= Q.KEYWORD_CORE})
        f = ans["field"]
        profile["fields"] = sorted(
            ({"name": k, "p": round(v, 3)} for k, v in f["probabilities"].items()
             if v >= Q.SECONDARY_FIELD_MIN_PROB or k == f["choice"]),
            key=lambda x: -x["p"])
        profile["level"] = _top(ans["level"])
        profile["level_confidence"] = round(ans["level"]["confidence"], 3)
        if ans["country"]["choice"] != "other":
            profile["country"] = ans["country"]["choice"]
        roles = sorted(((r, ans[f"role_{i}"]["noul"]) for i, r in enumerate(Q.ROLE_CATALOG)),
                       key=lambda x: -x[1])
        keep_n = 0
        for r, p in roles:
            keep = p >= Q.ROLE_KEEP and keep_n < Q.MAX_SEARCH_TERMS
            keep_n += keep
            profile["search_terms"].append({"name": r, "p": round(p, 3), "keep": keep})
    else:
        profile["keywords"] = [{"name": k, "used": None, "market": None, "keep": True, "core": True}
                               for k in keywords]
        profile["fields"] = [{"name": "ai_ml_engineering", "p": None}, {"name": "full_stack", "p": None}]
        low = text.lower()
        for r in Q.ROLE_CATALOG:
            hit = sum(w.lower() in low for w in r.split() if w not in ("Engineer", "Developer"))
            profile["search_terms"].append({"name": r, "p": None, "keep": False, "_hit": hit})
        profile["search_terms"].sort(key=lambda x: -x.pop("_hit"))
        for t in profile["search_terms"][:5]:
            t["keep"] = True
    return profile


# Fields you can edit on the Profile page; a rebuild keeps your edits (profile["overrides"]).
EDITABLE = ("name", "headline", "location", "email", "goal", "level", "country", "languages")


LOCAL_LANGUAGE = {"India": "Hindi", "Germany": "German", "France": "French", "Spain": "Spanish", "Netherlands": "Dutch",
                  "Poland": "Polish", "Portugal": "Portuguese", "Brazil": "Portuguese", "Mexico": "Spanish",
                  "Argentina": "Spanish", "Japan": "Japanese", "Indonesia": "Indonesian", "Vietnam": "Vietnamese",
                  "Pakistan": "Urdu", "Bangladesh": "Bengali", "Nepal": "Nepali", "Philippines": "Filipino"}


def default_languages(country: str) -> list:
    """English plus the country's main language; you can change this on the Profile page."""
    local = LOCAL_LANGUAGE.get(country or "")
    return ["English", local] if local else ["English"]


def country_of(location: str) -> str:
    """The country named in a resume's location line ("Pune, India", "Berlin", "India")."""
    from .pipeline import CITIES                      # local import: pipeline imports this module
    low = (location or "").lower()
    for c in Q.COUNTRIES:
        if re.search(rf"\b{re.escape(c.lower())}\b", low):
            return c
    for c, cities in CITIES.items():
        if re.search(rf"\b({cities})\b", low):
            return c
    return ""


def carry_overrides(old: Optional[dict], new: dict) -> dict:
    """After Kev re-reads the resume: keep what you set by hand — edited fields, skills and roles you
    switched on/off, and ones you added yourself."""
    if not old:
        return new
    legacy = "overrides" not in old        # made before edits were tracked: treat its picks as yours
    ov = dict(old.get("overrides") or {})
    new.update(ov)
    new["overrides"] = ov
    for key in ("keywords", "search_terms"):
        mine = {x["name"].lower(): x for x in old.get(key) or []}
        have = {x["name"].lower() for x in new.get(key) or []}
        for x in new.get(key) or []:
            o = mine.get(x["name"].lower())
            if o and (o.get("user_set") or legacy):
                x["keep"], x["user_set"] = o["keep"], True
        added = [o for o in old.get(key) or [] if (o.get("added") or (legacy and o.get("p" if key == "search_terms" else "used", 0) is None))
                 and o["name"].lower() not in have]
        new[key] = added + (new.get(key) or [])
    return new


def refresh_profile(old: Optional[dict], resume_path: Path, rev: str = "", display_name: str = "") -> dict:
    """A quick update without Kev (e.g. on GitHub, or while the engine is off): re-read the facts from the
    resume — header, summary, projects, skills — but keep Kev's earlier judgments (level, field, roles).
    New skills are kept until Kev looks at them."""
    fresh = build_profile(resume_path, None, display_name)
    fresh["resume_rev"] = rev
    if not old or old.get("scored_by") != "jev":
        return carry_overrides(old, fresh)
    new = dict(old)
    for k in ("name", "email", "location", "headline", "goal", "summary", "resume_text", "projects", "resume_file"):
        new[k] = fresh[k]
    names = {k["name"].lower() for k in fresh["keywords"]}
    known = {k["name"].lower() for k in old.get("keywords") or []}
    new["keywords"] = ([k for k in old.get("keywords") or [] if k["name"].lower() in names or k.get("added")]
                       + [{**k, "keep": True, "core": False} for k in fresh["keywords"] if k["name"].lower() not in known])
    new["resume_rev"] = rev
    return carry_overrides(old, new)


def compact_candidate(profile: dict) -> dict:
    """The small, relevant slice of the profile sent to Jev as `candidate`."""
    skills = [k["name"] for k in profile["keywords"] if k["keep"]]
    return {
        "headline": profile.get("headline", ""),
        "goal": profile.get("goal", ""),
        "level": profile.get("level", "entry"),
        "country": profile.get("country", ""),
        "location": profile.get("location", ""),
        "languages": profile.get("languages") or default_languages(profile.get("country", "")),
        "fields": [Q.FIELDS.get(f["name"], f["name"]) for f in profile.get("fields", [])],
        "skills": skills,
        "projects": profile.get("projects", {}),
        "summary": profile.get("summary", ""),
    }
