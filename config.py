# =============================================================================
#  config.py — Job search configuration
#  Edit this file to update your search terms, API keys, and preferences.
#  Never need to touch job_feed.py after initial setup.
# =============================================================================
import os
from pathlib import Path

def _env_val(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() == key:
                    return v.strip()
    return default

# ── Target role keywords (job must match at least ONE of these) ───────────────
# These are searched across the job title and description.
ROLE_TERMS = [
    "IT support",
    "help desk",
    "helpdesk",
    "service desk",
    "SOC analyst",
    "cybersecurity analyst",
    "cybersecurity specialist",
    "security analyst",
    "information security analyst",
    "information security specialist",
    "information technology specialist",  # primary federal IT title on USAJobs
    "desktop support",
    "technical support",
    "network technician",
    "systems support",
    "IT analyst",
    "NOC technician",
    "GRC analyst",
    "cloud support specialist",
    "IT generalist",
    "customer support",
    "data center technician",
    "datacenter technician",
]

# ── Level keywords (no longer a hard filter) ──────────────────────────────────
# These are NOT required to appear in a job posting. A role just needs to match
# ROLE_TERMS and not trigger EXCLUDE_TERMS; AI scoring handles experience fit.
# Kept here for reference / future use (e.g., boosting AI score hints).
LEVEL_TERMS = [
    "entry level",
    "entry-level",
    "junior",
    "tier 1",
    "tier1",
    "tier i",
    "associate",
    "trainee",
    "no experience required",
    "0-2 years",
    "0-1 year",
    "new grad",
    "recent graduate",
    "no experience necessary",
    "beginner",
    "early career",
    "early-career",
]

# ── Exclusion keywords (job is dropped if ANY of these appear) ─────────────────
# Filters out senior, management, and experienced-only roles.
EXCLUDE_TERMS = [
    "senior",
    "sr.",
    " sr ",
    "manager",
    "director",
    "team lead",
    "tech lead",
    "principal",
    "staff engineer",
    "5 years",
    "5+ years",
    "five years",
    "7 years",
    "7+ years",
    "ten years",
    "10 years",
]

# ── Remote / hybrid settings ──────────────────────────────────────────────────
# Jobs are included only if they match at least one REMOTE_TERM.
REMOTE_TERMS = [
    "remote",
    "work from home",
    "work-from-home",
    "wfh",
    "virtual",
    "telecommute",
    "anywhere in the u.s",
    "anywhere in the us",
    "telework",          # federal/USAJobs term for remote-eligible
]

# Hybrid jobs are included ONLY when:
#   1. Salary >= HYBRID_SALARY_MINIMUM, AND
#   2. Location is within HYBRID_MAX_MILES of HYBRID_CENTER_LAT/LON (zip 77083)
HYBRID_TERMS            = ["hybrid"]
HYBRID_SALARY_MINIMUM   = 65_000   # USD per year
HYBRID_MAX_MILES        = 30       # max commute radius in miles
HYBRID_CENTER_LAT       = 29.6813  # center of zip 77083, SW Houston TX
HYBRID_CENTER_LON       = -95.6130

# USAJobs: federal postings don't use "entry level" / "junior" — use a salary
# cap to approximate GS-5 through GS-9 (entry/junior federal IT grades).
USAJOBS_MAX_SALARY = 90_000  # GS-9 step 10 ~$71K; GS-11 step 5 ~$85K — cap keeps out GS-12+

# Remote: jobs with a stated salary below this are dropped; no-salary remote jobs pass to AI.
REMOTE_SALARY_MINIMUM   = 50_000
# Hybrid: annual minimum stays HYBRID_SALARY_MINIMUM; hourly is checked separately.
HYBRID_HOURLY_MINIMUM   = 30       # $30/hr = ~$62.4k annual
# ── Date filter ───────────────────────────────────────────────────────────────
# Drop any job posted more than this many hours ago.
MAX_AGE_HOURS      = 168   # initial fetch window: 7 days (168h), sorted recent-first
QUALIFY_MIN_COUNT  = 10   # if fewer than this many jobs score 6+, trigger fallback
FALLBACK_AGE_HOURS = 720  # fallback window: 30 days (720h)

# ── API credentials ───────────────────────────────────────────────────────────

# Adzuna — requires BOTH an App ID and an API Key.
#   1. Log in at https://developer.adzuna.com/
#   2. Go to Dashboard → Applications → your app
#   3. Copy the "Application ID" (short alphanumeric) and "API Key" (32-char hex)
ADZUNA_APP_ID  = _env_val("ADZUNA_APP_ID")
ADZUNA_API_KEY = _env_val("ADZUNA_API_KEY")

# JSearch via RapidAPI — aggregates LinkedIn, Indeed, Glassdoor, ZipRecruiter.
#   1. Sign up at https://rapidapi.com/
#   2. Search "JSearch" → subscribe to the one by letscrape-6bfde5 (free: 200 req/month)
#   3. Copy your RapidAPI key from the endpoint's "Code Snippets" panel
JSEARCH_API_KEY  = _env_val("JSEARCH_API_KEY")
JSEARCH_API_HOST = "jsearch.p.rapidapi.com"

# USAJobs — no formal key needed, but your email is required as the User-Agent.
#   Register (free) at https://developer.usajobs.gov/ for higher rate limits.
USAJOBS_USER_AGENT  = "kimearls24@outlook.com"       # required — replace with your email
USAJOBS_AUTH_KEY    = _env_val("USAJOBS_AUTH_KEY")                     # optional — leave blank if not registered

# ── Source on/off switches ────────────────────────────────────────────────────
# Set any source to False to skip it entirely during a run.
SOURCES = {
    "usajobs":         True,
    "adzuna":          True,
    "jsearch":         True,   # RapidAPI — aggregates LinkedIn, Indeed, Glassdoor, ZipRecruiter
    "remotive":        True,   # Free JSON API — remote-only tech jobs, no key needed
    "remoteok":        True,   # Free JSON API — remote-only tech jobs, no key needed
    "jobicy":          True,   # Free JSON API — remote-only jobs, no key needed
    "indeed":          False,  # RSS often rate-limited/0 results — covered by JSearch
    "linkedin":        False,  # No public API — use Apify actor instead
    "dice":            False,  # No public API — use Apify actor instead
    "weworkremotely":  False,  # Bot-detected (406) since 2026-05-14 — covered by other sources
}

# ── Scam blocklists ───────────────────────────────────────────────────────────
# Domains to hard-block — jobs from these URLs are dropped before scoring.
# Substring match: ".liveblog365.com" blocks all subdomains of that host.
SCAM_DOMAINS = [
    "theelitejob.com",
    "wfhforgeon.byethost7.com",
    "wfh.hstn.me",
    "career.zycto.com",
    ".liveblog365.com",
    "hirequorum.liveblog365.com",
    "jobspawn.liveblog365.com",
    "hirequill.liveblog365.com",
    "remotivix.liveblog365.com",
]

# Company names to hard-block (matched lowercase, stripped).
SCAM_COMPANY_NAMES = [
    "the elite job",
    "wfhforgeon",
    "wfh",
    "career.zycto",
    "flexible schedules",
    "only data entry",
    "training opportunities",
]

# ── Output settings ───────────────────────────────────────────────────────────
OUTPUT_SUBDIR    = "output"          # subfolder inside job-apps for saved files
TRACKER_FILENAME = "job_tracker.csv" # master application tracker file

# ── AI Agent settings (job_agent.py) ─────────────────────────────────────────
# pip install anthropic  (one-time setup)
AI_MODEL              = "claude-sonnet-4-6"  # Anthropic model used for job scoring
AI_SCORE_THRESHOLD    = 7                     # score >= this triggers APPLY NOW + doc generation
AI_SCORING_MAX_TOKENS = 500                   # max tokens for each scoring API response
AI_API_DELAY_SECONDS  = 2                     # seconds to pause between scoring API calls

# ── Journey chat settings ──────────────────────────────────────────────────────
CHAT_ENABLED       = True   # set False to disable the chat panel
CHAT_MAX_TOKENS    = 1500   # max tokens per Journey chat response
CHAT_HISTORY_LIMIT = 20     # messages (10 exchanges) sent to API

# ── Rema chat settings ─────────────────────────────────────────────────────────
REMA_CHAT_ENABLED    = True   # set False to disable Rema's chat panel
REMA_CHAT_MAX_TOKENS = 1500   # max tokens per Rema chat response

# ── Apify settings ─────────────────────────────────────────────────────────────
# Apify adds LinkedIn, Indeed, Glassdoor, Google Jobs, and Dice scrapers on top
# of the existing feed. Results are cached to logs/apify_cache.json and merged
# when job_agent.py runs. Set any source to False to skip it entirely.
#
# API token: set via the dashboard (Journey → Apify panel → SET TOKEN) or add
#   APIFY_API_TOKEN=apify_api_... to the .env file. Never store it here.
APIFY_SOURCES = {
    "linkedin":    True,
    "indeed":      True,
    "glassdoor":   True,
    "google_jobs": True,
    "dice":        True,
}

# ── Apify actor IDs ────────────────────────────────────────────────────────────
# All actors run on Apify compute credits (covered by the free $5/month credit).
# If you get "Actor with this name was not found", go to https://apify.com/store,
# search for the source, copy the actor ID (format: username/actor-name), and update here.
APIFY_ACTOR_IDS = {
    "linkedin":    "curious_coder/linkedin-jobs-scraper",   # pay-per-use ($0.001/result); takes LinkedIn search URLs
    "indeed":      "misceres/indeed-scraper",               # pay-per-use (~$3/1k jobs)
    "glassdoor":   "crawlerbros/glassdoor-jobs-scraper",    # pay-per-result ($5/1k); bebity expired free trial
    "google_jobs": "gio21/google-jobs-scraper",             # pay-per-use (~$0.008/job)
    "dice":        "fatihtahta/dice-jobs-scraper",          # pay-per-result ($0.59/1k); radekmie not found
}

APIFY_CACHE_MAX_HOURS = 24   # merge cache if fresher than this; else skip silently

# ── Apify Track 2 queries (remote income — appended to each source's T1 run) ──
# These are searched alongside T1 queries. Results are tagged track="2" and
# flow into the Track 2 scoring pipeline automatically.
APIFY_T2_QUERIES = [
    "data entry specialist remote",
    "virtual assistant remote",
    "billing specialist remote",
    "data analyst remote",
    "administrative coordinator remote",
    "medical billing specialist remote",
    "compliance coordinator remote",
    "operations coordinator remote",
]


# =============================================================================
#  TRACK 2 — REMOTE INCOME
#  Runs in parallel alongside Track 1 (Tech & Cybersecurity).
#  Remote-only, no-phone-or-low-phone roles, salary ≥ $45K or ≥ $22/hr.
#  Do NOT modify this section without also updating job_feed.py and job_agent.py.
# =============================================================================

# ── Track 2 salary thresholds ─────────────────────────────────────────────────
T2_SALARY_MINIMUM  = 45_000   # USD per year
T2_HOURLY_MINIMUM  = 22       # USD per hour  ($22/hr ≈ $45,760/yr)

# ── Phone exclusion terms — any listing containing these is dropped ───────────
T2_PHONE_EXCLUDE_TERMS = [
    "inbound calls",
    "outbound calls",
    "call center",
    "phone support",
    "high call volume",
    "customer calls",
    "answer phones",
    "phone queue",
    "telephonic",
]

# ── Kee's Track 2 skills — used for the 15-point skills match in scoring ──────
T2_SKILLS = [
    "salesforce crm", "salesforce", "data entry", "microsoft office",
    "microsoft office suite", "excel", "word", "outlook",
    "customer relationship management", "crm", "scheduling",
    "documentation", "process improvement", "billing", "insurance",
    "insurance products", "policy review", "virtual collaboration",
    "zoom", "teams", "microsoft teams", "written communication",
    "detail oriented", "high volume", "multi-tasking", "multitasking",
    "customer service", "communication", "organized", "administrative",
    "coordinator", "payroll", "accounts payable", "bookkeeping",
    "paralegal", "compliance", "quality assurance", "qa",
    "project management", "research", "data analysis", "data analyst",
]

# ── JSearch queries for Track 2 (4/run — total T1+T2 = 9/run ≈ 22 runs/month) ─
T2_JSEARCH_QUERIES = [
    "data entry remote",
    "virtual assistant remote",
    "billing specialist remote",
    "data analyst remote",
]

# ── Full keyword list used for Remotive / RemoteOK / Jobicy ───────────────────
T2_KEYWORDS = [
    # Data and Admin
    "data entry", "virtual assistant", "administrative coordinator",
    "executive assistant", "operations coordinator",
    "scheduling coordinator", "records management",
    # Finance and Insurance
    "claims adjuster", "underwriter", "insurance coordinator",
    "billing specialist", "accounts payable", "bookkeeping",
    "payroll specialist",
    # Healthcare adjacent
    "medical coder", "medical biller", "prior authorization",
    "healthcare operations",
    # Tech adjacent
    "technical writer", "content moderator", "qa tester",
    "data analyst", "salesforce administrator", "crm specialist",
    # Legal and Compliance
    "legal assistant", "compliance coordinator",
    "contract specialist", "paralegal",
    # Education and Training
    "instructional designer", "curriculum developer",
    "e-learning specialist", "training coordinator",
    # General remote
    "project coordinator", "research analyst",
    "grant writer", "procurement specialist", "vendor coordinator",
]
