#!/usr/bin/env python3
"""
apify_feed.py — Apify actor job fetchers for Job Apps Toolkit
=============================================================================
Adds five scraped sources on top of the existing feed:
  LinkedIn Jobs, Indeed, Glassdoor, Google Jobs, Dice

Results are cached to logs/apify_cache.json so job_agent.py can merge them
without waiting for actor runs every time. Trigger a fresh run via the
dashboard "RUN APIFY NOW" button or by calling fetch_all() directly.

Token setup: checks .env file → prompts interactively on first terminal run.
=============================================================================
"""

import os
import sys
import re
import json
import time
import datetime
import urllib.parse
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# ── Dependency check ──────────────────────────────────────────────────────────

try:
    from apify_client import ApifyClient
    _APIFY_AVAILABLE = True
except ImportError:
    _APIFY_AVAILABLE = False

# Load sibling modules (job_feed utilities, config)
sys.path.insert(0, str(SCRIPT_DIR))
try:
    import job_feed as _jf
    import config as cfg
    _MODULES_OK = True
except ImportError:
    _MODULES_OK = False

# ── Paths ─────────────────────────────────────────────────────────────────────

LOGS_DIR         = SCRIPT_DIR / "logs"
ENV_PATH         = SCRIPT_DIR / ".env"
APIFY_CACHE_PATH = LOGS_DIR / "apify_cache.json"   # combined summary (backward compat)
APIFY_USAGE_PATH = LOGS_DIR / "apify_usage.json"
APIFY_RUN_PATH   = LOGS_DIR / "apify_run.json"

# ── Per-source cache key → label mapping ──────────────────────────────────────

_LABEL_TO_KEY = {
    "LinkedIn (Apify)":    "linkedin",
    "Indeed (Apify)":      "indeed",
    "Glassdoor (Apify)":   "glassdoor",
    "Google Jobs (Apify)": "google_jobs",
    "Dice (Apify)":        "dice",
}
_KEY_TO_LABEL = {v: k for k, v in _LABEL_TO_KEY.items()}

# Per-source keys in fetch order (mirrors _SOURCES registry at bottom)
_SOURCE_KEYS = ["linkedin", "indeed", "glassdoor", "google_jobs", "dice"]


def _source_cache_path(key: str) -> Path:
    return LOGS_DIR / f"apify_cache_{key}.json"

# ── .env helpers ──────────────────────────────────────────────────────────────

def _load_env() -> dict:
    """Load key=value pairs from .env, return as dict."""
    vals = {}
    if not ENV_PATH.exists():
        return vals
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    return vals


def _save_env_key(key: str, value: str):
    """Add or update a single key in .env without touching other lines."""
    lines = []
    found = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    content = "\n".join(lines)
    if not content.endswith("\n"):
        content += "\n"
    ENV_PATH.write_text(content, encoding="utf-8")


# ── Token management ──────────────────────────────────────────────────────────

def get_apify_token() -> str:
    """Return Apify API token from env var or .env file. Empty str if unset."""
    token = os.environ.get("APIFY_API_TOKEN", "").strip()
    if token:
        return token
    return _load_env().get("APIFY_API_TOKEN", "").strip()


def save_apify_token(token: str):
    """Persist token to .env and current process environment."""
    token = token.strip()
    _save_env_key("APIFY_API_TOKEN", token)
    os.environ["APIFY_API_TOKEN"] = token


def prompt_for_token() -> str:
    """
    Interactive terminal prompt for the Apify API token.
    Prints a setup guide, saves on success. Returns token or "".
    """
    print("\n" + "=" * 62)
    print("  APIFY SETUP — First Run")
    print("=" * 62)
    print("""
  Apify scrapes LinkedIn, Indeed, Glassdoor, Google Jobs, and Dice
  to add hundreds of additional listings on top of your existing feed.

  GET YOUR FREE API TOKEN:
    1. Go to  https://console.apify.com/
    2. Sign up (free account — no credit card required)
    3. Avatar → Settings → Integrations → API tokens
    4. Click "Create API token" and copy it

  FREE TIER: $5 monthly credit — enough for ~50–100 actor runs.
  Apify charges per actor compute unit, not per call.

  SKIP: Press Enter to skip. Add the token later from Journey's
        dashboard → Apify panel → SET TOKEN, or add it directly
        to the .env file as:  APIFY_API_TOKEN=apify_api_...
""")
    try:
        token = input("  Paste your Apify API token (or Enter to skip): ").strip()
    except (EOFError, KeyboardInterrupt):
        token = ""

    if token:
        save_apify_token(token)
        print(f"  Token saved to {ENV_PATH.name}")
    else:
        print("  Skipped — Apify sources disabled until token is set.")
    print("=" * 62 + "\n")
    return token


# ── Usage tracking ────────────────────────────────────────────────────────────

