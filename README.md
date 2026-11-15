# OrbitalGuard AI

An AI agent for satellite collision-risk analysis and maneuver decision support.
It screens a catalog for close approaches, investigates each alert with deterministic physics
tools, recommends an avoidance maneuver for a human operator, draws the plan on a 3D globe, and
answers "what-if" questions by computing new options live.

> **Advisory only.** OrbitalGuard has no uplink and cannot command any spacecraft. Approving a
> recommendation only writes an audit record.

---

## What it does

```
Scenario / TLE catalog
   │  SGP4 propagation · all-pairs screening · TCA refinement
   ▼
Close-approach alert (RED < 1 km · AMBER < 2.5 km · WATCH < 5 km)
   │  auto-triggers an investigation
   ▼
AI agent (DeepSeek or any OpenAI-compatible LLM) ──calls──► 6 deterministic analysis tools
   │                                                          (every result = evidence E#)
   ▼
Recommendation (cites E#) ── server checks citations and numbers ──► operator approves / rejects (audit only)
   │
   ├─► 3D globe: burn points at T−N, maneuvered path, new closest approach, ghost satellite
   └─► Agent chat: ask why / what-if; the agent calls the same tools live, streamed step by step
```

**Design rule: the LLM decides *what to compute and how to conclude*; only project code produces
numbers.** Every distance, time and Δv comes from a tool result, is labelled with an evidence ID, and
is checked by the server before the operator sees it.

## Features

- **Physics engine** – SGP4 propagation (TEME), coarse-grid + bounded refinement screening, RIC miss
  components, TLE uncertainty estimate, impulsive maneuver model (SGP4 baseline + two-body/J2
  differential correction) with re-screening of the whole catalog for secondary conjunctions.
- **Investigation agent** – tool-use loop over `get_satellite_info`, `get_trajectory`,
  `analyze_close_approach`, `simulate_maneuver`, `compare_maneuvers`, `generate_incident_report`.
  The recommendation is validated (unknown evidence IDs or unmatched numbers are flagged) and
  repaired once if needed.
- **Live "what-if" chat** – per-alert *Sentinel* agents and a *Mission Director* (prioritises alerts).
  The chat agent can call the analysis tools itself; its reasoning, each tool call with arguments,
  each computed result (with evidence ID) and the answer are **streamed** as they happen. New
  maneuvers continue the investigation's numbering (M10, E16, …) and can be drawn on the globe.
- **Maneuver plan on the globe** – every simulated option as a burn marker on the current orbit
  ("T−170 min · 0.25 m/s retrograde"), the recommended maneuvered path, the new closest-approach
  point, and a ghost satellite flying the new path. *Replay from burn* jumps the mission clock there.
- **Human in the loop** – alert queue with *Review*, approval panel, decisions and chat questions in
  an audit log.
- **Graceful degradation** – no key, blocked network or LLM error → a deterministic rule-based planner
  runs the same tools and produces the same report schema, clearly labelled. CelesTrak unreachable →
  bundled TLE snapshot.
- **Visible error handling** – live backend/AI status pills, outage banner with retry, toasts for
  failed actions, investigation problems surfaced at the top of the assistant, chat timeouts with
  retry, and a crash boundary around the 3D view.

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
# 1. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate                  # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                  # macOS/Linux: cp; then add your key (optional)
uvicorn app.main:app --port 8000 --reload
#   API docs: http://localhost:8000/docs

