"""Every question the pipeline asks Jev, and every threshold/weight applied to the answers.

Kept in one file on purpose (TypeSafe's guidance): this is the thing to review and tune.
Question ids never reach the model, so each instruction carries its full meaning.

Pipeline stages and what Jev decides at each one:

  PROFILE   (state = resume text)
    P1  which extracted keywords to keep (demonstrated use / market value)      noul fan-out
    P2  the candidate's field                                                  choice
    P3  the candidate's level                                                  choice
    P4  the candidate's country (for location eligibility)                     choice
    P5  which role titles to search for                                        noul fan-out

  TRIAGE    (state = compact profile; one question pair per scraped job title)
    T1  is this title in the candidate's field                                 noul fan-out
    T2  is this title above the candidate's level                              noul fan-out

  EVALUATE  (state = {candidate, job})
    E*  tech role, field, level, location eligibility, experience, degree, skill
        alignment, interest alignment, red flags, work mode, employment type,
        which project to lead with, holistic verdict, matched skills, skill gaps

  SCORE     (code) composite match from E* answers, gates, and confidence flag
"""
from __future__ import annotations

from typing import Dict, List

from .jev import choice, noul, score

# ---------------------------------------------------------------------------
# Shared option sets
# ---------------------------------------------------------------------------
FIELDS: Dict[str, str] = {
    "ai_ml_engineering": "Building, training, fine-tuning, evaluating, or serving machine-learning or LLM models; ML infrastructure; MLOps",
    "applied_ai_products": "Building products or agents on top of LLMs/AI APIs: RAG, chatbots, agent tooling, AI features in apps",
    "data_science": "Statistics, analytics, experimentation, forecasting, and business insights from data",
    "data_engineering": "Data pipelines, ETL, warehouses, streaming, and large-scale scraping or ingestion",
    "backend": "Server-side services, APIs, and databases",
    "full_stack": "Product engineering across both frontend and backend",
    "frontend": "Web UI engineering: React, TypeScript, CSS",
    "devops_platform": "Cloud infrastructure, CI/CD, Kubernetes, SRE, and developer platforms",
    "research": "Research scientist roles centred on publishing or novel research",
    "other_engineering": "Mobile, embedded, QA, security, or other engineering specialties",
    "non_engineering": "Not an engineering role: sales, marketing, recruiting, legal, finance, operations, design, customer support, or product/program management",
}

LEVELS: Dict[str, str] = {
    "internship": "Internship, apprenticeship, or trainee position",
    "entry": "Entry level / junior / new grad / associate / SDE-1; roughly 0-2 years of professional experience",
    "mid": "Mid level / SDE-2; roughly 2-5 years of professional experience",
    "senior": "Senior or lead individual contributor; roughly 5+ years of professional experience",
    "staff_plus": "Staff, principal, architect, engineering manager, director, or head-of roles",
}

COUNTRIES = [
    "India", "United States", "United Kingdom", "Canada", "Germany", "France", "Netherlands",
    "Spain", "Poland", "Portugal", "Ireland", "Singapore", "United Arab Emirates", "Australia",
    "Brazil", "Mexico", "Argentina", "Nigeria", "Kenya", "South Africa", "Pakistan",
    "Bangladesh", "Sri Lanka", "Nepal", "Philippines", "Indonesia", "Vietnam", "Japan",
]

ROLE_CATALOG = [
    "Machine Learning Engineer", "AI Engineer", "LLM Engineer", "Applied AI Engineer",
    "Generative AI Engineer", "NLP Engineer", "MLOps Engineer", "Deep Learning Engineer",
    "Computer Vision Engineer", "Research Engineer", "Research Scientist", "Data Scientist",
    "Data Analyst", "Data Engineer", "Python Developer", "Django Developer", "Backend Engineer",
    "Full Stack Engineer", "Frontend Engineer", "React Developer", "Software Engineer",
    "DevOps Engineer", "Platform Engineer", "Site Reliability Engineer", "Web Scraping Engineer",
    "Automation Engineer", "Forward Deployed Engineer", "Solutions Engineer",
    "Developer Advocate", "Mobile Developer", "QA Automation Engineer",
]

