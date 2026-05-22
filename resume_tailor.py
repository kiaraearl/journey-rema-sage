#!/usr/bin/env python3
"""
============================================================
  resume_tailor.py  —  Resume tailoring tool
============================================================

HOW TO RUN:
    1. Save your resume as plain text in:   job-apps/master_resume.txt
    2. Paste the full job posting into:     job-apps/job_description.txt
    3. Open a terminal in the job-apps folder and run:
           python resume_tailor.py

AI MODE (strongly recommended — much better output):
    Set your Anthropic API key before running:

        Windows PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."
        Windows CMD:         set ANTHROPIC_API_KEY=sk-ant-...
        Mac / Linux:         export ANTHROPIC_API_KEY=sk-ant-...

    Get a free key at: https://console.anthropic.com

    Without a key: the script scores and reorders your bullets
    by keyword relevance and annotates low-scoring ones with
    suggested keywords to manually weave in.

OUTPUT:  output/tailored_resume_[Company]_[YYYY-MM-DD].txt

REQUIREMENTS:  Python 3.8+, no third-party packages needed.
============================================================
"""

import os
import sys
import re
import json
import datetime
import urllib.request
import urllib.error
import collections
from pathlib import Path

# ── File paths ────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent.resolve()
OUTPUT_DIR  = SCRIPT_DIR / "output"
RESUME_FILE = SCRIPT_DIR / "master_resume.txt"
JD_FILE     = SCRIPT_DIR / "job_description.txt"

# ── Words ignored during keyword extraction ───────────────────────────────────
STOPWORDS = {
    "a","an","the","and","or","but","in","on","at","to","for","of","with",
    "by","from","up","about","into","is","are","was","were","be","been",
    "being","have","has","had","do","does","did","will","would","could",
    "should","may","might","must","shall","can","we","you","they","he",
    "she","it","this","that","these","those","i","my","our","your","their",
    "its","who","which","what","when","where","why","how","all","both",
    "each","few","more","most","other","some","such","no","not","only",
    "same","so","than","too","very","just","now","as","if","while","also",
    "well","new","key","please","us","position","role","team","years","year",
    "experience","ability","skills","knowledge","work","working","opportunity",
    "company","looking","seeking","required","requirements","preferred",
    "qualifications","responsibilities","duties","include","including",
    "following","related","relevant","demonstrated","proven","able","plus",
    "etc","per","via","use","using","used","get","make","help","ensure",
    "provide","support","strong","excellent","good","great","high","person",
    "candidates","candidate","applicant","applicants","background","degree",
}

# ── Tech/tool names that receive a keyword-score boost ───────────────────────
TECH_TERMS = re.compile(
    r'\b(?:'
    r'Python|JavaScript|TypeScript|Java|Go|Rust|C\+\+|C#|Ruby|Swift|Kotlin|PHP|Scala'
    r'|React|Angular|Vue|Django|Flask|Spring|Rails|Laravel|Express|FastAPI|Next\.js|Nuxt'
    r'|AWS|GCP|Azure|Kubernetes|K8s|Docker|Terraform|Ansible|Jenkins|GitLab|GitHub'
    r'|PostgreSQL|MySQL|MongoDB|Redis|Elasticsearch|DynamoDB|Cassandra|BigQuery|Snowflake'
    r'|REST|GraphQL|gRPC|OAuth|JWT|API|SDK|CI/CD|DevOps|Agile|Scrum|Kanban|SAFe'
    r'|TDD|BDD|SDLC|OOP|SOLID|Node\.js|Linux|Unix|Git|Jira|Confluence|Notion'
    r'|machine learning|deep learning|NLP|LLM|data science|MLOps|TensorFlow|PyTorch'
    r'|Figma|Sketch|Tableau|Power BI|Excel|Salesforce|HubSpot|Zendesk|Datadog|Splunk'
    r'|HTML|CSS|SASS|LESS|Webpack|Vite|npm|yarn|pip|conda|Bash|PowerShell'
    r')\b',
    re.IGNORECASE,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_keywords(text: str, top_n: int = 40) -> list:
    """Score and rank the most important keywords in a job description."""
    tech_hits = {m.lower() for m in TECH_TERMS.findall(text)}

    words = re.findall(r"\b[a-zA-Z][a-zA-Z0-9+#]*\b", text)
    freq: dict = collections.defaultdict(float)
    for w in words:
        wl = w.lower()
        if wl not in STOPWORDS and len(wl) > 2:
            freq[wl] += 3.0 if wl in tech_hits else 1.0

    # Extra boost for words inside requirements / qualifications sections
    req_match = re.search(
        r'(?:required|qualifications?|must.have|requirements?)[\s\S]*?(?=\n\n|\Z)',
        text, re.IGNORECASE,
    )
    if req_match:
        for w in re.findall(r"\b[a-zA-Z][a-zA-Z0-9+#]*\b", req_match.group()):
            wl = w.lower()
            if wl not in STOPWORDS and len(wl) > 2:
                freq[wl] += 2.0

    # Include high-frequency two-word phrases
    clean = [w.lower() for w in words if w.lower() not in STOPWORDS and len(w) > 2]
    bigram_counts = collections.Counter(
        f"{clean[i]} {clean[i+1]}" for i in range(len(clean) - 1)
    )
    for bg, cnt in bigram_counts.items():
        if cnt >= 2:
            freq[bg] = freq.get(bg, 0) + cnt * 2.0

    return [kw for kw, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_n]]


