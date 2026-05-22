"""
legitimacy_scorer.py — Job listing trust scorer
Scores 0-100 based on URL signals, salary sanity, source, company presence,
posting age, and location patterns. Works from tracker CSV data only — no
external API calls, no blocking network requests.

Cache: logs/legitimacy_cache.json, 48-hour TTL.
Key by Application URL.
"""

import json
import re
import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent.resolve()
_CACHE_PATH  = _SCRIPT_DIR / "logs" / "legitimacy_cache.json"
_CACHE_TTL_H = 48

# ── ATS domains — presence in the apply URL = strong legitimacy signal ─────────
_ATS_DOMAINS = {
    "greenhouse.io":        "Greenhouse",
    "lever.co":             "Lever",
    "workday.com":          "Workday",
    "myworkdayjobs.com":    "Workday",
    "bamboohr.com":         "BambooHR",
    "icims.com":            "iCIMS",
    "taleo.net":            "Taleo",
    "successfactors.com":   "SAP SuccessFactors",
    "sapsf.com":            "SAP SuccessFactors",
    "jobvite.com":          "Jobvite",
    "smartrecruiters.com":  "SmartRecruiters",
    "ashbyhq.com":          "Ashby",
    "recruitee.com":        "Recruitee",
    "applytojob.com":       "ApplyToJob",
    "breezy.hr":            "Breezy HR",
    "jazz.co":              "JazzHR",
    "rippling.com":         "Rippling",
    "paylocity.com":        "Paylocity",
    "paycom.com":           "Paycom",
    "adp.com":              "ADP",
    "ultipro.com":          "UltiPro",
    "kronos.com":           "Kronos",
    "ceridian.com":         "Ceridian",
    "oraclecloud.com":      "Oracle HCM",
    "kenexa.com":           "IBM Kenexa",
    "clearcompany.com":     "ClearCompany",
    "careerplug.com":       "CareerPlug",
    "jobscore.com":         "JobScore",
    "workable.com":         "Workable",
    "teamtailor.com":       "Teamtailor",
    "pinpointhq.com":       "Pinpoint",
    "apploi.com":           "Apploi",
    "hirebridge.com":       "HireBridge",
    "silkroad.com":         "SilkRoad",
}

# ── Personal free-email domains → red flag if used as apply target ─────────────
_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "aol.com",
    "icloud.com", "protonmail.com", "proton.me", "outlook.com",
}

# ── Recognized job boards — their infrastructure screens some listings ─────────
_TRUSTED_BOARD_DOMAINS = {
    "usajobs.gov",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "remotive.com",
    "remoteok.com",
    "jobicy.com",
    "dice.com",
    "monster.com",
    "careerbuilder.com",
    "simplyhired.com",
    "flexjobs.com",
    "wellfound.com",
    "adzuna.com",
    "adzuna.co.uk",
    "jobs.lever.co",       # Lever-hosted pages look like a board
}

# ── Source field values from job_feed.py that indicate known platforms ─────────
_TRUSTED_SOURCES = {
    "usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy",
    "linkedin", "indeed", "glassdoor", "google jobs", "dice",
}

# ── Known scam domains — immediate score=0, no caching ────────────────────────
try:
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).parent.resolve()))
    import config as _cfg
    _KNOWN_SCAM_DOMAINS = [d.lower() for d in getattr(_cfg, "SCAM_DOMAINS", [])]
    _KNOWN_SCAM_COMPANY_NAMES = frozenset(n.lower() for n in getattr(_cfg, "SCAM_COMPANY_NAMES", []))
except Exception:
    _KNOWN_SCAM_DOMAINS = []
    _KNOWN_SCAM_COMPANY_NAMES = frozenset()


def _is_known_scam(url: str, company: str) -> bool:
    url_l     = url.lower()
    company_l = company.lower().strip()
    for domain in _KNOWN_SCAM_DOMAINS:
        if domain in url_l:
            return True
    return company_l in _KNOWN_SCAM_COMPANY_NAMES

# ── Entry-level salary sanity window (annual) ──────────────────────────────────
_SALARY_REALISTIC_MIN = 35_000
_SALARY_REALISTIC_MAX = 110_000
_SALARY_SUSPICIOUS    = 150_000   # above this for entry-level = red flag