# Common skills used to surface *gaps*: requirements the candidate doesn't list.
SKILL_VOCAB = [
    "Java", "Go", "Rust", "C++", "C#", ".NET", "Ruby on Rails", "PHP", "Kotlin", "Swift", "Scala",
    "Node.js", "Vue", "Angular", "GraphQL", "gRPC", "Redis", "MongoDB", "Elasticsearch",
    "Kafka", "Spark", "Airflow", "dbt", "Snowflake", "BigQuery", "AWS", "GCP", "Terraform",
    "TensorFlow", "JAX", "CUDA", "Ray", "MLflow", "vLLM", "Vector databases",
    "Computer vision", "Recommender systems", "Reinforcement learning", "Distributed systems",
    "Microservices", "React Native", "Flutter", "iOS", "Android", "Tableau", "Power BI",
]

# ---------------------------------------------------------------------------
# PROFILE stage (state = plain resume text)
# ---------------------------------------------------------------------------

def profile_questions(keywords: List[str], projects: List[str]) -> Dict[str, dict]:
    q: Dict[str, dict] = {
        "field": choice(
            "Which field is this candidate best positioned to be hired into, based on their "
            "projects, work experience, and stated goal?",
            FIELDS,
        ),
        "level": choice(
            "What job level matches this candidate's professional experience? Count paid work "
            "experience; personal projects strengthen a profile but do not add years.",
            LEVELS,
        ),
        "country": choice(
            "In which country does the candidate currently live, according to the resume header?",
            {c: None for c in COUNTRIES} | {"other": "A country not in this list or not stated"},
        ),
    }
    for i, kw in enumerate(keywords):
        q[f"kw_used_{i}"] = noul(
            f"Does the resume show the candidate actually using \"{kw}\" in a job or a project "
            f"(beyond just listing it in a skills section)?",
            true=f"{kw} appears in, or is clearly implied by, a described job or project",
            false=f"{kw} is only listed as a skill, or not present",
        )
        q[f"kw_market_{i}"] = noul(
            f"Is \"{kw}\" a skill that software, data, or machine-learning job postings commonly "
            f"list as a requirement?",
        )
    for i, role in enumerate(ROLE_CATALOG):
        q[f"role_{i}"] = noul(
            f"Would this candidate be a competitive applicant for \"{role}\" positions at a level "
            f"that matches their experience?",
            true="Their projects and skills directly match what this role does day to day",
            false="The role needs a different skill set or domain than the candidate has shown",
        )
    return q


KEYWORD_KEEP_USED = 0.5       # keep a keyword if demonstrably used ...
KEYWORD_KEEP_MARKET = 0.7     # ... or if listed and commonly required by employers
KEYWORD_CORE = 0.75           # "core" keywords (used ≥ this) are weighted higher in matching
ROLE_KEEP = 0.5               # role titles at/above this become search terms
MAX_SEARCH_TERMS = 7
SECONDARY_FIELD_MIN_PROB = 0.12

# ---------------------------------------------------------------------------
# TRIAGE stage (state = compact candidate profile, questions carry the titles)
# ---------------------------------------------------------------------------

def triage_questions(jobs: List[dict]) -> Dict[str, dict]:
    q: Dict[str, dict] = {}
    for i, j in enumerate(jobs):
        label = f"\"{j['title']}\" at {j['company']}"
        q[f"field_{i}"] = noul(
            f"Judging only by the job title {label}, is this a role in one of `candidate.fields`?",
            true="The title names a role whose daily work falls in one of `candidate.fields`",
            false="The title names a role in a different field, or a non-engineering role",
        )
        q[f"above_{i}"] = noul(
            f"Does the job title {label} explicitly name a level above `candidate.level` "
            f"(for example Senior, Staff, Principal, Lead, Manager, Director, Head)?",
        )
    return q


