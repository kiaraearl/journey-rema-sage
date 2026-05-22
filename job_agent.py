#!/usr/bin/env python3
"""
=============================================================================
  job_agent.py — AI-powered job application agent
=============================================================================

WHAT IT DOES:
  1. Fetches today's job listings via job_feed across all configured sources
  2. Scores each listing 1–10 using Claude AI against your resume and bio,
     flags hard disqualifiers and skill gaps, and assigns a recommendation
  3. For jobs scored >= AI_SCORE_THRESHOLD with APPLY NOW, auto-generates a
     tailored resume and cover letter
  4. Updates job_tracker.csv with scores, recommendations, gaps, and filenames
  5. Prints a clean terminal summary at the end

REQUIREMENTS (one-time install):
  pip install anthropic feedparser requests

HOW TO RUN:
  cd C:/Users/Kimea/Projects/job-apps
  python job_agent.py

ALL TUNEABLE SETTINGS live in config.py under the "AI Agent settings" block.
=============================================================================
"""

import os
import sys
import csv
import time
import json
import datetime
import re
from pathlib import Path

# Force UTF-8 output on Windows so arrow/dash characters don't crash the script
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# ── Dashboard integration ─────────────────────────────────────────────────────

def emit_event(event_type, **kwargs):
    """Output a structured JSON event line for the dashboard to parse."""
    print(f"__EVENT__:{json.dumps({'type': event_type, **kwargs})}", flush=True)


class _Tee:
    """Duplicate stdout to a log file while keeping terminal output intact."""
    def __init__(self, primary, secondary):
        self._p = primary
        self._s = secondary

    def write(self, data):
        self._p.write(data)
        self._p.flush()
        try:
            self._s.write(data)
            self._s.flush()
        except Exception:
            pass

    def flush(self):
        self._p.flush()
        try:
            self._s.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._p, name)


# =============================================================================
#  DEPENDENCY CHECKS
#  Give a clear install message before anything else can fail.
# =============================================================================

try:
    import anthropic
except ImportError:
    sys.exit(
        "ERROR: anthropic package is not installed.\n"
        "Fix:   pip install anthropic\n"
        "Then run this script again."
    )

try:
    import feedparser  # noqa: F401 — imported by job_feed at module load
except ImportError:
    sys.exit(
        "ERROR: feedparser is not installed.\n"
        "Fix:   pip install feedparser requests"
    )

try:
    import requests  # noqa: F401 — imported by job_feed at module load
except ImportError:
    sys.exit(
        "ERROR: requests is not installed.\n"
        "Fix:   pip install feedparser requests"
    )

# =============================================================================
#  LOAD CONFIG AND SIBLING SCRIPTS
#  All three sibling scripts are imported as modules so their functions can be
#  called directly — no subprocess, no file-passing, no interactive prompts.
# =============================================================================

SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

try:
    import config as cfg
except ImportError:
    sys.exit("ERROR: config.py not found. Run this script from inside the job-apps folder.")

try:
    import job_feed
    import resume_tailor
    import cover_letter as cover_letter_mod
    import resume_builder
except ImportError as exc:
    sys.exit(f"ERROR: Could not import a sibling script: {exc}")

# =============================================================================
#  CONSTANTS
# =============================================================================

OUTPUT_DIR        = SCRIPT_DIR / cfg.OUTPUT_SUBDIR
RESUMES_DIR       = SCRIPT_DIR / "resumes"
COVER_LETTERS_DIR = SCRIPT_DIR / "Cover Letters"
TRACKER_PATH = SCRIPT_DIR / cfg.TRACKER_FILENAME
LOGS_DIR     = SCRIPT_DIR / "logs"
RESUME_FILE  = SCRIPT_DIR / "master_resume.txt"
BIO_FILE     = SCRIPT_DIR / "bio.txt"

# Extra columns the agent adds to job_tracker.csv (appended after job_feed's schema).
# job_feed.py uses extrasaction="ignore", so these don't break existing reads/writes.
AGENT_EXTRA_FIELDS = [
    "AI Score",
    "AI Recommendation",
    "AI Reason",
    "Disqualifiers",
    "Gaps",
    "Resume File",
    "Cover Letter File",
    "ATS Score",
    # Track 2 columns (blank for T1 rows)
    "T2 Score",
    "T2 Remote Status",
    "T2 Phone Status",
]

# Full column order for tracker rewrites done by this script
FULL_CSV_FIELDNAMES = list(dict.fromkeys(job_feed.CSV_FIELDNAMES + AGENT_EXTRA_FIELDS))


# =============================================================================
#  SCAM CLEANUP
#  Runs once at startup — marks rows from known scam sources so they don't
#  show up as active targets. After the first clean run it becomes a no-op.
# =============================================================================