def guess_company(jd_text: str) -> str:
    """Attempt to auto-detect the company name from the job description."""
    patterns = [
        r"(?:Company|Employer|Organization):\s*([^\n]{2,60})",
        r"^([A-Z][a-zA-Z0-9&\s]{2,40}?)\s+is\s+(?:a|an|the)\b",
        r"(?:at|join|about)\s+([A-Z][a-zA-Z0-9&\s]{1,30}?)(?:\s+is|\s+are|\.|,)",
    ]
    for pat in patterns:
        m = re.search(pat, jd_text, re.MULTILINE)
        if m:
            name = m.group(1).strip().rstrip(".,;")
            if 2 < len(name) < 60:
                return name
    return "Company"


def safe_slug(name: str) -> str:
    """Convert a name to a safe filename segment."""
    return re.sub(r"[^\w]", "_", name).strip("_")[:40]


def score_text(text: str, keywords: list) -> float:
    """Fraction of keywords present in text (0.0 – 1.0)."""
    if not keywords:
        return 0.0
    tl = text.lower()
    return sum(1 for kw in keywords if kw in tl) / len(keywords)


def is_bullet(line: str) -> bool:
    """Return True if the line looks like a resume bullet point."""
    s = line.strip()
    return bool(s) and s[0] in "••–—*-·▪◦"


# ── Fallback: rule-based tailoring (no API key required) ─────────────────────

def tailor_rule_based(resume_text: str, jd_text: str, company: str, keywords: list) -> str:
    """
    Sort bullets by keyword relevance (most relevant first within each role),
    then annotate low-relevance bullets with suggested keywords to weave in.
    No API key needed, but requires manual editing before submitting.
    """
    lines = resume_text.splitlines()
    out: list = []
    buf: list = []  # consecutive bullet lines waiting to be scored and flushed

    def flush_buffer():
        if not buf:
            return
        ranked = sorted(
            ((score_text(b, keywords), b) for b in buf),
            key=lambda x: -x[0],
        )
        for score, line in ranked:
            out.append(line)
            if score < 0.02:  # bullet has almost no keyword overlap
                missing = [kw for kw in keywords[:20] if kw not in line.lower()][:5]
                if missing:
                    out.append(f"  >>> WEAVE IN: {', '.join(missing)}")
        buf.clear()

    # Prepend a keyword analysis block
    out += [
        "=" * 65,
        f"  Tailored for: {company}   |   {datetime.date.today()}",
        "=" * 65,
        "",
        "[KEYWORD ANALYSIS — delete this block before submitting]",
        f"Top {len(keywords)} job keywords found:",
    ]
    chunks = [keywords[i : i + 8] for i in range(0, min(len(keywords), 32), 8)]
    for chunk in chunks:
        out.append("  " + ", ".join(chunk))
    out += [
        "",
        "Bullets are sorted most-to-least relevant within each role.",
        "Lines marked '>>> WEAVE IN' need manual keyword integration.",
        "[END ANALYSIS — resume begins below]",
        "",
    ]

    for line in lines:
        if is_bullet(line):
            buf.append(line)
        else:
            flush_buffer()
            out.append(line)
    flush_buffer()

    return "\n".join(out)


# ── AI-powered tailoring via Anthropic API ────────────────────────────────────

def call_api(prompt: str, api_key: str) -> str:
    """POST to the Anthropic Messages API using only urllib (no pip installs)."""
    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["content"][0]["text"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode('utf-8')[:400]}")


