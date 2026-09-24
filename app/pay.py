"""What a job pays, and what to ask for — shown on every card.

If the posting lists pay, that range is used (and the ask sits inside it). If not, the estimate comes from
typical bands for someone based in your country at the role's level: remote for a foreign company, or an
office/remote role in India. These bands are rough market figures (2025–26), labelled as estimates on the
card; the ask leans lower when the role is above your level and higher when it's below.
"""
from __future__ import annotations

import re
from typing import Optional

LEVELS = ["internship", "entry", "mid", "senior", "staff_plus"]
# annual, except internships (monthly)
BANDS = {
    # foreign companies hiring remotely in India mostly pay location-adjusted rates, well under their home bands
    "foreign": {"currency": "USD", "internship": (600, 1500), "entry": (25_000, 40_000), "mid": (35_000, 55_000),
                "senior": (55_000, 85_000), "staff_plus": (80_000, 120_000)},
    "india": {"currency": "INR", "internship": (25_000, 60_000), "entry": (800_000, 1_600_000), "mid": (1_600_000, 3_000_000),
              "senior": (3_000_000, 5_500_000), "staff_plus": (5_500_000, 9_000_000)},
}
FX = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79}          # to show a foreign estimate in the employer's currency
SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹", "CAD": "CA$", "AUD": "A$"}
_CUR = [("₹", "INR"), ("inr", "INR"), ("lpa", "INR"), ("lakh", "INR"), ("€", "EUR"), ("eur", "EUR"), ("£", "GBP"), ("gbp", "GBP"),
        ("ca$", "CAD"), ("cad", "CAD"), ("a$", "AUD"), ("aud", "AUD"), ("$", "USD"), ("usd", "USD")]
_NUM = r"(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s*([kKmM]|lpa|lakhs?|l\b)?"
_RANGE = re.compile(rf"(?:[$€£₹]|ca\$|a\$)?\s*{_NUM}\s*(?:-|–|—|to)\s*(?:[$€£₹]|ca\$|a\$)?\s*{_NUM}", re.I)
_ONE = re.compile(rf"(?:[$€£₹]|ca\$|a\$)\s*{_NUM}|{_NUM}\s*(?:usd|eur|gbp|inr|lpa)\b", re.I)


def _value(num: str, unit: Optional[str]) -> float:
    num = num.replace(",", "")
    if num.count(".") > 1 or (re.fullmatch(r"\d{1,3}\.\d{3}", num)):      # 150.000 (European thousands)
        num = num.replace(".", "")
    v = float(num)
    u = (unit or "").lower()
    if u == "k":
        v *= 1_000
    elif u == "m":
        v *= 1_000_000
    elif u in ("lpa", "lakh", "lakhs", "l"):
        v *= 100_000
    return v


def parse_listed(text: str) -> Optional[dict]:
    """The pay a posting states: {currency, low, high, period}, annualised (internships stay monthly)."""
    if not text:
        return None
    for line in re.split(r"[\n|]", text):
        low_line = line.lower()
        if not re.search(r"[$€£₹]|\b(usd|eur|gbp|inr|lpa|salary|compensation|pay|ctc|stipend)\b", low_line):
            continue
        pay_words = re.search(r"\b(salary|compensation|comp|pay|ctc|base|ote|stipend|per (year|annum|month|hour)|annual|/\s*(yr|year|hr|hour|mo|month)|lpa|equity)\b", low_line)
        if not pay_words and re.search(r"\b(raised|rais|funding|funded|valuation|valued|series [a-f]|seed|investors?|revenue|arr|backed|grant|"
                                       r"customers|users|million|billion|bn)\b", low_line):
            continue                                                              # "we raised $781M" is not a salary
        m = _RANGE.search(line)
        if m:
            g = m.groups()
            lo, hi = _value(g[0], g[1] or g[3]), _value(g[2], g[3] or g[1])
        else:
            m = _ONE.search(line)
            if not m:
                continue
            g = m.groups()                               # two alternatives: (num, unit) from either side
            num, unit = (g[0], g[1]) if g[0] else (g[2], g[3])
            lo = hi = _value(num, unit)
        cur = next((c for tok, c in _CUR if tok in low_line), None)
        if not cur:
            continue
        period = ("hour" if re.search(r"/\s*h(ou)?r|per hour|hourly", low_line)
                  else "month" if re.search(r"/\s*mo(nth)?|per month|monthly|stipend", low_line) else "year")
        if period == "hour":
            lo, hi, period = lo * 2080, hi * 2080, "year"
        if lo < 1_000 and cur != "INR" and period == "year":                   # "$150 - 210K": the k binds both
            lo *= 1_000
        if hi < lo:
            lo, hi = hi, lo
        if (cur == "INR" and hi < 20_000) or (cur != "INR" and period == "year" and hi < 5_000):
            continue                                                              # a stray number, not pay
        if cur != "INR" and period == "month" and hi < 500:
            continue                                                              # a perk ("£75/mo wellness"), not pay
        if (cur == "INR" and hi > 50_000_000) or (cur != "INR" and hi > 600_000):
            continue                                                              # millions: funding or revenue
        return {"currency": cur, "low": lo, "high": hi, "period": period}
    return None


