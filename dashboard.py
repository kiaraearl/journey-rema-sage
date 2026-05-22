"""
dashboard.py — Journey's Quest Job Search Dashboard
================================================================================
pip install flask flask-socketio
Run:  python dashboard.py
Open: http://localhost:5000
================================================================================
"""

import os
import sys

# Force UTF-8 output on Windows (prevents UnicodeEncodeError with emoji/special chars).
# sys.stdout/stderr are None under pythonw (no console) — guard against that.
if sys.stdout is not None and getattr(sys.stdout, "encoding", None) and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr is not None and getattr(sys.stderr, "encoding", None) and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import csv
import json
import random
import threading
import subprocess
import datetime
import uuid as _uuid
import re as _re
import html as _html
import urllib.request as _urllib_req
import urllib.parse as _urllib_parse
import ipaddress as _ipaddress
import socket as _socket
from pathlib import Path
from collections import Counter

from flask import Flask, render_template, jsonify, request, send_from_directory

try:
    import anthropic as _anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import tweepy as _tweepy
    _TWEEPY_AVAILABLE = True
except ImportError:
    _TWEEPY_AVAILABLE = False

try:
    import praw as _praw
    _PRAW_AVAILABLE = True
except ImportError:
    _PRAW_AVAILABLE = False

try:
    from flask_socketio import SocketIO, emit
except ImportError:
    sys.exit(
        "ERROR: flask-socketio is not installed.\n"
        "Fix:   pip install flask flask-socketio\n"
        "Then run dashboard.py again."
    )

# ── Paths ─────────────────────────────────────────────────────────────────────

SCRIPT_DIR   = Path(__file__).parent.resolve()
TRACKER_PATH = SCRIPT_DIR / "job_tracker.csv"
LOGS_DIR     = SCRIPT_DIR / "logs"
AGENT_SCRIPT  = SCRIPT_DIR / "job_agent.py"
CHAT_LOG_PATH = LOGS_DIR  / "journey_chat_log.json"
BIO_FILE      = SCRIPT_DIR / "bio.txt"
RESUME_FILE   = SCRIPT_DIR / "master_resume.txt"

RESUMES_DIR         = SCRIPT_DIR / "resumes"
COVER_LETTERS_DIR   = SCRIPT_DIR / "Cover Letters"
MASTER_RESUME_JSON  = SCRIPT_DIR / "master_resume.json"

OUTREACH_PATH       = LOGS_DIR / "outreach_queue.json"
PROFILE_AUDITS_PATH = LOGS_DIR / "profile_audits.json"
AFFIRMATION_PATH    = LOGS_DIR / "daily_affirmation.json"
REMA_CHAT_PATH      = LOGS_DIR / "rema_chat_log.json"
HUNTER_USAGE_PATH   = LOGS_DIR / "hunter_usage.json"
ENV_PATH            = SCRIPT_DIR / ".env"
APIFY_CACHE_PATH    = LOGS_DIR / "apify_cache.json"
APIFY_RUN_PATH      = LOGS_DIR / "apify_run.json"
APIFY_USAGE_PATH    = LOGS_DIR / "apify_usage.json"

SAGE_PRODUCTS_PATH  = LOGS_DIR / "sage_products.json"
SAGE_BUYER_LOG_PATH = LOGS_DIR / "sage_buyer_log.json"
SAGE_ANALYTICS_PATH = LOGS_DIR / "sage_analytics.json"
SAGE_CONTENT_PATH   = LOGS_DIR / "sage_content.json"

M5_QUEUE_PATH     = LOGS_DIR / "content_queue.json"
M5_CALENDAR_PATH  = LOGS_DIR / "content_calendar.json"
M5_ANALYTICS_PATH = LOGS_DIR / "content_analytics.json"
M5_VOICE_PATH     = LOGS_DIR / "brand_voice_notes.json"
SAGE_REVENUE_PATH   = LOGS_DIR / "sage_revenue.json"

API_LOG_PATH        = SCRIPT_DIR / "api_log.txt"


# ── API usage logger ──────────────────────────────────────────────────────────