def tailor_with_api(resume_text: str, jd_text: str, company: str, api_key: str) -> str:
    """Ask Claude to rewrite resume bullets for the target role."""
    prompt = (
        "You are an expert resume coach. Tailor the candidate's resume for the job below.\n\n"
        "RULES:\n"
        "1. Never fabricate experience, skills, or accomplishments the candidate doesn't have.\n"
        "2. Rewrite bullet points with stronger action verbs and job-relevant keywords.\n"
        "3. Within each role, reorder bullets so the most relevant to THIS job come first.\n"
        "4. Update the Skills section to surface skills present in both the resume and JD.\n"
        "5. Keep the same sections and overall structure — do not add new jobs or degrees.\n"
        "6. Output ONLY the complete tailored resume — no commentary or markdown fencing.\n\n"
        f"COMPANY: {company}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"MASTER RESUME:\n{resume_text}\n\n"
        "Tailored resume:"
    )
    return call_api(prompt, api_key)


# ── Track 2 tailoring ────────────────────────────────────────────────────────

def tailor_with_api_t2(resume_text: str, jd_text: str, company: str, api_key: str) -> str:
    """
    Tailor resume for a Track 2 (Remote Income) role.
    Leads with customer service, communication, and organizational skills.
    Highlights Salesforce, ticketing, multi-tasking — de-emphasizes cyber certs
    unless the JD specifically calls for them. Target: 80%+ ATS match.
    """
    prompt = (
        "You are an expert resume coach tailoring a resume for a non-tech remote role.\n\n"
        "TRACK 2 RULES (follow these instead of tech-resume defaults):\n"
        "1. Lead with customer service, communication, and organizational strengths.\n"
        "2. Highlight: Salesforce, ticketing systems, multi-tasking, scheduling, "
        "documentation, billing, process improvement, Microsoft Office, Zoom/Teams.\n"
        "3. De-emphasize cybersecurity certifications (A+, Net+, Sec+) UNLESS the JD "
        "explicitly asks for IT or security skills.\n"
        "4. Rewrite bullets with strong action verbs relevant to admin/ops/finance roles.\n"
        "5. Surface insurance knowledge, high-volume workload management, and written "
        "communication as top-of-resume strengths.\n"
        "6. Keep the same sections and jobs — do not fabricate experience.\n"
        "7. Target 80%+ ATS keyword match against the job description.\n"
        "8. Output ONLY the complete tailored resume — no commentary or markdown fencing.\n\n"
        f"COMPANY: {company}\n\n"
        f"JOB DESCRIPTION:\n{jd_text}\n\n"
        f"MASTER RESUME:\n{resume_text}\n\n"
        "Tailored resume (Track 2 focus):"
    )
    return call_api(prompt, api_key)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print("\n=== Resume Tailor ===\n")

    # Validate input files
    for path, name in [(RESUME_FILE, "master_resume.txt"), (JD_FILE, "job_description.txt")]:
        if not path.exists():
            sys.exit(f"ERROR: {name} not found.\nExpected: {path}")
        if not path.read_text(encoding="utf-8").strip():
            sys.exit(f"ERROR: {name} is empty — add your content and try again.")

    resume_text = RESUME_FILE.read_text(encoding="utf-8").strip()
    jd_text     = JD_FILE.read_text(encoding="utf-8").strip()

    # Company name
    auto_company = guess_company(jd_text)
    entered      = input(f"Company name [{auto_company}]: ").strip()
    company      = entered or auto_company

    # Keyword analysis (used by both modes)
    print("\nAnalyzing job description...")
    keywords = extract_keywords(jd_text)
    preview  = ", ".join(keywords[:12]) + ("..." if len(keywords) > 12 else "")
    print(f"Extracted {len(keywords)} keywords: {preview}")

    # Choose tailoring mode
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        print("\nAPI key found — calling Claude for intelligent rewrite...")
        try:
            tailored = tailor_with_api(resume_text, jd_text, company, api_key)
            mode = "AI-tailored"
        except Exception as exc:
            print(f"Warning: API call failed ({exc})\nFalling back to keyword analysis...")
            tailored = tailor_rule_based(resume_text, jd_text, company, keywords)
            mode = "keyword-annotated (API failed)"
    else:
        print("\nNo ANTHROPIC_API_KEY found — using keyword analysis mode.")
        print("Tip: set the key for AI-powered rewriting (see script header).")
        tailored = tailor_rule_based(resume_text, jd_text, company, keywords)
        mode = "keyword-annotated"

    # Write output
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"tailored_resume_{safe_slug(company)}_{datetime.date.today()}.txt"
    out_path.write_text(tailored, encoding="utf-8")

    print(f"\n[{mode}] Saved: {out_path}")
    if "keyword-annotated" in mode:
        print("Review '>>> WEAVE IN' lines and integrate keywords naturally before submitting.")


if __name__ == "__main__":
    main()