def fmt(v: float, cur: str, period: str = "year") -> str:
    s = SYMBOL.get(cur, cur + " ")
    if cur == "INR":
        return f"₹{v / 100_000:.0f} LPA" if period == "year" else f"₹{v / 1000:.0f}k/mo"
    if period == "month":
        return f"{s}{v:,.0f}/mo"
    return f"{s}{v / 1000:.0f}k"


def fmt_range(lo: float, hi: float, cur: str, period: str = "year") -> str:
    if lo == hi:
        return fmt(lo, cur, period)
    s = SYMBOL.get(cur, cur + " ")
    if cur == "INR":
        return f"₹{lo / 100_000:.0f}–{hi / 100_000:.0f} LPA" if period == "year" else f"₹{lo / 1000:.0f}–{hi / 1000:.0f}k/mo"
    return f"{s}{lo:,.0f}–{hi:,.0f}/mo" if period == "month" else f"{s}{lo / 1000:.0f}–{hi / 1000:.0f}k"


def _nice(v: float, cur: str, period: str) -> float:
    step = 100_000 if cur == "INR" and period == "year" else 1_000 if period == "year" else 100
    return round(v / step) * step


def suggest(job: dict, job_level: str, cand_level: str, where: str) -> Optional[dict]:
    """where: 'india' (your country) or 'foreign' (a foreign employer, remote)."""
    li, ci = LEVELS.index(job_level) if job_level in LEVELS else 1, LEVELS.index(cand_level) if cand_level in LEVELS else 1
    lean = 0.3 if li > ci else 0.45 if li == ci else 0.65                     # where in the range to ask
    listed = parse_listed(f"{job.get('salary') or ''}\n{job.get('title') or ''}\n{job.get('description') or ''}")
    if listed:
        c, p = listed["currency"], listed["period"]
        ask = _nice(listed["low"] + lean * (listed["high"] - listed["low"]), c, p)
        abroad = where == "foreign" and c != "INR"
        return {"listed": True, "range": fmt_range(listed["low"], listed["high"], c, p), "ask": fmt(ask, c, p), "period": p,
                "abroad": abroad,
                "basis": "their range may be for hires in their own country — if they pay by location, expect less from India"
                         if abroad else "from the posting"}
    band = BANDS[where]
    lo, hi = band[job_level if job_level in band else "entry"]
    cur, p = band["currency"], ("month" if job_level == "internship" else "year")
    text = f"{job.get('location') or ''} {job.get('description') or ''}"[:4000]
    if where == "foreign" and re.search(r"€|\beur\b|\b(europe|eu|germany|france|spain|netherlands|poland|romania|portugal|italy|ireland)\b", text, re.I):
        cur = "EUR"
    elif where == "foreign" and re.search(r"£|\b(uk|united kingdom|london)\b", text, re.I):
        cur = "GBP"
    fx = FX.get(cur, 1.0)
    lo, hi = lo * fx, hi * fx
    ask = _nice(lo + lean * (hi - lo), cur, p)
    return {"listed": False, "range": fmt_range(_nice(lo, cur, p), _nice(hi, cur, p), cur, p),
            "ask": fmt(ask, cur, p), "period": p,
            "basis": "typical for this level, remote from India" if where == "foreign" else "typical for this level in India"}
