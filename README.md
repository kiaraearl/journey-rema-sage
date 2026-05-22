# Journey, Rema & Sage 🤖

> An automated job search pipeline built with Python, Claude AI, and multi-source job feeds.
> Built by Kiara Earl — IT & Cybersecurity job seeker, automation enthusiast.

---

## What Is This?

**Journey, Rema & Sage** is a fully automated job search system that ingests job listings
from multiple sources, scores them against a custom rubric, and automatically generates
tailored resumes and cover letters for high-scoring roles — all without manual effort.

The name represents three agents working together:
- **Journey** — the orchestrator; runs the pipeline end to end
- **Rema** — the researcher; finds and scores job listings
- **Sage** — the writer; generates resumes and cover letters via Claude AI

---

## Features

- 🔍 **Multi-source job ingestion** — Apify, Adzuna, JSearch (RapidAPI), USAJobs
- 🧠 **Dual-track scoring system**
  - Track 1 (IT/Cybersecurity): AI-scored via Claude API — roles scoring ≥7 auto-proceed
  - Track 2 (Remote Income): 100-point rule-based rubric (remote, salary, no-phone, skills)
- 📄 **Automated resume tailoring** — pulls from `master_resume.json`, tailors per role
- ✉️ **Cover letter generation** — Claude-powered, role-specific
- 🚫 **Legitimacy scoring** — filters scam and low-quality listings before processing
- 📊 **Flask dashboard** — live UI to view, filter, and manage tracked applications
- 📁 **CSV tracking** — persistent job tracker (`job_tracker.csv`) with status, scores, and metadata

---

## Project Structure

```
Job-apps/
├── job_agent.py          # Orchestrator — runs the full pipeline
├── job_feed.py           # Job ingestion from multiple sources
├── apify_feed.py         # Apify-specific feed handler
├── resume_tailor.py      # Tailors master resume to each role
├── resume_builder.py     # Builds final resume output
├── cover_letter.py       # Generates cover letters via Claude API
├── legitimacy_scorer.py  # Filters low-quality/scam listings
├── dashboard.py          # Flask UI for application tracking
├── config.py             # Track settings, salary thresholds, scoring rules
├── master_resume.json    # Source of truth for resume data
└── job_tracker.csv       # Persistent application tracker
```

---

## Scoring Logic

### Track 1 — IT / Cybersecurity
Roles: Help desk, SOC analyst, tech support, cybersecurity

- Listings are sent to Claude (`claude-sonnet-4-6`) for relevance and fit scoring
- Score ≥ 7 → auto-generate tailored resume + cover letter
- Filters: Remote salary min $50K | Hybrid min $65K

### Track 2 — Remote Income
Roles: Data entry, virtual assistant, billing, data analyst

100-point rule-based rubric:

| Criteria | Points |
|---|---|
| Fully remote | 30 |
| Meets salary threshold ($45K / $22/hr) | 30 |
| No phone/call requirements | 25 |
| Relevant skills match | 15 |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3 |
| AI / LLM | Anthropic Claude API (`claude-sonnet-4-6`) |
| Job Sources | Apify, Adzuna, JSearch (RapidAPI), USAJobs |
| Web UI | Flask |
| Data | JSON, CSV |
| Resume Output | DOCX (python-docx) |

---

## Stats (May 2026)

- 📋 **300+** roles tracked
- ✅ **~15** applications submitted
- 🏢 Roles applied to include: Optum, Paylocity, Spectrum Science, Cambium Networks, Vultr, One Inc, and more

---

## Why I Built This

I'm actively transitioning into IT and cybersecurity while working full-time and completing
my B.S. at WGU. Job searching manually at scale isn't sustainable — so I automated it.

This project demonstrates:
- Real-world Python application development
- API integration (Claude, Apify, RapidAPI)
- Scoring system design and logic
- Data pipeline architecture
- Practical AI-assisted automation

---

## Author

**Kiara Earl**
CompTIA A+ Certified | WGU B.S. Cybersecurity & Information Assurance (Expected 2027)
📧 kimearls24@outlook.com
📍 Houston, TX
🔗 [Portfolio](https://your-portfolio-url-here)

---

> *"I don't just look for jobs. I build systems that do it for me."*
