#!/usr/bin/env python3
"""
=============================================================================
  job_feed.py — Daily job search across multiple sources
=============================================================================

HOW TO RUN:
    1. Fill in config.py (API keys, your email for USAJobs, search terms)
    2. Install the two required libraries (one-time setup):
           pip install feedparser requests
    3. Open a terminal in the job-apps folder and run:
           python job_feed.py

WHAT IT DOES:
    - Pulls jobs from Indeed, USAJobs, Adzuna, JSearch (RapidAPI), Remotive,
      RemoteOK, We Work Remotely, and Jobicy RSS/APIs
    - Filters to entry-level IT/security/help-desk roles posted in the last 24h
    - Keeps remote jobs; keeps hybrid only if salary >= $65,000 (configurable)
    - Deduplicates across all sources
    - Prints a summary table to the terminal
    - Saves full results to output/new_jobs_[date].txt
    - Appends new entries to job_tracker.csv

NOTE ON LINKEDIN:
    LinkedIn removed public RSS feeds in 2013 and does not offer a public job
    API without a partnership agreement. The LinkedIn source reports 0 results
    by design. Workaround: create a LinkedIn Job Alert with your search terms
    and receive matching jobs by email each morning.

NOTE ON INDEED:
    Indeed has increasingly restricted automated RSS access. If Indeed returns
    0 results consistently, it is likely rate-limiting or blocking the request.
    This is normal; the other sources will still run.

REQUIREMENTS:
    Python 3.8+
    pip install feedparser requests
=============================================================================
"""

import sys
import os
import csv
import re
import math
import datetime
import time
import json
import urllib.parse
from pathlib import Path

# ── Dependency check with helpful install message ─────────────────────────────
try:
    import feedparser
except ImportError:
    sys.exit(
        "ERROR: feedparser is not installed.\n"
        "Fix:   pip install feedparser requests\n"
        "Then run this script again."
    )

try:
    import requests
except ImportError:
    sys.exit(
        "ERROR: requests is not installed.\n"
        "Fix:   pip install feedparser requests\n"
        "Then run this script again."
    )

# ── Load config.py from the same folder ──────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import config as cfg
except ImportError:
    sys.exit("ERROR: config.py not found. Run this script from inside the job-apps folder.")

OUTPUT_DIR   = SCRIPT_DIR / cfg.OUTPUT_SUBDIR
TRACKER_PATH = SCRIPT_DIR / cfg.TRACKER_FILENAME

# =============================================================================
#  GEO-ELIGIBILITY CONSTANTS
#  Used by _is_us_remote_eligible() to decide whether a remote job is open
#  to US-based workers (specifically Houston / Texas / US-wide).
# =============================================================================

_FOREIGN_COUNTRIES = frozenset({
    "united kingdom", "england", "scotland", "wales", "great britain",
    "canada", "germany", "france", "australia", "netherlands", "spain",
    "italy", "brazil", "india", "ireland", "new zealand", "singapore",
    "japan", "mexico", "poland", "ukraine", "portugal", "sweden",
    "norway", "denmark", "finland", "switzerland", "austria", "belgium",
    "argentina", "colombia", "philippines", "indonesia", "malaysia",
    "south africa", "nigeria", "kenya", "ghana", "egypt", "israel",
    "uae", "united arab emirates", "saudi arabia", "turkey", "russia",
    "china", "hong kong", "taiwan", "south korea", "pakistan",
    "bangladesh", "sri lanka", "nepal", "thailand", "vietnam",
    "czechia", "czech republic", "slovakia", "hungary", "romania",
    "bulgaria", "croatia", "serbia", "latvia", "lithuania", "estonia",
    "greece", "cyprus", "europe", "asia", "africa", "emea",
})

_US_GLOBAL_INDICATORS = frozenset({
    "united states", "usa", "u.s.a.", "u.s.", "us only", "us-based",
    "us remote", "remote us", "us residents", "us candidates",
    "worldwide", "anywhere", "global", "international",
    "north america", "americas", "latin america",
})

# ── Scam blocklists (pulled from config so users can maintain them there) ──────
_SCAM_DOMAINS       = [d.lower() for d in getattr(cfg, "SCAM_DOMAINS",       [])]
_SCAM_COMPANY_NAMES = frozenset(n.lower() for n in getattr(cfg, "SCAM_COMPANY_NAMES", []))


def is_scam_source(job) -> bool:
    """Return True if the job URL or company name matches a known scam source."""
    url     = (job.get("url")     or "").lower().strip()
    company = (job.get("company") or "").lower().strip()
    for domain in _SCAM_DOMAINS:
        if domain in url:
            return True
    return company in _SCAM_COMPANY_NAMES

# If any of these appear in the description the job is US-wide open — passes
# regardless of what the location field says (e.g. HQ is Dallas but job is US-remote)
_US_ANYWHERE_OVERRIDES = (
    "anywhere in the us",
    "anywhere in the u.s",
    "anywhere in the united states",
    "from anywhere in the us",
    "from anywhere in the united states",
    "work remotely from anywhere",
    "work from anywhere in the us",
    "work from anywhere in the u.s",
    "open to candidates throughout the us",
    "throughout the united states",
    "throughout the us",
    "all 50 states",
    "all us states",
    "any u.s. state",
    "any us state",
    "remote anywhere in the us",
    "remote anywhere in the u.s",
    "candidates in all states",
    "open to all us locations",
    "open to us candidates",
)

# Softer remote signals — used when location field names a specific non-Houston US city/state.
# Job must contain at least one of these or a _US_ANYWHERE_OVERRIDE to pass.
_REMOTE_FRIENDLY_SIGNALS = (
    "remote nationwide", "nationwide remote",
    "fully remote", "100% remote", "100 percent remote",
    "remote-first", "remote first",
    "work from home", "work-from-home", "wfh",
    "remote position", "remote opportunity", "remote role", "remote job",
    "this is a remote", "this role is remote", "this position is remote",
    "no location requirement", "location flexible", "location agnostic",
    "work remotely", "working remotely",
)

# Phrases that signal a specific residency requirement in the description
_LOCATION_REQUIRE_PHRASES = (
    "must reside in",
    "must be located in",
    "must live in",
    "must be based in",
    "requires residence in",
    "required to reside in",
    "required to live in",
    "required to be located in",
    "candidates must be in",
    "candidates must reside in",
    "applicants must be in",
    "applicants must reside in",
    "only accepting candidates from",
    "candidates located in",
    "candidates based in",
    "must work from",
    "employee must reside",
    "employee must live",
    "this position requires residence",
    "this role requires residence",
    "candidates must be located",
    "candidate must be located",
    "position requires candidate to be",
    "employees must reside",
    "located in the state of",
    "must be a resident of",
)

# Non-Houston Texas cities — if a residency restriction names one of these, reject
_TX_CITIES_NOT_HOUSTON = re.compile(
    r"\b(dallas|austin|san antonio|fort worth|el paso|arlington|"
    r"corpus christi|plano|laredo|lubbock|garland|irving|amarillo|"
    r"grand prairie|mckinney|frisco|pasadena|killeen|mcallen|mesquite|"
    r"waco|carrollton|midland|round rock|abilene|beaumont|odessa|"
    r"lewisville|sugar land|the woodlands|woodlands|katy|pearland|"
    r"richardson|denton|tyler|wichita falls|san marcos|port arthur|"
    r"allen|league city|edinburg|brownsville|college station|north texas)\b"
)