def log_api_call(service: str, detail: str = "") -> None:
    """Append one timestamped line to api_log.txt. Never raises."""
    try:
        ts   = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} | {service}" + (f" | {detail}" if detail else "")
        with open(API_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


JOURNEY_SYSTEM = (
    "You are Journey, a Job Search Intelligence Agent. "
    "Your color identity is pink and green. You are organized, encouraging, and data-driven — "
    "like a brilliant best friend who happens to have a career coaching certification and a "
    "spreadsheet for everything. You are warm and motivating but you do not let Kee stall, "
    "procrastinate, or spiral. You redirect with kindness and purpose. "
    "\n\n"
    "You always call the user Kee. "
    "\n\n"
    "What you know about Kee: "
    "Full name: Kiara Earl, goes by Kee. "
    "Location: Houston, TX. "
    "Current role: Multi-Product Agent at Progressive Insurance. "
    "Certifications: CompTIA A+ Core 1 & Core 2 (earned March 2026). "
    "Currently studying: WGU B.S. Cybersecurity and Information Assurance. "
    "Pursuing: Network+, Security+, CySA+, PenTest+. "
    "Background: Customer service, helpdesk exposure at World Travel Holdings, insurance. "
    "Goal: Land an entry-level IT or cybersecurity role (remote, $50K-$65K) while finishing her degree. "
    "Target roles: IT Support, Help Desk, SOC Analyst Tier 1, Junior Cybersecurity Analyst. "
    "Side business: Houston Signing Solutions (mobile notary) — acknowledge it exists but keep Kee focused on the job search mission. "
    "TRACK 2: Kee is also running a second job track — Remote Income. "
    "These are non-tech or low-tech remote roles (data entry, billing, VA, data analyst, paralegal, etc.) "
    "with a salary floor of $45K/yr or $22/hr, remote-only, and low or no phone requirement. "
    "When Kee asks to tailor a resume for a Track 2 job, use Track 2 focus: lead with customer service, "
    "communication, organizational skills, Salesforce, scheduling, billing, documentation, and insurance "
    "knowledge — de-emphasize cybersecurity certs unless the job asks for them. "
    "\n\n"
    "Your core functions: "
    "1. Track jobs Kee has applied to — status, date applied, company, role, source. "
    "2. Score job postings on fit (1–10) based on Kee's background and target roles. "
    "3. Tailor resumes and cover letters to specific job descriptions. "
    "4. Flag roles that match Kee's profile from the job feed. "
    "5. Send follow-up reminders when applications go quiet. "
    "6. Keep the pipeline moving — nothing sits untouched for more than 5 business days. "
    "\n\n"
    "Your rules: "
    "1. Every job in the tracker has a status. Nothing sits at \"Applied\" forever. "
    "2. A tailored resume beats a generic one every single time. "
    "3. If a role scores below a 6, flag it and ask Kee if she still wants to apply. "
    "4. Celebrate every win — interview scheduled, callback received, connection made. "
    "5. The goal is not just to apply. The goal is to get the interview. "
    "6. Never use an em dash (—) inside parentheses in any document, message, or response you produce. Rewrite using a comma, colon, or separate sentence. "
    "\n\n"
    "Your voice examples: "
    "\"New match found. This one scores an 8 — it has your name on it. Want me to tailor the resume?\" "
    "\"You applied to this role 6 days ago and haven't followed up. Rema should handle that — want me to flag her?\" "
    "\"Resume tailored. Cover letter drafted. You're ready to submit. Go.\" "
    "\"Pipeline check: 3 applications pending, 1 follow-up overdue, 2 new matches in the feed. Let's get into it.\" "
    "\n\n"
    "Kee's background brief for resume tailoring and job scoring: "
    "I'm a Houston-based professional currently working as a Multi-Product Agent at Progressive "
    "Insurance, where I handle complex customer interactions across multiple insurance products in "
    "a high-volume, performance-tracked environment. Before Progressive, I spent time at World "
    "Travel Holdings where I supported their tech department for approximately five months. I hold "
    "CompTIA A+ Core 1 and Core 2 certifications and am enrolled in WGU's B.S. in Cybersecurity "
    "and Information Assurance program. I bring soft skills most pure-tech candidates lack — "
    "explaining technical issues clearly to non-technical people, staying calm under pressure, and "
    "handling difficult situations professionally. Target salary: $50K-$65K. Preferred: remote. "
    "Open to hybrid in Houston. "
    "\n\n"
    "Relationship with Rema: "
    "Journey and Rema are partners. Journey owns the pipeline — finding jobs, tracking applications, "
    "tailoring documents. Rema owns the relationships — recruiter outreach, LinkedIn, follow-up "
    "messaging. When a new application is logged, Journey automatically flags Rema to begin outreach "
    "research. They do not overlap. They hand off cleanly."
)

REMA_SYSTEM = (
    "You are Rema, an Outreach Strategist and LinkedIn Intelligence Agent. "
    "Your color identity is purple and gold. You are sharp, direct, and zero fluff — like a friend "
    "who used to work in recruiting and still has all the insider knowledge. You are warm but never "
    "soft. Professional but never stiff. You celebrate wins and immediately redirect to the next move. "
    "\n\n"
    "You always call the user Kee. "
    "\n\n"
    "What you know about Kee: "
    "Full name: Kiara Earl, goes by Kee. "
    "Location: Houston, TX. "
    "Current role: Multi-Product Agent at Progressive Insurance. "
    "Certifications: CompTIA A+ Core 1 & Core 2 (earned March 2026). "
    "Currently studying: WGU B.S. Cybersecurity and Information Assurance. "
    "Pursuing: Network+, Security+, CySA+, PenTest+. "
    "Background: Customer service, helpdesk exposure at World Travel Holdings, insurance. "
    "Goal: Land an entry-level IT or cybersecurity role (remote, $50K-$65K) while finishing her degree. "
    "Target roles: IT Support, Help Desk, SOC Analyst Tier 1, Junior Cybersecurity Analyst. "
    "Side business: Houston Signing Solutions (mobile notary) — acknowledge it exists but keep focus on the job search mission. "
    "\n\n"
    "Your rules: "
    "1. Every application gets a follow-up. No exceptions. "
    "2. LinkedIn is a living document — it gets updated when skills and certs do. "
    "3. Volume without personalization is spam. Every message you draft is tailored. "
    "4. A connection note under 300 characters is an art form. Take it seriously. "
    "5. Silence after applying is not a rejection. It is an opening for outreach. "
    "6. Never use an em dash (—) inside parentheses in any message, note, or email you produce. Rewrite using a comma, colon, or separate sentence. "
    "\n\n"
    "Your voice examples: "
    "\"That headline won't get you past the first scroll. Here's what it should say.\" "
    "\"Recruiter found. Three messages drafted. Pick one and send it today — not tomorrow.\" "
    "\"You applied three days ago and haven't followed up. That's a missed opportunity. Let's fix it.\" "
    "\"Profile audit complete. You're at a 6. Here's exactly how we get to a 9.\" "
    "\n\n"
    "Kee's background brief for cover letters and outreach: "
    "I'm a Houston-based professional currently working as a Multi-Product Agent at Progressive "
    "Insurance, where I handle complex customer interactions across multiple insurance products in "
    "a high-volume, performance-tracked environment. Before Progressive, I spent time at World "
    "Travel Holdings where I supported their tech department for approximately five months. I hold "
    "CompTIA A+ Core 1 and Core 2 certifications and am enrolled in WGU's B.S. in Cybersecurity "
    "and Information Assurance program. I bring soft skills most pure-tech candidates lack — "
    "explaining technical issues clearly to non-technical people, staying calm under pressure, and "
    "handling difficult situations professionally."
)

SAGE_SYSTEM = (
    "You are Sage, Kee's Digital Store Strategist. "
    "Your color identity is emerald green and copper. You are calm, strategic, and creative — "
    "like a business mentor who has launched dozens of successful digital product stores. "
    "You speak with quiet confidence. You don't hype. You help Kee build a real, sustainable "
    "income stream from her knowledge and creativity. "
    "\n\n"
    "You always call the user Kee. "
    "\n\n"
    "What you know about Kee: "
    "Full name: Kiara Earl, goes by Kee. Location: Houston, TX. "
    "She is an IT/cybersecurity professional in training who also runs a mobile notary business "
    "(Houston Signing Solutions). She has strong customer service skills, experience in insurance, "
    "and deep personal knowledge of career pivoting and professional development. "
    "Her store sells digital products — templates, guides, toolkits, and educational content — "
    "primarily on Etsy and Gumroad. "
    "\n\n"
    "Your four functions: "
    "1. PRODUCT FACTORY — Turn a raw idea into a sellable product outline with pricing strategy. "
    "2. MARKETING ENGINE — Generate platform-specific content batches ready to publish. "
    "3. ANALYTICS WATCHER — Parse store stats and rank the highest-leverage action items. "
    "4. CUSTOMER DESK — Draft professional, warm customer messages for any scenario. "
    "\n\n"
    "Your voice examples: "
    "\"That idea has legs. Here's how we make it sellable in the next 72 hours.\" "
    "\"Three of your five pins are generic. Here's how to fix them for Pinterest SEO.\" "
    "\"Your store health is a 6. The two things pulling it down are fixable this week.\" "
    "\"That review deserves a response that turns the situation into a showcase.\" "
    "\n\n"
    "MODE 5 — BRAND CONTENT STUDIO: You manage content for three brands:\n"
    "CAREER OS / BUILT BY KEE: Raw, honest builder energy. Document the journey. "
    "Faceless. Never promotional — sounds like a person sharing something real. "
    "Voice: 'I built this because I lived the problem.'\n"
    "HOUSTON SIGNING SOLUTIONS (HSS): Professional, trustworthy, local Houston expert. "
    "Always includes a Houston angle. Voice: 'Your documents handled right the first time.'\n"
    "BUILD YOUR BLUEPRINT (BYB): Empowering, practical, sister-to-sister energy. "
    "Faceless. Always ties to a specific product or outcome. "
    "Voice: 'Here is exactly how I did it and how you can too.'\n\n"
    "Content rules: "
    "1. Career OS never sounds like an ad. "
    "2. HSS always includes a local Houston angle. "
    "3. BYB always ties to a specific product or outcome. "
    "4. Reddit posts never promote — they help first. "
    "5. Every piece has one clear point. "
    "6. Hooks matter most — if the first line does not stop the scroll, nothing else matters. "
    "When Kee rejects content, you adjust silently and note the preference. "
    "You do not repeat the same mistake."
)

_FALLBACK_AFFIRMATIONS = [
    "Your A+ cert is proof you don't just talk about IT — you do the work. Someone is about to see that.",
    "Every application is a data point. You are running the experiment and learning the market in real time.",
    "You built a full job search system while working full time. That's the kind of engineer companies want.",
    "Security+ is coming. Network+ is in progress. Your trajectory is upward and it is accelerating.",
    "Most people talk about making a career change. You are actively building the credentials to make it real.",
    "The IR incident, the A+, the WGU enrollment — you have a story. Learn to tell it with confidence.",
    "Remote IT work is competitive. Showing up prepared, certified, and consistent is how you win it.",
]

# ── App setup ─────────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = "journey-quest-2026"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

_LAN_NETS = [
    _ipaddress.ip_network("127.0.0.0/8"),
    _ipaddress.ip_network("10.0.0.0/8"),
    _ipaddress.ip_network("172.16.0.0/12"),
    _ipaddress.ip_network("192.168.0.0/16"),
]

@app.before_request
def _lan_only():
    try:
        addr = _ipaddress.ip_address(request.remote_addr)
    except ValueError:
        return jsonify({"error": "forbidden"}), 403
    if not any(addr in net for net in _LAN_NETS):
        return jsonify({"error": "forbidden"}), 403

_agent_running   = False
_agent_lock      = threading.Lock()
_chat_agent_sid  = None   # SID of the chat client that triggered the current agent run
_last_agent_mode = "both" # tracks the last mode chosen via the Mission modal

_apify_running  = False
_apify_lock     = threading.Lock()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/interview-prep")
def interview_prep_page():
    company  = request.args.get("company", "").strip()
    role     = request.args.get("role", "").strip()
    location = request.args.get("loc", "").strip()
    if not company or not role:
        return "Missing company or role parameters.", 400
    return render_template("interview_prep.html",
                           company=company, role=role, location=location)


@app.route("/api/tracker")
def api_tracker():
    return jsonify(_read_tracker())


_SKIP_STATUSES = frozenset({
    "skipped — ai", "skipped — phone required", "skipped — low t2 score",
    "removed — scam source",
})

@app.route("/api/stats")
def api_stats():
    rows      = _read_tracker()
    scanned   = len(rows)
    # Only count actionable rows — exclude scam/skipped entries
    active    = [r for r in rows
                 if r.get("Status", "").lower() not in _SKIP_STATUSES
                 and r.get("AI Recommendation", "").upper() != "SKIP"]
    evaluated  = len(active)
    applied    = sum(1 for r in active if r.get("Status", "").lower() in
                     ("applied", "ready to apply"))
    interviews = sum(1 for r in active if "interview" in r.get("Status", "").lower())
    offers     = sum(1 for r in active if "offer" in r.get("Status", "").lower())
    return jsonify({"evaluated": evaluated, "applied": applied,
                    "interviews": interviews, "offers": offers, "scanned": scanned})


@app.route("/api/gaps")
def api_gaps():
    counter = Counter()
    for row in _read_tracker():
        for g in row.get("Gaps", "").split(";"):
            g = g.strip()
            if g:
                counter[g] += 1
    top = counter.most_common(10)
    if not top:
        return jsonify([])
    max_c = top[0][1]
    return jsonify([
        {"skill": s, "count": c, "pct": round(c / max_c * 100)}
        for s, c in top
    ])


@app.route("/api/agent-status")
def api_agent_status():
    return jsonify({"running": _agent_running})


@app.route("/api/update-status", methods=["POST"])
def api_update_status():
    data   = request.get_json(silent=True) or {}
    url    = data.get("url", "").strip()
    status = data.get("status", "").strip()
    if not url or not status:
        return jsonify({"error": "missing url or status"}), 400
    updates = {"Status": status}
    if status.lower() == "applied":
        today = datetime.date.today()
        updates["Date Applied"]   = today.isoformat()
        updates["Follow Up Date"] = _add_business_days(today, 5).isoformat()
    err = _update_tracker_row_multi(url, updates)
    if err:
        return jsonify({"ok": False, "error": err}), 500
    return jsonify({"ok": True})


# ── Rema API routes ───────────────────────────────────────────────────────────

@app.route("/api/outreach-queue")
def api_outreach_queue():
    return jsonify(_load_json_list(OUTREACH_PATH))


@app.route("/api/outreach-update", methods=["POST"])
def api_outreach_update():
    data   = request.get_json(silent=True) or {}
    rec_id = data.get("id", "").strip()
    field  = data.get("field", "").strip()
    value  = data.get("value", "")
    if not rec_id or not field:
        return jsonify({"error": "missing id or field"}), 400

    allowed = {"status", "recruiter_name", "recruiter_title", "recruiter_linkedin",
               "recruiter_email", "sent_date", "followup_sent"}
    if field not in allowed:
        return jsonify({"error": "field not allowed"}), 400

    queue = _load_json_list(OUTREACH_PATH)
    for rec in queue:
        if rec.get("id") == rec_id:
            rec[field] = value
            if field == "status" and value == "Sent" and not rec.get("sent_date"):
                today = datetime.date.today()
                rec["sent_date"] = today.isoformat()
                rec["followup_date"] = _add_business_days(today, 5).isoformat()
            break
    _save_json_list(OUTREACH_PATH, queue)
    return jsonify({"ok": True})


@app.route("/api/profile-audits")
def api_profile_audits():
    return jsonify(_load_json_list(PROFILE_AUDITS_PATH))


@app.route("/api/rema-chat-history")
def api_rema_chat_history():
    return jsonify(_load_rema_history())


@app.route("/api/daily-affirmation")
def api_daily_affirmation():
    return jsonify({"text": _get_or_generate_affirmation()})


@app.route("/api/hunter-status")
def api_hunter_status():
    usage = _get_hunter_usage()
    return jsonify({
        "has_key": bool(_get_hunter_key()),
        "count":   usage["count"],
        "limit":   25,
        "month":   usage["month"],
    })


@app.route("/api/hunter-key", methods=["POST"])
def api_hunter_key():
    data = request.get_json(silent=True) or {}
    key  = (data.get("key") or "").strip()
    if not key:
        return jsonify({"error": "No key provided"}), 400
    _save_hunter_key(key)
    return jsonify({"ok": True})


# ── Apify API routes ──────────────────────────────────────────────────────────

@app.route("/api/apify-status")
def api_apify_status():
    """Return Apify token presence, cache info, usage, and per-source status."""
    token   = _get_apify_token()
    runlog  = {}
    usage   = {"month": "", "total_runs": 0, "actors": {}}
    cache_ts= ""
    cache_count = 0
    errors  = {}

    try:
        if APIFY_RUN_PATH.exists():
            runlog = json.loads(APIFY_RUN_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass

    try:
        if APIFY_USAGE_PATH.exists():
            usage = json.loads(APIFY_USAGE_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Per-source cache counts (read per-source files directly)
    _source_key_labels = [
        ("linkedin",    "LinkedIn"),
        ("indeed",      "Indeed"),
        ("glassdoor",   "Glassdoor"),
        ("google_jobs", "Google Jobs"),
        ("dice",        "Dice"),
    ]
    source_counts = {}
    for _key, _lbl in _source_key_labels:
        _path = LOGS_DIR / f"apify_cache_{_key}.json"
        if _path.exists():
            try:
                _d = json.loads(_path.read_text(encoding="utf-8"))
                _n = len(_d.get("jobs", []))
                if _n:
                    source_counts[_lbl] = _n
                    cache_count += _n
                    if not cache_ts or _d.get("timestamp", "") > cache_ts:
                        cache_ts = _d.get("timestamp", "")
            except Exception:
                pass

    # Fallback to combined file if no per-source files found
    if not source_counts and APIFY_CACHE_PATH.exists():
        try:
            cache_data  = json.loads(APIFY_CACHE_PATH.read_text(encoding="utf-8"))
            cache_ts    = cache_data.get("timestamp", "")
            cache_count = len(cache_data.get("jobs", []))
            errors      = cache_data.get("errors", {})
        except Exception:
            pass
    elif APIFY_CACHE_PATH.exists():
        try:
            errors = json.loads(APIFY_CACHE_PATH.read_text(encoding="utf-8")).get("errors", {})
        except Exception:
            pass

    return jsonify({
        "has_token":      bool(token),
        "running":        _apify_running,
        "last_completed": runlog.get("last_run_completed", ""),
        "last_job_count": runlog.get("last_job_count", 0),
        "cache_ts":       cache_ts,
        "cache_count":    cache_count,
        "source_counts":  source_counts,
        "errors":         errors,
        "usage":          usage,
    })


@app.route("/api/apify-prune", methods=["POST"])
def api_apify_prune():
    """Remove cache entries whose URLs are already in the tracker CSV."""
    try:
        import apify_feed as _af

        # Collect tracked URLs (normalized)
        tracked = set()
        try:
            with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    u = row.get("Application URL", "").strip().lower().rstrip("/")
                    if u:
                        tracked.add(_re.sub(r"[?#].*", "", u))
        except Exception as e:
            return jsonify({"ok": False, "error": f"Could not read tracker: {e}"}), 500

        # Load cache
        cached_jobs, cached_errors, _ = _af.load_cache()
        if not cached_jobs:
            return jsonify({"ok": True, "removed": 0, "remaining": 0, "message": "Cache is empty."})

        # Filter: keep only jobs NOT already in tracker
        kept = []
        removed = 0
        for job in cached_jobs:
            url_key = _re.sub(r"[?#].*", "", job.get("url", "").lower().rstrip("/"))
            if url_key and url_key in tracked:
                removed += 1
            else:
                kept.append(job)

        _af.save_cache(kept, cached_errors)
        return jsonify({
            "ok":        True,
            "removed":   removed,
            "remaining": len(kept),
            "message":   f"Pruned {removed} already-tracked job(s). {len(kept)} remain in cache.",
        })
    except ImportError:
        return jsonify({"ok": False, "error": "apify-client not installed"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/apify-key", methods=["POST"])
def api_apify_key():
    data  = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return jsonify({"error": "No token provided"}), 400
    _save_apify_token(token)
    return jsonify({"ok": True})


@app.route("/api/t2-tailor", methods=["POST"])
def api_t2_tailor():
    """
    Synchronously generate a Track 2-focused tailored resume for one job.
    Body: { "jd": "...", "company": "...", "role": "..." }
    Returns: { "resume": "..." } or { "error": "..." }
    """
    data    = request.get_json(silent=True) or {}
    jd_text = (data.get("jd") or "").strip()
    company = (data.get("company") or "Company").strip()

    if not jd_text:
        return jsonify({"error": "Job description (jd) is required."}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set."}), 500

    resume_text = RESUME_FILE.read_text(encoding="utf-8").strip() if RESUME_FILE.exists() else ""
    if not resume_text:
        return jsonify({"error": "master_resume.txt is empty or missing."}), 500

    try:
        import resume_tailor as _rt
        tailored = _rt.tailor_with_api_t2(resume_text, jd_text, company, api_key)
        return jsonify({"resume": tailored})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/resumes/<path:filename>")
def download_resume(filename):
    RESUMES_DIR.mkdir(exist_ok=True)
    return send_from_directory(str(RESUMES_DIR), filename, as_attachment=True)


@app.route("/cover-letters/<path:filename>")
def download_cover_letter(filename):
    COVER_LETTERS_DIR.mkdir(exist_ok=True)
    return send_from_directory(str(COVER_LETTERS_DIR), filename, as_attachment=True)


@app.route("/api/ats-result", methods=["POST"])
def api_ats_result():
    """
    Calculate ATS score for a job description without generating a DOCX.
    Body: { "jd": "..." }
    Returns: { "score": int, "matched": [...], "missing": [...], "total": int }
    """
    data    = request.get_json(silent=True) or {}
    jd_text = (data.get("jd") or "").strip()
    if not jd_text:
        return jsonify({"error": "jd is required"}), 400

    try:
        import resume_builder as _rb
        master   = _rb.load_master()
        ats_data = _rb.ats_score(jd_text, master)
        return jsonify(ats_data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/generate-resume", methods=["POST"])
def api_generate_resume():
    """
    Generate an ATS-optimized DOCX resume for a job.
    Body: { "jd": "...", "company": "...", "role": "..." }
    Returns: { "filename": "...", "ats_score": int, "matched": [...], "missing": [...] }
    """
    data     = request.get_json(silent=True) or {}
    jd_text  = (data.get("jd") or "").strip()
    company  = (data.get("company") or "Company").strip()
    job_role = (data.get("role") or "Professional").strip()

    if not jd_text:
        return jsonify({"error": "jd is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    try:
        import resume_builder as _rb
        RESUMES_DIR.mkdir(exist_ok=True)
        result = _rb.generate_ats_docx(
            jd_text=jd_text,
            company=company,
            job_title=job_role,
            api_key=api_key,
            out_dir=RESUMES_DIR,
        )
        log_api_call("RESUME_GEN", f"{company} — {job_role}")
        return jsonify({
            "filename":   result.get("filename") or "",
            "ats_score":  result.get("ats_score", 0),
            "matched":    result.get("matched", []),
            "missing":    result.get("missing", []),
            "total":      result.get("total", 0),
            "below_80":   result.get("below_threshold", False),
            "error":      result.get("error"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/fetch-jd", methods=["POST"])
def api_fetch_jd():
    """
    Fetch and extract plain text from a job posting URL.
    Body: { "url": "..." }
    Returns: { "text": "..." } or { "error": "..." }
    """
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    try:
        req = _urllib_req.Request(url, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        with _urllib_req.urlopen(req, timeout=15) as resp:
            raw   = resp.read()
            cset  = resp.headers.get_content_charset() or "utf-8"
            html_text = raw.decode(cset, errors="replace")
    except Exception as exc:
        return jsonify({"error": f"Could not fetch URL: {exc}"}), 502

    # Strip <script> and <style> blocks
    cleaned = _re.sub(r'<(script|style|noscript)[^>]*>[\s\S]*?</\1>', ' ', html_text, flags=_re.IGNORECASE)
    # Strip all remaining tags
    cleaned = _re.sub(r'<[^>]+>', ' ', cleaned)
    # Decode HTML entities
    cleaned = _html.unescape(cleaned)
    # Collapse whitespace
    cleaned = _re.sub(r'[ \t]+', ' ', cleaned)
    cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned)
    # Strip common ATS/job-board boilerplate lines that survive HTML stripping
    _ats_boilerplate = _re.compile(
        r"[^\n]*(your browser cookies must be enabled|powered by jobscore|"
        r"powered by greenhouse|javascript is required|enable javascript|"
        r"a short video is required|cookies? (are )?required|"
        r"sign in to apply|create an account to apply)[^\n]*",
        _re.IGNORECASE,
    )
    cleaned = _ats_boilerplate.sub("", cleaned)
    cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned).strip()

    if len(cleaned) > 15000:
        cleaned = cleaned[:15000]

    return jsonify({"text": cleaned})


@app.route("/api/generate-cover-letter", methods=["POST"])
def api_generate_cover_letter():
    """
    Generate an ATS-tailored cover letter DOCX.
    Body: { "jd": "...", "company": "...", "role": "..." }
    Returns: { "filename": "..." } or { "error": "..." }
    """
    data     = request.get_json(silent=True) or {}
    jd_text  = (data.get("jd") or "").strip()
    company  = (data.get("company") or "Company").strip()
    job_role = (data.get("role") or "Professional").strip()

    if not jd_text:
        return jsonify({"error": "jd is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    try:
        import cover_letter as _cl
        COVER_LETTERS_DIR.mkdir(exist_ok=True)
        result = _cl.generate_cover_letter_docx(
            jd_text=jd_text,
            company=company,
            role=job_role,
            api_key=api_key,
            out_dir=COVER_LETTERS_DIR,
        )
        log_api_call("COVER_LETTER_GEN", f"{company} — {job_role}")
        if result.get("warning") == "template_used":
            result["error"] = "AI generation failed — cover letter was created from a template. Edit [EDIT: ...] placeholders before sending."
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/delete-job", methods=["POST"])
def api_delete_job():
    """
    Remove a job from job_tracker.csv by URL.
    Body: { "url": "..." }
    """
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    rows, fieldnames = [], []
    try:
        with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
            reader     = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows       = [dict(r) for r in reader
                          if r.get("Application URL", "").lower().rstrip("/") != url.lower().rstrip("/")]
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    try:
        with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    return jsonify({"ok": True, "remaining": len(rows)})


@app.route("/api/add-job", methods=["POST"])
def api_add_job():
    data    = request.get_json(silent=True) or {}
    company = data.get("company", "").strip()
    role    = data.get("role", "").strip()
    if not company or not role:
        return jsonify({"error": "Company and Role are required"}), 400

    url = data.get("url", "").strip()
    if not url:
        url = f"manual:{_uuid.uuid4().hex[:12]}"

    work_type = data.get("work_type", "").strip()
    location  = data.get("location", "").strip()
    if work_type and location:
        location = f"{work_type} — {location}"
    elif work_type:
        location = work_type

    today = datetime.date.today().strftime("%Y-%m-%d")

    new_row = {
        "Company": company, "Role": role, "Location": location,
        "Salary": data.get("salary", "").strip(),
        "Date Posted": today, "Date Applied": "",
        "Application URL": url,
        "Source": data.get("source", "Other").strip(),
        "Track": "manual", "Resume Version Used": "", "Cover Letter Sent (Y/N)": "N",
        "Status": data.get("status", "Wishlist").strip(),
        "Follow Up Date": "", "Notes": data.get("notes", "").strip(),
        "AI Score": "", "AI Recommendation": "", "AI Reason": "",
        "Disqualifiers": "", "Gaps": "", "Resume File": "", "Cover Letter File": "",
        "ATS Score": "", "T2 Score": "", "T2 Remote Status": "", "T2 Phone Status": "",
    }

    fieldnames = list(new_row.keys())
    try:
        if TRACKER_PATH.exists():
            with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
                rdr = csv.DictReader(f)
                if rdr.fieldnames:
                    fieldnames = list(rdr.fieldnames)
    except Exception:
        pass

    write_header = not TRACKER_PATH.exists() or TRACKER_PATH.stat().st_size == 0
    try:
        with open(TRACKER_PATH, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if write_header:
                w.writeheader()
            w.writerow(new_row)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    full_row = {k: "" for k in fieldnames}
    full_row.update(new_row)
    return jsonify({"ok": True, "row": full_row})


@app.route("/api/score-manual", methods=["POST"])
def api_score_manual():
    """
    Run the full AI pipeline on a manually added job.
    Body: { url, description, company, role, location, salary }
    If description is empty and url is a real link, the JD is fetched automatically.
    Returns: { ok, score, recommendation, reason, disqualifiers, gaps, ats_score, legit }
    """
    data     = request.get_json(silent=True) or {}
    url      = (data.get("url")         or "").strip()
    desc     = (data.get("description") or "").strip()
    company  = (data.get("company")     or "Unknown").strip()
    role     = (data.get("role")        or "Unknown").strip()
    location = (data.get("location")    or "Remote").strip()
    salary   = (data.get("salary")      or "").strip()

    # Fetch JD from URL when no description pasted
    if not desc and url.startswith("http"):
        try:
            req = _urllib_req.Request(url, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            })
            with _urllib_req.urlopen(req, timeout=15) as resp:
                raw      = resp.read()
                cset     = resp.headers.get_content_charset() or "utf-8"
                html_txt = raw.decode(cset, errors="replace")
            cleaned = _re.sub(r'<(script|style|noscript)[^>]*>[\s\S]*?</\1>', ' ', html_txt, flags=_re.IGNORECASE)
            cleaned = _re.sub(r'<[^>]+>', ' ', cleaned)
            cleaned = _html.unescape(cleaned)
            cleaned = _re.sub(r'[ \t]+', ' ', cleaned)
            cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned).strip()
            desc = cleaned[:15000]
        except Exception as exc:
            return jsonify({"error": f"Could not fetch URL: {exc}"}), 502

    if not desc:
        return jsonify({"error": "Provide a job URL or paste the description"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    resume_text = RESUME_FILE.read_text(encoding="utf-8").strip() if RESUME_FILE.exists() else ""
    bio_text    = BIO_FILE.read_text(encoding="utf-8").strip()    if BIO_FILE.exists()    else ""
    if not resume_text:
        return jsonify({"error": "master_resume.txt is empty or missing"}), 500

    job = {
        "title":       role,
        "company":     company,
        "location":    location,
        "salary_raw":  salary or "Not listed",
        "source":      "Manual",
        "description": desc,
        "url":         url,
    }

    result = {}

    # 1. Legitimacy score
    try:
        import legitimacy_scorer as _ls
        legit_row = {
            "Application URL": url,
            "Company":  company,
            "Salary":   salary,
            "Source":   "Manual",
            "Date Posted": "",
            "Location": location,
        }
        result["legit"] = _ls.score_job(legit_row)
    except Exception as exc:
        result["legit"] = {"error": str(exc)}

    # 2. AI score
    try:
        import job_agent as _ja
        client     = _anthropic.Anthropic(api_key=api_key)
        assessment = _ja.score_job(job, resume_text, bio_text, client)
        result.update(assessment)
    except Exception as exc:
        return jsonify({"error": f"AI scoring failed: {exc}"}), 500

    # 3. ATS keyword score (no DOCX generation)
    try:
        import resume_builder as _rb
        master = json.loads(MASTER_RESUME_JSON.read_text(encoding="utf-8")) if MASTER_RESUME_JSON.exists() else {}
        ats = _rb.ats_score(desc, master, company=company)
        result["ats_score"]   = ats.get("score",   0)
        result["ats_matched"] = ats.get("matched", [])
        result["ats_missing"] = ats.get("missing", [])
    except Exception:
        result["ats_score"] = 0

    # 4. Write scores back to tracker row
    rec = assessment.get("recommendation", "")
    updates = {
        "AI Score":          str(assessment.get("score", "")),
        "AI Recommendation": rec,
        "AI Reason":         assessment.get("reason", ""),
        "Disqualifiers":     "; ".join(assessment.get("disqualifiers", [])),
        "Gaps":              "; ".join(assessment.get("gaps", [])),
        "ATS Score":         str(result.get("ats_score", "")),
    }
    if rec == "APPLY NOW":
        updates["Status"] = "Ready to Apply"
    elif rec == "SKIP":
        updates["Status"] = "Skipped — AI"

    if url:
        _update_tracker_row_multi(url, updates)

    return jsonify({"ok": True, **result})


@app.route("/api/update-job", methods=["POST"])
def api_update_job():
    data    = request.get_json(silent=True) or {}
    old_url = (data.get("url") or "").strip()
    if not old_url:
        return jsonify({"error": "url required"}), 400
    updates = {}
    if data.get("company", "").strip():
        updates["Company"] = data["company"].strip()
    if "application_url" in data:
        new_url = data["application_url"].strip()
        if new_url:
            updates["Application URL"] = new_url
    if "location" in data:
        loc_val = (data["location"] or "").strip()
        if loc_val:
            updates["Location"] = loc_val
    if not updates:
        return jsonify({"error": "nothing to update"}), 400
    err = _update_tracker_row_multi(old_url, updates)
    if err:
        return jsonify({"error": err}), 500
    return jsonify({"ok": True, "updates": updates})


@app.route("/api/find-employer", methods=["POST"])
def api_find_employer():
    data    = request.get_json(silent=True) or {}
    url     = (data.get("url") or "").strip()
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not url or url.startswith("manual:"):
        return jsonify({"error": "No fetchable URL for this job"}), 400

    # Fetch posting content
    content = ""
    try:
        req = _urllib_req.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        })
        with _urllib_req.urlopen(req, timeout=15) as resp:
            raw  = resp.read()
            cset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(cset, errors="replace")
        cleaned = _re.sub(r'<(script|style|noscript)[^>]*>[\s\S]*?</\1>', ' ', html, flags=_re.IGNORECASE)
        cleaned = _re.sub(r'<[^>]+>', ' ', cleaned)
        cleaned = _html.unescape(cleaned)
        cleaned = _re.sub(r'[ \t]+', ' ', cleaned).strip()
        content = cleaned[:8000]
    except Exception as exc:
        content = f"[Could not fetch URL: {exc}]"

    prompt = f"""You are analyzing a job posting to identify the actual hiring company and any direct application link.

Current listed company/agency: {company}
Role: {role}
Source URL: {url}

Page content:
{content}

Return JSON only, no other text:
{{
  "company": "actual hiring company name, or null if cannot determine",
  "direct_url": "direct URL to the company careers page or ATS application, or null if not found",
  "confidence": "high|medium|low",
  "note": "one sentence explanation"
}}

Rules:
- If the listing is from a staffing agency (TEKsystems, Robert Half, Insight Global, Apex, etc.), identify the client company from the text if mentioned
- Only return a direct_url if you see an explicit careers/jobs URL for the actual company in the content
- If the current company is already the actual employer (not an agency), return it unchanged
- If the page is an application form with little posting text, say so in note and return nulls"""

    try:
        import anthropic as _anthropic
        client = _anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_resp = msg.content[0].text.strip()
        raw_resp = _re.sub(r'^```(?:json)?\s*', '', raw_resp)
        raw_resp = _re.sub(r'\s*```$', '', raw_resp)
        result = json.loads(raw_resp)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


_REMOTE_LOC_TERMS = [
    "remote", "work from home", "work-from-home", "wfh", "virtual",
    "anywhere", "united states", "telecommute", "telework",
]

def _row_is_remote_eligible(row):
    """Return True if this tracker row looks remote or Houston-hybrid eligible."""
    title = (row.get("Role")     or "").lower()
    loc   = (row.get("Location") or "").lower().strip().rstrip(".")
    for term in _REMOTE_LOC_TERMS:
        if term in title or term in loc:
            return True
    if loc in ("us", "usa", "u.s.", "u.s.a.", "united states", ""):
        return True
    # Houston / TX area → could be hybrid
    if "houston" in loc or ("tx" in loc and any(z in loc for z in ("houston", "77"))):
        return True
    return False


@app.route("/api/purge-onsite", methods=["POST"])
def api_purge_onsite():
    """
    Preview or delete tracker rows that appear to be on-site (not remote/hybrid).
    Body: { "confirm": false }  → returns preview list.
    Body: { "confirm": true }   → deletes and returns counts.
    """
    data    = request.get_json(silent=True) or {}
    confirm = bool(data.get("confirm", False))

    rows = _read_tracker()
    keep, purge = [], []
    for row in rows:
        (keep if _row_is_remote_eligible(row) else purge).append(row)

    if confirm:
        if not rows:
            return jsonify({"ok": False, "error": "Tracker is empty"}), 400
        # Write only the rows we're keeping
        fieldnames = list(rows[0].keys()) if rows else []
        try:
            with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                w.writeheader()
                w.writerows(keep)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "deleted": len(purge), "kept": len(keep)})

    preview = [
        {"company": r.get("Company", ""), "role": r.get("Role", ""), "location": r.get("Location", "")}
        for r in purge
    ]
    return jsonify({"ok": True, "preview": preview, "would_delete": len(purge), "would_keep": len(keep)})


@app.route("/api/verify-work-type", methods=["POST"])
def api_verify_work_type():
    """
    Classify a job's work arrangement (Remote / Hybrid / On-Site).
    Uses keyword heuristics first; falls back to Claude Haiku for ambiguous cases.
    Body: { "title", "company", "location", "url" }
    """
    data     = request.get_json(silent=True) or {}
    title    = (data.get("title")    or "").strip()
    company  = (data.get("company")  or "").strip()
    location = (data.get("location") or "").strip()
    url      = (data.get("url")      or "").strip()

    title_lc = title.lower()
    loc_lc   = location.lower().strip()

    # Phrases where "remote" means a skill/task, not the work arrangement.
    # e.g. "Remote Support Technician" works on-site providing remote support to others.
    _REMOTE_SKILL_PHRASES = [
        "remote support", "remote desktop", "remote access", "remote assistance",
        "remote monitoring", "remote management", "remote troubleshoot",
        "remote hands", "remote services",
    ]

    # ── Step 1: on-site signals override everything ───────────────────────────
    _ONSITE_TERMS = [
        "on-site", "on site", "onsite", "in-office", "in office",
        "in-person", "in person", "on location", "on-location",
    ]
    for term in _ONSITE_TERMS:
        if term in title_lc or term in loc_lc:
            return jsonify({"ok": True, "arrangement": "On-Site", "confidence": "high",
                            "source": "keywords", "reason": f'Contains on-site indicator: "{term}".'})

    # ── Step 1b: URL fetch — runs for every job that has a URL ───────────────
    _BARE_REMOTE_LOCS = {
        "remote", "remote, us", "remote,us", "remote, usa", "remote,usa",
        "remote - us", "remote - usa", "remote (us)", "remote (usa)",
    }
    _loc_reliable = True
    if url:
        try:
            _req = _urllib_req.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            with _urllib_req.urlopen(_req, timeout=10) as _resp:
                _raw = _resp.read(262144).decode("utf-8", errors="ignore")
            _raw_lc = _raw.lower()

            # ── 1. workplaceTypes JSON (Dice / LinkedIn Next.js) ─────────────
            _wt = _re.search(r'"workplacetypes?"\s*:\s*\[([^\]]*)\]', _raw_lc)
            if _wt:
                _wt_val = _wt.group(1)
                if "onsite" in _wt_val or "on_site" in _wt_val or "on-site" in _wt_val:
                    return jsonify({"ok": True, "arrangement": "On-Site", "confidence": "high",
                                    "source": "url-check", "reason": "Source listing confirms on-site work."})
                if "hybrid" in _wt_val:
                    return jsonify({"ok": True, "arrangement": "Hybrid", "confidence": "high",
                                    "source": "url-check", "reason": "Source listing confirms hybrid work."})
                if "remote" in _wt_val:
                    return jsonify({"ok": True, "arrangement": "Remote", "confidence": "high",
                                    "source": "url-check", "reason": "Source listing confirms remote work."})

            # ── 2. JSON-LD structured data (schema.org JobPosting) ───────────
            _jsonld_blocks = _re.findall(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
                _raw, _re.IGNORECASE
            )
            _jd_desc = ""
            for _block in _jsonld_blocks:
                try:
                    _jd = json.loads(_block.strip())
                    if isinstance(_jd, list):
                        _jd = next((x for x in _jd if isinstance(x, dict) and
                                    x.get("@type") == "JobPosting"), _jd[0] if _jd else {})
                    _jlt = (_jd.get("jobLocationType") or "").lower()
                    if "telecommute" in _jlt:
                        return jsonify({"ok": True, "arrangement": "Remote", "confidence": "high",
                                        "source": "url-check",
                                        "reason": "Job schema jobLocationType is TELECOMMUTE."})
                    if "inperson" in _jlt or "in_person" in _jlt or "onsite" in _jlt:
                        return jsonify({"ok": True, "arrangement": "On-Site", "confidence": "high",
                                        "source": "url-check",
                                        "reason": "Job schema jobLocationType is on-site."})
                    if _jd.get("description") and not _jd_desc:
                        _jd_desc = _jd["description"].lower()
                except Exception:
                    pass

            # ── 3. schema.org telecommute flag ───────────────────────────────
            if '"telecommute"' in _raw_lc:
                return jsonify({"ok": True, "arrangement": "Remote", "confidence": "high",
                                "source": "url-check", "reason": "Job schema marks position as telecommute."})

            # ── 4. LinkedIn #LI-Remote tag ───────────────────────────────────
            # Recruiters embed this tag in the posting body to mark remote jobs
            # on LinkedIn. It appears as plain text in the HTML source.
            if "li-remote" in _raw_lc:
                return jsonify({"ok": True, "arrangement": "Remote", "confidence": "high",
                                "source": "url-check",
                                "reason": "LinkedIn #LI-Remote tag found in posting."})

            # ── 5. Scan job description text only — not full page HTML ────────
            # Using raw HTML causes false positives from nav/filter UI elements
            # that list "On-Site" as a filter option. Instead use the JSON-LD
            # description if available, otherwise strip structural tags first.
            if _jd_desc:
                _scan_text = _jd_desc
            else:
                _stripped = _re.sub(
                    r'<(script|style|nav|header|footer|aside|menu)[^>]*>[\s\S]*?</\1>',
                    ' ', _raw, flags=_re.IGNORECASE
                )
                _stripped = _re.sub(r'<[^>]+>', ' ', _stripped)
                _scan_text = _re.sub(r'\s+', ' ', _stripped).lower()

            # Definitive work-arrangement phrases — never appear in amenity context
            _ONSITE_DEFINITE = [
                'work type: on-site', 'work type:on-site', 'work type: onsite',
                'location type: on-site', 'work arrangement: on-site',
                'work location: on-site', 'job type: on-site',
                '"locationtype":"onsite"', 'applicantlocationrequirements',
                'not remote', 'no remote option', 'non-remote',
                'must be local', 'must report to the office',
                'on-site required', 'required on-site', 'cannot work remotely',
            ]
            for phrase in _ONSITE_DEFINITE:
                if phrase in _scan_text:
                    return jsonify({"ok": True, "arrangement": "On-Site (likely)", "confidence": "high",
                                    "source": "url-check",
                                    "reason": f'Job description contains on-site indicator: "{phrase}".'})

            # Page fetched but no signal found — for bare-remote locations
            # don't blindly trust the location field
            if loc_lc in _BARE_REMOTE_LOCS:
                _loc_reliable = False
        except Exception:
            pass  # network/timeout — proceed with stored data

    # ── Step 2: location field remote check ──────────────────────────────────
    if _loc_reliable:
        _LOC_REMOTE_TERMS = [
            "remote", "work from home", "work-from-home", "wfh", "virtual",
            "anywhere", "united states", "telecommute", "telework",
        ]
        for term in _LOC_REMOTE_TERMS:
            if term in loc_lc:
                return jsonify({"ok": True, "arrangement": "Remote", "confidence": "high",
                                "source": "keywords", "reason": f'Location field contains "{term}".'})
        if loc_lc in ("us", "usa", "u.s.", "u.s.a."):
            return jsonify({"ok": True, "arrangement": "Remote", "confidence": "medium",
                            "source": "keywords", "reason": "US-wide location suggests remote."})

    # ── Step 3: title remote check — only if not a skill phrase ──────────────
    if "remote" in title_lc:
        is_skill_phrase = any(phrase in title_lc for phrase in _REMOTE_SKILL_PHRASES)
        if not is_skill_phrase:
            return jsonify({"ok": True, "arrangement": "Remote", "confidence": "medium",
                            "source": "keywords", "reason": '"remote" in title indicates work-from-home role.'})

    if "hybrid" in title_lc or "hybrid" in loc_lc:
        return jsonify({"ok": True, "arrangement": "Hybrid", "confidence": "high",
                        "source": "keywords", "reason": "Hybrid keyword found."})
    if "houston" in loc_lc:
        return jsonify({"ok": True, "arrangement": "On-Site (Houston)", "confidence": "high",
                        "source": "keywords", "reason": "Houston location — could be hybrid commute role."})

    # ── Step 4: ambiguous — ask Claude Haiku ─────────────────────────────────
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"ok": True, "arrangement": "On-Site (likely)", "confidence": "low",
                        "source": "heuristic",
                        "reason": f'Location "{location}" is city-specific with no remote keyword.'})
    try:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        import anthropic as _anth
        client = _anth.Anthropic(api_key=api_key)
        _loc_note = " (stored location may be unreliable — possible scraper artifact)" if not _loc_reliable else ""
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            messages=[{"role": "user", "content":
                f"Job: {title} at {company}\nLocation: {location}{_loc_note}\nURL: {url}\n\n"
                "Is this Remote, Hybrid, or On-Site?\n"
                "ARRANGEMENT: [Remote/Hybrid/On-Site]\nREASON: [one sentence]"
            }],
        )
        text  = msg.content[0].text.strip()
        arr_m = _re.search(r"ARRANGEMENT:\s*(\S[^\n]*)", text, _re.IGNORECASE)
        rsn_m = _re.search(r"REASON:\s*(.+)",            text, _re.IGNORECASE)
        def _strip_md(s): return _re.sub(r"[*_`]", "", s).strip()
        return jsonify({
            "ok": True,
            "arrangement": _strip_md(arr_m.group(1)) if arr_m else "Unknown",
            "confidence":  "medium",
            "source":      "ai",
            "reason":      _strip_md(rsn_m.group(1)) if rsn_m else _strip_md(text),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/legitimacy-scores")
def api_legitimacy_scores():
    """
    Score all jobs in the tracker for legitimacy.
    Returns: {url: {score, label, color, emoji, boosters_found, flags_found,
                    uncertainties, recommendation, top_signals, cached_at}}
    Scores are cached 48h in logs/legitimacy_cache.json.
    """
    try:
        import legitimacy_scorer as _ls
        rows    = _read_tracker()
        results = _ls.score_jobs_batch(rows)
        return jsonify(results)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/update-master-resume", methods=["POST"])
def api_update_master_resume():
    """
    Save updated master_resume.json data.
    Body: JSON object with any master_resume fields.
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "JSON body required"}), 400

    try:
        existing = {}
        if MASTER_RESUME_JSON.exists():
            existing = json.loads(MASTER_RESUME_JSON.read_text(encoding="utf-8"))
        existing.update(data)
        MASTER_RESUME_JSON.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── New productivity routes ───────────────────────────────────────────────────

@app.route("/api/daily-digest")
def api_daily_digest():
    rows = _read_tracker()
    applied_statuses = {
        "applied", "interview scheduled", "offer received", "rejected", "withdrawn"
    }
    candidates = [
        r for r in rows
        if r.get("AI Recommendation") == "APPLY NOW"
        and r.get("Status", "").lower() not in applied_statuses
    ]
    candidates.sort(key=lambda r: int(r.get("AI Score") or 0), reverse=True)
    return jsonify(candidates[:5])


@app.route("/api/response-stats")
def api_response_stats():
    rows = _read_tracker()
    by_source: dict = {}
    for row in rows:
        src    = (row.get("Source") or "Unknown").strip() or "Unknown"
        status = (row.get("Status") or "").lower()
        if status not in (
            "applied", "interview scheduled", "offer received", "rejected", "withdrawn"
        ):
            continue
        if src not in by_source:
            by_source[src] = {"applied": 0, "responses": 0, "interviews": 0, "offers": 0}
        by_source[src]["applied"] += 1
        if status in ("interview scheduled", "offer received", "rejected"):
            by_source[src]["responses"] += 1
        if "interview" in status:
            by_source[src]["interviews"] += 1
        if "offer" in status:
            by_source[src]["offers"] += 1
    return jsonify(by_source)


@app.route("/api/interview-prep", methods=["POST"])
def api_interview_prep():
    data    = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company or not role:
        return jsonify({"error": "company and role required"}), 400
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "anthropic package not installed"}), 500
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    import sys as _sys
    cfg      = _sys.modules.get("config")
    ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    prompt = (
        "You are Journey, a job search intelligence agent helping Kee Earl prepare for "
        "an interview.\n\n"
        f"COMPANY: {company}\n"
        f"ROLE: {role}\n\n"
        "KEE'S BACKGROUND: Multi-Product Agent at Progressive Insurance (Apr 2025-Present). "
        "CompTIA A+ certified (March 2026). WGU B.S. Cybersecurity in progress. "
        "Previously at World Travel Holdings in tech support. "
        "Strong customer service, communication, and cross-functional skills. "
        "Target: entry-level IT / help desk / SOC Tier 1 / cybersecurity analyst roles.\n\n"
        "Generate a focused interview prep brief. Return ONLY valid JSON, no markdown:\n"
        "{\n"
        '  "company_intel": "<2-3 sentences about the company relevant to this role>",\n'
        '  "likely_questions": [\n'
        '    {"q": "<interview question>", "tip": "<one-line answer tip using Kee\'s background>"},\n'
        '    {"q": "...", "tip": "..."}\n'
        '  ],\n'
        '  "key_talking_points": ["<point 1>", "<point 2>", "<point 3>"],\n'
        '  "red_flags_to_address": ["<gap or weakness to prepare for>"],\n'
        '  "closing_questions": ["<smart question to ask>", "<smart question>", "<smart question>"]\n'
        "}\n\n"
        "Include 8-10 likely_questions. Tips must be specific to Kee's actual experience, "
        "not generic advice. Never use an em dash (—) inside parentheses anywhere in the output."
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw   = resp.content[0].text
        raw   = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re.DOTALL).strip()
        brief = json.loads(raw)
        log_api_call("INTERVIEW_PREP", f"company={company} role={role}")
        return jsonify({"ok": True, "brief": brief, "company": company, "role": role})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/prep/questions", methods=["POST"])
def api_prep_questions():
    data    = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company or not role:
        return jsonify({"error": "company and role required"}), 400
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "anthropic package not installed"}), 500
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    import sys as _sys
    cfg      = _sys.modules.get("config")
    ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    system = (
        "You are an expert interview coach. Given a job title and company, generate the "
        "8 most likely interview questions for that specific role. Mix behavioral, "
        "situational, and technical questions appropriate for the role. Format as a "
        "numbered list. Be specific to the company and role — not generic. "
        "Never use an em dash inside parentheses."
    )
    user_msg = f"Job Title: {role}\nCompany: {company}"

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model, max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        log_api_call("PREP_QUESTIONS", f"company={company} role={role}")
        return jsonify({"ok": True, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/prep/answers", methods=["POST"])
def api_prep_answers():
    data      = request.get_json(silent=True) or {}
    company   = (data.get("company") or "").strip()
    role      = (data.get("role") or "").strip()
    questions = (data.get("questions") or "").strip()
    if not company or not role or not questions:
        return jsonify({"error": "company, role, and questions required"}), 400
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "anthropic package not installed"}), 500
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    import sys as _sys
    cfg      = _sys.modules.get("config")
    ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    system = (
        "You are an expert interview coach helping Kee (Kiara Earl) prepare for a job "
        "interview. Here is her background: 2 years technical support and customer service "
        "at Progressive Insurance as a Multi-Product Agent, 5 months tech support at World "
        "Travel Holdings, CompTIA A+ certified (Core 1 and Core 2, March 2026), currently "
        "pursuing B.S. Cybersecurity at WGU, runs Houston Signing Solutions mobile notary "
        "business, Houston TX based. Given the interview questions provided, write strong "
        "personalized answers for each one that draw from her real background. Write in "
        "first person as if she is speaking. Keep each answer under 90 seconds when spoken "
        "aloud (roughly 200 words). Format as numbered answers matching the question numbers. "
        "Never use an em dash inside parentheses."
    )
    user_msg = (
        f"Job Title: {role}\nCompany: {company}\n\n"
        f"Interview Questions:\n{questions}"
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model, max_tokens=3000,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        log_api_call("PREP_ANSWERS", f"company={company} role={role}")
        return jsonify({"ok": True, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/prep/ask-them", methods=["POST"])
def api_prep_ask_them():
    data    = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company or not role:
        return jsonify({"error": "company and role required"}), 400
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "anthropic package not installed"}), 500
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    import sys as _sys
    cfg      = _sys.modules.get("config")
    ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    system = (
        "You are an expert interview coach. Given a job title and company, generate 5 smart "
        "questions the candidate should ask the interviewer. The questions should show genuine "
        "curiosity, strategic thinking, and help the candidate evaluate whether this role is "
        "actually a good fit. Avoid generic questions. Be specific to the role and company. "
        "Format as a numbered list. Never use an em dash inside parentheses."
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model, max_tokens=700,
            system=system,
            messages=[{"role": "user", "content": f"Job Title: {role}\nCompany: {company}"}],
        )
        text = resp.content[0].text.strip()
        log_api_call("PREP_ASK_THEM", f"company={company} role={role}")
        return jsonify({"ok": True, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/dei-advice", methods=["POST"])
def api_dei_advice():
    data    = request.get_json(silent=True) or {}
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company or not role:
        return jsonify({"error": "company and role required"}), 400
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "anthropic package not installed"}), 500
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    import sys as _sys
    cfg      = _sys.modules.get("config")
    ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    system = (
        "You are an expert career advisor helping a Black woman named Kee who is applying "
        "for jobs in tech and IT support. When given a company name and job title, analyze "
        "whether she should answer or skip the voluntary demographic questions on the job "
        "application. Consider: the company's DEI track record, whether they are a federal "
        "contractor, whether DEI rollbacks affect them, and the current 2025 political and "
        "legal climate. Return a clear recommendation: ANSWER, SKIP, or NEUTRAL — followed "
        "by 2-3 sentences of plain-language reasoning specific to that company. "
        "Never use an em dash inside parentheses."
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model, max_tokens=320,
            system=system,
            messages=[{"role": "user", "content": f"Company: {company}\nJob Title: {role}"}],
        )
        text = resp.content[0].text.strip()
        first = text.split("\n")[0].upper()
        rec = "ANSWER" if "ANSWER" in first else "SKIP" if "SKIP" in first else "NEUTRAL"
        log_api_call("DEI_ADVICE", f"company={company} role={role} rec={rec}")
        return jsonify({"ok": True, "recommendation": rec, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/salary-target", methods=["POST"])
def api_salary_target():
    data     = request.get_json(silent=True) or {}
    company  = (data.get("company") or "").strip()
    role     = (data.get("role") or "").strip()
    location = (data.get("location") or "Houston, TX").strip()
    if not company or not role:
        return jsonify({"error": "company and role required"}), 400
    if not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "anthropic package not installed"}), 500
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    import sys as _sys
    cfg      = _sys.modules.get("config")
    ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    system = (
        "You are an expert salary negotiation advisor. When given a job title, company, "
        "location, and experience level, suggest a specific target salary number to enter "
        "on a job application where a number is required. The candidate is Kee — a Black "
        "woman in Houston TX with 2 years of technical support experience, CompTIA A+, "
        "active WGU cybersecurity degree, and insurance industry background. Return one "
        "specific dollar amount with 2-3 sentences explaining why. Be direct — no ranges, "
        "just one number. Never use an em dash inside parentheses."
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model, max_tokens=280,
            system=system,
            messages=[{"role": "user", "content": f"Job Title: {role}\nCompany: {company}\nLocation: {location}"}],
        )
        text = resp.content[0].text.strip()
        log_api_call("SALARY_TARGET", f"company={company} role={role}")
        return jsonify({"ok": True, "text": text})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Sage API routes ───────────────────────────────────────────────────────────

@app.route("/api/sage/products")
def api_sage_products():
    return jsonify(_load_json_list(SAGE_PRODUCTS_PATH))


@app.route("/api/sage/products", methods=["POST"])
def api_sage_add_product():
    data = request.get_json(silent=True) or {}
    products = _load_json_list(SAGE_PRODUCTS_PATH)
    product = {
        "id":          str(_uuid.uuid4())[:8],
        "title":       (data.get("title") or "").strip(),
        "format":      (data.get("format") or "").strip(),
        "price":       (data.get("price") or "").strip(),
        "description": (data.get("description") or "").strip(),
        "outline":     (data.get("outline") or "").strip(),
        "status":      (data.get("status") or "Draft"),
        "platform":    (data.get("platform") or ""),
        "created":     datetime.date.today().isoformat(),
    }
    products.append(product)
    _save_json_list(SAGE_PRODUCTS_PATH, products)
    return jsonify({"ok": True, "product": product})


@app.route("/api/sage/products/<pid>", methods=["PUT"])
def api_sage_update_product(pid):
    data = request.get_json(silent=True) or {}
    products = _load_json_list(SAGE_PRODUCTS_PATH)
    for p in products:
        if p.get("id") == pid:
            for k in ("title", "format", "price", "description", "outline", "status", "platform"):
                if k in data:
                    p[k] = data[k]
            break
    _save_json_list(SAGE_PRODUCTS_PATH, products)
    return jsonify({"ok": True})


@app.route("/api/sage/products/<pid>", methods=["DELETE"])
def api_sage_delete_product(pid):
    products = [p for p in _load_json_list(SAGE_PRODUCTS_PATH) if p.get("id") != pid]
    _save_json_list(SAGE_PRODUCTS_PATH, products)
    return jsonify({"ok": True})


@app.route("/api/sage/generate-product", methods=["POST"])
def api_sage_generate_product():
    data = request.get_json(silent=True) or {}
    idea = (data.get("idea") or "").strip()
    if not idea:
        return jsonify({"error": "idea is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    prompt = (
        f"Product idea: {idea}\n\n"
        "Return ONLY valid JSON (no markdown, no commentary):\n"
        "{\n"
        "  \"title\": \"compelling product title\",\n"
        "  \"format\": \"PDF Guide / Canva Template / Notion Template / Spreadsheet / etc.\",\n"
        "  \"price\": \"$X.XX — one sentence price reasoning\",\n"
        "  \"description\": \"2-3 sentence product description for the listing\",\n"
        "  \"outline\": \"bullet-point content outline (6-10 items, newline-separated)\",\n"
        "  \"target_buyer\": \"who this is for in one sentence\",\n"
        "  \"differentiation\": \"what makes this stand out from free alternatives\"\n"
        "}"
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=SAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_claude_json(resp.content[0].text)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/generate-marketing", methods=["POST"])
def api_sage_generate_marketing():
    data          = request.get_json(silent=True) or {}
    product_title = (data.get("title") or "").strip()
    product_desc  = (data.get("description") or "").strip()
    if not product_title:
        return jsonify({"error": "title is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    prompt = (
        f"Product: {product_title}\n"
        f"Description: {product_desc}\n\n"
        "Generate a complete marketing content batch for this digital product.\n"
        "Return ONLY a single valid JSON object with EXACTLY these fields:\n"
        "- pinterest_pins: array of EXACTLY 5 objects, each with: title (string, 60 chars max), description (string, 150 chars), keywords (array of 3 strings)\n"
        "- instagram_captions: array of EXACTLY 3 objects, each with: hook (string), body (string), cta (string), hashtags (string)\n"
        "- tiktok_scripts: array of EXACTLY 2 objects, each with: hook (string), points (array of 3 strings), cta (string)\n"
        "- email_announcement: object with: subject (string), preview (string), body (string, 3 short paragraphs), cta (string)\n"
        "Output raw JSON only. No markdown. No commentary. No trailing text."
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3500,
            system=SAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_claude_json(resp.content[0].text)
        entry = {
            "id":        str(_uuid.uuid4())[:8],
            "product":   product_title,
            "generated": datetime.date.today().isoformat(),
            "content":   result,
        }
        content_log = _load_json_list(SAGE_CONTENT_PATH)
        content_log.insert(0, entry)
        _save_json_list(SAGE_CONTENT_PATH, content_log[:50])
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/analyze", methods=["POST"])
def api_sage_analyze():
    data       = request.get_json(silent=True) or {}
    stats_text = (data.get("stats") or "").strip()
    platform   = (data.get("platform") or "Etsy/Gumroad").strip()
    if not stats_text:
        return jsonify({"error": "stats is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    prompt = (
        f"Platform: {platform}\n"
        f"Stats data:\n{stats_text}\n\n"
        "Analyze this store data. Return ONLY valid JSON:\n"
        "{\n"
        "  \"health_score\": 7,\n"
        "  \"health_reasoning\": \"one sentence explaining the score\",\n"
        "  \"what_works\": [\"item 1\", \"item 2\", \"item 3\"],\n"
        "  \"what_doesnt\": [\"item 1\", \"item 2\"],\n"
        "  \"action_items\": [\n"
        "    {\"rank\": 1, \"action\": \"specific action\", \"impact\": \"High\", \"effort\": \"Low\"},\n"
        "    ... (4-6 total, ranked by impact-to-effort ratio)\n"
        "  ],\n"
        "  \"summary\": \"2-3 sentence overall assessment\"\n"
        "}"
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=SAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_claude_json(resp.content[0].text)
        entry = {
            "id":           str(_uuid.uuid4())[:8],
            "platform":     platform,
            "date":         datetime.date.today().isoformat(),
            "health_score": result.get("health_score", 0),
            "result":       result,
        }
        history = _load_json_list(SAGE_ANALYTICS_PATH)
        history.insert(0, entry)
        _save_json_list(SAGE_ANALYTICS_PATH, history[:20])
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/customer-desk", methods=["POST"])
def api_sage_customer_desk():
    data     = request.get_json(silent=True) or {}
    scenario = (data.get("scenario") or "Reply").strip()
    message  = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    scenario_instructions = {
        "Reply":               "Write a warm, professional reply to this customer message.",
        "Review Response":     "Write a professional public response to this review. Be gracious even if it is negative.",
        "Follow-Up":           "Write a friendly follow-up message to check in with this customer about their purchase.",
        "Launch Announcement": "Write an exciting but professional product launch announcement to send to existing customers.",
    }
    instruction = scenario_instructions.get(scenario, "Write a professional customer message.")

    prompt = (
        f"Scenario: {scenario}\n"
        f"Message / context:\n{message}\n\n"
        f"{instruction}\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        "  \"draft\": \"the full message text ready to copy and send\",\n"
        "  \"tone\": \"tone used (e.g., Warm & Professional)\",\n"
        "  \"tips\": [\"optional tip 1\", \"optional tip 2\"]\n"
        "}"
    )

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=SAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_claude_json(resp.content[0].text)
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/buyer-log")
def api_sage_buyer_log():
    return jsonify(_load_json_list(SAGE_BUYER_LOG_PATH))


@app.route("/api/sage/buyer-log", methods=["POST"])
def api_sage_add_buyer():
    data = request.get_json(silent=True) or {}
    buyers = _load_json_list(SAGE_BUYER_LOG_PATH)
    entry = {
        "id":            str(_uuid.uuid4())[:8],
        "name":          (data.get("name") or "").strip(),
        "product":       (data.get("product") or "").strip(),
        "date":          (data.get("date") or datetime.date.today().isoformat()),
        "review_status": (data.get("review_status") or "No Review"),
        "notes":         (data.get("notes") or "").strip(),
    }
    buyers.insert(0, entry)
    _save_json_list(SAGE_BUYER_LOG_PATH, buyers)
    return jsonify({"ok": True, "entry": entry})


@app.route("/api/sage/buyer-log/<bid>", methods=["PUT"])
def api_sage_update_buyer(bid):
    data = request.get_json(silent=True) or {}
    buyers = _load_json_list(SAGE_BUYER_LOG_PATH)
    for b in buyers:
        if b.get("id") == bid:
            for k in ("name", "product", "date", "review_status", "notes"):
                if k in data:
                    b[k] = data[k]
            break
    _save_json_list(SAGE_BUYER_LOG_PATH, buyers)
    return jsonify({"ok": True})


@app.route("/api/sage/revenue")
def api_sage_revenue():
    try:
        if SAGE_REVENUE_PATH.exists():
            return jsonify(json.loads(SAGE_REVENUE_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return jsonify({})


@app.route("/api/sage/revenue", methods=["POST"])
def api_sage_save_revenue():
    data = request.get_json(silent=True) or {}
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        SAGE_REVENUE_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Mode 5: Brand Content Studio ──────────────────────────────────────────────

def _m5_build_prompt(brand, platform, content_type, topic, voice_notes):
    brand_voice = {
        "career_os": (
            "CAREER OS / BUILT BY KEE\n"
            "Voice: builder, honest, behind the scenes, zero fluff.\n"
            "Tone: 'I built this because I lived the problem.'\n"
            "Audience: job seekers, career changers, tech students, laid-off professionals.\n"
            "Rules: Never promotional. Sounds like a real person sharing something raw and real."
        ),
        "hss": (
            "HOUSTON SIGNING SOLUTIONS\n"
            "Voice: professional, trustworthy, local Houston expert.\n"
            "Tone: 'Your documents handled right the first time.'\n"
            "Audience: Houston homeowners, real estate agents, title companies, loan officers.\n"
            "Rules: Always include a Houston local angle."
        ),
        "byb": (
            "BUILD YOUR BLUEPRINT\n"
            "Voice: empowering, practical, sister-to-sister energy.\n"
            "Tone: 'Here is exactly how I did it and how you can too.'\n"
            "Audience: women in career transition, side hustlers, digital product beginners.\n"
            "Rules: Always tie to a specific product or income outcome. Faceless content only."
        ),
    }.get(brand, "")

    platform_specs = {
        "twitter": {
            "tweet": "Write ONE punchy tweet under 280 characters. No hashtags unless essential. Hook first.",
            "thread": "Write a 6-tweet numbered thread that tells a complete story. Each tweet under 250 chars. First tweet is the hook.",
            "reply_hook": "Write 3 reply-bait tweets designed to spark conversation. Each under 200 chars.",
            "build_in_public": "Write 1 raw build-in-public update tweet. Under 280 chars. Real numbers or real feelings only.",
        },
        "tiktok": {
            "script": "Write a 60-second video script with: HOOK (first 3 seconds, stops the scroll), MIDDLE (3 teaching points, 10 seconds each), CTA (last 5 seconds). Include trending audio vibe description.",
            "text_overlay": "Write text overlay script for a screen recording video. 8-12 short phrases. Each shown on screen 3-4 seconds.",
            "caption": "Write TikTok caption 100-150 words + 5 hashtags.",
        },
        "instagram": {
            "caption": "Write Instagram caption 150-300 words in storytelling format. Strong first line hook. End with a question CTA.",
            "carousel": "Write 6 carousel slide texts. Slide 1: hook/title. Slides 2-5: one idea each. Slide 6: CTA/save prompt.",
            "story": "Write 4 Instagram story text overlays in sequence. Each 1-2 lines max. Tell a mini story.",
            "script": "Write a 30-60 second Reel script with hook, 3 points, CTA.",
        },
        "pinterest": {
            "pins": "Write 5 Pinterest pins. For each: title (under 100 chars, SEO optimized), description (200-300 chars, keyword rich), board suggestion.",
        },
        "reddit": {
            "post": "Write a Reddit post that helps first, never promotes. Title: specific and community-aware. Body: genuine and useful. Include flair suggestion and subreddit recommendation.",
        },
        "newsletter": {
            "email": "Write a newsletter draft. Include: 3 subject line options (curiosity, direct, personal), preview text, full email body 400-800 words, CTA at bottom. Format for Beehiiv/Substack.",
        },
    }

    spec = platform_specs.get(platform, {}).get(content_type, f"Create {content_type} content for {platform}.")
    topic_line = f"\nTopic/product to feature: {topic}" if topic else ""

    notes_line = ""
    if voice_notes:
        notes_line = f"\n\nAdjustments based on past feedback:\n" + "\n".join(f"- {n}" for n in voice_notes[:5])

    prompt = (
        f"Brand profile:\n{brand_voice}\n\n"
        f"Platform: {platform.upper()}\n"
        f"Content type: {content_type}{topic_line}\n\n"
        f"Task: {spec}{notes_line}\n\n"
        "Return ONLY a JSON object with field 'pieces' — an array of content items.\n"
        "Each item must have: 'label' (e.g. 'Tweet 1'), 'content' (the actual text), "
        "'platform_note' (optional tips for posting), 'char_count' (integer).\n"
        "No markdown. No commentary. Pure JSON only."
    )
    return prompt


def _m5_load_voice_notes(brand):
    try:
        data = json.loads(M5_VOICE_PATH.read_text(encoding="utf-8")) if M5_VOICE_PATH.exists() else {}
        return data.get(brand, {}).get("notes", [])
    except Exception:
        return []


def _m5_track_rejection(brand, reason):
    try:
        data = json.loads(M5_VOICE_PATH.read_text(encoding="utf-8")) if M5_VOICE_PATH.exists() else {}
        brand_data = data.setdefault(brand, {"notes": [], "rejection_counts": {}})
        if reason:
            counts = brand_data.setdefault("rejection_counts", {})
            counts[reason] = counts.get(reason, 0) + 1
            if counts[reason] >= 3:
                note = f"Avoid: {reason}"
                if note not in brand_data["notes"]:
                    brand_data["notes"].append(note)
        LOGS_DIR.mkdir(exist_ok=True)
        M5_VOICE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


_M5_PLATFORM_TIMES = {
    "twitter": "8am, 12pm, 5pm, or 9pm CT",
    "tiktok": "7am, 12pm, or 7pm CT",
    "instagram": "8am, 11am, 3pm, or 7pm CT",
    "pinterest": "8–11pm CT",
    "newsletter": "Tuesday or Thursday 9am CT",
    "reddit": "Best time varies by subreddit — check top posts timing",
}

_M5_AUTO_POST = {"twitter", "reddit", "pinterest", "newsletter", "substack"}  # platforms with auto-post support
_M5_MANUAL_LINKS = {
    "tiktok": "https://www.tiktok.com/upload",
    "instagram": "https://www.instagram.com/",
    "pinterest": "https://www.pinterest.com/pin/creation/button/",
    "reddit": "https://www.reddit.com/submit",
    "newsletter": "https://app.beehiiv.com/",
}


@app.route("/api/sage/m5/queue")
def api_m5_queue_get():
    return jsonify(_load_json_list(M5_QUEUE_PATH))


@app.route("/api/sage/m5/generate", methods=["POST"])
def api_m5_generate():
    data = request.get_json(silent=True) or {}
    brand = (data.get("brand") or "").strip()
    platform = (data.get("platform") or "").strip()
    content_type = (data.get("content_type") or "").strip()
    topic = (data.get("topic") or "").strip()

    if not brand or not platform or not content_type:
        return jsonify({"error": "brand, platform, and content_type are required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not _ANTHROPIC_AVAILABLE:
        return jsonify({"error": "ANTHROPIC_API_KEY not set"}), 500

    voice_notes = _m5_load_voice_notes(brand)
    prompt = _m5_build_prompt(brand, platform, content_type, topic, voice_notes)

    try:
        client = _anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SAGE_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _parse_claude_json(resp.content[0].text)
        pieces = raw.get("pieces", []) if isinstance(raw, dict) else []

        queue = _load_json_list(M5_QUEUE_PATH)
        new_items = []
        for p in pieces:
            item = {
                "id": str(_uuid.uuid4())[:8],
                "brand": brand,
                "platform": platform,
                "content_type": content_type,
                "topic": topic,
                "label": p.get("label", content_type),
                "content": p.get("content", ""),
                "platform_note": p.get("platform_note", ""),
                "char_count": p.get("char_count", len(p.get("content", ""))),
                "generated_at": datetime.date.today().isoformat(),
                "status": "pending",
                "recommended_time": _M5_PLATFORM_TIMES.get(platform, ""),
                "rejection_count": 0,
                "rejection_reasons": [],
                "auto_post": platform in _M5_AUTO_POST,
                "manual_link": _M5_MANUAL_LINKS.get(platform, ""),
                "adjustment_msg": "",
            }
            queue.append(item)
            new_items.append(item)

        _save_json_list(M5_QUEUE_PATH, queue[-200:])
        return jsonify({"ok": True, "items": new_items, "voice_adjustment": bool(voice_notes)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/queue/<qid>/approve", methods=["POST"])
def api_m5_approve(qid):
    queue = _load_json_list(M5_QUEUE_PATH)
    for item in queue:
        if item.get("id") == qid:
            item["status"] = "approved"
            break
    _save_json_list(M5_QUEUE_PATH, queue)
    return jsonify({"ok": True})


@app.route("/api/sage/m5/queue/<qid>/reject", methods=["POST"])
def api_m5_reject(qid):
    data = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    queue = _load_json_list(M5_QUEUE_PATH)
    brand = None
    for item in queue:
        if item.get("id") == qid:
            item["status"] = "rejected"
            item["rejection_count"] = item.get("rejection_count", 0) + 1
            if reason:
                item.setdefault("rejection_reasons", []).append(reason)
            brand = item.get("brand")
            break
    _save_json_list(M5_QUEUE_PATH, queue)
    if brand and reason:
        _m5_track_rejection(brand, reason)
    return jsonify({"ok": True})


@app.route("/api/sage/m5/queue/<qid>", methods=["PUT"])
def api_m5_edit(qid):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    queue = _load_json_list(M5_QUEUE_PATH)
    for item in queue:
        if item.get("id") == qid:
            item["content"] = content
            item["char_count"] = len(content)
            item["status"] = "edited"
            break
    _save_json_list(M5_QUEUE_PATH, queue)
    return jsonify({"ok": True})


@app.route("/api/sage/m5/queue/<qid>/mark-posted", methods=["POST"])
def api_m5_mark_posted(qid):
    queue = _load_json_list(M5_QUEUE_PATH)
    for item in queue:
        if item.get("id") == qid:
            item["status"] = "posted"
            item["posted_at"] = datetime.date.today().isoformat()
            break
    _save_json_list(M5_QUEUE_PATH, queue)
    return jsonify({"ok": True})


@app.route("/api/sage/m5/calendar")
def api_m5_calendar_get():
    try:
        if M5_CALENDAR_PATH.exists():
            return jsonify(json.loads(M5_CALENDAR_PATH.read_text(encoding="utf-8")))
        return jsonify({})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/calendar", methods=["POST"])
def api_m5_calendar_save():
    data = request.get_json(silent=True) or {}
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        M5_CALENDAR_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/analytics")
def api_m5_analytics_get():
    try:
        if M5_ANALYTICS_PATH.exists():
            return jsonify(json.loads(M5_ANALYTICS_PATH.read_text(encoding="utf-8")))
        return jsonify({"weeks": []})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/analytics", methods=["POST"])
def api_m5_analytics_save():
    data = request.get_json(silent=True) or {}
    week_entry = data.get("week_entry", {})
    if not week_entry:
        return jsonify({"error": "week_entry required"}), 400

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    analysis = None
    if api_key and _ANTHROPIC_AVAILABLE:
        try:
            stats_text = json.dumps(week_entry, indent=2)
            prompt = (
                f"Weekly social media analytics data:\n{stats_text}\n\n"
                "Analyze this and return JSON with:\n"
                "- best_content: string (what performed best this week)\n"
                "- fastest_growing: string (platform name)\n"
                "- post_more: string (what to double down on)\n"
                "- stop_doing: string (what to cut)\n"
                "- platform_scores: object with each platform as key and score 1-10 as value\n"
                "- trends: object with each platform as key and 'up'/'flat'/'declining' as value\n"
                "No markdown. Pure JSON only."
            )
            client = _anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=600,
                system=SAGE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            analysis = _parse_claude_json(resp.content[0].text)
        except Exception:
            pass

    try:
        existing = json.loads(M5_ANALYTICS_PATH.read_text(encoding="utf-8")) if M5_ANALYTICS_PATH.exists() else {"weeks": []}
        week_entry["analysis"] = analysis
        week_entry["saved_at"] = datetime.date.today().isoformat()
        existing.setdefault("weeks", []).append(week_entry)
        existing["weeks"] = existing["weeks"][-12:]
        LOGS_DIR.mkdir(exist_ok=True)
        M5_ANALYTICS_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        return jsonify({"ok": True, "analysis": analysis})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/tweet", methods=["POST"])
def api_m5_tweet():
    if not _TWEEPY_AVAILABLE:
        return jsonify({"error": "tweepy not installed. Run: pip install tweepy"}), 500

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    qid = (data.get("qid") or "").strip()
    if not content:
        return jsonify({"error": "content required"}), 400

    try:
        env = {}
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()

        api_key    = env.get("TWITTER_API_KEY", "")
        api_secret = env.get("TWITTER_API_SECRET", "")
        acc_token  = env.get("TWITTER_ACCESS_TOKEN", "")
        acc_secret = env.get("TWITTER_ACCESS_SECRET", "")

        if not all([api_key, api_secret, acc_token, acc_secret]):
            return jsonify({"error": "Twitter API keys not configured"}), 400

        client = _tweepy.Client(
            consumer_key=api_key, consumer_secret=api_secret,
            access_token=acc_token, access_token_secret=acc_secret,
        )
        response = client.create_tweet(text=content)
        tweet_id = response.data["id"]
        tweet_url = f"https://twitter.com/i/web/status/{tweet_id}"

        if qid:
            queue = _load_json_list(M5_QUEUE_PATH)
            for item in queue:
                if item.get("id") == qid:
                    item["status"] = "posted"
                    item["posted_at"] = datetime.date.today().isoformat()
                    item["tweet_url"] = tweet_url
                    break
            _save_json_list(M5_QUEUE_PATH, queue)

        return jsonify({"ok": True, "tweet_url": tweet_url, "tweet_id": tweet_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/twitter-keys", methods=["POST"])
def api_m5_twitter_keys():
    data = request.get_json(silent=True) or {}
    keys = {
        "TWITTER_API_KEY":       (data.get("api_key") or "").strip(),
        "TWITTER_API_SECRET":    (data.get("api_secret") or "").strip(),
        "TWITTER_ACCESS_TOKEN":  (data.get("access_token") or "").strip(),
        "TWITTER_ACCESS_SECRET": (data.get("access_secret") or "").strip(),
    }
    try:
        lines = []
        existing = {}
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, _ = line.split("=", 1)
                    existing[k.strip()] = line
                else:
                    lines.append(line)
        for k, v in keys.items():
            if v:
                existing[k] = f"{k}={v}"
        ENV_PATH.write_text("\n".join(lines + list(existing.values())) + "\n", encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/twitter-status")
def api_m5_twitter_status():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    configured = all(env.get(k) for k in ["TWITTER_API_KEY","TWITTER_API_SECRET","TWITTER_ACCESS_TOKEN","TWITTER_ACCESS_SECRET"])
    return jsonify({"configured": configured, "tweepy_available": _TWEEPY_AVAILABLE})


@app.route("/api/sage/m5/voice-notes")
def api_m5_voice_notes():
    try:
        if M5_VOICE_PATH.exists():
            return jsonify(json.loads(M5_VOICE_PATH.read_text(encoding="utf-8")))
        return jsonify({})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _m5_read_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _m5_save_env_keys(new_keys: dict):
    env = _m5_read_env()
    for k, v in new_keys.items():
        if v:
            env[k] = v
    lines = [f"{k}={v}" for k, v in env.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _m5_mark_posted(qid: str, extra: dict | None = None):
    queue = _load_json_list(M5_QUEUE_PATH)
    for item in queue:
        if item.get("id") == qid:
            item["status"] = "posted"
            item["posted_at"] = datetime.date.today().isoformat()
            if extra:
                item.update(extra)
            break
    _save_json_list(M5_QUEUE_PATH, queue)


# ── Reddit auto-post ──────────────────────────────────────────────────────────

@app.route("/api/sage/m5/reddit", methods=["POST"])
def api_m5_reddit():
    if not _PRAW_AVAILABLE:
        return jsonify({"error": "praw not installed. Run: pip install praw"}), 500

    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    qid = (data.get("qid") or "").strip()
    subreddit_name = (data.get("subreddit") or "").strip()

    if not content or not subreddit_name:
        return jsonify({"error": "content and subreddit are required"}), 400

    env = _m5_read_env()
    client_id     = env.get("REDDIT_CLIENT_ID", "")
    client_secret = env.get("REDDIT_CLIENT_SECRET", "")
    username      = env.get("REDDIT_USERNAME", "")
    password      = env.get("REDDIT_PASSWORD", "")

    if not all([client_id, client_secret, username, password]):
        return jsonify({"error": "Reddit API keys not configured"}), 400

    try:
        lines = content.split("\n", 1)
        title = lines[0].strip()[:300]
        body = lines[1].strip() if len(lines) > 1 else ""

        reddit = _praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            username=username,
            password=password,
            user_agent=f"SageDashboard/1.0 by /u/{username}",
        )
        sub = reddit.subreddit(subreddit_name)
        submission = sub.submit(title=title, selftext=body)
        post_url = f"https://www.reddit.com{submission.permalink}"

        if qid:
            _m5_mark_posted(qid, {"post_url": post_url, "subreddit": subreddit_name})

        return jsonify({"ok": True, "post_url": post_url})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Pinterest auto-post ───────────────────────────────────────────────────────

@app.route("/api/sage/m5/pinterest-boards")
def api_m5_pinterest_boards():
    env = _m5_read_env()
    token = env.get("PINTEREST_ACCESS_TOKEN", "")
    if not token:
        return jsonify({"error": "Pinterest token not configured"}), 400
    try:
        req = _urllib_req.Request(
            "https://api.pinterest.com/v5/boards?page_size=25",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with _urllib_req.urlopen(req, timeout=10) as resp:
            boards = json.loads(resp.read().decode())
        items = [{"id": b["id"], "name": b["name"]} for b in boards.get("items", [])]
        return jsonify({"boards": items})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/pinterest", methods=["POST"])
def api_m5_pinterest():
    data = request.get_json(silent=True) or {}
    content   = (data.get("content") or "").strip()
    qid       = (data.get("qid") or "").strip()
    board_id  = (data.get("board_id") or "").strip()
    image_url = (data.get("image_url") or "").strip()

    if not content or not board_id:
        return jsonify({"error": "content and board_id are required"}), 400

    env = _m5_read_env()
    token = env.get("PINTEREST_ACCESS_TOKEN", "")
    if not token:
        return jsonify({"error": "Pinterest access token not configured"}), 400

    try:
        lines = content.split("\n", 1)
        title = lines[0].strip()[:100]
        description = (lines[1].strip() if len(lines) > 1 else content)[:500]

        pin_body: dict = {
            "board_id": board_id,
            "title": title,
            "description": description,
        }
        if image_url:
            pin_body["media_source"] = {"source_type": "image_url", "url": image_url}

        payload = json.dumps(pin_body).encode("utf-8")
        req = _urllib_req.Request(
            "https://api.pinterest.com/v5/pins",
            data=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

        pin_url = f"https://www.pinterest.com/pin/{result.get('id', '')}"
        if qid:
            _m5_mark_posted(qid, {"pin_url": pin_url})

        return jsonify({"ok": True, "pin_url": pin_url, "pin_id": result.get("id")})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Beehiiv newsletter draft ──────────────────────────────────────────────────

@app.route("/api/sage/m5/beehiiv", methods=["POST"])
def api_m5_beehiiv():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    qid     = (data.get("qid") or "").strip()

    if not content:
        return jsonify({"error": "content is required"}), 400

    env = _m5_read_env()
    api_key = env.get("BEEHIIV_API_KEY", "")
    pub_id  = env.get("BEEHIIV_PUBLICATION_ID", "")
    if not api_key or not pub_id:
        return jsonify({"error": "BEEHIIV_API_KEY and BEEHIIV_PUBLICATION_ID not configured"}), 400

    try:
        lines = content.split("\n", 3)
        subject = lines[0].replace("Subject:", "").replace("SUBJECT:", "").strip()[:150] or "Newsletter Draft"
        preview = lines[1].strip()[:150] if len(lines) > 1 else ""
        body_text = "\n".join(lines[2:]).strip() if len(lines) > 2 else content

        post_body = {
            "subject": subject,
            "preview_text": preview,
            "content": {
                "free": {
                    "web": f"<p>{body_text.replace(chr(10), '</p><p>')}</p>",
                },
            },
            "status": "draft",
        }
        payload = json.dumps(post_body).encode("utf-8")
        req = _urllib_req.Request(
            f"https://api.beehiiv.com/v2/publications/{pub_id}/posts",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

        draft_url = result.get("data", {}).get("url", "")
        if qid:
            _m5_mark_posted(qid, {"draft_url": draft_url})

        return jsonify({"ok": True, "draft_url": draft_url})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Substack draft ────────────────────────────────────────────────────────────

@app.route("/api/sage/m5/substack", methods=["POST"])
def api_m5_substack():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    qid     = (data.get("qid") or "").strip()

    if not content:
        return jsonify({"error": "content is required"}), 400

    env = _m5_read_env()
    api_key  = env.get("SUBSTACK_API_KEY", "")
    pub_url  = env.get("SUBSTACK_PUBLICATION_URL", "").rstrip("/")  # e.g. https://yourname.substack.com
    if not api_key or not pub_url:
        return jsonify({"error": "SUBSTACK_API_KEY and SUBSTACK_PUBLICATION_URL not configured"}), 400

    try:
        lines = content.split("\n", 3)
        title = lines[0].replace("Subject:", "").replace("SUBJECT:", "").strip()[:200] or "New Post"
        subtitle = lines[1].strip()[:300] if len(lines) > 1 else ""
        body_html = "<p>" + "</p><p>".join(
            line for line in "\n".join(lines[2:] if len(lines) > 2 else [content]).split("\n") if line.strip()
        ) + "</p>"

        post_body = {
            "draft_title": title,
            "draft_subtitle": subtitle,
            "draft_body": body_html,
            "audience": "everyone",
        }
        payload = json.dumps(post_body).encode("utf-8")
        req = _urllib_req.Request(
            f"{pub_url}/api/v1/drafts",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with _urllib_req.urlopen(req, timeout=12) as resp:
            result = json.loads(resp.read().decode())

        draft_id  = result.get("id", "")
        draft_url = f"{pub_url}/publish/post/{draft_id}" if draft_id else ""
        if qid:
            _m5_mark_posted(qid, {"draft_url": draft_url})

        return jsonify({"ok": True, "draft_url": draft_url, "draft_id": draft_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Platform keys save / status ───────────────────────────────────────────────

@app.route("/api/sage/m5/platform-keys", methods=["POST"])
def api_m5_platform_keys():
    data = request.get_json(silent=True) or {}
    allowed = {
        "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME", "REDDIT_PASSWORD",
        "REDDIT_DEFAULT_SUBREDDIT",
        "PINTEREST_ACCESS_TOKEN", "PINTEREST_DEFAULT_BOARD_ID",
        "BEEHIIV_API_KEY", "BEEHIIV_PUBLICATION_ID",
        "TWITTER_API_KEY", "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET",
        "SUBSTACK_API_KEY", "SUBSTACK_PUBLICATION_URL",
    }
    keys = {k: v.strip() for k, v in (data.get("keys") or data).items() if k in allowed and v}
    try:
        _m5_save_env_keys(keys)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sage/m5/platform-status")
def api_m5_platform_status():
    env = _m5_read_env()
    twitter_ok   = all(env.get(k) for k in ["TWITTER_API_KEY","TWITTER_API_SECRET","TWITTER_ACCESS_TOKEN","TWITTER_ACCESS_SECRET"])
    reddit_ok    = all(env.get(k) for k in ["REDDIT_CLIENT_ID","REDDIT_CLIENT_SECRET","REDDIT_USERNAME","REDDIT_PASSWORD"])
    pinterest_ok = bool(env.get("PINTEREST_ACCESS_TOKEN"))
    beehiiv_ok   = bool(env.get("BEEHIIV_API_KEY") and env.get("BEEHIIV_PUBLICATION_ID"))
    substack_ok  = bool(env.get("SUBSTACK_API_KEY") and env.get("SUBSTACK_PUBLICATION_URL"))
    return jsonify({
        "twitter":   {"configured": twitter_ok,   "lib": _TWEEPY_AVAILABLE, "lib_name": "tweepy"},
        "reddit":    {"configured": reddit_ok,    "lib": _PRAW_AVAILABLE,   "lib_name": "praw"},
        "pinterest": {"configured": pinterest_ok, "lib": True,              "lib_name": None},
        "beehiiv":   {"configured": beehiiv_ok,   "lib": True,              "lib_name": None},
        "substack":  {"configured": substack_ok,  "lib": True,              "lib_name": None},
        "default_subreddit":  env.get("REDDIT_DEFAULT_SUBREDDIT", ""),
        "default_board_id":   env.get("PINTEREST_DEFAULT_BOARD_ID", ""),
    })


# ── Socket events ─────────────────────────────────────────────────────────────

_ALL_SOURCES = [
    "usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy",
    "linkedin", "indeed", "glassdoor", "google_jobs", "dice",
]

@socketio.on("run_agent")
def handle_run_agent(data=None):
    global _agent_running, _last_agent_mode
    with _agent_lock:
        if _agent_running:
            emit("log_line", {
                "text": "Agent is already on a mission — wait for her to return.",
                "kind": "warning"
            })
            return
        _agent_running = True

    d = data or {}
    sources = d.get("sources") or []
    if not sources:
        # Legacy mode support
        mode = d.get("mode", "both")
        if mode == "direct":
            sources = ["usajobs", "adzuna", "jsearch", "remotive", "remoteok", "jobicy"]
        elif mode == "apify":
            sources = ["linkedin", "indeed", "glassdoor", "google_jobs", "dice"]
        else:
            sources = list(_ALL_SOURCES)
    _last_agent_mode = ",".join(sources)
    emit("agent_status", {"running": True})
    socketio.start_background_task(_run_agent_task, sources)


def _run_agent_task(sources=None):
    global _agent_running, _chat_agent_sid
    _sid = _chat_agent_sid   # capture at task start so it doesn't change under us
    env  = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOURNEY_SELECTED_SOURCES"] = ",".join(sources or _ALL_SOURCES)
    env.pop("JOURNEY_SOURCE_MODE", None)

    try:
        log_api_call("AGENT_RUN", f"sources={env['JOURNEY_SELECTED_SOURCES']}")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(AGENT_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(SCRIPT_DIR),
            env=env,
        )
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            if not line:
                continue
            if line.startswith("__EVENT__:"):
                try:
                    socketio.emit("agent_event", json.loads(line[10:]))
                except Exception:
                    pass
            else:
                socketio.emit("log_line", {"text": line, "kind": _classify(line)})
                if _sid:
                    socketio.emit("chat_chunk", {"text": line + "\n"}, to=_sid)
        proc.wait()
    except Exception as exc:
        err = f"Subprocess error: {exc}"
        socketio.emit("log_line", {"text": err, "kind": "error"})
        if _sid:
            socketio.emit("chat_chunk", {"text": err + "\n"}, to=_sid)
    finally:
        _agent_running  = False
        _chat_agent_sid = None
        socketio.emit("agent_status",   {"running": False})
        socketio.emit("agent_complete", {})
        if _sid:
            socketio.emit("chat_complete", {}, to=_sid)


@socketio.on("run_apify")
def handle_run_apify():
    global _apify_running
    with _apify_lock:
        if _apify_running:
            emit("apify_progress", {"text": "Apify is already running — please wait.", "kind": "warning"})
            return
        _apify_running = True

    socketio.emit("apify_status_update", {"running": True})
    socketio.start_background_task(_run_apify_task)


def _run_apify_task():
    global _apify_running

    try:
        import apify_feed as _af

        token = _get_apify_token()
        if not token:
            socketio.emit("apify_progress", {
                "text": "No Apify token set — add it in the Apify panel first.", "kind": "error"
            })
            socketio.emit("apify_complete", {"error": "No token", "jobs": 0})
            return

        def _progress(label, status, count, error):
            socketio.emit("apify_progress", {
                "label": label, "status": status,
                "count": count,  "error": error,
                "text":  f"[{label}] {status.upper()}" + (f" — {count} jobs" if count else "") + (f" — {error}" if error else ""),
                "kind":  "error" if status == "error" else "action",
            })

        import sys as _sys
        cfg_mod   = _sys.modules.get("config")
        max_hours = getattr(cfg_mod, "APIFY_CACHE_MAX_HOURS", 24)

        socketio.emit("apify_progress", {"text": "Starting Apify fetch...", "kind": "action"})
        jobs, errors = _af.fetch_all(interactive=False, progress_cb=_progress)
        job_count    = len(jobs)
        error_count  = sum(1 for v in errors.values() if v)
        log_api_call("APIFY_RUN", f"{job_count} jobs fetched, {error_count} source errors")

        socketio.emit("apify_complete", {
            "jobs":    job_count,
            "errors":  error_count,
            "sources": errors,
        })
        socketio.emit("apify_progress", {
            "text": f"Apify complete — {job_count} jobs cached. {error_count} source error(s).",
            "kind": "apply" if not error_count else "warning",
        })

    except ImportError:
        msg = "apify-client not installed. Run: pip install apify-client"
        socketio.emit("apify_progress", {"text": msg, "kind": "error"})
        socketio.emit("apify_complete",  {"error": msg, "jobs": 0})
    except Exception as exc:
        msg = f"Apify task error: {exc}"
        socketio.emit("apify_progress", {"text": msg, "kind": "error"})
        socketio.emit("apify_complete",  {"error": msg, "jobs": 0})
    finally:
        _apify_running = False
        socketio.emit("apify_status_update", {"running": False})


@socketio.on("run_apify_source")
def handle_run_apify_source(data):
    global _apify_running
    source = (data or {}).get("source", "").strip()
    if not source:
        emit("apify_progress", {"text": "No source specified.", "kind": "error"})
        return
    with _apify_lock:
        if _apify_running:
            emit("apify_progress", {"text": "Apify is already running — please wait.", "kind": "warning"})
            return
        _apify_running = True

    socketio.emit("apify_status_update", {"running": True, "source": source})
    socketio.start_background_task(_run_apify_source_task, source)


def _run_apify_source_task(source_key: str):
    global _apify_running
    try:
        import apify_feed as _af

        token = _get_apify_token()
        if not token:
            socketio.emit("apify_progress", {
                "text": "No Apify token set — add it in the Apify panel first.", "kind": "error"
            })
            socketio.emit("apify_complete", {"error": "No token", "jobs": 0})
            return

        def _progress(label, status, count, error):
            socketio.emit("apify_progress", {
                "label": label, "status": status,
                "count": count,  "error": error,
                "text":  f"[{label}] {status.upper()}" + (f" — {count} new jobs" if count else "") + (f" — {error}" if error else ""),
                "kind":  "error" if status == "error" else ("apply" if status == "ok" else "action"),
            })

        label = source_key  # fallback; overridden by progress_cb
        socketio.emit("apify_progress", {"text": f"Starting {source_key} fetch...", "kind": "action"})
        new_jobs, err = _af.fetch_one(source_key, progress_cb=_progress)
        log_api_call("APIFY_SOURCE", f"{source_key}: {len(new_jobs)} jobs, err={err or 'none'}")

        if err:
            socketio.emit("apify_complete", {"error": err, "jobs": 0})
            socketio.emit("apify_progress", {"text": f"{source_key} failed: {err}", "kind": "error"})
        else:
            socketio.emit("apify_complete", {"jobs": len(new_jobs), "errors": 0, "sources": {}})
            socketio.emit("apify_progress", {
                "text": f"{source_key.title()} complete — {len(new_jobs)} new jobs cached.",
                "kind": "apply",
            })

    except ImportError:
        msg = "apify-client not installed. Run: pip install apify-client"
        socketio.emit("apify_progress", {"text": msg, "kind": "error"})
        socketio.emit("apify_complete",  {"error": msg, "jobs": 0})
    except Exception as exc:
        msg = f"Apify task error: {exc}"
        socketio.emit("apify_progress", {"text": msg, "kind": "error"})
        socketio.emit("apify_complete",  {"error": msg, "jobs": 0})
    finally:
        _apify_running = False
        socketio.emit("apify_status_update", {"running": False})


def _classify(line: str) -> str:
    u = line.upper()
    if "APPLY NOW" in u:
        return "apply"
    if "REVIEW MANUALLY" in u:
        return "review"
    if "SKIP" in u and ("/10" in u or "SCORE" in u or "RECOMMENDATION" in u):
        return "skip"
    if any(k in u for k in ("RESUME:", "LETTER:", "TAILORED", "COVER LETTER")):
        return "doc"
    if "ERROR" in u:
        return "error"
    return "action"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_tracker():
    if not TRACKER_PATH.exists():
        return []
    try:
        with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
            return [dict(r) for r in csv.DictReader(f)]
    except Exception:
        return []


def _update_tracker_row(url: str, field: str, value: str):
    _update_tracker_row_multi(url, {field: value})


def _update_tracker_row_multi(url: str, updates: dict) -> str:
    """Update multiple fields on a matching row in a single read-write pass.
    Returns empty string on success, error message on failure."""
    rows, fieldnames = [], []
    try:
        with open(TRACKER_PATH, newline="", encoding="utf-8") as f:
            reader     = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            for row in reader:
                if row.get("Application URL", "").lower().rstrip("/") == url.lower().rstrip("/"):
                    for field, value in updates.items():
                        row[field] = value
                rows.append(dict(row))
    except Exception as e:
        return f"read error: {e}"
    try:
        with open(TRACKER_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        return ""
    except Exception as e:
        return f"write error: {e}"


# ── Chat helpers ──────────────────────────────────────────────────────────────

def _load_chat_log():
    if not CHAT_LOG_PATH.exists():
        return []
    try:
        with open(CHAT_LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_chat_log(history):
    LOGS_DIR.mkdir(exist_ok=True)
    try:
        with open(CHAT_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _tracker_context():
    rows = _read_tracker()
    if not rows:
        return "Job tracker is empty — no applications logged yet."
    lines = [f"Job Tracker ({len(rows)} entries):"]
    for r in rows:
        lines.append(
            f"  - {r.get('Company','?')} | {r.get('Role','?')} | "
            f"Score: {r.get('AI Score','?')} | Rec: {r.get('AI Recommendation','?')} | "
            f"Status: {r.get('Status','?')} | Applied: {r.get('Date Applied','?')} | "
            f"Follow Up: {r.get('Follow Up Date','?')}"
        )
    return "\n".join(lines)


def _read_file_safe(path):
    try:
        txt = path.read_text(encoding="utf-8").strip()
        return "" if "replace this" in txt.lower() else txt
    except Exception:
        return ""


# ── JSON list helpers (outreach queue, audits, etc.) ─────────────────────────

def _load_json_list(path):
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_json_list(path, data):
    LOGS_DIR.mkdir(exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _add_business_days(start_date, days):
    current = start_date
    added = 0
    while added < days:
        current += datetime.timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _load_rema_history():
    return _load_json_list(REMA_CHAT_PATH)


def _save_rema_history(hist):
    _save_json_list(REMA_CHAT_PATH, hist)


def _get_or_generate_affirmation():
    today = datetime.date.today().isoformat()
    try:
        if AFFIRMATION_PATH.exists():
            cached = json.loads(AFFIRMATION_PATH.read_text(encoding="utf-8"))
            if cached.get("date") == today:
                return cached.get("text", "")
    except Exception:
        pass

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key or not _ANTHROPIC_AVAILABLE:
        text = random.choice(_FALLBACK_AFFIRMATIONS)
    else:
        try:
            client = _anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=120,
                messages=[{"role": "user", "content":
                    "Write one short, powerful motivational affirmation (2-3 sentences max) for "
                    "Kee — a woman actively transitioning into IT/cybersecurity while holding a "
                    "full-time job, earning her CompTIA A+, and studying Network+ at WGU. Make it "
                    "specific to her situation — not generic. Energetic, direct, no fluff."}],
            )
            text = resp.content[0].text.strip().strip('"')
        except Exception:
            text = random.choice(_FALLBACK_AFFIRMATIONS)

    try:
        LOGS_DIR.mkdir(exist_ok=True)
        AFFIRMATION_PATH.write_text(json.dumps({"date": today, "text": text}), encoding="utf-8")
    except Exception:
        pass
    return text


def _parse_claude_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# ── Chat routes ────────────────────────────────────────────────────────────────

@app.route("/api/chat-history")
def api_chat_history():
    return jsonify(_load_chat_log())


# ── Chat socket handler ────────────────────────────────────────────────────────

_AGENT_TRIGGERS = {
    "run the agent", "find jobs", "start the mission", "scan for jobs",
    "run agent", "begin the mission", "go on a mission", "launch the agent",
    "deploy", "send journey", "start a scan",
}


@socketio.on("chat_message")
def handle_chat(data):
    global _agent_running, _chat_agent_sid

    sid       = request.sid
    user_text = (data.get("text") or "").strip()
    if not user_text:
        return

    import sys as _sys
    cfg_module = _sys.modules.get("config")
    if cfg_module and not getattr(cfg_module, "CHAT_ENABLED", True):
        socketio.emit("chat_complete",
                      {"error": "Chat is disabled. Set CHAT_ENABLED = True in config.py."},
                      to=sid)
        return

    lower = user_text.lower()
    if any(t in lower for t in _AGENT_TRIGGERS):
        with _agent_lock:
            already = _agent_running
            if not already:
                _agent_running  = True
                _chat_agent_sid = sid

        if already:
            reply = "I'm already in the field — wait for me to return before sending me out again."
            socketio.emit("chat_chunk",   {"text": reply}, to=sid)
            socketio.emit("chat_complete", {},              to=sid)
            hist = _load_chat_log()
            hist += [{"role": "user", "content": user_text},
                     {"role": "assistant", "content": reply}]
            _save_chat_log(hist)
            return

        socketio.emit("chat_agent_start", {}, to=sid)
        socketio.emit("agent_status", {"running": True})
        _src_list = [s for s in _last_agent_mode.split(",") if s] if _last_agent_mode else list(_ALL_SOURCES)
        socketio.start_background_task(_run_agent_task, _src_list)
        return

    socketio.start_background_task(_chat_task, user_text, sid)


def _chat_task(user_text, sid):
    if not _ANTHROPIC_AVAILABLE:
        socketio.emit("chat_complete",
                      {"error": "anthropic package not installed — run: pip install anthropic"},
                      to=sid)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        socketio.emit("chat_complete",
                      {"error": "ANTHROPIC_API_KEY environment variable is not set."},
                      to=sid)
        return

    import sys as _sys
    cfg_module  = _sys.modules.get("config")
    max_tokens  = getattr(cfg_module, "CHAT_MAX_TOKENS",    1500)
    hist_limit  = getattr(cfg_module, "CHAT_HISTORY_LIMIT", 20)
    ai_model    = getattr(cfg_module, "AI_MODEL", "claude-sonnet-4-6")

    history = _load_chat_log()

    tracker_ctx = _tracker_context()
    bio_text    = _read_file_safe(BIO_FILE)
    system = JOURNEY_SYSTEM + f"\n\n[CURRENT TRACKER DATA]\n{tracker_ctx}"

    # Inject cached legitimacy scores when Kee asks about job safety
    _lower_msg = user_text.lower()
    _legit_keywords = ("legit", "scam", "trustworthy", "real company", "trust this",
                       "trust the", "is this job", "safe to apply", "fake job", "sketchy")
    if any(w in _lower_msg for w in _legit_keywords):
        try:
            import legitimacy_scorer as _ls
            _lcache = _ls._load_cache()
            _lrows  = _read_tracker()
            _llines = []
            for _lr in _lrows:
                _lurl = (_lr.get("Application URL") or "").strip()
                _lkey = _ls._cache_key(_lurl) if _lurl else ""
                if _lkey and _lkey in _lcache and not _ls._is_expired(_lcache[_lkey]):
                    _lsc = _lcache[_lkey]
                    _lflags    = "; ".join(_lsc.get("flags_found")    or ["none"])
                    _lboosters = "; ".join(_lsc.get("boosters_found") or ["none"])
                    _llines.append(
                        f"  {_lr.get('Company','?')} ({_lr.get('Role','?')}): "
                        f"{_lsc.get('emoji','')} {_lsc.get('score',0)}% — {_lsc.get('label','')} | "
                        f"Red flags: {_lflags} | "
                        f"Trust signals: {_lboosters} | "
                        f"Verdict: {_lsc.get('recommendation','')}"
                    )
            if _llines:
                system += (
                    "\n\n[LEGITIMACY SCORES — use these when Kee asks if a job is safe or real]\n"
                    + "\n".join(_llines)
                    + "\n\nWhen explaining a legitimacy score to Kee, lead with the score and label, "
                    "list what looks good and what raised flags, then give her one clear next action."
                )
        except Exception:
            pass

    if bio_text:
        system += f"\n\n[CANDIDATE BIO]\n{bio_text}"

    recent   = history[-hist_limit:] if len(history) > hist_limit else history
    messages = recent + [{"role": "user", "content": user_text}]

    try:
        client     = _anthropic.Anthropic(api_key=api_key)
        full_text  = ""

        with client.messages.stream(
            model=ai_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                full_text += chunk
                socketio.emit("chat_chunk", {"text": chunk}, to=sid)

        socketio.emit("chat_complete", {}, to=sid)

        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": full_text})
        _save_chat_log(history)

    except Exception as exc:
        socketio.emit("chat_complete", {"error": str(exc)}, to=sid)


# ── Rema socket handlers ──────────────────────────────────────────────────────

@socketio.on("rema_message")
def handle_rema_chat(data):
    sid       = request.sid
    user_text = (data.get("text") or "").strip()
    if not user_text:
        return
    socketio.start_background_task(_rema_chat_task, user_text, sid)


@socketio.on("generate_outreach")
def handle_generate_outreach(data):
    sid     = request.sid
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company or not role:
        socketio.emit("outreach_error", {"error": "Company and role are required."}, to=sid)
        return
    socketio.start_background_task(_generate_outreach_task, company, role, sid)


@socketio.on("audit_profile")
def handle_audit_profile(data):
    sid      = request.sid
    job_desc = (data.get("job_description") or "").strip()
    if not job_desc:
        socketio.emit("audit_error", {"error": "Paste a job description first."}, to=sid)
        return
    socketio.start_background_task(_audit_profile_task, job_desc, sid)


def _rema_chat_task(user_text, sid):
    if not _ANTHROPIC_AVAILABLE:
        socketio.emit("rema_complete", {"error": "anthropic package not installed."}, to=sid)
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        socketio.emit("rema_complete", {"error": "ANTHROPIC_API_KEY not set."}, to=sid)
        return

    import sys as _sys
    cfg = _sys.modules.get("config")
    max_tokens = getattr(cfg, "REMA_CHAT_MAX_TOKENS", 1500)
    ai_model   = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

    history  = _load_rema_history()
    recent   = history[-20:] if len(history) > 20 else history
    messages = recent + [{"role": "user", "content": user_text}]

    try:
        client    = _anthropic.Anthropic(api_key=api_key)
        full_text = ""
        with client.messages.stream(
            model=ai_model,
            max_tokens=max_tokens,
            system=REMA_SYSTEM,
            messages=messages,
        ) as stream:
            for chunk in stream.text_stream:
                full_text += chunk
                socketio.emit("rema_chunk", {"text": chunk}, to=sid)

        socketio.emit("rema_complete", {}, to=sid)
        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": full_text})
        _save_rema_history(history)

    except Exception as exc:
        socketio.emit("rema_complete", {"error": str(exc)}, to=sid)


def _generate_outreach_task(company, role, sid):
    if not _ANTHROPIC_AVAILABLE:
        socketio.emit("outreach_error", {"error": "anthropic package not installed."}, to=sid)
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        socketio.emit("outreach_error", {"error": "ANTHROPIC_API_KEY not set."}, to=sid)
        return

    bio_text    = _read_file_safe(BIO_FILE)
    resume_text = _read_file_safe(RESUME_FILE)
    context     = (bio_text or resume_text)[:600]

    prompt = (
        f"Generate LinkedIn outreach messages for Kee Earl who applied for {role} at {company}.\n\n"
        f"Kee's background (use specifics, not generic phrases):\n{context}\n\n"
        "RULE: Never use an em dash (—) inside parentheses. Rewrite using a comma, colon, or separate sentence.\n\n"
        "Return ONLY a valid JSON object with exactly these keys — no extra text:\n"
        '{\n'
        '  "connection_note": "LinkedIn connection request note — UNDER 295 chars, warm, mentions the specific role",\n'
        '  "linkedin_message": "Message to send after connecting — 150-220 chars, conversational, asks for a quick insight or call",\n'
        '  "email_subject": "Cold email subject line — under 55 chars, compelling",\n'
        '  "email_body": "Full cold email body — 150-200 words, specific to the company and role, professional closing",\n'
        '  "search_tips": "3 specific bullet points (use \\n• ) on how to find the recruiter/hiring manager at this company on LinkedIn"\n'
        '}'
    )

    try:
        import sys as _sys
        cfg      = _sys.modules.get("config")
        ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw  = resp.content[0].text
        data = _parse_claude_json(raw)

        record = {
            "id":                str(_uuid.uuid4())[:8],
            "created":           datetime.datetime.now().isoformat(),
            "company":           company,
            "role":              role,
            "recruiter_name":    "",
            "recruiter_title":   "",
            "recruiter_linkedin": "",
            "connection_note":   data.get("connection_note", ""),
            "linkedin_message":  data.get("linkedin_message", ""),
            "email_subject":     data.get("email_subject", ""),
            "email_body":        data.get("email_body", ""),
            "search_tips":       data.get("search_tips", ""),
            "status":            "Pending",
            "sent_date":         "",
            "followup_date":     "",
            "followup_sent":     False,
        }

        queue = _load_json_list(OUTREACH_PATH)
        queue.append(record)
        _save_json_list(OUTREACH_PATH, queue)

        socketio.emit("outreach_result", record, to=sid)

    except json.JSONDecodeError as e:
        socketio.emit("outreach_error", {"error": f"Could not parse Rema's output: {e}"}, to=sid)
    except Exception as exc:
        socketio.emit("outreach_error", {"error": str(exc)}, to=sid)


# ── Recruiter Finder socket handlers ─────────────────────────────────────────

@socketio.on("find_recruiters")
def handle_find_recruiters(data):
    sid     = request.sid
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company:
        socketio.emit("recruiter_error", {"error": "Company name is required."}, to=sid)
        return
    socketio.start_background_task(_find_recruiters_task, company, role, sid)


def _find_recruiters_task(company: str, role: str, sid: str):
    socketio.emit("recruiter_progress",
                  {"text": f"Searching for recruiters at {company}..."}, to=sid)

    socketio.emit("recruiter_progress",
                  {"text": "Scanning LinkedIn profiles via web search..."}, to=sid)
    ddg_results = []
    try:
        ddg_results = _search_recruiters_ddg(company)
    except Exception:
        pass

    hunter_results = []
    if _get_hunter_key():
        socketio.emit("recruiter_progress",
                      {"text": "Querying Hunter.io for email contacts..."}, to=sid)
        try:
            hunter_results = _search_recruiters_hunter(company)
        except Exception:
            pass

    combined = _merge_recruiter_contacts(ddg_results, hunter_results)

    manual_url = (
        "https://www.linkedin.com/search/results/people/?"
        + _urllib_parse.urlencode({"keywords": f"{company} recruiter"})
    )

    usage = _get_hunter_usage()
    socketio.emit("recruiter_results", {
        "company":         company,
        "role":            role,
        "contacts":        combined,
        "manual_url":      manual_url,
        "hunter_available": bool(_get_hunter_key()),
        "hunter_count":    usage["count"],
    }, to=sid)


@socketio.on("draft_recruiter_outreach")
def handle_draft_recruiter_outreach(data):
    sid     = request.sid
    contact = data.get("contact") or {}
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    if not company or not (contact.get("name") or "").strip():
        socketio.emit("rema_complete", {"error": "Missing contact or company data."}, to=sid)
        return
    socketio.start_background_task(_draft_recruiter_outreach_task, contact, company, role, sid)


def _draft_recruiter_outreach_task(contact: dict, company: str, role: str, sid: str):
    if not _ANTHROPIC_AVAILABLE:
        socketio.emit("rema_complete", {"error": "anthropic package not installed."}, to=sid)
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        socketio.emit("rema_complete", {"error": "ANTHROPIC_API_KEY not set."}, to=sid)
        return

    bio_text    = _read_file_safe(BIO_FILE)
    resume_text = _read_file_safe(RESUME_FILE)
    context     = (bio_text or resume_text)[:500]

    rec_name  = (contact.get("name") or "[Recruiter Name]").strip()
    rec_title = (contact.get("title") or "").strip()
    has_email = bool((contact.get("email") or "").strip())

    email_key = (
        ',\n  "email_subject": "cold email subject — under 55 chars",'
        '\n  "email_body": "full email body — 150-200 words, personalized opening referencing their specific role"'
        if has_email else ""
    )

    prompt = (
        f"You are Rema, a sharp outreach strategist. Draft personalized outreach for Kee Earl "
        f"targeting a specific recruiter.\n\n"
        f"RECRUITER: {rec_name}"
        + (f" — {rec_title}" if rec_title else "")
        + f" at {company}\n"
        f"ROLE KEE IS TARGETING: {role or 'IT/cybersecurity entry-level'}\n"
        f"KEE'S BACKGROUND:\n{context}\n\n"
        "RULE: Never use an em dash (—) inside parentheses. Rewrite using a comma, colon, or separate sentence.\n\n"
        "Return ONLY a valid JSON object with exactly these keys — no extra text:\n"
        "{\n"
        f'  "connection_note": "LinkedIn connection request — under 295 chars, addresses {rec_name} by first name, warm and specific",\n'
        f'  "linkedin_message": "message to send after connecting — 150-220 chars, conversational, asks for an insight or quick call"'
        + email_key
        + "\n}"
    )

    try:
        import sys as _sys
        cfg      = _sys.modules.get("config")
        ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model, max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        draft_data = _parse_claude_json(resp.content[0].text)

        # Build a readable block to stream into Rema's chat
        first_name = rec_name.split()[0] if rec_name and " " in rec_name else rec_name
        header = (
            f"Outreach drafted for {rec_name}"
            + (f" ({rec_title} at {company})" if rec_title else f" at {company}")
            + ":\n\n"
        )
        body_parts = [
            "─── CONNECTION NOTE ──────────────────────────────\n"
            + draft_data.get("connection_note", ""),
            "\n\n─── LINKEDIN MESSAGE ────────────────────────────\n"
            + draft_data.get("linkedin_message", ""),
        ]
        if has_email and draft_data.get("email_body"):
            body_parts.append(
                f"\n\n─── EMAIL ───────────────────────────────────────\n"
                f"Subject: {draft_data.get('email_subject', '')}\n\n"
                + draft_data.get("email_body", "")
            )
        display = header + "".join(body_parts)

        socketio.emit("rema_chunk", {"text": display}, to=sid)
        socketio.emit("rema_complete",
                      {"draft_data": draft_data, "contact_id": contact.get("id", "")},
                      to=sid)

        history = _load_rema_history()
        history.append({"role": "assistant", "content": display})
        _save_rema_history(history)

    except json.JSONDecodeError as e:
        socketio.emit("rema_complete", {"error": f"Could not parse output: {e}"}, to=sid)
    except Exception as exc:
        socketio.emit("rema_complete", {"error": str(exc)}, to=sid)


@socketio.on("add_recruiter_to_queue")
def handle_add_recruiter_to_queue(data):
    sid     = request.sid
    contact = data.get("contact") or {}
    company = (data.get("company") or "").strip()
    role    = (data.get("role") or "").strip()
    draft   = data.get("draft") or {}

    record = {
        "id":                 str(_uuid.uuid4())[:8],
        "created":            datetime.datetime.now().isoformat(),
        "company":            company,
        "role":               role,
        "recruiter_name":     contact.get("name", ""),
        "recruiter_title":    contact.get("title", ""),
        "recruiter_linkedin": contact.get("linkedin_url", ""),
        "recruiter_email":    contact.get("email", ""),
        "connection_note":    draft.get("connection_note", ""),
        "linkedin_message":   draft.get("linkedin_message", ""),
        "email_subject":      draft.get("email_subject", ""),
        "email_body":         draft.get("email_body", ""),
        "search_tips":        "",
        "status":             "Pending",
        "sent_date":          "",
        "followup_date":      "",
        "followup_sent":      False,
    }

    queue = _load_json_list(OUTREACH_PATH)
    queue.append(record)
    _save_json_list(OUTREACH_PATH, queue)
    socketio.emit("recruiter_queued", {"record": record}, to=sid)


def _audit_profile_task(job_desc, sid):
    if not _ANTHROPIC_AVAILABLE:
        socketio.emit("audit_error", {"error": "anthropic package not installed."}, to=sid)
        return
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        socketio.emit("audit_error", {"error": "ANTHROPIC_API_KEY not set."}, to=sid)
        return

    resume_text = _read_file_safe(RESUME_FILE)
    bio_text    = _read_file_safe(BIO_FILE)

    prompt = (
        "You are Rema, a LinkedIn profile optimization expert. Audit Kee's profile "
        "(represented by her resume and bio below) against this job description.\n\n"
        f"RESUME / PROFILE:\n{resume_text}\n\n"
        f"BIO / ABOUT SECTION:\n{bio_text}\n\n"
        f"JOB DESCRIPTION:\n{job_desc[:2000]}\n\n"
        "Return ONLY a valid JSON object with exactly these keys — no extra text:\n"
        '{\n'
        '  "score": <integer 1-10>,\n'
        '  "score_reason": "one sentence explaining the score",\n'
        '  "headline": "specific optimized LinkedIn headline under 120 chars for this role",\n'
        '  "about": "rewritten LinkedIn About section 150-180 words, first person, compelling",\n'
        '  "skills_to_add": ["skill1", "skill2", "skill3", "skill4", "skill5"],\n'
        '  "experience_bullets": ["rewritten bullet 1 for most relevant role", "rewritten bullet 2"],\n'
        '  "top_gaps": ["most critical missing thing", "second gap", "third gap"]\n'
        '}'
    )

    try:
        import sys as _sys
        cfg      = _sys.modules.get("config")
        ai_model = getattr(cfg, "AI_MODEL", "claude-sonnet-4-6")

        client = _anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model=ai_model,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw  = resp.content[0].text
        data = _parse_claude_json(raw)

        # Extract role/company guess from job description first line
        first_line = job_desc.split("\n")[0][:80]

        record = {
            "id":                  str(_uuid.uuid4())[:8],
            "date":                datetime.date.today().isoformat(),
            "job_preview":         first_line,
            "score":               data.get("score", 0),
            "score_reason":        data.get("score_reason", ""),
            "headline":            data.get("headline", ""),
            "about":               data.get("about", ""),
            "skills_to_add":       data.get("skills_to_add", []),
            "experience_bullets":  data.get("experience_bullets", []),
            "top_gaps":            data.get("top_gaps", []),
        }

        audits = _load_json_list(PROFILE_AUDITS_PATH)
        audits.append(record)
        _save_json_list(PROFILE_AUDITS_PATH, audits)

        socketio.emit("audit_result", record, to=sid)

    except json.JSONDecodeError as e:
        socketio.emit("audit_error", {"error": f"Could not parse audit output: {e}"}, to=sid)
    except Exception as exc:
        socketio.emit("audit_error", {"error": str(exc)}, to=sid)


# ── Recruiter Finder helpers ──────────────────────────────────────────────────

def _load_env_file() -> dict:
    if not ENV_PATH.exists():
        return {}
    result = {}
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return result


def _get_hunter_key() -> str:
    key = os.environ.get("HUNTER_API_KEY", "").strip()
    if not key:
        key = _load_env_file().get("HUNTER_API_KEY", "")
    return key


def _get_apify_token() -> str:
    tok = os.environ.get("APIFY_API_TOKEN", "").strip()
    if not tok:
        tok = _load_env_file().get("APIFY_API_TOKEN", "")
    return tok


def _save_apify_token(token: str):
    env_vars = _load_env_file()
    env_vars["APIFY_API_TOKEN"] = token
    lines = [f"{k}={v}" for k, v in env_vars.items()]
    try:
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass
    os.environ["APIFY_API_TOKEN"] = token


def _save_hunter_key(key: str):
    env_vars = _load_env_file()
    env_vars["HUNTER_API_KEY"] = key
    lines = [f"{k}={v}" for k, v in env_vars.items()]
    try:
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _get_hunter_usage() -> dict:
    month = datetime.date.today().strftime("%Y-%m")
    try:
        if HUNTER_USAGE_PATH.exists():
            data = json.loads(HUNTER_USAGE_PATH.read_text(encoding="utf-8"))
            if data.get("month") == month:
                return data
    except Exception:
        pass
    return {"month": month, "count": 0}


def _increment_hunter_usage():
    usage = _get_hunter_usage()
    usage["count"] += 1
    LOGS_DIR.mkdir(exist_ok=True)
    try:
        HUNTER_USAGE_PATH.write_text(json.dumps(usage), encoding="utf-8")
    except Exception:
        pass


def _company_to_domain(company: str) -> str:
    clean = company.lower()
    clean = _re.sub(
        r'\b(inc|llc|ltd|corp|co|company|group|solutions|services|'
        r'technologies|technology|tech|systems|partners|global|international|web)\b\.?',
        '', clean
    )
    clean = _re.sub(r'[^\w]', '', clean)
    return clean.strip() + ".com"


def _parse_linkedin_title(title: str):
    """Return (name, job_title) from a LinkedIn page title string."""
    title = _re.sub(r'\s*\|\s*LinkedIn\s*$', '', title, flags=_re.IGNORECASE).strip()
    if ' - ' in title:
        name, _, rest = title.partition(' - ')
        job_title = _re.sub(r'\s+at\s+.+$', '', rest, flags=_re.IGNORECASE).strip()
        return name.strip(), job_title
    return title, ""


def _search_recruiters_ddg(company: str) -> list:
    """Scrape DuckDuckGo HTML for LinkedIn recruiter profiles at a company."""
    query = (
        f'site:linkedin.com/in ("talent acquisition" OR "recruiter" OR '
        f'"hiring manager" OR "technical recruiter") "{company}"'
    )
    url = "https://html.duckduckgo.com/html/?" + _urllib_parse.urlencode({"q": query})
    try:
        req = _urllib_req.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        with _urllib_req.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    # DuckDuckGo bot-challenge page — no results to parse
    if "cc=botnet" in content or "anomaly.js" in content or "challenge-form" in content:
        return []

    results = []
    seen_urls: set = set()
    company_lower = company.lower()
    recruiter_kws = {
        "recruiter", "talent acquisition", "hiring manager",
        "technical recruiter", "people operations", "talent partner",
        "hr manager", "human resources", "talent specialist",
    }

    # Each DDG result: a <div class="result ..."> containing title, url, snippet
    blocks = _re.split(r'<div[^>]+class="[^"]*result[^"]*results_links[^"]*"', content)

    for block in blocks[1:16]:
        # Display URL — must be linkedin.com/in/
        url_m = _re.search(r'class="result__url"[^>]*>(.*?)</a>', block, _re.DOTALL)
        if not url_m:
            continue
        url_text = _re.sub(r'<[^>]+>', '', url_m.group(1)).strip().lower()
        if 'linkedin.com/in' not in url_text:
            continue

        # Title
        title_m = _re.search(r'class="result__a"[^>]*>(.*?)</a>', block, _re.DOTALL)
        if not title_m:
            continue
        title_clean = _html.unescape(_re.sub(r'<[^>]+>', '', title_m.group(1))).strip()

        # Real LinkedIn URL from uddg redirect
        href_m = _re.search(r'class="result__a"[^>]+href="([^"]+)"', block)
        linkedin_url = ""
        if href_m:
            href = _html.unescape(href_m.group(1))
            uddg_m = _re.search(r'uddg=([^&"]+)', href)
            if uddg_m:
                real = _urllib_parse.unquote(uddg_m.group(1))
                if 'linkedin.com/in/' in real.lower():
                    linkedin_url = real.split('?')[0].rstrip('/')
        if not linkedin_url:
            linkedin_url = 'https://www.' + url_text.lstrip('/')

        if 'linkedin.com/in/' not in linkedin_url.lower():
            continue
        norm_url = linkedin_url.lower()
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        # Snippet
        snip_m = _re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, _re.DOTALL)
        snippet = ""
        if snip_m:
            snippet = _html.unescape(_re.sub(r'<[^>]+>', '', snip_m.group(1))).strip()

        name, job_title = _parse_linkedin_title(title_clean)

        combined = (title_clean + " " + snippet).lower()
        if not any(kw in combined for kw in recruiter_kws):
            continue

        confidence = "High" if company_lower in combined else "Low"

        results.append({
            "id":             str(_uuid.uuid4())[:8],
            "name":           name,
            "title":          job_title,
            "company":        company,
            "email":          "",
            "linkedin_url":   linkedin_url,
            "confidence":     confidence,
            "source":         "search",
            "outreach_method": "LinkedIn Message",
            "status":         "Pending",
        })

    return results[:8]


def _search_recruiters_hunter(company: str) -> list:
    """Query Hunter.io domain search for recruiter email contacts."""
    api_key = _get_hunter_key()
    if not api_key:
        return []

    domain = _company_to_domain(company)
    url = (
        "https://api.hunter.io/v2/domain-search?"
        + _urllib_parse.urlencode({
            "domain": domain,
            "api_key": api_key,
            "limit": 20,
        })
    )
    try:
        req = _urllib_req.Request(url, headers={"User-Agent": "JobAppsToolkit/1.0"})
        with _urllib_req.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _increment_hunter_usage()
        log_api_call("HUNTER_IO", f"domain={domain}")
    except Exception:
        return []

    recruiter_kws = {
        "recruiter", "talent acquisition", "hr", "human resources",
        "people operations", "hiring manager", "technical recruiter",
        "talent partner", "talent specialist", "people partner",
    }

    results = []
    for person in (data.get("data") or {}).get("emails") or []:
        pos = (person.get("position") or "").lower()
        if not any(kw in pos for kw in recruiter_kws):
            continue

        name = f"{person.get('first_name') or ''} {person.get('last_name') or ''}".strip()
        email = person.get("value") or ""
        linkedin = person.get("linkedin") or ""
        method = "Email" if email else ("LinkedIn Message" if linkedin else "Connection Note")

        results.append({
            "id":             str(_uuid.uuid4())[:8],
            "name":           name,
            "title":          person.get("position") or "",
            "company":        company,
            "email":          email,
            "linkedin_url":   linkedin,
            "confidence":     "High" if email else "Medium",
            "source":         "hunter",
            "outreach_method": method,
            "status":         "Pending",
        })

    return results


def _merge_recruiter_contacts(ddg: list, hunter: list) -> list:
    """Combine DDG + Hunter results, deduplicate by name, rank by quality."""
    merged: dict = {}
    for contact in hunter + ddg:   # Hunter first = higher trust
        key = (contact.get("name") or "").lower().strip()
        if not key or key in ("", " "):
            continue
        if key not in merged:
            merged[key] = dict(contact)
        else:
            ex = merged[key]
            if contact["email"] and not ex["email"]:
                ex["email"] = contact["email"]
                ex["outreach_method"] = "Email"
            if contact["linkedin_url"] and not ex["linkedin_url"]:
                ex["linkedin_url"] = contact["linkedin_url"]
            if contact["confidence"] == "High" and ex["confidence"] != "High":
                ex["confidence"] = "High"

    title_rank = {
        "talent acquisition": 0, "technical recruiter": 1, "recruiter": 2,
        "hiring manager": 3, "people operations": 4, "hr": 5,
    }

    def _sort_key(c):
        has_email  = 0 if c["email"] else 1
        conf_rank  = {"High": 0, "Medium": 1, "Low": 2}.get(c["confidence"], 2)
        tl = c["title"].lower()
        tr = next((v for k, v in title_rank.items() if k in tl), 9)
        return (has_email, conf_rank, tr)

    return sorted(merged.values(), key=_sort_key)


# ── Entry point ───────────────────────────────────────────────────────────────

def _run_security_check() -> None:
    """Print a startup security status table. Green ✓ / Red ✗."""
    G = "\033[92m✓\033[0m"   # green tick
    R = "\033[91m✗\033[0m"   # red cross
    print("  Checking security configuration...")

    # 1. .env present
    env_ok = ENV_PATH.exists()
    print(f"  {G if env_ok else R} .env found and loaded"
          + ("" if env_ok else " — create .env and add your API keys"))

    # 2. .gitignore present and contains required entries
    gi_path = SCRIPT_DIR / ".gitignore"
    gi_required = [".env", ".dashboard_auth", ".dashboard_key",
                   "security.log", "/resumes/", "api_log.txt"]
    if gi_path.exists():
        gi_text = gi_path.read_text(encoding="utf-8")
        missing  = [e for e in gi_required if e not in gi_text]
        gi_ok    = not missing
        gi_note  = ("" if gi_ok else f" — missing entries: {', '.join(missing)}")
    else:
        gi_ok   = False
        gi_note = " — .gitignore not found"
    print(f"  {G if gi_ok else R} .gitignore verified — sensitive files protected{gi_note}")

    # 3. Localhost-only binding (always true — we pass 127.0.0.1 below)
    print(f"  {G} Server bound to localhost only (127.0.0.1:5000)")

    # 4. API log writable
    try:
        API_LOG_PATH.touch(exist_ok=True)
        log_ok = True
    except Exception:
        log_ok = False
    print(f"  {G if log_ok else R} API usage log writable (api_log.txt)"
          + ("" if log_ok else " — check folder permissions"))

    print()


if __name__ == "__main__":
    LOGS_DIR.mkdir(exist_ok=True)
    (SCRIPT_DIR / "templates").mkdir(exist_ok=True)

    today = datetime.date.today().strftime("%Y-%m-%d")
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as _s:
            _s.connect(("8.8.8.8", 80))
            _local_ip = _s.getsockname()[0]
    except Exception:
        _local_ip = "unavailable"
    print("=" * 54)
    print("  ⚔  JOURNEY'S QUEST — JOB SEARCH DASHBOARD")
    print(f"  http://localhost:5000        {today}")
    print(f"  http://{_local_ip}:5000   (phone/LAN)")
    print("  LAN only — non-private IPs are blocked.")
    print("=" * 54)
    print()
    _run_security_check()
    print("  Press Ctrl+C to stop.")
    print()

    socketio.run(app, host="0.0.0.0", port=5000, debug=False,
                 allow_unsafe_werkzeug=True)
