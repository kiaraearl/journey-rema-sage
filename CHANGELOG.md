# Job Apps Toolkit — Changelog

---

## 2026-05-14 — Journey Chat Interface

### Added

**`dashboard.py`**
- `JOURNEY_SYSTEM` constant — Journey's full character and candidate background system prompt
- `_load_chat_log()` / `_save_chat_log()` — persists chat history to `logs/journey_chat_log.json`
- `_tracker_context()` — builds a live CSV summary injected into every Journey API call
- `_read_file_safe()` — reads `bio.txt` safely, returns empty string if still placeholder
- `GET /api/chat-history` — serves persisted chat history on page load
- `@socketio.on("chat_message")` handler — routes user input to agent trigger or AI chat task
- `_chat_task()` background function — streams Journey's response token-by-token via `chat_chunk` SocketIO events
- Modified `_run_agent_task()` — when agent is triggered from chat, also streams every log line to the triggering client SID via `chat_chunk`; emits `chat_complete` when done
- `_AGENT_TRIGGERS` set — phrases that trigger the agent pipeline ("run the agent", "find jobs", "start the mission", etc.)
- `_chat_agent_sid` global — tracks which client SID triggered the current agent run

**`config.py`**
- `CHAT_ENABLED = True` — toggle to disable chat panel without touching other code
- `CHAT_MAX_TOKENS = 1500` — max tokens per Journey response
- `CHAT_HISTORY_LIMIT = 20` — messages (10 exchanges) sent to API to control token costs

**`templates/dashboard.html`**
- "TALK TO JOURNEY" panel — full-width, sits between Run Mission button and Targets Identified
- RPG dialogue box styling: dark background, hot pink border with glow
- Journey messages left — pink pixel font (`Press Start 2P`, 7px), pink border bubble
- User messages right — neon green sans-serif, green border bubble
- Avatar indicators: `JRN` (pink) left / `YOU` (green) right
- Thinking indicator: blinking cursor + "Journey is thinking..." text
- Agent-stream mode: when agent is triggered from chat, output renders in monospace (`Share Tech Mono`) instead of pixel font
- Input textarea with pink glow focus, auto-resize up to 130px
- SEND button styled as RPG action button
- Enter to send, Shift+Enter for newline
- `loadChatHistory()` — fetches persisted history on load; removes static greeting if real history exists
- `chat_agent_start` socket event — switches bubble to monospace mode, updates mission log UI state
- `chat_chunk` / `chat_complete` socket events — stream and finalize Journey's response

### Journey's Capabilities (via chat)
1. **Job search stats** — pulls live tracker data, responds in character
2. **Agent pipeline trigger** — "run the agent" / "find jobs" / "start the mission" → streams mission log live into chat
3. **Score a job on demand** — paste a JD → 1–10 score, disqualifiers, gaps, recommendation
4. **Cover letter on demand** — paste a JD → full cover letter using `bio.txt` + system context
5. **Interview prep** — "prep me for a help desk interview" → 10 role-specific questions with answer frameworks
6. **Follow-up emails** — "write a follow up for [company]" → pulls tracker row, drafts email
7. **General career Q&A** — any IT/cybersecurity career question answered in character

### Known Gaps / Next Steps
- `master_resume.txt` and `bio.txt` still contain placeholder text — must be filled before agent scoring and cover letters reflect real content
- LinkedIn source always returns 0 (no public API) — use LinkedIn email alerts as workaround
- Indeed is frequently rate-limited

---

## 2026-05-14 — Initial Build

- `job_feed.py` — fetches from Dice (RSS), USAJobs (REST), Adzuna (REST), Indeed, LinkedIn
- `job_agent.py` — AI scoring pipeline: fetch → score → generate docs → update tracker
- `resume_tailor.py` — tailors `master_resume.txt` for a single job via Anthropic API
- `cover_letter.py` — generates cover letter for a single job via Anthropic API
- `config.py` — single config file for all keys, thresholds, and search terms
- `dashboard.py` + `templates/dashboard.html` — Flask + SocketIO RPG dashboard
  - Character panel (Journey pixel avatar), mission log, stats bars, skills-to-unlock gaps tracker
  - Job cards (color-coded by recommendation, score as XP)
  - Sortable tracker table with live status dropdown
  - "Send Journey on Mission" button streams agent output in real time