def purge_scam_rows():
    """Mark tracker rows from known scam sources as removed."""
    if not TRACKER_PATH.exists():
        return
    try:
        with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
            fieldnames = f.name and list(rows[0].keys()) if rows else []
    except Exception as exc:
        print(f"  [Scam Purge] Could not read tracker: {exc}")
        return

    # Re-read properly to get fieldnames
    try:
        with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
    except Exception:
        return

    flagged = 0
    for row in rows:
        url     = (row.get("Application URL") or "").lower()
        company = (row.get("Company") or "").lower().strip()
        already_removed = "removed" in row.get("Status", "").lower()
        if already_removed:
            continue
        is_scam = False
        for domain in getattr(cfg, "SCAM_DOMAINS", []):
            if domain.lower() in url:
                is_scam = True
                break
        if not is_scam and company in {n.lower() for n in getattr(cfg, "SCAM_COMPANY_NAMES", [])}:
            is_scam = True
        if is_scam:
            row["Status"] = "Removed — Scam Source"
            row["Notes"]  = "Auto-removed: known scam domain/company"
            flagged += 1

    if flagged:
        with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"  [Scam Purge] Flagged {flagged} row(s) as 'Removed — Scam Source'.")
    else:
        print(f"  [Scam Purge] Tracker is clean — no scam rows found.")


# =============================================================================
#  STEP 1 — FETCH FRESH JOB LISTINGS
#  Calls job_feed's individual fetcher functions, runs the filter pipeline,
#  saves the output file, and appends new entries to job_tracker.csv.
#  Returns the filtered list of job dicts.
# =============================================================================

def get_fresh_jobs(age_hours=None, exclude_urls=None):
    """
    Run the full job_feed pipeline and return the filtered job list.
    age_hours overrides cfg.MAX_AGE_HOURS for the recency filter.
    exclude_urls is a set of URLs to skip (used on fallback passes).
    """
    today_str = datetime.date.today().isoformat()
    hours = age_hours if age_hours is not None else cfg.MAX_AGE_HOURS

    # Pre-populate exclude_urls with every URL already in the tracker so
    # previously-seen jobs aren't re-fetched, re-scored, or re-billed.
    existing_tracker_urls = set()
    if TRACKER_PATH.exists():
        try:
            with open(TRACKER_PATH, newline="", encoding="utf-8") as _f:
                for _row in csv.DictReader(_f):
                    _url = (_row.get("Application URL") or "").strip()
                    if _url:
                        existing_tracker_urls.add(re.sub(r"[?#].*", "", _url.lower().rstrip("/")))
        except Exception:
            pass
    if existing_tracker_urls:
        exclude_urls = (exclude_urls or set()) | existing_tracker_urls
        print(f"  Pre-loaded {len(existing_tracker_urls)} already-tracked URL(s) — will skip re-scoring.")
    print(f"  Filters: last {hours}h ({hours // 24}d) | remote min ${cfg.REMOTE_SALARY_MINIMUM:,} | hybrid min ${cfg.HYBRID_SALARY_MINIMUM:,}\n")

    fetchers = {
        "indeed":         job_feed.fetch_indeed,
        "linkedin":       job_feed.fetch_linkedin,
        "dice":           job_feed.fetch_dice,
        "usajobs":        job_feed.fetch_usajobs,
        "adzuna":         job_feed.fetch_adzuna,
        "jsearch":        job_feed.fetch_jsearch,
        "remotive":       job_feed.fetch_remotive,
        "remoteok":       job_feed.fetch_remoteok,
        "weworkremotely": job_feed.fetch_weworkremotely,
        "jobicy":         job_feed.fetch_jobicy,
    }

    source_labels = {
        "indeed":         "Indeed",
        "linkedin":       "LinkedIn",
        "dice":           "Dice",
        "usajobs":        "USAJobs",
        "adzuna":         "Adzuna",
        "jsearch":        "JSearch",
        "remotive":       "Remotive",
        "remoteok":       "RemoteOK",
        "weworkremotely": "WeWorkRemotely",
        "jobicy":         "Jobicy",
    }

    jobs_by_source = {}
    all_raw = []

    # Determine which sources to run from env var (set by dashboard)
    _sel_raw = os.environ.get("JOURNEY_SELECTED_SOURCES", "")
    if _sel_raw.strip():
        _selected = {s.strip().lower() for s in _sel_raw.split(",") if s.strip()}
    else:
        # Fallback: legacy env var or run everything
        _legacy = os.environ.get("JOURNEY_SOURCE_MODE", "both")
        if _legacy == "direct":
            _selected = {"usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy", "weworkremotely"}
        elif _legacy == "apify":
            _selected = {"linkedin", "indeed", "glassdoor", "google_jobs", "dice"}
        else:
            _selected = {"usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy",
                         "weworkremotely", "linkedin", "indeed", "glassdoor", "google_jobs", "dice"}

    _apify_key_to_label = {
        "linkedin":    "LinkedIn (Apify)",
        "indeed":      "Indeed (Apify)",
        "glassdoor":   "Glassdoor (Apify)",
        "google_jobs": "Google Jobs (Apify)",
        "dice":        "Dice (Apify)",
    }
    _selected_apify_labels = {_apify_key_to_label[k] for k in _selected if k in _apify_key_to_label}
    _run_direct = bool(_selected - set(_apify_key_to_label.keys()))
    _run_apify  = bool(_selected_apify_labels)

    if not _run_direct:
        print("  [Direct sources] Skipped — not selected.")
    else:
        for name, fetcher in fetchers.items():
            label = source_labels.get(name, name.capitalize())
            if name not in _selected:
                print(f"  [{label}] Not selected — skipping.")
                jobs_by_source[label] = []
                continue
            if not cfg.SOURCES.get(name, True):
                print(f"  [{label}] Disabled in config.py — skipping.")
                jobs_by_source[label] = []
                continue
            print(f"  Fetching {label}...")
            raw = fetcher()
            jobs_by_source[label] = raw
            all_raw.extend(raw)
            print(f"  [{label}] {len(raw)} raw listing(s)")

    # ── Apify cached results — T1 only (T2 handled in main) ────────────────────
    try:
        import apify_feed
        max_hours = getattr(cfg, "APIFY_CACHE_MAX_HOURS", 24)
        if not _run_apify:
            print("  [Apify] Skipped — no Apify sources selected.")
        elif not apify_feed.cache_is_stale(max_hours) or not _run_direct:
            if apify_feed.cache_is_stale(max_hours):
                print("  [Apify] Cache is stale — loading anyway (Apify-only mode). Run 'RUN APIFY NOW' to refresh.")
            apify_jobs, _apify_errs, _apify_ts = apify_feed.load_cache()
            if apify_jobs:
                # Only merge Track 1 jobs, filtered by selected actors
                apify_t1 = [
                    j for j in apify_jobs
                    if j.get("track", "1") != "2" and j.get("source", "") in _selected_apify_labels
                ]
                existing_url_keys = {
                    re.sub(r"[?#].*", "", j["url"].lower().rstrip("/"))
                    for j in all_raw
                }
                fresh_apify = []
                for j in apify_t1:
                    uk = re.sub(r"[?#].*", "", j["url"].lower().rstrip("/"))
                    if uk not in existing_url_keys:
                        existing_url_keys.add(uk)
                        fresh_apify.append(j)
                if fresh_apify:
                    for label in sorted({j["source"] for j in fresh_apify}):
                        src_list = [j for j in fresh_apify if j["source"] == label]
                        jobs_by_source[label] = src_list
                        print(f"  [{label}] {len(src_list)} T1 job(s) from Apify cache")
                    all_raw.extend(fresh_apify)
                    t2_count = sum(1 for j in apify_jobs if j.get("track") == "2")
                    print(f"  Apify cache merged: {len(fresh_apify)} T1 + {t2_count} T2 job(s) cached.")
        else:
            if apify_feed.is_available():
                print("  [Apify] Cache stale — click 'RUN APIFY NOW' in the dashboard to refresh.")
            else:
                print("  [Apify] No token set — open dashboard → Apify panel → SET TOKEN to enable.")
    except ImportError:
        pass
    except Exception as _apify_exc:
        print(f"  [Apify] Skipped: {_apify_exc}")

    print("\n  Applying filters (keywords → recency → location → dedup)...")
    filtered = job_feed.apply_filters(all_raw, age_hours=hours)

    # Drop any URLs already processed in a previous pass
    if exclude_urls:
        before = len(filtered)
        filtered = [j for j in filtered if j["url"] not in exclude_urls]
        dropped = before - len(filtered)
        if dropped:
            print(f"  Skipped {dropped} already-seen job(s) from earlier pass.")

    job_feed.print_summary(jobs_by_source, filtered)

    if filtered:
        OUTPUT_DIR.mkdir(exist_ok=True)
        out_path = job_feed.save_to_file(filtered, today_str)
        added    = job_feed.append_to_tracker(filtered)
        print(f"  Saved {len(filtered)} job(s) → {out_path.name}")
        print(f"  Added {added} new entry/entries to tracker.")

    return filtered