# ── Direct-career-page URL path fragments ─────────────────────────────────────
_CAREER_PATHS = (
    "/careers/", "/career/", "/jobs/", "/job/", "/apply/",
    "/open-positions/", "/openings/", "/opportunities/", "/work-with-us/",
)


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    if _CACHE_PATH.exists():
        try:
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(exist_ok=True)
    try:
        _CACHE_PATH.write_text(
            json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def _cache_key(url: str) -> str:
    return url.strip().lower()[:250]


def _is_expired(entry: dict) -> bool:
    ts = entry.get("cached_at")
    if not ts:
        return True
    try:
        cached = datetime.datetime.fromisoformat(ts)
        return (datetime.datetime.now() - cached).total_seconds() > _CACHE_TTL_H * 3600
    except Exception:
        return True


# ── Signal detectors ───────────────────────────────────────────────────────────

def _detect_ats(url: str) -> str | None:
    if not url:
        return None
    ul = url.lower()
    for domain, name in _ATS_DOMAINS.items():
        if domain in ul:
            return name
    return None


def _has_personal_email_apply(url: str) -> bool:
    if not url:
        return False
    ul = url.lower()
    if "mailto:" not in ul:
        return False
    return any(d in ul for d in _PERSONAL_EMAIL_DOMAINS)


def _is_gov_url(url: str) -> bool:
    if not url:
        return False
    ul = url.lower()
    return ".gov" in ul or "usajobs" in ul


def _is_trusted_board_url(url: str) -> bool:
    if not url:
        return False
    ul = url.lower()
    return any(b in ul for b in _TRUSTED_BOARD_DOMAINS)


def _is_direct_career_page(url: str) -> bool:
    """True when the URL points to a company's own career site (not a board)."""
    if not url or _is_trusted_board_url(url) or _is_gov_url(url):
        return False
    if _detect_ats(url):
        return True   # ATS-hosted = company's own hiring pipeline
    ul = url.lower()
    return any(p in ul for p in _CAREER_PATHS)


def _parse_salary_number(salary_str: str) -> int | None:
    if not salary_str:
        return None
    nums = re.findall(r"[\d,]+", salary_str.replace("$", ""))
    values = []
    for n in nums:
        raw = n.replace(",", "")
        if raw.isdigit() and len(raw) >= 2:
            v = int(raw)
            values.append(v)
    if not values:
        return None
    mx = max(values)
    if mx < 500:      # hourly rate
        return mx * 2080
    if mx < 1_500:    # weekly (unlikely, but guard)
        return mx * 52
    return mx         # annual


def _is_recent(date_str: str) -> bool:
    if not date_str:
        return False
    try:
        d = date_str.split("T")[0].split(" ")[0]
        posted = datetime.datetime.strptime(d, "%Y-%m-%d")
        return (datetime.datetime.now() - posted).days <= 30
    except Exception:
        return False


def _is_multi_location(location: str) -> bool:
    if not location:
        return False
    ll = location.lower()
    if any(p in ll for p in ("multiple location", "various location", "nationwide", "all states", "all locations")):
        return True
    return location.count(",") >= 4


# ── Core scorer ────────────────────────────────────────────────────────────────

def score_job(job: dict) -> dict:
    """
    Score one job (tracker dict or raw job dict) for legitimacy 0-100.
    Returns full breakdown dict.
    """
    url      = (job.get("Application URL") or job.get("url")         or "").strip()
    company  = (job.get("Company")         or job.get("company")     or "").strip()
    salary   = (job.get("Salary")          or job.get("salary")      or "").strip()
    source   = (job.get("Source")          or job.get("source")      or "").strip().lower()
    datep    = (job.get("Date Posted")     or job.get("date_posted") or "").strip()
    location = (job.get("Location")        or job.get("location")    or "").strip()

    # ── Known scam — immediate block, not cached ──────────────────────────────
    if _is_known_scam(url, company):
        return {
            "score": 0, "label": "Known Scam Source", "color": "#ff4444",
            "emoji": "🔴",
            "boosters_found": [], "flags_found": ["Domain is on the known scam blocklist"],
            "uncertainties": [],
            "recommendation": "BLOCKED — This domain is on the known scam list. Do not apply.",
            "top_signals": [("🔴", "Blocklisted domain")],
        }

    # ── Cache lookup ──────────────────────────────────────────────────────────
    cache = _load_cache()
    key   = _cache_key(url) if url else ""
    if key and key in cache and not _is_expired(cache[key]):
        return cache[key]

    score          = 50
    boosters_found = []
    flags_found    = []
    uncertainties  = []

    # ── Pre-compute signals ───────────────────────────────────────────────────
    ats_name        = _detect_ats(url)
    is_gov          = _is_gov_url(url)
    is_board        = _is_trusted_board_url(url)
    is_career_page  = _is_direct_career_page(url)
    salary_num      = _parse_salary_number(salary)
    is_recent_post  = _is_recent(datep)
    is_multi_loc    = _is_multi_location(location)
    is_personal_em  = _has_personal_email_apply(url)
    trusted_source  = source in _TRUSTED_SOURCES

    # ── CRITICAL RED FLAGS (-15 each) ─────────────────────────────────────────
    if is_personal_em:
        score -= 15
        flags_found.append("Apply link routes to a personal email (Gmail/Yahoo/etc.) — serious red flag")

    # Description-based critical checks can't run from tracker CSV data
    uncertainties.append(
        "Cannot auto-check for SSN/bank-info requests — read the full description carefully"
    )
    uncertainties.append(
        "Cannot auto-check for 'buy your own equipment' language — read the full description"
    )

    # ── MAJOR RED FLAGS (-10 each) ────────────────────────────────────────────
    bad_company = not company or company.lower() in ("unknown", "n/a", "", "company", "none")
    if bad_company:
        score -= 10
        flags_found.append("Company name is missing or listed as unknown — cannot verify existence")

    if salary_num and salary_num > _SALARY_SUSPICIOUS:
        score -= 10
        flags_found.append(
            f"Salary appears unrealistically high for an entry-level role ({salary}) — verify this is correct"
        )

    # Domain age: can't check without WHOIS
    if url and not is_gov and not is_board and not ats_name:
        uncertainties.append(
            "Domain registration age not verified — check whois.domaintools.com if suspicious"
        )

    # ── MINOR RED FLAGS (-8 each) ─────────────────────────────────────────────
    if not ats_name and not is_gov and not is_board:
        score -= 8
        flags_found.append("No recognized ATS system detected in the apply link")

    if is_multi_loc:
        loc_display = location[:60] + ("…" if len(location) > 60 else "")
        score -= 8
        flags_found.append(f"Posted across many locations at once: {loc_display}")

    # Grammar/vague description: can't check without the description text
    uncertainties.append(
        "Job description quality (vague vs. detailed) not auto-checked — read the posting"
    )

    # ── STRONG TRUST BOOSTERS (+10 each) ──────────────────────────────────────
    if is_gov:
        score += 10
        boosters_found.append("Posted on a verified .gov / USAJobs federal platform")

    if ats_name:
        score += 10
        boosters_found.append(f"Legitimate ATS system detected: {ats_name}")

    if is_career_page and not ats_name:
        score += 10
        boosters_found.append("Job links directly to the company's own career page")

    # ── MODERATE TRUST BOOSTERS (+7 each) ─────────────────────────────────────
    if salary_num and _SALARY_REALISTIC_MIN <= salary_num <= _SALARY_REALISTIC_MAX:
        score += 7
        boosters_found.append("Salary falls within a realistic range for entry-level remote/IT work")

    # Detailed description — can't check; mark as unverified
    uncertainties.append(
        "Glassdoor reviews and LinkedIn company page not auto-checked — search the company name manually"
    )

    # ── MINOR TRUST BOOSTERS (+5 each) ────────────────────────────────────────
    if is_board:
        score += 5
        boosters_found.append("Listed on a recognized job platform (adds a layer of vetting)")

    if is_recent_post:
        score += 5
        boosters_found.append("Posted within the last 30 days (fresh, active listing)")

    if trusted_source and not is_gov and not is_board:
        score += 5
        boosters_found.append(f"Fetched from a recognized job source ({source})")

    # ── CLAMP ─────────────────────────────────────────────────────────────────
    score = max(0, min(100, score))

    # ── LABEL + RECOMMENDATION ────────────────────────────────────────────────
    if score >= 75:
        label = "Likely Legit"
        color = "#4caf50"
        emoji = "🟢"
        recommendation = (
            f"This listing scores {score}% — solid signals. Before submitting, do a quick Google "
            "of the company name and confirm the apply link domain matches their official site. "
            "If both check out, you're good to apply."
        )
    elif score >= 40:
        label = "Verify Before Applying"
        color = "#ff9800"
        emoji = "🟡"
        recommendation = (
            f"This listing scores {score}% — not obviously fake, but needs a manual check. "
            "Search the company on LinkedIn and Google. Confirm the application link goes "
            "to a legitimate company domain or known ATS system before entering any personal info."
        )
    else:
        label = "Possible Scam"
        color = "#f44336"
        emoji = "🔴"
        recommendation = (
            f"This listing scores {score}% and has multiple red flags. "
            "Do NOT submit personal information until you've independently verified this company is real. "
            "Google the company name + 'scam', check the apply link domain, "
            "and look for an actual company website and LinkedIn page."
        )

    # ── TOP 2 QUICK SIGNALS ────────────────────────────────────────────────────
    top_signals = []
    for f in flags_found[:2]:
        top_signals.append(["🚫", f[:60]])
    remaining = 2 - len(top_signals)
    for b in boosters_found[:remaining]:
        top_signals.append(["✅", b[:60]])

    result = {
        "score":          score,
        "label":          label,
        "color":          color,
        "emoji":          emoji,
        "boosters_found": boosters_found,
        "flags_found":    flags_found,
        "uncertainties":  uncertainties,
        "recommendation": recommendation,
        "top_signals":    top_signals,
        "cached_at":      datetime.datetime.now().isoformat(),
    }

    if key:
        cache[key] = result
        _save_cache(cache)

    return result


def score_jobs_batch(jobs: list) -> dict:
    """Score a list of job dicts. Returns {url: score_dict}."""
    results = {}
    for job in jobs:
        url = (job.get("Application URL") or job.get("url") or "").strip()
        if not url:
            continue
        results[url] = score_job(job)
    return results