TRIAGE_FIELD_MIN = 0.35       # lenient: deep evaluation makes the real call
TRIAGE_ABOVE_MAX = 0.75
TRIAGE_BATCH = 60             # jobs per triage request (2 questions each)

# ---------------------------------------------------------------------------
# EVALUATE stage (state = {"candidate": ..., "job": ...})
# ---------------------------------------------------------------------------

def evaluate_questions(candidate: dict, gap_skills: List[str]) -> Dict[str, dict]:
    country = candidate.get("country") or "the candidate's country"
    projects = candidate.get("projects") or {}
    q: Dict[str, dict] = {
        "tech_role": noul(
            "Is `job` a hands-on technical individual-contributor role (engineering, data, ML, "
            "or research) rather than sales, recruiting, marketing, support, or pure management?",
        ),
        "field": choice("Which field does `job` belong to?", FIELDS),
        "level": choice("What level is `job` hiring for?", LEVELS),
        "location": choice(
            f"Could someone who lives in {country} and holds work authorization only in {country} "
            f"be hired for `job` without moving to another country?",
            {
                "remote_open": f"Remote, and open worldwide or explicitly including {country} or its region",
                "local_office": f"Onsite or hybrid at an office located in {country}",
                "restricted": f"Requires living in, relocating to, or being authorized to work in a place other than {country} (for example US-only, EU-only, or a specific city abroad)",
                "unclear": "The posting does not say where the employee must be located",
            },
        ),
        "experience": choice(
            "What is the minimum professional experience `job` asks for?",
            {
                "not_stated": "No years of experience are mentioned",
                "0_2": "0 to 2 years, new grad, or entry level",
                "2_4": "2 to 4 years",
                "4_7": "4 to 7 years",
                "7_plus": "7 or more years",
            },
        ),
        "degree": choice(
            "What education does `job` require?",
            {
                "none_stated": "No degree requirement is mentioned",
                "any_degree_or_equivalent": "A bachelor's degree in any field, or equivalent practical experience",
                "cs_engineering_degree": "A bachelor's degree specifically in computer science, engineering, or a closely related field",
                "advanced_degree": "A master's degree or PhD is required",
            },
        ),
        "skill_alignment": score(
            "How well do `candidate.skills` and `candidate.projects` cover the core technical "
            "requirements of `job`?",
            [
                "The job's core requirements are in a different domain; almost none of the candidate's skills apply",
                "A few candidate skills appear, but the main required skills are ones the candidate lacks",
                "About half of the core required skills are ones the candidate has",
                "Most core required skills are ones the candidate has; one or two important gaps",
                "Nearly every core required skill is one the candidate has used in work or projects",
            ],
        ),
        "interest_alignment": score(
            "How closely does the day-to-day work in `job` match `candidate.goal`?",
            [
                "Unrelated to the candidate's stated goal",
                "Tangential: some overlap, but mostly different work",
                "Related: a reasonable step toward the stated goal",
                "Squarely the kind of role the candidate says they want",
            ],
        ),
        "red_flags": noul(
            "Does `job` show signs of being a scam, unpaid, commission-only, a pay-to-apply or "
            "training-fee scheme, or a vague posting with no real role described?",
        ),
        "work_mode": choice(
            "Where is the work in `job` done?",
            {"remote": "Fully remote", "hybrid": "Hybrid: some days in an office",
             "onsite": "Onsite at an office", "unclear": "Not stated"},
        ),
        "employment": choice(
            "What type of employment is `job`?",
            {"full_time": "Full-time permanent", "contract": "Contract or freelance",
             "part_time": "Part-time", "internship": "Internship"},
        ),
        "should_apply": noul(
            "Is `job` a realistic and worthwhile application for `candidate`, considering skills, "
            "level, and goal (ignore location)?",
            true="A reasonable hiring manager would seriously consider this candidate",
            false="The candidate would very likely be screened out or the role doesn't fit their goal",
        ),
    }
    if projects:
        q["lead_project"] = choice(
            "Which one of the candidate's projects is most relevant to the work in `job`, and "
            "should be mentioned first in the application?",
            dict(projects) | {"none": "None of the projects is relevant to this job"},
        )
    for i, kw in enumerate(candidate.get("skills", [])):
        q[f"has_{i}"] = noul(
            f"Does `job` mention \"{kw}\" (or a direct equivalent) as a required or preferred "
            f"skill, or as part of the team's stack?",
        )
    for i, kw in enumerate(gap_skills):
        q[f"gap_{i}"] = noul(f"Does `job` list \"{kw}\" as a required skill?")
    return q