def _score_batch(jobs, resume_text, bio_text, client, api_errors, idx_offset=0, total=None):
    """
    Score a list of jobs with Claude. Returns a list of scored result dicts.
    idx_offset and total are used to print correct [N/M] counters when this
    is a second pass appended to an earlier batch.
    """
    display_total = total if total is not None else len(jobs)
    scored = []

    for i, job in enumerate(jobs, 1):
        display_i = idx_offset + i
        label = f"{job['title']} @ {job['company'] or 'Unknown'}"
        print(f"  [{display_i:>2}/{display_total}]  {label[:54]}")

        try:
            assessment = score_job(job, resume_text, bio_text, client)
            print(f"          Score: {assessment.get('score', '?')}/10  |  {assessment.get('recommendation', '?')}")
            _dp = job.get("date_posted")
            emit_event("score",
                title=job["title"],
                company=job.get("company") or "",
                score=assessment.get("score", 0),
                rec=assessment.get("recommendation", ""),
                reason=assessment.get("reason", ""),
                gaps=assessment.get("gaps", []),
                date_posted=_dp.strftime("%Y-%m-%d") if _dp else "",
            )
            scored.append({
                "job":               job,
                "assessment":        assessment,
                "resume_file":       None,
                "cover_letter_file": None,
            })

        except Exception as exc:
            print(f"          ERROR: {exc}")
            api_errors.append({"job": job, "error": str(exc)})
            scored.append({
                "job": job,
                "assessment": {
                    "score":          0,
                    "recommendation": "REVIEW MANUALLY",
                    "reason":         "API error — review manually",
                    "disqualifiers":  [],
                    "gaps":           [],
                },
                "resume_file":       None,
                "cover_letter_file": None,
            })

        if i < len(jobs):
            time.sleep(cfg.AI_API_DELAY_SECONDS)

    return scored