# CSV column order — job_feed entries use this full schema.
# Columns added here extend the original tracker gracefully.
CSV_FIELDNAMES = [
    "Company",
    "Role",
    "Location",
    "Salary",
    "Date Posted",
    "Date Applied",
    "Application URL",
    "Source",
    "Track",
    "Resume Version Used",
    "Cover Letter Sent (Y/N)",
    "Status",
    "Follow Up Date",
    "Notes",
]


# =============================================================================
#  DATA STRUCTURE
#  All job listings are represented as plain dicts with these keys.
# =============================================================================

def make_job(title, company, location, salary_raw, salary_value,
             date_posted, url, source, description="", track="1"):
    """Return a job dict with all fields normalized to consistent types."""
    return {
        "title":        (title or "").strip(),
        "company":      (company or "").strip(),
        "location":     (location or "").strip(),
        "salary_raw":   salary_raw or "",          # raw string, e.g. "$55,000 – $70,000"
        "salary_value": salary_value,              # int (annual USD) or None
        "date_posted":  date_posted,               # datetime (UTC) or None
        "url":          (url or "").strip(),
        "source":       source,
        "description":  (description or "")[:2000],  # cap length for filtering
        "is_remote":    False,   # set by classify_location()
        "is_hybrid":    False,   # set by classify_location()
        "track":        track,   # "1" (Tech/Cyber) or "2" (Remote Income)
    }


# =============================================================================
#  UTILITY FUNCTIONS
# =============================================================================

def normalize(text):
    """Lowercase and collapse whitespace for consistent comparisons."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()




def _is_hourly_salary(job):
    """Return True if the salary was stated as an hourly rate."""
    raw = (job.get("salary_raw") or "").lower()
    return bool(re.search(r"/\s*hr|per\s*hour|/\s*hour", raw))


# ── Geocoding + proximity helpers ─────────────────────────────────────────────

_geocode_cache: dict = {}

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in miles between two lat/lon points."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _geocode(location_str: str):
    """
    Return (lat, lon) for a location string using Nominatim (free, no key).
    Results are cached in-process so each unique city is only looked up once.
    Returns None on failure.
    """
    key = normalize(location_str)
    if key in _geocode_cache:
        return _geocode_cache[key]
    result = None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location_str, "format": "json", "limit": 1, "countrycodes": "us"},
            headers={"User-Agent": "job-search-agent/1.0 (personal use)"},
            timeout=6,
        )
        data = resp.json()
        if data:
            result = (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        pass
    _geocode_cache[key] = result
    return result


def _within_hybrid_radius(location_str: str) -> bool:
    """
    Return True if location_str geocodes to within HYBRID_MAX_MILES of
    the configured center point (zip 77083, SW Houston TX).
    Falls back to True when geocoding fails so borderline jobs aren't silently
    dropped — Journey's AI scoring will flag the location mismatch.
    """
    if not location_str:
        return False
    loc = normalize(location_str)
    # Fast path: clearly not in Texas → reject without a network call
    if loc and not any(x in loc for x in ("tx", "texas", "houston", "77")):
        return False
    coords = _geocode(location_str)
    if coords is None:
        # Geocoding failed — accept only if location string mentions Houston
        return "houston" in loc
    dist = _haversine_miles(cfg.HYBRID_CENTER_LAT, cfg.HYBRID_CENTER_LON, coords[0], coords[1])
    return dist <= cfg.HYBRID_MAX_MILES


def extract_salary(text):
    """
    Search text for a salary figure and return (raw_string, annual_int).
    Returns (None, None) if no salary pattern is found.
    Converts hourly rates to annual (× 2,080 hours).
    Uses the higher end of a range for the comparison value.
    """
    if not text:
        return None, None

    # Pattern: $XX/hr or $XX per hour
    m = re.search(r"\$(\d+(?:\.\d+)?)\s*(?:/\s*hr|per\s*hour)", text, re.IGNORECASE)
    if m:
        hourly = float(m.group(1))
        return m.group(0).strip(), int(hourly * 2080)

    # Pattern: $XX,XXX – $XX,XXX or $XXk – $XXk (salary range — take the high end)
    m = re.search(
        r"\$\s*(\d[\d,]*)\s*[Kk]?\s*(?:–|-|to)\s*\$?\s*(\d[\d,]*)\s*[Kk]?",
        text, re.IGNORECASE,
    )
    if m:
        raw = m.group(0).strip()
        hi = int(m.group(2).replace(",", ""))
        if hi < 500:     # value is in thousands (e.g., "80k")
            hi *= 1000
        return raw, hi

    # Pattern: single $XX,XXX or $XXk
    m = re.search(
        r"\$\s*(\d[\d,]+)\s*[Kk]?(?:\s*(?:per\s+year|/\s*yr|annually|a\s+year))?",
        text, re.IGNORECASE,
    )
    if m:
        raw = m.group(0).strip()
        val = int(m.group(1).replace(",", ""))
        if val < 500:
            val *= 1000
        if val > 10_000:   # sanity check — ignore implausibly small numbers
            return raw, val

    return None, None


def classify_location(job):
    """
    Detect whether a job is remote or hybrid by scanning location + description.
    Updates job['is_remote'] and job['is_hybrid'] in place.
    If a listing appears both remote and hybrid, remote takes precedence.
    """
    combined = normalize(job["location"] + " " + job["description"][:500])

    remote_hits = [t.lower() for t in cfg.REMOTE_TERMS]
    hybrid_hits = [t.lower() for t in cfg.HYBRID_TERMS]

    job["is_remote"] = any(t in combined for t in remote_hits)
    job["is_hybrid"] = any(t in combined for t in hybrid_hits) and not job["is_remote"]


def matches_query(job):
    """
    Return True if the job satisfies the Boolean search criteria.

    - Must contain at least one ROLE_TERM and no EXCLUDE_TERMs.
    - LEVEL_TERMS are NOT required — a job doesn't need to say "entry level" or
      "junior"; AI scoring determines experience fit (≥60% match threshold).
    - USAJobs: salary cap approximates GS-5–GS-11 entry/junior federal grades.
    """
    full = normalize(job["title"] + " " + job["description"])

    has_role    = any(t.lower() in full for t in cfg.ROLE_TERMS)
    is_excluded = any(t.lower() in full for t in cfg.EXCLUDE_TERMS)

    if not has_role or is_excluded:
        return False

    source = job.get("source", "")

    if source == "USAJobs":
        salary = job.get("salary_value") or 0
        return (salary == 0) or (salary <= cfg.USAJOBS_MAX_SALARY)

    return True


def is_recent(dt, age_hours=None):
    """
    Return True if dt is within age_hours of now (defaults to cfg.MAX_AGE_HOURS).
    Returns True when date is unknown — assume recent rather than silently drop.
    """
    if dt is None:
        return True
    hours = age_hours if age_hours is not None else cfg.MAX_AGE_HOURS
    now = datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (now - dt).total_seconds() <= hours * 3600


def _recency_bucket(dp):
    """0 = posted within 24 h, 1 = 24-48 h, 2 = older or unknown."""
    if not dp:
        return 2
    d = dp if dp.tzinfo else dp.replace(tzinfo=datetime.timezone.utc)
    h = (datetime.datetime.now(datetime.timezone.utc) - d).total_seconds() / 3600
    return 0 if h <= 24 else (1 if h <= 48 else 2)


def _is_us_remote_eligible(job) -> bool:
    """
    Return True if this remote job is open to US-based (Houston/Texas) workers.

    Pass/fail order:
      1. Description contains a US-anywhere override phrase → PASS immediately
      2. Foreign country in location without US/global indicator → FAIL
      3. "remote" in location field → job-board HQ-city convention; trust it, fall through
      4. Location contains a US/global indicator → pass, fall through
      5. Location contains "houston" or Texas statewide → pass, fall through
      6. Location is empty → pass, fall through
      7. Specific non-Houston US city/state in location: require _REMOTE_FRIENDLY_SIGNALS
         in description or FAIL
      8. Residency-restriction phrase scan → FAIL if restriction points outside Houston
    """
    loc  = normalize(job.get("location", ""))
    desc = normalize((job.get("description", "") or "")[:1500])

    # 1. Explicit US-anywhere override (highest priority — beats location field)
    if any(p in desc for p in _US_ANYWHERE_OVERRIDES):
        return True

    # 2. Foreign country in location field without a US/global indicator
    if any(fc in loc for fc in _FOREIGN_COUNTRIES):
        combined_hint = loc + " " + desc[:300]
        if not any(ui in combined_hint for ui in _US_GLOBAL_INDICATORS):
            return False

    # 3-6: location field pass conditions
    if "remote" in loc:
        pass  # "Remote, Austin TX" etc. — job-board HQ-city convention, trust it
    elif any(ui in loc for ui in _US_GLOBAL_INDICATORS):
        pass  # "United States", "US remote", "anywhere", etc.
    elif "houston" in loc:
        pass  # our city
    elif ("texas" in loc or " tx" in loc or "(tx)" in loc) and not _TX_CITIES_NOT_HOUSTON.search(loc):
        pass  # Texas statewide with no specific non-Houston city
    elif not loc:
        pass  # no location listed — assume open
    else:
        # Specific non-Houston US location: require soft remote signals in description
        if not any(p in desc for p in _REMOTE_FRIENDLY_SIGNALS):
            return False

    # 8. Residency restriction scan
    for phrase in _LOCATION_REQUIRE_PHRASES:
        idx = desc.find(phrase)
        if idx == -1:
            continue
        after = desc[idx + len(phrase): idx + len(phrase) + 100].strip()

        if any(ui in after for ui in _US_GLOBAL_INDICATORS):
            continue
        if "houston" in after:
            continue
        if "texas" in after or " tx" in after or "(tx)" in after:
            if not _TX_CITIES_NOT_HOUSTON.search(after):
                continue
        return False

    return True


def passes_location_filter(job):
    """
    Remote: pass always when no salary listed (AI evaluates likely pay);
            pass when salary stated >= REMOTE_SALARY_MINIMUM.
    Hybrid: salary must meet HYBRID_SALARY_MINIMUM or HYBRID_HOURLY_MINIMUM;
            location must be within 30 mi of zip 77083.
    On-site: always dropped.
    """
    if job["is_remote"]:
        if not _is_us_remote_eligible(job):
            return False
        if job["salary_value"] is None:
            return True   # no salary — AI will evaluate company/role/pay likelihood
        return job["salary_value"] >= cfg.REMOTE_SALARY_MINIMUM

    if job["is_hybrid"]:
        if job["salary_value"] is None:
            return False
        if _is_hourly_salary(job):
            passes_salary = (job["salary_value"] / 2080) >= cfg.HYBRID_HOURLY_MINIMUM
        else:
            passes_salary = job["salary_value"] >= cfg.HYBRID_SALARY_MINIMUM
        if not passes_salary:
            return False
        return _within_hybrid_radius(job.get("location", ""))

    return False


def dedup(jobs):
    """
    Remove duplicate listings using two fingerprints:
      1. Normalized URL (strips query strings)
      2. Normalized (title, company) pair
    The first occurrence of each is kept; subsequent duplicates are dropped.
    """
    seen_urls = set()
    seen_tc   = set()
    unique    = []

    for job in jobs:
        url_key = re.sub(r"[?#].*", "", job["url"].lower().rstrip("/"))
        tc_key  = normalize(job["title"] + job["company"])

        if url_key in seen_urls or tc_key in seen_tc:
            continue

        seen_urls.add(url_key)
        seen_tc.add(tc_key)
        unique.append(job)

    return unique


# =============================================================================
#  RSS HELPER
#  feedparser is used for all RSS sources. requests fetches the content first
#  so we can set a proper User-Agent and timeout.
# =============================================================================

def fetch_rss(url, source_label, timeout=15):
    """
    Fetch an RSS feed URL and return a list of feedparser entry objects.
    Returns an empty list on any network or parse error.
    """
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
            timeout=timeout,
        )
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if feed.bozo and not feed.entries:
            return []
        return feed.entries or []
    except Exception as exc:
        print(f"    [{source_label}] Feed error: {exc}")
        return []


def rss_date(entry):
    """Extract a UTC-aware datetime from a feedparser entry, or return None."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                pass
    return None