# ---------------------------------------------------------------------------
# SCORE stage — composite match computed in code from the answers above.
# ---------------------------------------------------------------------------
WEIGHTS = {
    "skill_alignment": 0.32,
    "interest_alignment": 0.20,
    "should_apply": 0.20,
    "level_fit": 0.16,
    "experience_fit": 0.12,
}

# How acceptable each job level is, given the candidate's level (probability-weighted).
LEVEL_FIT = {
    "internship": {"internship": 1.0, "entry": 0.8, "mid": 0.2, "senior": 0.0, "staff_plus": 0.0},
    "entry":      {"internship": 0.6, "entry": 1.0, "mid": 0.6, "senior": 0.1, "staff_plus": 0.0},
    "mid":        {"internship": 0.1, "entry": 0.6, "mid": 1.0, "senior": 0.5, "staff_plus": 0.1},
    "senior":     {"internship": 0.0, "entry": 0.2, "mid": 0.7, "senior": 1.0, "staff_plus": 0.6},
    "staff_plus": {"internship": 0.0, "entry": 0.1, "mid": 0.4, "senior": 0.9, "staff_plus": 1.0},
}
EXPERIENCE_FIT = {
    "internship": {"not_stated": 0.8, "0_2": 1.0, "2_4": 0.3, "4_7": 0.0, "7_plus": 0.0},
    "entry":      {"not_stated": 0.8, "0_2": 1.0, "2_4": 0.6, "4_7": 0.15, "7_plus": 0.0},
    "mid":        {"not_stated": 0.8, "0_2": 0.8, "2_4": 1.0, "4_7": 0.6, "7_plus": 0.1},
    "senior":     {"not_stated": 0.8, "0_2": 0.4, "2_4": 0.8, "4_7": 1.0, "7_plus": 0.8},
    "staff_plus": {"not_stated": 0.8, "0_2": 0.2, "2_4": 0.5, "4_7": 0.9, "7_plus": 1.0},
}

# Hard gates: a job failing any of these never reaches the board. They lean on the atomic, factual
# answers (location, level, years asked), which engines get right far more often than the holistic
# skill/"should apply" judgments — see bench/ for the measurements behind this.
GATE_TECH_ROLE_MIN = 0.4
GATE_NON_ENGINEERING_MAX = 0.6
GATE_LOCATION_ELIGIBLE_MIN = 0.4  # p(remote_open) + p(local_office) + half of p(unclear); drop below this
GATE_LEVEL_FIT_MIN = 0.35         # probability-weighted LEVEL_FIT; below = mostly above the candidate's level
GATE_EXPERIENCE_FIT_MIN = 0.35    # probability-weighted EXPERIENCE_FIT; below = asks for many more years
GATE_RED_FLAGS_MAX = 0.6

ADVANCED_DEGREE_PENALTY = 12      # points off when a master's/PhD is required (p > 0.6)
UNCLEAR_LOCATION_PENALTY = 6      # points off when location is unclear (p > 0.6)
SKILL_MATCH_MIN = 0.55            # has_* / gap_* threshold to show a skill chip
LOW_CONFIDENCE = 0.5              # below this, the card shows a "Jev unsure" badge
DEFAULT_MIN_MATCH = 40            # jobs scoring below this are not loaded on the board (bench/: Kev-4B keeps
                                  # 4/4 good jobs with 1 false positive at 40; 1/4 at 55. The hard gates above
                                  # do the real filtering, this only trims weak matches.)