# =============================================================================
#  TRACK 2 SCORING  (rule-based, no API call)
#  100-point rubric:
#    Remote confirmed  30 pts
#    Salary meets min  30 pts
#    No phone terms    25 pts
#    Skills match      15 pts
# =============================================================================

def score_t2_job(job):
    """
    Score a Track 2 job with the 100-point rule-based rubric.
    Returns a dict: score, remote_status, phone_status, salary_status, skills_matched.
    """
    full = re.sub(r"\s+", " ", (
        (job.get("title") or "") + " " + (job.get("description") or "")
    ).lower()).strip()

    # ── Remote (30 pts) ──────────────────────────────────────────────────────
    remote_pts    = 30 if job.get("is_remote") else 0
    remote_status = "confirmed" if job.get("is_remote") else "ambiguous"

    # ── Salary (30 pts) ──────────────────────────────────────────────────────
    sal = job.get("salary_value")
    hourly_raw = (job.get("salary_raw") or "").lower()
    is_hourly  = bool(re.search(r"/\s*hr|per\s*hour|/\s*hour", hourly_raw))

    if sal is None:
        salary_pts    = 0
        salary_status = "unlisted"
    elif is_hourly and (sal / 2080) >= getattr(cfg, "T2_HOURLY_MINIMUM", 22):
        salary_pts    = 30
        salary_status = "confirmed"
    elif not is_hourly and sal >= getattr(cfg, "T2_SALARY_MINIMUM", 45_000):
        salary_pts    = 30
        salary_status = "confirmed"
    else:
        salary_pts    = 0
        salary_status = "below_min"

    # ── Phone (25 pts) ───────────────────────────────────────────────────────
    phone_exclude = [t.lower() for t in getattr(cfg, "T2_PHONE_EXCLUDE_TERMS", [])]
    if any(t in full for t in phone_exclude):
        phone_pts    = 0
        phone_status = "excluded"
    elif any(w in full for w in (" phone", " call ", " calls ", "telephon")):
        phone_pts    = 12
        phone_status = "unclear"
    else:
        phone_pts    = 25
        phone_status = "no_phone"

    # ── Skills (15 pts — 3 pts per match, max 5) ─────────────────────────────
    t2_skills = [s.lower() for s in getattr(cfg, "T2_SKILLS", [])]
    matched   = [s for s in t2_skills if s in full]
    skills_pts = min(15, len(set(matched)) * 3)

    total = remote_pts + salary_pts + phone_pts + skills_pts

    return {
        "score":          total,
        "remote_status":  remote_status,
        "phone_status":   phone_status,
        "salary_status":  salary_status,
        "skills_matched": list(set(matched))[:5],
    }


# =============================================================================
#  STEP 2 — AI SCORING
#  Sends each job to Claude with the candidate's resume and bio.
#  Claude returns structured JSON with score, reason, disqualifiers, gaps,
#  and a recommendation.
# =============================================================================