def strip_html(text):
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", " ", text or "")


# =============================================================================
#  SOURCE: Indeed
#  Uses Indeed's RSS feed. Indeed has become increasingly restrictive with
#  automated access; 0 results or HTTP 403 errors are common and expected.
# =============================================================================

def fetch_indeed():
    jobs      = []
    seen_urls = set()

    # Each query is a targeted phrase — combining role + level + remote
    queries = [
        "IT support entry level remote",
        "help desk entry level remote",
        "service desk junior remote",
        "SOC analyst entry level remote",
        "cybersecurity analyst junior remote",
        "helpdesk tier 1 remote",
    ]

    for query in queries:
        url = (
            "https://www.indeed.com/rss"
            f"?q={urllib.parse.quote(query)}"
            "&sort=date&fromage=1&remotejob=1"
        )
        entries = fetch_rss(url, "Indeed")

        for entry in entries:
            link = getattr(entry, "link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            title   = getattr(entry, "title", "Unknown")
            summary = strip_html(getattr(entry, "summary", ""))
            company = getattr(entry, "author", "")

            # Indeed often formats titles as "Job Title - Company Name"
            if not company and " - " in title:
                title, company = title.rsplit(" - ", 1)

            salary_raw, salary_val = extract_salary(summary + " " + title)

            job = make_job(
                title=title, company=company,
                location=getattr(entry, "location", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=rss_date(entry), url=link,
                source="Indeed", description=summary,
            )
            classify_location(job)
            jobs.append(job)

        time.sleep(0.6)  # be polite between requests

    return jobs


# =============================================================================
#  SOURCE: LinkedIn
#  LinkedIn removed public RSS job feeds in 2013. This source returns 0 results
#  by design. To get LinkedIn jobs, create a Job Alert at linkedin.com/jobs and
#  have matching jobs emailed to you daily.
# =============================================================================

def fetch_linkedin():
    print("    [LinkedIn] No public RSS/API available — skipping.")
    print("    [LinkedIn] Tip: create a LinkedIn Job Alert for your keywords")
    print("               at linkedin.com/jobs → set alert frequency to Daily.")
    return []


# =============================================================================
#  SOURCE: Dice
#  Dice focuses on technology roles and has an RSS feed.
#  Good for IT support, help desk, and entry-level cybersecurity listings.
# =============================================================================

def fetch_dice():
    jobs      = []
    seen_urls = set()

    queries = [
        "IT support entry level",
        "help desk entry level",
        "service desk junior",
        "SOC analyst entry level",
        "cybersecurity analyst junior",
    ]

    for query in queries:
        # Dice RSS URL — hyphenate the query for the slug format
        slug = re.sub(r"\s+", "-", query.lower())
        urls_to_try = [
            f"https://www.dice.com/jobs/q-{urllib.parse.quote(query)}-jobs.rss",
            f"https://www.dice.com/jobs/q-{slug}-jobs.rss",
        ]

        entries = []
        for url in urls_to_try:
            entries = fetch_rss(url, "Dice")
            if entries:
                break

        for entry in entries:
            link = getattr(entry, "link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            title   = getattr(entry, "title", "Unknown")
            summary = strip_html(getattr(entry, "summary", ""))
            company = ""

            # Dice often formats as "Job Title at Company"
            if " at " in title:
                title, company = title.rsplit(" at ", 1)

            # Try to pull location from the summary text
            location = ""
            loc_m = re.search(r"Location[:\s]+([^\n<|]+)", summary, re.IGNORECASE)
            if loc_m:
                location = loc_m.group(1).strip()

            salary_raw, salary_val = extract_salary(summary + " " + title)

            job = make_job(
                title=title, company=company,
                location=location or "",
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=rss_date(entry), url=link,
                source="Dice", description=summary,
            )
            classify_location(job)
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# =============================================================================
#  SOURCE: USAJobs
#  Official US federal government jobs API. No API key required for basic use;
#  your email address must be sent as the User-Agent. Good for federal IT and
#  cybersecurity roles (DoD, DHS, VA, etc.).
# =============================================================================

def fetch_usajobs():
    jobs      = []
    seen_urls = set()

    headers = {
        "Host":       "data.usajobs.gov",
        "User-Agent": cfg.USAJOBS_USER_AGENT,
    }
    if cfg.USAJOBS_AUTH_KEY:
        headers["Authorization-Key"] = cfg.USAJOBS_AUTH_KEY

    search_terms = [
        "IT support",
        "help desk",
        "information technology specialist",
        "SOC analyst",
        "cybersecurity",
        "information security",
    ]

    for term in search_terms:
        params = {
            "Keyword":        term,
            "DatePosted":     max(1, -(-cfg.MAX_AGE_HOURS // 24)),
            "ResultsPerPage": 50,
            "GradeMin":       "05",       # GS-5: entry-level federal IT
            "GradeMax":       "11",       # GS-11: journey-level, accessible with certs/degree
        }

        try:
            resp = requests.get(
                "https://data.usajobs.gov/api/search",
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [USAJobs] Error for '{term}': {exc}")
            continue

        items = data.get("SearchResult", {}).get("SearchResultItems", [])

        for item in items:
            d = item.get("MatchedObjectDescriptor", {})

            # Prefer the Apply URL; fall back to Position URI
            urls = d.get("ApplyURI") or []
            url  = urls[0] if urls else d.get("PositionURI", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Salary — USAJobs returns min/max and the rate interval
            salary_raw, salary_val = None, None
            remunerations = d.get("PositionRemuneration") or []
            if remunerations:
                r = remunerations[0]
                lo   = r.get("MinimumRange", "")
                hi   = r.get("MaximumRange", "")
                rate = r.get("RateIntervalCode", "")
                if lo or hi:
                    salary_raw = f"${lo}–${hi} {rate}".strip()
                    try:
                        salary_val = int(float(hi or lo))
                        if salary_val < 10_000:  # hourly rate
                            salary_val *= 2080
                    except (ValueError, TypeError):
                        pass

            # Date posted
            date_posted = None
            ds = d.get("PublicationStartDate", "")
            if ds:
                try:
                    date_posted = datetime.datetime.fromisoformat(
                        ds.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Location — take the first listed
            locations = d.get("PositionLocation") or [{}]
            location  = locations[0].get("LocationName", "")

            # Description lives inside UserArea
            desc = (
                d.get("UserArea", {})
                 .get("Details", {})
                 .get("JobSummary", "")
            )

            job = make_job(
                title=d.get("PositionTitle", "Unknown"),
                company=d.get("OrganizationName", "US Government"),
                location=location, salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="USAJobs", description=desc,
            )
            classify_location(job)
            jobs.append(job)

        time.sleep(0.3)

    return jobs


# =============================================================================
#  SOURCE: Adzuna
#  Aggregates listings from many boards. Requires ADZUNA_APP_ID and
#  ADZUNA_API_KEY in config.py. Register at https://developer.adzuna.com/
# =============================================================================

def fetch_adzuna():
    # Guard: skip cleanly if the App ID hasn't been configured yet
    if not cfg.ADZUNA_APP_ID or cfg.ADZUNA_APP_ID == "your-app-id-here":
        print("    [Adzuna] ADZUNA_APP_ID not set in config.py — skipping.")
        print("    [Adzuna] Log into developer.adzuna.com → Dashboard → your app")
        print("             and copy the Application ID into config.py.")
        return []

    jobs      = []
    seen_urls = set()

    queries = [
        "IT support remote",
        "help desk remote",
        "service desk remote",
        "SOC analyst remote",
        "cybersecurity analyst remote",
        "technical support remote",
        "desktop support remote",
        "customer support remote",
        "data center technician",
    ]

    for query in queries:
        params = {
            "app_id":          cfg.ADZUNA_APP_ID,
            "app_key":         cfg.ADZUNA_API_KEY,
            "results_per_page": 50,
            "what":            query,
            "where":           "United States",
            "max_days_old":    max(1, -(-cfg.MAX_AGE_HOURS // 24)),
            "sort_by":         "date",
        }

        try:
            resp = requests.get(
                "https://api.adzuna.com/v1/api/jobs/us/search/1",
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [Adzuna] Error for '{query}': {exc}")
            continue

        for item in data.get("results", []):
            url = item.get("redirect_url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # Salary
            salary_raw, salary_val = None, None
            sal_min = item.get("salary_min")
            sal_max = item.get("salary_max")
            if sal_min or sal_max:
                hi  = sal_max or sal_min
                lo  = sal_min or sal_max
                salary_raw = f"${int(lo):,} – ${int(hi):,}"
                salary_val = int(hi)

            # Date posted
            date_posted = None
            created = item.get("created", "")
            if created:
                try:
                    date_posted = datetime.datetime.fromisoformat(
                        created.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            company  = (item.get("company") or {}).get("display_name", "")
            location = (item.get("location") or {}).get("display_name", "")

            job = make_job(
                title=item.get("title", "Unknown"),
                company=company, location=location,
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="Adzuna", description=item.get("description", ""),
            )
            classify_location(job)
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# =============================================================================
#  SOURCE: JSearch (RapidAPI)
#  Aggregates listings from LinkedIn, Indeed, Glassdoor, and ZipRecruiter.
#  Requires JSEARCH_API_KEY in config.py. Free tier: 200 requests/month.
#  Sign up at https://rapidapi.com/ → subscribe to JSearch by letscrape-6bfde5.
# =============================================================================

def fetch_jsearch():
    if not cfg.JSEARCH_API_KEY or cfg.JSEARCH_API_KEY == "your-rapidapi-key-here":
        print("    [JSearch] JSEARCH_API_KEY not set in config.py — skipping.")
        return []

    jobs      = []
    seen_urls = set()

    hours = cfg.MAX_AGE_HOURS
    if hours <= 24:
        date_posted = "today"
    elif hours <= 72:
        date_posted = "3days"
    elif hours <= 168:
        date_posted = "week"
    else:
        date_posted = "month"

    queries = [
        "IT support entry level remote",
        "help desk entry level remote",
        "SOC analyst entry level remote",
        "cybersecurity analyst remote",
        "technical support remote",
    ]

    headers = {
        "Content-Type":   "application/json",
        "x-rapidapi-host": cfg.JSEARCH_API_HOST,
        "x-rapidapi-key":  cfg.JSEARCH_API_KEY,
    }

    for query in queries:
        try:
            resp = requests.get(
                f"https://{cfg.JSEARCH_API_HOST}/search",
                headers=headers,
                params={
                    "query":       query,
                    "num_pages":   "1",
                    "country":     "us",
                    "date_posted": date_posted,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [JSearch] Error for '{query}': {exc}")
            continue

        for item in (data.get("data") or []):
            if not isinstance(item, dict):
                continue
            try:
                url = item.get("job_apply_link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                salary_raw, salary_val = None, None
                sal_min = item.get("job_min_salary")
                sal_max = item.get("job_max_salary")
                if sal_min or sal_max:
                    hi  = sal_max or sal_min
                    lo  = sal_min or sal_max
                    period = (item.get("job_salary_period") or "").lower()
                    salary_raw = f"${int(lo):,} – ${int(hi):,}"
                    salary_val = int(hi * 2080) if "hour" in period else int(hi)

                date_posted_dt = None
                posted_str = item.get("job_posted_at_datetime_utc", "")
                if posted_str:
                    try:
                        date_posted_dt = datetime.datetime.fromisoformat(
                            posted_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                city     = item.get("job_city", "") or ""
                state    = item.get("job_state", "") or ""
                location = f"{city}, {state}".strip(", ") if (city or state) else ""
                if item.get("job_is_remote") and not location:
                    location = "Remote"

                job = make_job(
                    title=item.get("job_title", "Unknown"),
                    company=item.get("employer_name", ""),
                    location=location,
                    salary_raw=salary_raw, salary_value=salary_val,
                    date_posted=date_posted_dt, url=url,
                    source="JSearch",
                    description=item.get("job_description", ""),
                )
                classify_location(job)
                if item.get("job_is_remote"):
                    job["is_remote"] = True
                jobs.append(job)
            except Exception as exc:
                print(f"    [JSearch] Skipped malformed item: {exc}")

        time.sleep(0.5)

    return jobs


# =============================================================================
#  SOURCE: Remotive
#  Free public JSON API — remote tech jobs only. No API key needed.
# =============================================================================

def fetch_remotive():
    jobs      = []
    seen_urls = set()

    queries = [
        "IT support",
        "help desk",
        "service desk",
        "SOC analyst",
        "cybersecurity",
        "technical support",
    ]

    for query in queries:
        try:
            resp = requests.get(
                "https://remotive.com/api/remote-jobs",
                params={"search": query, "limit": 50},
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [Remotive] Error for '{query}': {exc}")
            continue

        for item in data.get("jobs", []):
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            salary_raw, salary_val = extract_salary(item.get("salary", ""))

            date_posted = None
            pub = item.get("publication_date", "")
            if pub:
                try:
                    date_posted = datetime.datetime.fromisoformat(
                        pub.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            job = make_job(
                title=item.get("title", "Unknown"),
                company=item.get("company_name", ""),
                location=item.get("candidate_required_location", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="Remotive",
                description=strip_html(item.get("description", "")),
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# =============================================================================
#  SOURCE: RemoteOK
#  Free public JSON API — remote tech jobs only. No API key needed.
#  Rate-limit: allow 1.5s between requests.
# =============================================================================

def fetch_remoteok():
    jobs      = []
    seen_urls = set()

    tags = ["support", "security", "it"]

    for tag in tags:
        try:
            resp = requests.get(
                f"https://remoteok.com/api?tag={urllib.parse.quote(tag)}",
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [RemoteOK] Error for tag '{tag}': {exc}")
            continue

        # First element is a metadata/legal notice object — skip it
        for item in (data[1:] if isinstance(data, list) and len(data) > 1 else []):
            if not isinstance(item, dict):
                continue

            url = item.get("apply_url") or item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            salary_raw, salary_val = None, None
            sal_min = item.get("salary_min") or 0
            sal_max = item.get("salary_max") or 0
            if sal_max > 0:
                salary_raw = (f"${int(sal_min):,} – ${int(sal_max):,}"
                              if sal_min else f"${int(sal_max):,}")
                salary_val = int(sal_max)

            date_posted = None
            epoch = item.get("epoch")
            if epoch:
                try:
                    date_posted = datetime.datetime.fromtimestamp(
                        int(epoch), tz=datetime.timezone.utc
                    )
                except (ValueError, OSError):
                    pass

            job = make_job(
                title=item.get("position", "Unknown"),
                company=item.get("company", ""),
                location=item.get("location", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="RemoteOK",
                description=strip_html(item.get("description", "")),
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(1.5)

    return jobs


# =============================================================================
#  SOURCE: We Work Remotely
#  RSS feed — remote-only tech jobs. No API key needed.
# =============================================================================

def fetch_weworkremotely():
    jobs      = []
    seen_urls = set()

    queries = [
        "IT support",
        "help desk",
        "cybersecurity",
        "technical support",
    ]

    for query in queries:
        url     = (f"https://weworkremotely.com/remote-jobs/search.rss"
                   f"?term={urllib.parse.quote(query)}")
        entries = fetch_rss(url, "WeWorkRemotely")

        for entry in entries:
            link = getattr(entry, "link", "")
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)

            title   = getattr(entry, "title", "Unknown")
            summary = strip_html(getattr(entry, "summary", ""))

            # WWR title format is often "Company: Job Title"
            company = ""
            if ": " in title:
                parts   = title.split(": ", 1)
                company = parts[0].strip()
                title   = parts[1].strip()

            salary_raw, salary_val = extract_salary(summary + " " + title)

            job = make_job(
                title=title, company=company,
                location="Remote",
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=rss_date(entry), url=link,
                source="WeWorkRemotely",
                description=summary,
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# =============================================================================
#  SOURCE: Jobicy
#  Free public JSON API — remote jobs. No API key needed.
# =============================================================================

def fetch_jobicy():
    jobs      = []
    seen_urls = set()

    tags = ["it", "cybersecurity", "support", "networking"]

    for tag in tags:
        try:
            resp = requests.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={"count": 50, "tag": tag},
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [Jobicy] Error for tag '{tag}': {exc}")
            continue

        for item in data.get("jobs", []):
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            salary_raw, salary_val = None, None
            sal_min = item.get("annualSalaryMin")
            sal_max = item.get("annualSalaryMax")
            if sal_min or sal_max:
                hi  = sal_max or sal_min
                lo  = sal_min or sal_max
                salary_raw = f"${int(lo):,} – ${int(hi):,}"
                salary_val = int(hi)

            date_posted = None
            pub = item.get("pubDate", "")
            if pub:
                try:
                    date_posted = datetime.datetime.fromisoformat(
                        pub.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            job = make_job(
                title=item.get("jobTitle", "Unknown"),
                company=item.get("companyName", ""),
                location=item.get("jobGeo", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="Jobicy",
                description=strip_html(item.get("jobDescription", "")),
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# =============================================================================
#  FILTER PIPELINE
#  Runs all filters in sequence and returns the final sorted, deduped list.
# =============================================================================

def apply_filters(jobs, age_hours=None):
    """
    Runs the full filter chain:
      0. Scam source block (hard-drop before any other work)
      1. Keyword match  (role + level terms, minus excluded terms)
      2. Recency        (posted within age_hours, defaults to cfg.MAX_AGE_HOURS)
      3. Location       (remote always; hybrid only if salary threshold met)
      4. Sort           (24 h first, 24-48 h second, older last; remote before hybrid within bucket)
      5. Deduplication  (by URL and by title+company pair)
    """
    # Step 0: drop known scam sources before scoring
    scam_count = sum(1 for j in jobs if is_scam_source(j))
    if scam_count:
        print(f"  Dropped {scam_count} known-scam listing(s).")
    jobs = [j for j in jobs if not is_scam_source(j)]

    # Step 1: keyword relevance
    jobs = [j for j in jobs if matches_query(j)]

    # Step 2: recency
    jobs = [j for j in jobs if is_recent(j["date_posted"], age_hours)]

    # Step 3: location requirement
    jobs = [j for j in jobs if passes_location_filter(j)]

    # Step 4: sort — 24 h newest first, then 24-48 h, then older; remote before hybrid within each bucket
    jobs.sort(key=lambda j: (
        _recency_bucket(j["date_posted"]),
        0 if j["is_remote"] else 1,
        -(j["date_posted"].timestamp() if j["date_posted"] else 0),
    ))

    # Step 5: dedup
    jobs = dedup(jobs)

    return jobs


# =============================================================================
#  OUTPUT FUNCTIONS
# =============================================================================

def fmt_date(dt):
    """Format a datetime for display; return 'Unknown' if None."""
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "Unknown"


def print_summary(jobs_by_source, filtered_jobs):
    """Print a formatted results table to the terminal."""
    source_counts = {}
    for j in filtered_jobs:
        source_counts[j["source"]] = source_counts.get(j["source"], 0) + 1

    print("\n" + "=" * 58)
    print("  JOB SEARCH RESULTS")
    print("=" * 58)
    print(f"  {'Source':<14}  {'Raw fetched':>12}  {'After filters':>13}")
    print("  " + "-" * 44)

    total_raw = 0
    for source_label, raw_list in jobs_by_source.items():
        n_raw      = len(raw_list)
        n_filtered = source_counts.get(source_label, 0)
        total_raw += n_raw
        print(f"  {source_label:<14}  {n_raw:>12}  {n_filtered:>13}")

    print("  " + "-" * 44)
    print(f"  {'TOTAL':<14}  {total_raw:>12}  {len(filtered_jobs):>13}")
    print("=" * 58)

    if filtered_jobs:
        n_remote = sum(1 for j in filtered_jobs if j["is_remote"])
        n_hybrid = sum(1 for j in filtered_jobs if j["is_hybrid"])
        print(f"  Remote: {n_remote}   Qualifying hybrid: {n_hybrid}")
        print("=" * 58)


def save_to_file(jobs, date_str):
    """
    Write full job details to output/new_jobs_[date].txt.
    Returns the output path.
    """
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"new_jobs_{date_str}.txt"

    lines = [
        f"Job Feed Results — {date_str}",
        f"Generated:  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total jobs: {len(jobs)}",
        "=" * 70,
        "",
    ]

    for i, job in enumerate(jobs, 1):
        loc_tag = "[REMOTE]" if job["is_remote"] else "[HYBRID]" if job["is_hybrid"] else ""
        lines += [
            f"[{i:>3}]  {job['title']}",
            f"       Company:  {job['company'] or 'Unknown'}",
            f"       Location: {job['location'] or 'Remote'}  {loc_tag}",
            f"       Salary:   {job['salary_raw'] or 'Not listed'}",
            f"       Posted:   {fmt_date(job['date_posted'])}",
            f"       Source:   {job['source']}",
            f"       URL:      {job['url']}",
            "",
        ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def append_to_tracker(jobs):
    """
    Append new (non-duplicate) jobs to job_tracker.csv.
    Skips any job whose URL already appears in the file.
    Migrates existing rows to the new column schema (old columns are preserved
    where they match; unmapped columns are left blank).
    Returns the count of newly added rows.
    """
    # Build a set of URLs already in the tracker
    existing_urls = set()
    existing_rows = []
    existing_fieldnames = []
    tracker_exists = TRACKER_PATH.exists()

    if tracker_exists:
        try:
            with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                existing_fieldnames = list(reader.fieldnames or [])
                for row in reader:
                    existing_rows.append(row)
                    url = row.get("Application URL", "").strip().lower().rstrip("/")
                    if url:
                        existing_urls.add(url)
        except Exception as exc:
            print(f"  Warning: could not read {TRACKER_PATH}: {exc}")

    # Build new rows for jobs not already tracked
    new_rows = []
    for job in jobs:
        url_key = job["url"].lower().rstrip("/")
        if url_key in existing_urls:
            continue
        existing_urls.add(url_key)

        location = (
            job["location"]
            or ("Remote" if job["is_remote"] else "Hybrid" if job["is_hybrid"] else "")
        )

        new_rows.append({
            "Company":               job["company"] or "",
            "Role":                  job["title"],
            "Location":              location,
            "Salary":                job["salary_raw"] or "",
            "Date Posted":           fmt_date(job["date_posted"]) if job["date_posted"] else "",
            "Date Applied":          "",
            "Application URL":       job["url"],
            "Source":                job["source"],
            "Track":                 job.get("track", "1"),
            "Resume Version Used":   "",
            "Cover Letter Sent (Y/N)": "N",
            "Status":                "To Apply",
            "Follow Up Date":        "",
            "Notes":                 "",
        })

    if not new_rows:
        return 0

    # Rewrite the CSV preserving any extra columns already in the file
    # (e.g. AI Score, AI Recommendation added by job_agent.py) so previously-
    # scored jobs don't lose their data when new jobs are appended.
    all_fieldnames = list(dict.fromkeys(CSV_FIELDNAMES + existing_fieldnames))
    all_rows = existing_rows + new_rows
    with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    return len(new_rows)


# =============================================================================
#  TRACK 2 — REMOTE INCOME FEED
#  Fetches non-tech remote roles from JSearch, Remotive, RemoteOK, and Jobicy.
#  All T2 jobs have track="2" set; none of the T1 filter logic is applied here.
# =============================================================================

def t2_phone_status(job):
    """
    Return "excluded" | "unclear" | "no_phone" based on T2_PHONE_EXCLUDE_TERMS.
    "excluded" means a hard phone term was found — job should be dropped.
    "unclear" means generic phone language was found — flag with a badge.
    "no_phone" means no phone language found — clean.
    """
    full = normalize(job.get("title", "") + " " + job.get("description", ""))
    for term in cfg.T2_PHONE_EXCLUDE_TERMS:
        if term.lower() in full:
            return "excluded"
    if any(w in full for w in ("phone", " call ", "calls", "telephon")):
        return "unclear"
    return "no_phone"


def t2_remote_status(job):
    """Return "confirmed" if is_remote is True, else "ambiguous"."""
    return "confirmed" if job.get("is_remote") else "ambiguous"


def t2_salary_status(job):
    """Return "confirmed" | "below_min" | "unlisted" for T2 salary thresholds."""
    sal = job.get("salary_value")
    if sal is None:
        return "unlisted"
    hourly = _is_hourly_salary(job)
    if hourly:
        return "confirmed" if (sal / 2080) >= cfg.T2_HOURLY_MINIMUM else "below_min"
    return "confirmed" if sal >= cfg.T2_SALARY_MINIMUM else "below_min"


# ── T2 source: JSearch ────────────────────────────────────────────────────────

def fetch_t2_jsearch():
    """Fetch Track 2 jobs from JSearch using T2_JSEARCH_QUERIES."""
    if not cfg.JSEARCH_API_KEY or cfg.JSEARCH_API_KEY == "your-rapidapi-key-here":
        return []

    jobs      = []
    seen_urls = set()

    hours = cfg.MAX_AGE_HOURS
    if hours <= 24:
        date_posted = "today"
    elif hours <= 72:
        date_posted = "3days"
    elif hours <= 168:
        date_posted = "week"
    else:
        date_posted = "month"

    headers = {
        "Content-Type":    "application/json",
        "x-rapidapi-host": cfg.JSEARCH_API_HOST,
        "x-rapidapi-key":  cfg.JSEARCH_API_KEY,
    }

    for query in getattr(cfg, "T2_JSEARCH_QUERIES", []):
        try:
            resp = requests.get(
                f"https://{cfg.JSEARCH_API_HOST}/search",
                headers=headers,
                params={
                    "query":       query + " remote",
                    "num_pages":   "1",
                    "country":     "us",
                    "date_posted": date_posted,
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [T2-JSearch] Error for '{query}': {exc}")
            continue

        for item in (data.get("data") or []):
            if not isinstance(item, dict):
                continue
            try:
                url = item.get("job_apply_link", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                salary_raw, salary_val = None, None
                sal_min = item.get("job_min_salary")
                sal_max = item.get("job_max_salary")
                if sal_min or sal_max:
                    hi  = sal_max or sal_min
                    lo  = sal_min or sal_max
                    period = (item.get("job_salary_period") or "").lower()
                    salary_raw = f"${int(lo):,} – ${int(hi):,}"
                    salary_val = int(hi * 2080) if "hour" in period else int(hi)

                date_posted_dt = None
                posted_str = item.get("job_posted_at_datetime_utc", "")
                if posted_str:
                    try:
                        date_posted_dt = datetime.datetime.fromisoformat(
                            posted_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                city     = item.get("job_city", "") or ""
                state    = item.get("job_state", "") or ""
                location = f"{city}, {state}".strip(", ") if (city or state) else ""
                if item.get("job_is_remote") and not location:
                    location = "Remote"

                job = make_job(
                    title=item.get("job_title", "Unknown"),
                    company=item.get("employer_name", ""),
                    location=location,
                    salary_raw=salary_raw, salary_value=salary_val,
                    date_posted=date_posted_dt, url=url,
                    source="JSearch",
                    description=item.get("job_description", ""),
                    track="2",
                )
                classify_location(job)
                if item.get("job_is_remote"):
                    job["is_remote"] = True
                jobs.append(job)
            except Exception as exc:
                print(f"    [T2-JSearch] Skipped malformed item: {exc}")

        time.sleep(0.5)

    return jobs


# ── T2 source: Remotive ───────────────────────────────────────────────────────

def fetch_t2_remotive():
    """Fetch Track 2 jobs from Remotive using a broader keyword set."""
    jobs      = []
    seen_urls = set()

    t2_queries = ["data entry", "billing", "virtual assistant", "data analyst",
                  "administrative", "bookkeeping", "paralegal", "technical writer"]

    for query in t2_queries:
        try:
            resp = requests.get(
                "https://remotive.com/api/remote-jobs",
                params={"search": query, "limit": 50},
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [T2-Remotive] Error for '{query}': {exc}")
            continue

        for item in data.get("jobs", []):
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            salary_raw, salary_val = extract_salary(item.get("salary", ""))
            date_posted = None
            pub = item.get("publication_date", "")
            if pub:
                try:
                    date_posted = datetime.datetime.fromisoformat(
                        pub.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            job = make_job(
                title=item.get("title", "Unknown"),
                company=item.get("company_name", ""),
                location=item.get("candidate_required_location", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="Remotive",
                description=strip_html(item.get("description", "")),
                track="2",
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# ── T2 source: RemoteOK ───────────────────────────────────────────────────────

def fetch_t2_remoteok():
    """Fetch Track 2 jobs from RemoteOK using T2-relevant tags."""
    jobs      = []
    seen_urls = set()

    for tag in ["admin", "finance", "legal", "analyst", "coordinator"]:
        try:
            resp = requests.get(
                f"https://remoteok.com/api?tag={urllib.parse.quote(tag)}",
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [T2-RemoteOK] Error for tag '{tag}': {exc}")
            continue

        for item in (data[1:] if isinstance(data, list) and len(data) > 1 else []):
            if not isinstance(item, dict):
                continue
            url = item.get("apply_url") or item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            salary_raw, salary_val = None, None
            sal_min = item.get("salary_min") or 0
            sal_max = item.get("salary_max") or 0
            if sal_max > 0:
                salary_raw = (f"${int(sal_min):,} – ${int(sal_max):,}"
                              if sal_min else f"${int(sal_max):,}")
                salary_val = int(sal_max)

            date_posted = None
            epoch = item.get("epoch")
            if epoch:
                try:
                    date_posted = datetime.datetime.fromtimestamp(
                        int(epoch), tz=datetime.timezone.utc
                    )
                except (ValueError, OSError):
                    pass

            job = make_job(
                title=item.get("position", "Unknown"),
                company=item.get("company", ""),
                location=item.get("location", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="RemoteOK",
                description=strip_html(item.get("description", "")),
                track="2",
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(1.5)

    return jobs


# ── T2 source: Jobicy ─────────────────────────────────────────────────────────

def fetch_t2_jobicy():
    """Fetch Track 2 jobs from Jobicy using T2-relevant tags."""
    jobs      = []
    seen_urls = set()

    for tag in ["admin", "finance", "legal", "writing", "analyst"]:
        try:
            resp = requests.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={"count": 50, "tag": tag},
                headers={"User-Agent": "Mozilla/5.0 (compatible; job-search-bot/1.0)"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"    [T2-Jobicy] Error for tag '{tag}': {exc}")
            continue

        for item in data.get("jobs", []):
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            salary_raw, salary_val = None, None
            sal_min = item.get("annualSalaryMin")
            sal_max = item.get("annualSalaryMax")
            if sal_min or sal_max:
                hi  = sal_max or sal_min
                lo  = sal_min or sal_max
                salary_raw = f"${int(lo):,} – ${int(hi):,}"
                salary_val = int(hi)

            date_posted = None
            pub = item.get("pubDate", "")
            if pub:
                try:
                    date_posted = datetime.datetime.fromisoformat(
                        pub.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            job = make_job(
                title=item.get("jobTitle", "Unknown"),
                company=item.get("companyName", ""),
                location=item.get("jobGeo", "Remote"),
                salary_raw=salary_raw, salary_value=salary_val,
                date_posted=date_posted, url=url,
                source="Jobicy",
                description=strip_html(item.get("jobDescription", "")),
                track="2",
            )
            job["is_remote"] = True
            jobs.append(job)

        time.sleep(0.5)

    return jobs


# ── T2 filter pipeline ────────────────────────────────────────────────────────

def apply_t2_filters(jobs, age_hours=None):
    """
    Filter pipeline for Track 2 jobs:
    0. Scam source block
    1. Remote-only hard filter
    2. US remote eligibility (same geo logic as T1)
    3. Phone exclusion (drop jobs with hard phone terms)
    4. Salary: drop only if salary stated below minimum; no-salary jobs pass (flagged later)
    5. Recency (same age_hours as T1)
    6. T2 keyword relevance — title or description must mention at least one T2 keyword
    7. Dedup
    8. Sort — 24 h newest first, then 24-48 h, then older
    """
    t2_kw = [k.lower() for k in getattr(cfg, "T2_KEYWORDS", [])]

    scam_count = sum(1 for j in jobs if is_scam_source(j))
    if scam_count:
        print(f"  [T2] Dropped {scam_count} known-scam listing(s).")
    jobs = [j for j in jobs if not is_scam_source(j)]

    filtered = []
    for job in jobs:
        # 1. Remote-only
        if not job.get("is_remote"):
            continue

        # 2. US remote eligibility
        if not _is_us_remote_eligible(job):
            continue

        # 3. Hard phone exclusion
        if t2_phone_status(job) == "excluded":
            continue

        # 4. Salary: if stated and below minimum, drop
        sal_st = t2_salary_status(job)
        if sal_st == "below_min":
            continue

        # 5. Recency
        if not is_recent(job["date_posted"], age_hours):
            continue

        # 6. T2 keyword relevance (very broad — title or description contains any T2 keyword)
        full = normalize(job.get("title", "") + " " + job.get("description", ""))
        if t2_kw and not any(kw in full for kw in t2_kw):
            continue

        filtered.append(job)

    # 7. Dedup (within T2)
    filtered = dedup(filtered)

    # 8. Sort — 24 h newest first, then 24-48 h, then older
    filtered.sort(key=lambda j: (
        _recency_bucket(j["date_posted"]),
        -(j["date_posted"].timestamp() if j["date_posted"] else 0),
    ))

    return filtered


# ── T2 main entry point ───────────────────────────────────────────────────────

def get_t2_jobs(age_hours=None, exclude_urls=None):
    """
    Run the full Track 2 fetch + filter pipeline.
    Returns a list of T2 job dicts (track="2").
    exclude_urls: optional set of URL strings to skip (for cross-track dedup).
    """
    fetchers = [fetch_t2_jsearch, fetch_t2_remotive, fetch_t2_remoteok, fetch_t2_jobicy]
    all_raw  = []

    labels = ["T2-JSearch", "T2-Remotive", "T2-RemoteOK", "T2-Jobicy"]
    for label, fetcher in zip(labels, fetchers):
        print(f"  Fetching {label}...")
        try:
            raw = fetcher()
        except Exception as exc:
            print(f"  [{label}] Error: {exc}")
            raw = []
        all_raw.extend(raw)
        print(f"  [{label}] {len(raw)} raw listing(s)")

    hours    = age_hours if age_hours is not None else cfg.MAX_AGE_HOURS
    filtered = apply_t2_filters(all_raw, age_hours=hours)

    if exclude_urls:
        filtered = [j for j in filtered if j["url"] not in exclude_urls]

    return filtered


# =============================================================================
#  MAIN
# =============================================================================

SOURCE_LABELS = {
    "indeed":          "Indeed",
    "linkedin":        "LinkedIn",
    "dice":            "Dice",
    "usajobs":         "USAJobs",
    "adzuna":          "Adzuna",
    "jsearch":         "JSearch",
    "remotive":        "Remotive",
    "remoteok":        "RemoteOK",
    "weworkremotely":  "WeWorkRemotely",
    "jobicy":          "Jobicy",
}


def main():
    today_str = datetime.date.today().isoformat()
    print(f"\n=== Job Feed  |  {today_str} ===")
    print(f"Filters: last {cfg.MAX_AGE_HOURS}h  |  remote min ${cfg.REMOTE_SALARY_MINIMUM:,}  |  hybrid min ${cfg.HYBRID_SALARY_MINIMUM:,}\n")

    # Map source names to their fetcher functions
    fetchers = {
        "indeed":          fetch_indeed,
        "linkedin":        fetch_linkedin,
        "dice":            fetch_dice,
        "usajobs":         fetch_usajobs,
        "adzuna":          fetch_adzuna,
        "jsearch":         fetch_jsearch,
        "remotive":        fetch_remotive,
        "remoteok":        fetch_remoteok,
        "weworkremotely":  fetch_weworkremotely,
        "jobicy":          fetch_jobicy,
    }

    jobs_by_source: dict = {}
    all_raw: list = []

    for name, fetcher in fetchers.items():
        label = SOURCE_LABELS.get(name, name.capitalize())

        if not cfg.SOURCES.get(name, True):
            print(f"  [{label}] Disabled in config.py — skipping.")
            jobs_by_source[label] = []
            continue

        print(f"  Fetching {label}...")
        raw = fetcher()
        jobs_by_source[label] = raw
        all_raw.extend(raw)
        print(f"  [{label}] {len(raw)} raw listing(s) fetched.")

    # Run the filter pipeline
    print("\n  Applying filters (keywords → recency → location → dedup)...")
    filtered = apply_filters(all_raw)

    # Terminal summary
    print_summary(jobs_by_source, filtered)

    if not filtered:
        print("\n  No jobs matched today's filters.")
        print("  Suggestions:")
        print("    - Add more terms to LEVEL_TERMS in config.py")
        print("    - Increase MAX_AGE_HOURS (e.g., 48)")
        print("    - Check that USAJOBS_USER_AGENT is your email address")
        print()
        return

    # Save output file
    out_path = save_to_file(filtered, today_str)
    print(f"\n  Saved {len(filtered)} job(s) to: {out_path}")

    # Append to tracker
    added = append_to_tracker(filtered)
    print(f"  Added {added} new entry/entries to: {TRACKER_PATH}")
    print()


if __name__ == "__main__":
    main()