def _load_usage() -> dict:
    month = datetime.date.today().strftime("%Y-%m")
    default = {"month": month, "total_runs": 0, "actors": {}}
    if APIFY_USAGE_PATH.exists():
        try:
            data = json.loads(APIFY_USAGE_PATH.read_text(encoding="utf-8"))
            if data.get("month") == month:
                return data
        except Exception:
            pass
    return default


def _save_usage(data: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    APIFY_USAGE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _record_actor_run(source_key: str, item_count: int, error: str = ""):
    usage = _load_usage()
    usage["total_runs"] = usage.get("total_runs", 0) + 1
    entry = usage["actors"].setdefault(source_key, {
        "runs": 0, "items": 0, "last_run": "", "last_error": ""
    })
    entry["runs"]       += 1
    entry["items"]      += item_count
    entry["last_run"]    = datetime.datetime.now().isoformat()
    entry["last_error"]  = error
    _save_usage(usage)


def get_usage() -> dict:
    """Return the current month's Apify usage dict."""
    return _load_usage()


# ── Last-run log ──────────────────────────────────────────────────────────────

def _load_runlog() -> dict:
    if APIFY_RUN_PATH.exists():
        try:
            return json.loads(APIFY_RUN_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_runlog(data: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    APIFY_RUN_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_last_run_info() -> dict:
    """Return info about the last Apify fetch run."""
    return _load_runlog()


# ── Cache (apify_cache.json) ──────────────────────────────────────────────────

def _serialize_job(job: dict) -> dict:
    """Convert datetime fields to ISO strings for JSON storage."""
    j = dict(job)
    dp = j.get("date_posted")
    if isinstance(dp, datetime.datetime):
        j["date_posted"] = dp.isoformat()
    elif dp is None:
        j["date_posted"] = ""
    return j


def _deserialize_job(data: dict) -> dict:
    """Restore datetime fields from ISO strings."""
    j = dict(data)
    raw_date = j.get("date_posted", "")
    if raw_date:
        try:
            j["date_posted"] = datetime.datetime.fromisoformat(raw_date)
        except ValueError:
            j["date_posted"] = None
    else:
        j["date_posted"] = None
    return j


def save_source_cache(key: str, jobs: list, source_errors: dict = None):
    """Save one source's results to its own per-source cache file."""
    LOGS_DIR.mkdir(exist_ok=True)
    payload = {
        "source":    key,
        "date":      datetime.date.today().isoformat(),
        "timestamp": datetime.datetime.now().isoformat(),
        "jobs":      [_serialize_job(j) for j in jobs],
        "errors":    source_errors or {},
    }
    _source_cache_path(key).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _apply_remote_heuristic(jobs: list) -> list:
    """Re-apply remote detection in place (for cache loads where desc may be missing)."""
    for job in jobs:
        if not job.get("is_remote"):
            if _MODULES_OK:
                _jf.classify_location(job)
            if not job.get("is_remote"):
                title_lc = (job.get("title") or "").lower()
                loc_lc   = (job.get("location") or "").lower()
                if ("remote" in title_lc or "united states" in loc_lc or
                        loc_lc in ("remote", "anywhere", "us", "usa")):
                    job["is_remote"] = True
    return jobs


def load_source_cache(key: str) -> tuple:
    """Load one source's per-source cache. Returns (jobs, errors, timestamp_str)."""
    path = _source_cache_path(key)
    if not path.exists():
        return [], {}, ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        jobs = [_deserialize_job(j) for j in data.get("jobs", [])]
        return _apply_remote_heuristic(jobs), data.get("errors", {}), data.get("timestamp", "")
    except Exception:
        return [], {}, ""


def save_cache(jobs: list, errors: dict):
    """
    Write Apify results. Splits into per-source files and writes a combined
    summary to apify_cache.json for dashboard stats / backward compatibility.
    """
    LOGS_DIR.mkdir(exist_ok=True)

    # Write per-source files
    by_key = {}
    for j in jobs:
        key = _LABEL_TO_KEY.get(j.get("source", ""), "unknown")
        by_key.setdefault(key, []).append(j)
    for key, src_jobs in by_key.items():
        if key != "unknown":
            label = _KEY_TO_LABEL.get(key, key)
            save_source_cache(key, src_jobs, {label: errors.get(label, "")})

    # Write combined summary
    payload = {
        "date":      datetime.date.today().isoformat(),
        "timestamp": datetime.datetime.now().isoformat(),
        "jobs":      [_serialize_job(j) for j in jobs],
        "errors":    errors,
    }
    APIFY_CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_cache() -> tuple:
    """
    Load and merge all per-source caches. Falls back to the combined
    apify_cache.json if no per-source files exist.
    Returns (jobs, errors, timestamp_str).
    """
    per_source_exists = any(_source_cache_path(k).exists() for k in _SOURCE_KEYS)

    if per_source_exists:
        all_jobs, all_errors, latest_ts = [], {}, ""
        for key in _SOURCE_KEYS:
            jobs, errs, ts = load_source_cache(key)
            all_jobs.extend(jobs)
            all_errors.update(errs)
            if ts and (not latest_ts or ts > latest_ts):
                latest_ts = ts
        return all_jobs, all_errors, latest_ts

    # Fallback: combined file
    if not APIFY_CACHE_PATH.exists():
        return [], {}, ""
    try:
        data = json.loads(APIFY_CACHE_PATH.read_text(encoding="utf-8"))
        jobs = [_deserialize_job(j) for j in data.get("jobs", [])]
        return _apply_remote_heuristic(jobs), data.get("errors", {}), data.get("timestamp", "")
    except Exception:
        return [], {}, ""


def cache_is_stale(max_hours: int = 24) -> bool:
    """
    Return False (not stale) if any per-source cache file is within max_hours.
    Falls back to the combined file if no per-source files exist.
    """
    now = datetime.datetime.now()
    for key in _SOURCE_KEYS:
        path = _source_cache_path(key)
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ts = data.get("timestamp", "")
            if ts and (now - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600 < max_hours:
                return False
        except Exception:
            continue

    # Check combined fallback
    if APIFY_CACHE_PATH.exists():
        try:
            data = json.loads(APIFY_CACHE_PATH.read_text(encoding="utf-8"))
            ts = data.get("timestamp", "")
            if ts and (now - datetime.datetime.fromisoformat(ts)).total_seconds() / 3600 < max_hours:
                return False
        except Exception:
            pass
    return True


# ── Config helpers ────────────────────────────────────────────────────────────

def _actor_id(key: str, fallback: str) -> str:
    """Return actor ID from config.APIFY_ACTOR_IDS, or fallback."""
    if _MODULES_OK:
        ids = getattr(cfg, "APIFY_ACTOR_IDS", {})
        return ids.get(key, fallback)
    return fallback


def _t2_queries() -> list:
    """Return Track 2 keyword strings from config."""
    if _MODULES_OK:
        return list(getattr(cfg, "APIFY_T2_QUERIES", []))
    return []


# ── Actor helper ──────────────────────────────────────────────────────────────

def _run_actor(client: "ApifyClient", actor_id: str, run_input: dict,
               timeout_secs: int = 300) -> list:
    """
    Run an Apify actor synchronously and return dataset items.
    Raises RuntimeError on failure so the caller can log and continue.
    """
    run = client.actor(actor_id).call(run_input=run_input, timeout_secs=timeout_secs)
    items = client.dataset(run["defaultDatasetId"]).list_items().items
    return items or []


def _field(item: dict, *keys, default="") -> str:
    """Try multiple field names; return first non-empty string value."""
    for k in keys:
        # Support dot-notation for nested fields e.g. "company.name"
        if "." in k:
            parts = k.split(".", 1)
            sub = item.get(parts[0])
            if isinstance(sub, dict):
                val = sub.get(parts[1], "")
            else:
                val = ""
        else:
            val = item.get(k, "")
        if val and str(val).strip():
            return str(val).strip()
    return default


def _parse_date(item: dict, *keys) -> "datetime.datetime | None":
    """Try multiple date field names and return a UTC-aware datetime or None."""
    for k in keys:
        raw = item.get(k)
        if not raw:
            continue
        if isinstance(raw, (int, float)):
            try:
                return datetime.datetime.fromtimestamp(raw, tz=datetime.timezone.utc)
            except (ValueError, OSError):
                continue
        try:
            s = str(raw).strip().replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(s)
        except ValueError:
            continue
    return None


def _make(item: dict, title_keys, company_keys, location_keys,
          desc_keys, url_keys, salary_keys, date_keys, source: str) -> "dict | None":
    """
    Build a standard job dict from an Apify item using prioritized field lists.
    Returns None if no URL found (unusable listing).
    """
    url = _field(item, *url_keys)
    if not url:
        return None

    title   = _field(item, *title_keys) or "Unknown"
    company = _field(item, *company_keys)
    loc     = _field(item, *location_keys)
    desc    = _jf.strip_html(_field(item, *desc_keys))

    # Salary: try dedicated salary fields first, then description
    sal_raw, sal_val = _jf.extract_salary(_field(item, *salary_keys))
    if not sal_raw:
        sal_raw, sal_val = _jf.extract_salary(desc[:500])

    date_posted = _parse_date(item, *date_keys)

    job = _jf.make_job(
        title=title, company=company, location=loc,
        salary_raw=sal_raw, salary_value=sal_val,
        date_posted=date_posted, url=url,
        source=source, description=desc,
    )
    _jf.classify_location(job)
    return job


# ── Source: LinkedIn Jobs Scraper (curious_coder) ─────────────────────────────
# Actor takes LinkedIn search URLs directly. Builds URLs from keyword lists,
# runs T1 and T2 as two separate calls so track tagging stays clean.

def _li_search_url(keyword: str, location: str = "", remote: bool = False) -> str:
    """Build a LinkedIn job search URL with a past-week date filter."""
    params = {"keywords": keyword, "f_TPR": "r604800"}  # r604800 = past 7 days
    if location:
        params["location"] = location
    if remote:
        params["f_WT"] = "2"  # LinkedIn remote-work filter
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


# T1 split: remote-searched URLs get is_remote forced; Houston searches rely on classify_location
_LI_T1_REMOTE_URLS = [
    _li_search_url("SOC Analyst",                  remote=True),
    _li_search_url("Cybersecurity Analyst",        remote=True),
    _li_search_url("IT Specialist",                remote=True),
    _li_search_url("Information Security Analyst", remote=True),
    _li_search_url("Technical Support Specialist", remote=True),
]
_LI_T1_HOUSTON_URLS = [
    _li_search_url("IT Support", "Houston, TX"),
    _li_search_url("Help Desk",  "Houston, TX"),
]


def _fetch_linkedin(client: "ApifyClient") -> list:
    actor = _actor_id("linkedin", "curious_coder/linkedin-jobs-scraper")
    jobs, seen = [], set()

    t2_urls = [_li_search_url(kw, remote=True) for kw in _t2_queries()]
    # (track, url_list, force_remote) — remote-searched jobs get is_remote=True baked in
    run_groups = [
        ("1", _LI_T1_REMOTE_URLS,  True),
        ("1", _LI_T1_HOUSTON_URLS, False),
        ("2", t2_urls,             True),
    ]

    for track, urls, force_remote in run_groups:
        if not urls:
            continue
        try:
            items = _run_actor(client, actor, {
                "urls":                 urls,
                "jobCountLimit":        25,
                "scrapeCompanyDetails": False,
            })
            for item in items:
                job = _make(item,
                    title_keys   = ("jobTitle", "title", "positionName"),
                    company_keys = ("companyName", "company", "employer"),
                    location_keys= ("jobLocation", "location", "locationText"),
                    desc_keys    = ("jobDescription", "description", "details"),
                    url_keys     = ("jobUrl", "url", "applyUrl", "link"),
                    salary_keys  = ("salary", "salaryText", "compensation"),
                    date_keys    = ("postedAt", "publishedAt", "datePosted"),
                    source       = "LinkedIn (Apify)",
                )
                if job and job["url"] not in seen:
                    seen.add(job["url"])
                    job["track"] = track
                    if force_remote:
                        job["is_remote"] = True
                    jobs.append(job)
        except Exception as exc:
            err_msg = str(exc)
            if "not found" in err_msg.lower():
                err_msg += f" — update APIFY_ACTOR_IDS['linkedin'] in config.py (current: '{actor}')"
            raise RuntimeError(f"LinkedIn T{track}: {err_msg}") from exc
        time.sleep(2)

    return jobs


# ── Source: Indeed Scraper ────────────────────────────────────────────────────

_INDEED_T1_QUERIES = [
    "IT support entry level remote",
    "help desk entry level remote",
    "SOC analyst entry level remote",
    "cybersecurity analyst junior remote",
    "IT specialist entry level",
    "information security analyst entry level",
]


def _fetch_indeed(client: "ApifyClient") -> list:
    actor  = _actor_id("indeed", "misceres/indeed-scraper")
    jobs, seen = [], set()

    # All Indeed queries use location="Remote" → force is_remote=True on every result
    query_sets = [
        ("1", _INDEED_T1_QUERIES),
        ("2", _t2_queries()),
    ]

    for track, queries in query_sets:
        for query in queries:
            try:
                items = _run_actor(client, actor, {
                    "queries":     [{"keyword": query, "location": "Remote"}],
                    "maxResults":  20,
                    "countryCode": "us",
                    "proxy":       {"useApifyProxy": True},
                })
                for item in items:
                    job = _make(item,
                        title_keys   = ("positionName", "title", "jobTitle"),
                        company_keys = ("company", "companyName", "employer"),
                        location_keys= ("location", "jobLocation"),
                        desc_keys    = ("description", "jobDescription"),
                        url_keys     = ("url", "jobUrl", "applyUrl"),
                        salary_keys  = ("salary", "salaryText"),
                        date_keys    = ("postedAt", "datePosted", "publishedAt"),
                        source       = "Indeed (Apify)",
                    )
                    if job and job["url"] not in seen:
                        seen.add(job["url"])
                        job["track"] = track
                        job["is_remote"] = True  # searched with location="Remote"
                        jobs.append(job)
            except Exception as exc:
                err_msg = str(exc)
                if "not found" in err_msg.lower():
                    err_msg += f" — update APIFY_ACTOR_IDS['indeed'] in config.py (current: '{actor}')"
                raise RuntimeError(f"Indeed query '{query}': {err_msg}") from exc
            time.sleep(1.5)
    return jobs


# ── Source: Glassdoor Jobs Scraper (crawlerbros) ──────────────────────────────
# Actor: crawlerbros/glassdoor-jobs-scraper — pay-per-result ($5/1k), no rental needed.
# Input:  {"keyword": str, "location": str, "maxItems": int}
# Output: flat dict — jobTitle, companyName, location, description, jobUrl,
#         salary_min/salary_max/salaryPeriod (numeric), ageInDays (int).

# Tuples: (keyword, location, force_remote)
_GD_T1_QUERIES = [
    ("IT support entry level",      "Remote",      True),
    ("help desk entry level",       "Remote",      True),
    ("SOC analyst entry level",     "Remote",      True),
    ("cybersecurity analyst junior","Remote",      True),
    ("IT support",                  "Houston, TX", False),
]


def _prep_glassdoor(item: dict) -> dict:
    """Flatten/enrich a crawlerbros glassdoor item for _make()."""
    flat = dict(item)
    # Build salary text from numeric min/max fields
    lo  = item.get("salary_min")
    hi  = item.get("salary_max")
    per = item.get("salaryPeriod", "ANNUAL")
    if lo or hi:
        sfx = "/ yr" if per == "ANNUAL" else "/ hr"
        if lo and hi:
            flat["salaryText"] = f"${int(lo):,} – ${int(hi):,} {sfx}"
        elif hi:
            flat["salaryText"] = f"up to ${int(hi):,} {sfx}"
        else:
            flat["salaryText"] = f"${int(lo):,}+ {sfx}"
    # Convert ageInDays integer → ISO date string for _parse_date()
    age = item.get("ageInDays")
    if isinstance(age, (int, float)) and age >= 0:
        dt = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=age)
        flat["listingDate"] = dt.isoformat()
    return flat


def _fetch_glassdoor(client: "ApifyClient") -> list:
    actor  = _actor_id("glassdoor", "crawlerbros/glassdoor-jobs-scraper")
    jobs, seen = [], set()

    t2_pairs = [(kw, "Remote", True) for kw in _t2_queries()]
    query_sets = [
        ("1", _GD_T1_QUERIES),
        ("2", t2_pairs),
    ]

    for track, queries in query_sets:
        for keyword, location, force_remote in queries:
            try:
                items = _run_actor(client, actor, {
                    "keyword":  keyword,
                    "location": location,
                    "maxItems": 20,
                })
                for raw in items:
                    item = _prep_glassdoor(raw)
                    job = _make(item,
                        title_keys   = ("jobTitle", "title", "positionName"),
                        company_keys = ("companyName", "employerName", "company", "employer"),
                        location_keys= ("location", "locationName", "jobLocation"),
                        desc_keys    = ("description", "jobDescription", "details"),
                        url_keys     = ("jobUrl", "url", "applyUrl"),
                        salary_keys  = ("salaryText", "payInfo", "salary", "compensation"),
                        date_keys    = ("listingDate", "postedAt", "datePosted"),
                        source       = "Glassdoor (Apify)",
                    )
                    if job and job["url"] not in seen:
                        seen.add(job["url"])
                        job["track"] = track
                        if force_remote:
                            job["is_remote"] = True
                        jobs.append(job)
            except Exception as exc:
                err_msg = str(exc)
                if "not found" in err_msg.lower():
                    err_msg += f" — update APIFY_ACTOR_IDS['glassdoor'] in config.py (current: '{actor}')"
                raise RuntimeError(f"Glassdoor query '{keyword}': {err_msg}") from exc
            time.sleep(1.5)
    return jobs


# ── Source: Google Jobs Scraper ───────────────────────────────────────────────

# Tuples: (query_string, force_remote)
_GOOGLE_T1_QUERIES = [
    ("entry level cybersecurity jobs remote",   True),
    ("help desk jobs Houston TX",               False),
    ("SOC analyst entry level remote",          True),
    ("IT support jobs remote",                  True),
    ("junior IT support specialist remote",     True),
]
# Set of query strings that were remote-searched, for quick lookup
_GOOGLE_REMOTE_QUERY_SET = {q for q, fr in _GOOGLE_T1_QUERIES if fr}


def _prep_google_jobs(item: dict) -> dict:
    """
    Flatten gio21/google-jobs-scraper output into fields _make() can consume.
    - URL lives inside applyOptions list; extract the best direct link.
    - Salary is split across salaryMin/salaryMax/salaryCurrency/salaryPeriod.
    - Query echo field is sourceQuery, not query.
    """
    prepped = dict(item)

    # URL: extract from applyOptions — prefer non-Google ATS links
    apply_opts = item.get("applyOptions") or []
    if isinstance(apply_opts, list) and apply_opts:
        chosen = ""
        for opt in apply_opts:
            link      = opt.get("link") or opt.get("url") or ""
            publisher = (opt.get("publisher") or opt.get("network") or "").lower()
            if link and "google" not in publisher:
                chosen = link
                break
        if not chosen:
            first  = apply_opts[0]
            chosen = first.get("link") or first.get("url") or ""
        prepped["url"] = chosen

    # Salary: build a readable string from structured fields
    sal_min  = item.get("salaryMin")
    sal_max  = item.get("salaryMax")
    period   = (item.get("salaryPeriod") or "").lower()
    if sal_min or sal_max:
        lo, hi = (int(sal_min) if sal_min else None), (int(sal_max) if sal_max else None)
        if lo and hi:
            sal_str = f"${lo:,} – ${hi:,}"
        elif hi:
            sal_str = f"${hi:,}"
        else:
            sal_str = f"${lo:,}"
        if period in ("hour", "hourly"):
            sal_str += "/hr"
        elif period in ("year", "annual", "annually"):
            sal_str += "/yr"
        prepped["salary"] = sal_str

    # Normalise query echo field name
    prepped["query"] = item.get("sourceQuery", "")

    return prepped


def _fetch_google_jobs(client: "ApifyClient") -> list:
    actor = _actor_id("google_jobs", "gio21/google-jobs-scraper")
    jobs, seen = [], set()

    t2_qs = _t2_queries()
    all_queries = [(q, fr, "1") for q, fr in _GOOGLE_T1_QUERIES] + [(q, True, "2") for q in t2_qs]
    q_strings   = [q for q, _, _ in all_queries]
    t2_q_set    = set(t2_qs)

    try:
        items = _run_actor(client, actor, {
            "queries":            q_strings,
            "maxItems":           200,
            "datePosted":         "month",
            "countryCode":        "us",
            "languageCode":       "en",
            "proxyConfiguration": {"useApifyProxy": True},
        })
        for raw in items:
            item         = _prep_google_jobs(raw)
            query_used   = item.get("query", "")
            track        = "2" if query_used in t2_q_set else "1"
            force_remote = query_used in _GOOGLE_REMOTE_QUERY_SET or query_used in t2_q_set
            job = _make(item,
                title_keys   = ("title", "jobTitle", "positionName"),
                company_keys = ("companyName", "company", "employer"),
                location_keys= ("location", "jobLocation"),
                desc_keys    = ("description", "descriptionHtml", "jobDescription"),
                url_keys     = ("url", "applyLink", "jobUrl", "applyUrl"),
                salary_keys  = ("salary", "salaryText"),
                date_keys    = ("postedAtIso", "postedAt", "publishedAt", "datePosted"),
                source       = "Google Jobs (Apify)",
            )
            if job and job["url"] not in seen:
                seen.add(job["url"])
                job["track"] = track
                if force_remote or item.get("workFromHome"):
                    job["is_remote"] = True
                jobs.append(job)
    except Exception as exc:
        err_msg = str(exc)
        if "not found" in err_msg.lower():
            err_msg += f" — update APIFY_ACTOR_IDS['google_jobs'] in config.py (current: '{actor}')"
        raise RuntimeError(f"Google Jobs: {err_msg}") from exc
    return jobs


# ── Source: Dice Jobs Scraper (fatihtahta) ────────────────────────────────────
# Actor: fatihtahta/dice-jobs-scraper — pay-per-result ($0.59/1k), no rental needed.
# Input:  {"queries": [str, ...], "workplaceType": ["Remote"], "postedDate": "Last 7 Days",
#          "maxResults": int}  — min maxResults = 100.
# Output: deeply nested — flatten with _flatten_dice() before passing to _make().

_DICE_T1_QUERIES = [
    "cybersecurity analyst entry level",
    "IT support specialist",
    "SOC analyst entry level",
    "information security analyst",
    "help desk entry level",
]

_DICE_T2_QUERIES = [
    "data analyst entry level",
    "technical writer",
    "QA tester entry level",
]


def _flatten_dice(item: dict) -> dict:
    """Flatten the nested fatihtahta/dice-jobs-scraper output for _make()."""
    sc      = item.get("source_context") or {}
    co      = item.get("content") or {}
    ents    = item.get("entities") or {}
    comp    = ents.get("company") or {}
    loc     = item.get("location") or {}
    pricing = item.get("pricing") or {}
    ts      = item.get("timestamps") or {}
    return {
        "title":       co.get("title", ""),
        "company":     comp.get("name", ""),
        "location":    loc.get("formatted_location", ""),
        "description": co.get("description_text", "") or co.get("summary", ""),
        "url":         sc.get("record_url", ""),
        "salaryText":  pricing.get("salary_text", ""),
        "postedAt":    ts.get("posted_at", ""),
    }


def _fetch_dice(client: "ApifyClient") -> list:
    actor  = _actor_id("dice", "fatihtahta/dice-jobs-scraper")
    jobs, seen = [], set()

    # Run T1 and T2 as two separate batched calls to preserve track tagging.
    # All Dice queries are Remote → force is_remote=True on every result.
    for track, queries in [("1", _DICE_T1_QUERIES), ("2", _DICE_T2_QUERIES)]:
        try:
            items = _run_actor(client, actor, {
                "queries":       queries,
                "workplaceType": ["Remote"],
                "postedDate":    "Last 7 Days",
                "maxResults":    100,
            }, timeout_secs=600)
            for raw in items:
                item = _flatten_dice(raw)
                job = _make(item,
                    title_keys   = ("title",),
                    company_keys = ("company",),
                    location_keys= ("location",),
                    desc_keys    = ("description",),
                    url_keys     = ("url",),
                    salary_keys  = ("salaryText",),
                    date_keys    = ("postedAt",),
                    source       = "Dice (Apify)",
                )
                if job and job["url"] not in seen:
                    # The actor's workplaceType filter is unreliable — verify each result.
                    # Trust classify_location(); also accept US-wide location strings that
                    # Dice uses for remote roles ("United States", "US", "Remote", etc.).
                    if not job.get("is_remote") and not job.get("is_hybrid"):
                        loc_lc   = (job.get("location") or "").lower().strip()
                        title_lc = (job.get("title")    or "").lower()
                        if (loc_lc in ("remote", "us", "usa", "united states", "anywhere", "") or
                                "united states" in loc_lc or
                                "remote" in title_lc):
                            job["is_remote"] = True
                        else:
                            continue  # actor returned an on-site listing; skip it
                    seen.add(job["url"])
                    job["track"] = track
                    jobs.append(job)
        except Exception as exc:
            err_msg = str(exc)
            if "not found" in err_msg.lower():
                err_msg += f" — update APIFY_ACTOR_IDS['dice'] in config.py (current: '{actor}')"
            raise RuntimeError(f"Dice Track {track}: {err_msg}") from exc
        time.sleep(2)
    return jobs


# ── Source registry ───────────────────────────────────────────────────────────

_SOURCES = [
    ("linkedin",    "LinkedIn (Apify)",    _fetch_linkedin),
    ("indeed",      "Indeed (Apify)",      _fetch_indeed),
    ("glassdoor",   "Glassdoor (Apify)",   _fetch_glassdoor),
    ("google_jobs", "Google Jobs (Apify)", _fetch_google_jobs),
    ("dice",        "Dice (Apify)",        _fetch_dice),
]


# ── Main public API ───────────────────────────────────────────────────────────

def fetch_all(
    existing_urls: set = None,
    interactive:   bool = True,
    progress_cb          = None,
) -> tuple:
    """
    Run all enabled Apify actors. Saves results to apify_cache.json.

    Args:
        existing_urls: URLs already in the main feed — used for cross-dedup.
        interactive:   If True, prompt for token when missing (terminal mode).
        progress_cb:   Optional callable(source_label, status, count, error)
                       called after each actor completes.

    Returns:
        (jobs_list, errors_dict)
        errors_dict maps source_label → error string (empty str = success).
    """
    if not _APIFY_AVAILABLE:
        msg = "apify-client not installed. Fix: pip install apify-client"
        print(f"    [Apify] {msg}")
        return [], {"all": msg}

    if not _MODULES_OK:
        msg = "Could not import job_feed or config — run from job-apps folder"
        print(f"    [Apify] {msg}")
        return [], {"all": msg}

    token = get_apify_token()
    if not token:
        if interactive:
            token = prompt_for_token()
        if not token:
            msg = "No API token. Set it in the dashboard → Apify panel → SET TOKEN."
            print(f"    [Apify] {msg}")
            return [], {"all": msg}

    apify_sources_cfg = getattr(cfg, "APIFY_SOURCES", {})
    client     = ApifyClient(token)
    all_jobs   = []
    errors     = {}
    seen_urls  = set(existing_urls or [])

    runlog = _load_runlog()
    runlog["last_run_started"] = datetime.datetime.now().isoformat()
    _save_runlog(runlog)

    for key, label, fetcher in _SOURCES:
        if not apify_sources_cfg.get(key, True):
            print(f"    [{label}] Disabled in APIFY_SOURCES — skipping.")
            if progress_cb:
                progress_cb(label, "disabled", 0, "")
            continue

        print(f"    Fetching {label} via Apify...")
        if progress_cb:
            progress_cb(label, "running", 0, "")

        try:
            raw    = fetcher(client)
            # Deduplicate against existing feed and across Apify sources
            unique = []
            for job in raw:
                url_key = re.sub(r"[?#].*", "", job["url"].lower().rstrip("/"))
                if url_key not in seen_urls:
                    seen_urls.add(url_key)
                    unique.append(job)

            all_jobs.extend(unique)
            errors[label] = ""
            _record_actor_run(key, len(unique))
            save_source_cache(key, unique, {label: ""})   # save immediately per-source
            print(f"    [{label}] {len(raw)} raw → {len(unique)} new after dedup")
            if progress_cb:
                progress_cb(label, "ok", len(unique), "")

        except Exception as exc:
            err = str(exc)
            errors[label] = err
            _record_actor_run(key, 0, error=err)
            print(f"    [{label}] ERROR: {err}")
            if progress_cb:
                progress_cb(label, "error", 0, err)

        time.sleep(2)

    # Write combined summary for dashboard stats
    LOGS_DIR.mkdir(exist_ok=True)
    _combined_payload = {
        "date":      datetime.date.today().isoformat(),
        "timestamp": datetime.datetime.now().isoformat(),
        "jobs":      [_serialize_job(j) for j in all_jobs],
        "errors":    errors,
    }
    APIFY_CACHE_PATH.write_text(json.dumps(_combined_payload, indent=2), encoding="utf-8")

    runlog = _load_runlog()
    runlog["last_run_completed"] = datetime.datetime.now().isoformat()
    runlog["last_job_count"]     = len(all_jobs)
    runlog["last_errors"]        = {k: bool(v) for k, v in errors.items()}
    _save_runlog(runlog)

    return all_jobs, errors


def fetch_one(
    source_key:    str,
    existing_urls: set = None,
    progress_cb        = None,
) -> tuple:
    """
    Run a single Apify source by key (e.g. 'linkedin', 'dice').
    Merges new results into the existing cache file.
    Returns (new_jobs_list, error_str).
    """
    if not _APIFY_AVAILABLE:
        return [], "apify-client not installed. Fix: pip install apify-client"
    if not _MODULES_OK:
        return [], "Could not import job_feed or config — run from job-apps folder"

    token = get_apify_token()
    if not token:
        return [], "No API token. Set it in the dashboard → Apify panel → SET TOKEN."

    source_map = {key: (label, fetcher) for key, label, fetcher in _SOURCES}
    if source_key not in source_map:
        return [], f"Unknown source key: {source_key!r}"

    label, fetcher = source_map[source_key]

    apify_sources_cfg = getattr(cfg, "APIFY_SOURCES", {})
    if not apify_sources_cfg.get(source_key, True):
        return [], f"{label} is disabled in APIFY_SOURCES."

    client = ApifyClient(token)

    # Build seen-url set from caller + existing cache (for dedup)
    cached_jobs, cached_errors, _ = load_cache()
    seen_urls = set(existing_urls or [])
    for j in cached_jobs:
        seen_urls.add(re.sub(r"[?#].*", "", j["url"].lower().rstrip("/")))

    if progress_cb:
        progress_cb(label, "running", 0, "")

    try:
        raw = fetcher(client)
        unique = []
        for job in raw:
            url_key = re.sub(r"[?#].*", "", job["url"].lower().rstrip("/"))
            if url_key not in seen_urls:
                seen_urls.add(url_key)
                unique.append(job)

        # Merge with existing per-source cache for this key
        existing_for_source, _, _ = load_source_cache(source_key)
        merged_for_source = existing_for_source + unique
        save_source_cache(source_key, merged_for_source, {label: ""})
        _record_actor_run(source_key, len(unique))

        # Rebuild combined summary
        all_cached, all_errs, _ = load_cache()
        all_errs[label] = ""
        _combined = {
            "date":      datetime.date.today().isoformat(),
            "timestamp": datetime.datetime.now().isoformat(),
            "jobs":      [_serialize_job(j) for j in all_cached],
            "errors":    all_errs,
        }
        LOGS_DIR.mkdir(exist_ok=True)
        APIFY_CACHE_PATH.write_text(json.dumps(_combined, indent=2), encoding="utf-8")

        if progress_cb:
            progress_cb(label, "ok", len(unique), "")
        return unique, ""

    except Exception as exc:
        err = str(exc)
        _record_actor_run(source_key, 0, error=err)
        if progress_cb:
            progress_cb(label, "error", 0, err)
        return [], err


def is_available() -> bool:
    """Return True if apify-client is installed and a token is set."""
    return _APIFY_AVAILABLE and bool(get_apify_token())


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running Apify feed fetch...")
    jobs, errs = fetch_all(interactive=True)
    print(f"\nDone. {len(jobs)} total jobs fetched.")
    for src, err in errs.items():
        if err:
            print(f"  ERROR [{src}]: {err}")
    if jobs:
        print(f"  Cache saved to {APIFY_CACHE_PATH}")