def score_job(job, resume_text, bio_text, client):
    """
    Ask Claude to evaluate one job listing against the candidate profile.

    Returns a dict:
        score          int 1–10
        reason         str  one-sentence explanation
        disqualifiers  list[str]  hard blockers (empty list if none)
        gaps           list[str]  wanted skills/certs candidate lacks
        recommendation str  APPLY NOW | REVIEW MANUALLY | SKIP
    """
    # Build a concise job snapshot — cap description to stay within token budget
    job_info = (
        f"Title:    {job['title']}\n"
        f"Company:  {job['company'] or 'Unknown'}\n"
        f"Location: {job['location'] or 'Remote'}\n"
        f"Salary:   {job['salary_raw'] or 'Not listed'}\n"
        f"Source:   {job['source']}\n\n"
        f"Description:\n{job['description'][:1500]}"
    )

    prompt = f"""You are a career advisor evaluating a job listing for a candidate.

CANDIDATE RESUME:
{resume_text[:3000]}

CANDIDATE BIO:
{bio_text[:800]}

JOB LISTING:
{job_info}

Evaluate this job and respond with ONLY valid JSON — no markdown fencing, no extra text:
{{
  "score": <integer 1–10>,
  "reason": "<one sentence: why this score>",
  "disqualifiers": ["<hard blocker if any — empty list if none>"],
  "gaps": ["<cert or skill the job wants that the candidate lacks — empty list if none>"],
  "recommendation": "<APPLY NOW | REVIEW MANUALLY | SKIP>"
}}

SCORING GUIDE:
  9–10  Excellent match, meets nearly all requirements
  7–8   Good match, meets most requirements with minor gaps
  5–6   Partial match, worth manual review
  1–4   Poor match or significant mismatches

HARD DISQUALIFIERS — only these warrant a SKIP:
  - Requires a 4-year degree AND does NOT offer an equivalent experience path
  - Requires 5 or more years of direct experience with no transferable route
  - Salary clearly stated below $50,000
  - On-site only with no remote or hybrid option

DEGREE NOTE: If the posting says "bachelor's degree OR equivalent experience," "degree preferred,"
  or "degree or equivalent," this is NOT a disqualifier. Evaluate whether the candidate's
  practical experience, certifications, and projects are reasonably equivalent.

MATCHING RULES — exact requirement matching is NOT required:
  - Evaluate holistically: transferable skills, certifications, personal projects, and
    practical experience all count toward meeting requirements.
  - If the candidate covers 60%+ of requirements through any combination of direct or
    transferable experience, do not skip — recommend REVIEW MANUALLY or APPLY NOW.
  - A+ certification, customer-facing roles, IT projects, and self-study count as evidence.
  - Missing a specific tool or software = flag as a gap only, never a disqualifier.

RECOMMENDATION RULES:
  APPLY NOW      → score >= {cfg.AI_SCORE_THRESHOLD} and no hard disqualifiers
  REVIEW MANUALLY → score 5–6, OR transferable skills offset notable gaps
  SKIP            → score <= 4 or a true hard disqualifier applies"""

    response = client.messages.create(
        model=cfg.AI_MODEL,
        max_tokens=cfg.AI_SCORING_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fencing in case Claude added it despite the instruction
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()

    return json.loads(raw)


# =============================================================================
#  STEP 3 — DOCUMENT GENERATION
#  Calls resume_tailor and cover_letter functions directly (no file I/O or
#  interactive prompts needed — the job description is passed as a string).
#  Saves output files and returns just the filenames (not full paths).
# =============================================================================

def generate_documents(job, resume_text, bio_text, api_key, today_str):
    """
    Generate an ATS-optimized DOCX resume and a DOCX cover letter for one APPLY NOW job.

    Returns (resume_filename, cover_letter_filename, ats_score_int) as a 3-tuple.
    resume_filename goes to /resumes; cover_letter_filename goes to /Cover Letters.
    Raises on API or file-write errors — caller handles the exception.
    """
    RESUMES_DIR.mkdir(exist_ok=True)
    COVER_LETTERS_DIR.mkdir(exist_ok=True)

    company = job["company"] or resume_tailor.guess_company(job["description"])
    role    = cover_letter_mod.guess_role(job["description"])

    # — ATS-optimized DOCX resume —
    docx_result = resume_builder.generate_ats_docx(
        jd_text=job["description"],
        company=company,
        job_title=role,
        api_key=api_key,
        out_dir=RESUMES_DIR,
    )
    resume_filename = docx_result.get("filename") or ""
    ats_score_val   = docx_result.get("ats_score", 0)

    if docx_result.get("below_threshold"):
        print(f"    ATS score {ats_score_val}% (below 80%). Missing: {', '.join(docx_result.get('missing', [])[:8])}")
    else:
        print(f"    ATS score: {ats_score_val}%")

    if docx_result.get("error"):
        print(f"    Warning: {docx_result['error']}")

    # Pause between API calls
    time.sleep(cfg.AI_API_DELAY_SECONDS)

    # — Cover letter (DOCX, same logic as manual button) —
    cl_result = cover_letter_mod.generate_cover_letter_docx(
        jd_text=job["description"],
        company=company,
        role=role,
        api_key=api_key,
        out_dir=COVER_LETTERS_DIR,
    )
    cl_filename = cl_result.get("filename") or ""
    if cl_result.get("error"):
        print(f"    Cover letter warning: {cl_result['error']}")

    return resume_filename, cl_filename, ats_score_val


# =============================================================================
#  STEP 4 — UPDATE TRACKER
#  Reads job_tracker.csv, merges AI assessment data into rows that match by
#  Application URL, and rewrites the file with the extended column schema.
#  Rows from previous runs that weren't scored today are preserved unchanged.
# =============================================================================

def update_tracker(scored_results, t2_scored=None):
    """Write AI scores, recommendations, gaps, and doc filenames into the tracker."""
    # Build URL → result lookups for T1 and T2
    by_url_t1 = {
        r["job"]["url"].lower().rstrip("/"): r
        for r in scored_results
    }
    by_url_t2 = {
        r["job"]["url"].lower().rstrip("/"): r
        for r in (t2_scored or [])
    }

    # Read current tracker
    existing_rows = []
    if TRACKER_PATH.exists():
        try:
            with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
                existing_rows = list(csv.DictReader(f))
        except Exception as exc:
            print(f"  Warning: could not read tracker — {exc}")

    for row in existing_rows:
        url_key = row.get("Application URL", "").lower().rstrip("/")

        # ── Track 1 rows ──────────────────────────────────────────────────────
        if url_key in by_url_t1:
            r          = by_url_t1[url_key]
            assessment = r["assessment"]
            row["Track"]             = "1"
            row["AI Score"]          = assessment.get("score", "")
            row["AI Recommendation"] = assessment.get("recommendation", "")
            row["AI Reason"]         = assessment.get("reason", "")
            row["Disqualifiers"]     = "; ".join(assessment.get("disqualifiers", []))
            row["Gaps"]              = "; ".join(assessment.get("gaps", []))
            row["Resume File"]       = r.get("resume_file") or ""
            row["Cover Letter File"] = r.get("cover_letter_file") or ""
            row["ATS Score"]         = r.get("ats_score") or ""
            if r.get("resume_file"):
                row["Resume Version Used"] = r["resume_file"]
            if r.get("cover_letter_file"):
                row["Cover Letter Sent (Y/N)"] = "Y"
            rec = assessment.get("recommendation", "")
            if rec == "APPLY NOW":
                row["Status"] = "Ready to Apply"
            elif rec == "SKIP":
                row["Status"] = "Skipped — AI"
            # REVIEW MANUALLY leaves status unchanged for human review

        # ── Track 2 rows ──────────────────────────────────────────────────────
        elif url_key in by_url_t2:
            r         = by_url_t2[url_key]
            t2_result = r.get("t2_result", {})
            t2_score  = t2_result.get("score", 0)
            phone_st  = t2_result.get("phone_status", "")
            row["Track"]            = "2"
            row["T2 Score"]         = t2_score
            row["T2 Remote Status"] = t2_result.get("remote_status", "")
            row["T2 Phone Status"]  = phone_st
            if phone_st == "excluded":
                row["Status"] = "Skipped — Phone Required"
            elif t2_score >= 75:
                row["Status"] = "Ready to Apply"
            elif t2_score >= 50:
                row["Status"] = "Review — T2"
            else:
                row["Status"] = "Skipped — Low T2 Score"

    # Rewrite the CSV with the extended schema
    with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=FULL_CSV_FIELDNAMES, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(existing_rows)


# =============================================================================
#  TERMINAL REPORT
#  Printed after all four steps complete — summarises what happened.
# =============================================================================

def print_report(scored_results, api_errors):
    """Print a clean end-of-run summary to the terminal."""
    apply_now = [r for r in scored_results if r["assessment"].get("recommendation") == "APPLY NOW"]
    review    = [r for r in scored_results if r["assessment"].get("recommendation") == "REVIEW MANUALLY"]
    skipped   = [r for r in scored_results if r["assessment"].get("recommendation") == "SKIP"]
    docs_made = [r for r in apply_now if r.get("resume_file")]

    sep = "=" * 62

    print(f"\n{sep}")
    print(f"  JOB AGENT REPORT  |  {datetime.date.today()}")
    print(sep)
    print(f"  Total jobs pulled:              {len(scored_results):>4}")
    print(f"  Scored by AI:                   {len(scored_results) - len(api_errors):>4}")
    print(f"  API errors:                     {len(api_errors):>4}")
    print()
    print(f"  APPLY NOW  (score {cfg.AI_SCORE_THRESHOLD}+):         {len(apply_now):>4}")
    print(f"  REVIEW MANUALLY:                {len(review):>4}")
    print(f"  SKIP:                           {len(skipped):>4}")

    if docs_made:
        print(f"\n  DOCUMENTS GENERATED  ({len(docs_made)} job(s))")
        print("  " + "-" * 58)
        for r in docs_made:
            job      = r["job"]
            score    = r["assessment"].get("score", "?")
            ats_val  = r.get("ats_score", "")
            ats_str  = f"  ATS {ats_val}%" if ats_val else ""
            print(f"  [{score}/10]{ats_str}  {job['title']} — {job['company'] or 'Unknown'}")
            print(f"         Resume:  {r['resume_file']}")
            print(f"         Letter:  {r['cover_letter_file']}")
            print(f"         Reason:  {r['assessment'].get('reason', '')}")
            gaps = r["assessment"].get("gaps", [])
            if gaps:
                print(f"         Gaps:    {', '.join(gaps)}")
            print()

    elif apply_now:
        print(f"\n  {len(apply_now)} APPLY NOW job(s) ready — generate docs manually from the dashboard.")

    if api_errors:
        print(f"\n  API ERRORS ({len(api_errors)})")
        print("  " + "-" * 58)
        for err in api_errors:
            job = err.get("job", {})
            print(f"  - {job.get('title', 'Unknown')} @ {job.get('company', 'Unknown')}")
            print(f"    {err.get('error', '')}")

    print(f"\n  Tracker: {TRACKER_PATH}")
    print(sep)
    print()


# =============================================================================
#  MAIN
# =============================================================================

def main():
    today_str = datetime.date.today().isoformat()

    # ── Set up run log ────────────────────────────────────────────────────────
    LOGS_DIR.mkdir(exist_ok=True)
    _run_ts   = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    _log_file = open(LOGS_DIR / f"agent_{_run_ts}.log", "w", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, _log_file)

    print(f"\n{'=' * 62}")
    print(f"  JOB AGENT  |  {today_str}")
    print(f"{'=' * 62}")

    # ── Validate input files ──────────────────────────────────────────────────
    for path, name in [(RESUME_FILE, "master_resume.txt"), (BIO_FILE, "bio.txt")]:
        if not path.exists():
            sys.exit(f"ERROR: {name} not found.\nExpected: {path}")
        if not path.read_text(encoding="utf-8").strip():
            sys.exit(f"ERROR: {name} is empty — fill it in before running the agent.")

    resume_text = RESUME_FILE.read_text(encoding="utf-8").strip()
    bio_text    = BIO_FILE.read_text(encoding="utf-8").strip()

    # ── Validate API key ──────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        sys.exit(
            "ERROR: ANTHROPIC_API_KEY environment variable is not set.\n"
            "Fix (permanent, PowerShell):\n"
            '  [System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")'
        )

    # ── Step 0: Purge any scam rows already in tracker ───────────────────────
    print("\n[0/4]  SCAM CLEANUP")
    print("-" * 62)
    purge_scam_rows()

    # ── Step 1: Fetch Track 1 jobs ────────────────────────────────────────────
    print("\n[1/4]  FETCHING TRACK 1 JOB LISTINGS  (Tech & Cybersecurity)")
    print("-" * 62)
    jobs = get_fresh_jobs()
    emit_event("fetched", total=len(jobs))

    if not jobs:
        print("\nNo Track 1 jobs matched today's filters.")
        print("Try expanding ROLE_TERMS / LEVEL_TERMS or increasing MAX_AGE_HOURS in config.py.")

    # ── Step 1B: Fetch Track 2 jobs ───────────────────────────────────────────
    print("\n[1B/4]  FETCHING TRACK 2 JOB LISTINGS  (Remote Income)")
    print("-" * 62)
    t1_urls = {j["url"] for j in jobs}

    # Read selected sources (same env var used by get_fresh_jobs)
    _sel_raw_t2 = os.environ.get("JOURNEY_SELECTED_SOURCES", "")
    if _sel_raw_t2.strip():
        _selected_t2 = {s.strip().lower() for s in _sel_raw_t2.split(",") if s.strip()}
    else:
        _legacy_t2 = os.environ.get("JOURNEY_SOURCE_MODE", "both")
        if _legacy_t2 == "direct":
            _selected_t2 = {"usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy"}
        elif _legacy_t2 == "apify":
            _selected_t2 = {"linkedin", "indeed", "glassdoor", "google_jobs", "dice"}
        else:
            _selected_t2 = {"usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy",
                            "linkedin", "indeed", "glassdoor", "google_jobs", "dice"}

    _apify_labels_t2 = {
        "linkedin":    "LinkedIn (Apify)",
        "indeed":      "Indeed (Apify)",
        "glassdoor":   "Glassdoor (Apify)",
        "google_jobs": "Google Jobs (Apify)",
        "dice":        "Dice (Apify)",
    }
    _sel_apify_labels_t2 = {_apify_labels_t2[k] for k in _selected_t2 if k in _apify_labels_t2}
    _run_direct_t2 = bool(_selected_t2 - set(_apify_labels_t2.keys()))
    _run_apify_t2  = bool(_sel_apify_labels_t2)

    if not _run_direct_t2:
        print("  [T2 Direct] Skipped — not selected.")
        t2_jobs = []
    else:
        t2_jobs = job_feed.get_t2_jobs(exclude_urls=t1_urls)

    # Merge Track 2 jobs from Apify cache (tagged track="2" by apify_feed)
    try:
        import apify_feed as _af2
        if not _run_apify_t2:
            pass  # Apify skipped by mission setting
        elif not _af2.cache_is_stale(getattr(cfg, "APIFY_CACHE_MAX_HOURS", 24)) or not _run_direct_t2:
            apify_all, _, _ = _af2.load_cache()
            all_seen_t2 = t1_urls | {j["url"] for j in t2_jobs}
            apify_t2_new = []
            for j in apify_all:
                if (j.get("track") == "2"
                        and j["url"] not in all_seen_t2
                        and j.get("source", "") in _sel_apify_labels_t2):
                    all_seen_t2.add(j["url"])
                    j["track"] = "2"
                    apify_t2_new.append(j)
            if apify_t2_new:
                filtered_t2 = job_feed.apply_t2_filters(apify_t2_new, age_hours=getattr(cfg, "MAX_AGE_HOURS", 168))
                t2_jobs.extend(filtered_t2)
                print(f"  [Apify] {len(apify_t2_new)} T2 cached → {len(filtered_t2)} passed T2 filters")
    except Exception as _apify_t2_exc:
        print(f"  [Apify T2] Skipped: {_apify_t2_exc}")

    emit_event("t2_fetched", total=len(t2_jobs))
    print(f"  Track 2: {len(t2_jobs)} job(s) after filters.")

    if not jobs and not t2_jobs:
        print("\nNo jobs matched today's filters in either track.")
        return

    # ── Step 2: Score Track 1 with AI ────────────────────────────────────────
    client     = anthropic.Anthropic(api_key=api_key)
    api_errors = []
    scored_results = []

    if jobs:
        print(f"\n[2/4]  SCORING {len(jobs)} TRACK 1 JOB(S) WITH AI  (model: {cfg.AI_MODEL})")
        print("-" * 62)
        scored_results = _score_batch(
            jobs, resume_text, bio_text, client, api_errors,
            idx_offset=0, total=len(jobs),
        )

        # Fallback: expand to 30-day window if too few qualifying jobs
        qualifying_count = sum(
            1 for r in scored_results if r["assessment"].get("score", 0) >= 6
        )
        if qualifying_count < cfg.QUALIFY_MIN_COUNT:
            print(f"\n  {qualifying_count} job(s) scored 6+ (threshold: {cfg.QUALIFY_MIN_COUNT}).")
            print(f"  Expanding search to {cfg.FALLBACK_AGE_HOURS // 24}-day window...")
            seen_urls   = {r["job"]["url"] for r in scored_results}
            extra_jobs  = get_fresh_jobs(age_hours=cfg.FALLBACK_AGE_HOURS, exclude_urls=seen_urls)
            if extra_jobs:
                emit_event("fetched", total=len(jobs) + len(extra_jobs))
                print(f"\n  Scoring {len(extra_jobs)} additional job(s) from extended window...")
                extra_scored = _score_batch(
                    extra_jobs, resume_text, bio_text, client, api_errors,
                    idx_offset=len(scored_results), total=len(scored_results) + len(extra_jobs),
                )
                scored_results.extend(extra_scored)
            else:
                print("  No additional jobs found in the extended window.")
    else:
        print("\n[2/4]  No Track 1 jobs to score — skipping AI scoring.")

    # ── Step 2B: Score Track 2 with rule-based rubric ────────────────────────
    t2_scored = []
    if t2_jobs:
        print(f"\n[2B/4]  SCORING {len(t2_jobs)} TRACK 2 JOB(S)  (rule-based, no API cost)")
        print("-" * 62)
        job_feed.append_to_tracker(t2_jobs)
        for t2j in t2_jobs:
            result   = score_t2_job(t2j)
            t2_score = result["score"]
            label    = "⚡ STRONG" if t2_score >= 75 else "MATCH" if t2_score >= 50 else "REVIEW"
            print(f"  T2 [{t2_score:>3}/100] {t2j['title'][:38]:<38} @ {(t2j.get('company') or '?')[:16]:<16} — {label}")

            t2_scored.append({
                "job":       t2j,
                "t2_result": result,
                "resume_file":       None,
                "cover_letter_file": None,
            })

            if t2_score >= 75:
                _dp2 = t2j.get("date_posted")
                emit_event(
                    "t2_strong_match",
                    title=t2j["title"],
                    company=t2j.get("company") or "",
                    score=t2_score,
                    remote_status=result["remote_status"],
                    phone_status=result["phone_status"],
                    track_label="Track 2 Match — Remote Income",
                    date_posted=_dp2.strftime("%Y-%m-%d") if _dp2 else "",
                )
    else:
        print("\n[2B/4]  No Track 2 jobs fetched — skipping T2 scoring.")

    # ── Step 3: Document generation skipped — generate manually from the dashboard ──
    apply_now = [
        r for r in scored_results
        if r["assessment"].get("recommendation") == "APPLY NOW"
        and r["assessment"].get("score", 0) >= cfg.AI_SCORE_THRESHOLD
    ]
    print(f"\n[3/4]  {len(apply_now)} APPLY NOW job(s) ready — use 📄 GEN BOTH on each card to generate docs.")
    print("-" * 62)

    # ── Step 4: Update tracker ────────────────────────────────────────────────
    print(f"\n[4/4]  UPDATING JOB TRACKER")
    print("-" * 62)
    update_tracker(scored_results, t2_scored=t2_scored)
    print(f"  {TRACKER_PATH.name} updated.")

    # ── Final report ──────────────────────────────────────────────────────────
    print_report(scored_results, api_errors)

    t2_strong  = len([r for r in t2_scored if r["t2_result"].get("score", 0) >= 75])
    t2_match   = len([r for r in t2_scored if 50 <= r["t2_result"].get("score", 0) < 75])
    if t2_scored:
        sep = "=" * 62
        print(f"\n{sep}")
        print(f"  TRACK 2 REPORT  (Remote Income)")
        print(sep)
        print(f"  Total T2 scored:     {len(t2_scored):>4}")
        print(f"  Strong match (75+):  {t2_strong:>4}")
        print(f"  Match (50–74):       {t2_match:>4}")
        print(sep)

    # ── Emit completion event for dashboard ───────────────────────────────────
    _apply_c  = len([r for r in scored_results if r["assessment"].get("recommendation") == "APPLY NOW"])
    _review_c = len([r for r in scored_results if r["assessment"].get("recommendation") == "REVIEW MANUALLY"])
    _skip_c   = len([r for r in scored_results if r["assessment"].get("recommendation") == "SKIP"])
    emit_event("complete",
        apply=_apply_c, review=_review_c, skip=_skip_c, errors=len(api_errors),
        t2_total=len(t2_scored), t2_strong=t2_strong,
    )
    _log_file.close()


if __name__ == "__main__":
    main()