# 2. Frontend (second terminal)
cd frontend
npm install
npm run dev                             # http://localhost:3000 (demo login) · dashboard: /mission
```

The frontend proxies `/api/*` to `http://127.0.0.1:8000`. If the backend runs on another port, start
the frontend with `BACKEND_URL`, e.g. `BACKEND_URL=http://127.0.0.1:8001 npm run dev`
(PowerShell: `$env:BACKEND_URL="http://127.0.0.1:8001"; npm run dev`).

On startup the backend loads scenario 3 and auto-investigates its RED/AMBER alerts (one LLM run per
alert unless an investigation is already awaiting approval).

## Configure the AI

Edit `backend/.env` (never commit it; it is git-ignored) and **restart the backend** – `.env` is read
only at startup.

| Option | Settings |
|---|---|
| DeepSeek (default) | `DEEPSEEK_API_KEY=sk-…` · `ORBITALGUARD_MODEL=deepseek-flash` |
| Local model (offline) | `LLM_BASE_URL=http://localhost:11434/v1` · `LLM_API_KEY=ollama` · `ORBITALGUARD_MODEL=<ollama model>` |
| No AI | leave the key empty, or `ORBITALGUARD_AGENT_MODE=offline` |

- Model names change. List the ones your key can use: `GET https://api.deepseek.com/models` with
  `Authorization: Bearer <key>`. `deepseek-flash` is fast and cheap (good for demos);
  `deepseek-v4-pro` is stronger but slower.
- Any OpenAI-compatible endpoint works through `LLM_BASE_URL`.
- Check the live status in the header pill or `GET /api/health?probe=true` (`llm.state`:
  `online`, `unreachable`, `auth_error`, `error`, `not_configured`).
- Streaming costs the same tokens as a normal call. A what-if question usually triggers several tool
  rounds, so it costs more than a plain question (still cents with `deepseek-flash`).

**Team setup:** share `.env.example`, not `.env`. Each teammate copies it and adds their own key (or a
key shared privately). Without a key everything still runs on the rule-based planner.

## Using the dashboard

1. **Alert queue** (top) – alerts sorted by severity and TCA. *Review* opens that alert's approval panel.
2. **Globe** – orbits and objects animate on the mission clock; the selected alert's pair is
   highlighted. Green dashed line = recommended maneuvered path; markers = burn points of simulated
   options; ghost satellite = the spacecraft after the burn.
3. **Collision Assistant** – status, recommendation (click to expand), predicted miss / Δv / new
   alerts, time to TCA and to the proposed burn, *replay from burn*, investigation details (tool
   timeline, maneuver comparison, separation chart, report) and *Review pending decision*.
4. **Agent Group Chat** – choose *Sentinel* (this alert) or *Director* (all alerts). Try:
   - "Why this risk level?"
   - "What if we burn 90 min before TCA instead?"
   - "如果只用0.2 m/s，最早什么时候点火能让距离超过4 km？" (answers in the question's language)

   Expand 💭 to read the model's reasoning; each 🔧 card is a tool call computed by OrbitalGuard
   physics; *show on globe* draws a newly simulated maneuver.
5. **Mission timeline** (bottom) – play/pause, speed, scrub, jump to events.

## Demo scenarios

| Scenario | Data | What happens |
|---|---|---|
| 1 · Normal Operations | Simulated | 16 objects, 6 h screening, no alerts |
| 2 · Potential Collision Event | Simulated | AMBER, ~1.4 km miss: monitor vs. contingency maneuver |
| 3 · Maneuver Evaluation | Simulated | Two alerts: **RED** KESTREL × ORION-9 (~0.12 km, TCA T+185 min) and **AMBER** VEGA × DEB FRAG-5021 (~1.17 km, TCA T+270 min) → maneuver trade studies |
| 4 · Real Catalog | CelesTrak, else bundled snapshot | Up to 120 real objects (incl. ISS and CSS/Tianhe) screened from "now"; real objects are never assumed maneuverable |

Load one with `POST /api/scenarios/{nominal|conjunction|maneuver|live}/load`.

## Architecture

```
frontend/ (Next.js 15, React 19, React Three Fiber, Tailwind)
  /mission ── polls /api/state, /api/investigations/{id}; streams /api/chat/stream (SSE)
     │  Next rewrite /api/* → FastAPI (compression off so the stream is not buffered)
backend/ (FastAPI, SQLite)
  physics/    propagation · screening · maneuver            (deterministic)
  agent/      tools (6) · runner (LLM loop) · offline (rule-based planner)
              report (validation) · chat (Q&A) · chat_stream (live tool use, SSE)
  services/   state (scenario, events) · plan (globe geometry) · health (LLM probe)
  scenarios/  engineered demo encounters · CelesTrak / snapshot catalog
```

**Investigation loop** (`agent/runner.py`): OpenAI-compatible `chat.completions` with function tools,
≤ 16 tool calls, 180 s wall clock, per-tool timeout. Any LLM failure (auth, network, refusal,
invalid JSON twice, budget) hands over to the rule-based planner; the investigation records
`mode = llm | llm+fallback | fallback`.

**Chat stream events** (`POST /api/chat/stream`, Server-Sent Events):
`start` · `thought` (reasoning delta) · `text` (narration/answer delta) · `tool_call` · `tool_result`
(evidence ID, summary, maneuver ID) · `notice` · `final` (answer, cited evidence, grounding check,
tool-call count).

**Context:** the LLM API is stateless, so the server rebuilds the context for every question (alert,
thresholds, latest finished investigation, its evidence and maneuver comparison, or an all-alert
summary for the Director) and sends it with the recent chat history. The frontend sends only the
question and history.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Backend + LLM status (`?probe=true` forces an LLM check) |
| GET | `/api/scenarios` · POST `/api/scenarios/{id}/load` | List / load a scenario (auto-investigates RED/AMBER) |
| GET | `/api/state` | Active scenario, objects, events, investigations, thresholds |
| GET | `/api/objects/{id}` · `/api/trajectories` | Object summary · positions for the globe |
| GET | `/api/events` · `/api/events/{id}/separation` | Alerts · separation vs time (optionally with a maneuver) |
| POST | `/api/investigations` | Start an investigation for an event |
| GET | `/api/investigations/{id}` | Status, steps, evidence, recommendation, validation, report |
| GET | `/api/investigations/{id}/plan` | Simulated options (T−N burns) + maneuvered path (`?maneuver_id=`) |
| POST | `/api/investigations/{id}/decision` | Record approve / reject (audit only) |
| POST | `/api/chat` | One grounded answer (no tools) |
| POST | `/api/chat/stream` | Streaming answer with live tool use |
| GET | `/api/audit` | Audit log |

Interactive docs: `http://localhost:8000/docs`. Unexpected errors return JSON with a `reference` that
also appears in the server log.

## Configuration (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` / `LLM_API_KEY` | – | Enables the LLM agent |
| `LLM_BASE_URL` | `https://api.deepseek.com` | Any OpenAI-compatible endpoint |
| `ORBITALGUARD_MODEL` | `deepseek-flash` | Model name |
| `ORBITALGUARD_AGENT_MODE` | `auto` | `offline` forces the rule-based planner |
| `SCREEN_THRESHOLD_KM` / `RED_THRESHOLD_KM` / `AMBER_THRESHOLD_KM` | 5 / 1 / 2.5 | Screening and severity thresholds |
| `TARGET_MISS_KM` | 2 | Minimum avoidance target (the agent uses max(this, 2× combined σ)) |
| `AGENT_MAX_TOOL_CALLS` / `AGENT_WALL_CLOCK_S` / `TOOL_TIMEOUT_S` | 16 / 180 / 30 | Investigation limits |
| `CHAT_TIMEOUT_S` / `CHAT_LLM_BACKOFF_S` | 20 / 120 | Chat LLM timeout; after a connection failure, answer rule-based for this long |
| `ORBITALGUARD_DB` | `backend/orbitalguard.db` | SQLite file (delete to reset; recreated on start) |

## Project structure

```
backend/
  app/main.py                 FastAPI app, endpoints, error handler
  app/physics/                propagation.py · screening.py · maneuver.py · constants.py
  app/agent/                  tools.py · runner.py · offline.py · report.py · chat.py · chat_stream.py
  app/services/               state.py · plan.py · health.py
  app/scenarios/              builder.py (engineered encounters) · catalog.py · data/snapshot_tles.txt
  tests/                      physics, agent loop, chat, streaming, plan, error handling
frontend/
  src/app/mission/page.tsx    mission control page
  src/components/             Globe · AgentPanel · EventQuestions (chat) · MissionReadings · TimelineBar
                              SystemStatus · Toasts · ErrorBoundary · ObjectPanel · ReportModal · SeparationChart
  src/lib/                    api.ts (REST + SSE client) · clock.ts · types.ts · toast.ts
docs/PLAN.md                  original architecture, team plan and demo script
```

## Tests

```bash
cd backend && .venv\Scripts\python -m pytest -q        # 25 tests, no network or key needed
cd frontend && npx tsc --noEmit && npx eslint src && npm run build
```

The backend tests use fake LLM clients and cover: engineered encounters stay where the demo expects
them; the agent loop and its fallback; chat context, grounding checks and fallback; the streaming
tool loop (tool arguments arriving in pieces, evidence/maneuver numbering, streamed text = final
answer); the plan path reproducing the reported miss distance (±10 m); LLM status and readable 500s.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| AI pill says *unreachable* on a campus network | Some networks block DeepSeek at DNS level (e.g. `api.deepseek.com` → a block page). Use another network, a local Ollama model, or `ORBITALGUARD_AGENT_MODE=offline`. Do not try to bypass the institution's block. |
| Key set but AI still off | `.env` is only read at startup: restart uvicorn. Check `GET /api/health?probe=true`. |
| Model not found | Use a name returned by `GET {LLM_BASE_URL}/models`. |
| Backend code changes not picked up | `--reload` occasionally hangs on Windows; stop and restart uvicorn. |
| Chat appears all at once instead of streaming | Restart `npm run dev` after pulling (`next.config.ts` disables compression; config changes need a restart). |
| "Proposed burn time passed" | The page opens 10 min before TCA; recommended burns are earlier. Use *replay from burn*. |
| Red banner "Backend connection lost" | The API is not running or `BACKEND_URL` points to the wrong port. |
| Want a clean slate | Stop the backend and delete `backend/orbitalguard.db`. |

## Limitations

- Screening uses SGP4 point estimates; TLEs carry no covariance, so **no collision probability (Pc)**
  is computed. The 1σ figure is an order-of-magnitude TLE-age model.
- Maneuvers are impulsive and demonstration-grade: no finite burns, propellant, attitude constraints,
  execution errors or drag differences. Not suitable for flight operations.
- Simulated objects are fictional and labelled `SIMULATED` everywhere; real catalog objects are never
  assumed maneuverable.
- Approval is an audit record only; there is no command path to any spacecraft.
- The login page stores a demo call sign in the browser; there is no authentication.
- `docs/PLAN.md` is the original plan; parts of its demo script predate the current scenarios.
