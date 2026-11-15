# OrbitalGuard AI — Technical Plan

> An intelligent agent for satellite collision risk analysis and mission decision support.
> Hackathon build: 4 developers, 24 hours. Advisory system only. It never commands real spacecraft.

---

## 1. Feasibility assessment

| Area | Verdict | Notes |
|---|---|---|
| SGP4 propagation + close-approach screening | **Feasible (4–6 h)** | The `sgp4` library does the heavy lifting. Use a coarse grid plus bounded scalar refinement. |
| Engineered demo conjunctions | **Feasible, needs care** | Build synthetic objects with `Satrec.sgp4init`, then solve for the debris elements so a miss of about 150 m happens at a chosen time. Deterministic. |
| Maneuver simulation | **Feasible if simplified** | Impulsive Δv + two-body/J2 numerical integration, applied as a *differential correction* on top of the SGP4 baseline. Defensible and honest. Not flight-grade. |
| Real tool-calling agent | **Feasible (LLM tool use via OpenAI-compatible API, DeepSeek by default)** | Use a manual loop so we control logging, step limits, timeouts and the approval gate. |
| Collision **probability** (Pc) | **Cut** | Needs covariance data that public TLEs do not have. We report *miss distance* and say clearly that this is screening, not Pc. |
| CesiumJS | **Swap for Three.js** | Cesium + Next.js asset wiring and Ion tokens add 2–4 h of risk. React Three Fiber renders a GMST-rotated Earth with orbits in an inertial frame. It looks great and works offline. |
| LangGraph | **Cut** | A 150-line explicit loop is easier to debug and to explain to judges. |
| Live CelesTrak | **Optional, with fallback** | Fetch with a timeout. On failure, use the bundled snapshot. The demo never depends on it. |

**Overall:** achievable if the team freezes the API contract by hour 2 and protects the demo path (scenario → detection → agent → approval) above everything else.

## 2. MVP features (must ship)
1. Scenario loader (nominal / conjunction / maneuver-trade / live-or-snapshot) with clear **SIMULATED / REAL** labels.
2. SGP4 propagation, all-pairs screening, TCA refinement and miss distance.
3. 3D globe with orbits, animated satellites, selection, highlighted conjunction pair and a time slider.
4. Event detection that **auto-triggers** the investigation agent.
5. An agent with 6 tools, a visible tool-call timeline and a structured final recommendation.
6. Maneuver comparison table plus a separation-vs-time chart.
7. Human approve/reject checkpoint, persisted in SQLite.
8. Deterministic fallback agent when the LLM is unavailable (clearly labeled).

## 3. Postpone or remove
- Collision probability, covariance and Monte Carlo → *future work slide*.
- Finite-burn modeling, attitude, propulsion constraints.
- User accounts and authentication. The operator name is free text, and the approval is an audit record.
- WebSockets → use 1 s polling (simpler, robust on venue Wi-Fi).
- Chatbot UI → at most a small "ask the agent a follow-up" box, only if time remains.
- Full-catalog screening (30k objects) → screen ≤ 200 objects. Mention k-d tree / sieve filters as scale-up work.

## 4. System architecture

```
┌──────────────────────── Next.js (frontend) ─────────────────────────┐
│ Left: catalog/search │ Center: R3F globe + orbits │ Right: agent    │
│ Bottom: timeline, alerts, event log          (polls REST every 1s)  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ JSON / REST
┌───────────────────────────────▼─────────────────────────────────────┐
│ FastAPI                                                             │
│  routers: scenarios · objects · events · investigations · maneuvers │
│  ┌──────────────┐   ┌──────────────────┐   ┌─────────────────────┐  │
│  │ Physics core │◄──│ Agent tool layer │◄──│ Agent runner         │  │
│  │ sgp4, screen,│   │ (validated JSON, │   │ LLM loop     ─or─    │  │
│  │ maneuver     │   │  evidence IDs)   │   │ deterministic plan   │  │
│  └──────┬───────┘   └────────┬─────────┘   └──────────┬──────────┘  │
│         └────────── SQLite (objects, events, runs, steps, reports) ─┘
└─────────────────────────────────────────────────────────────────────┘
```

Key principle: **the LLM decides *what to investigate*. Only deterministic code produces numbers.** Every tool result gets an evidence ID (`E1`, `E2`, …). The final report must cite these IDs, and the server rejects citations that do not exist.

## 5. Libraries
- **Backend:** fastapi, uvicorn, numpy, scipy (`minimize_scalar`, `solve_ivp`, `least_squares`), sgp4, openai (OpenAI-compatible client for DeepSeek / Ollama), httpx, pytest. Persistence uses the stdlib `sqlite3`, so there is no ORM to fight with.
- **Frontend:** next 15, react 19, typescript, tailwind, three, @react-three/fiber, @react-three/drei, recharts.

## 6. Directory structure
```
backend/
  app/
    main.py              FastAPI app + routers
    config.py            settings (env)
    db.py                sqlite schema + helpers
    models.py            pydantic API models
    physics/
      constants.py
      propagation.py     SGP4 wrapper, TEME states, GMST
      screening.py       coarse grid + refine (TCA, miss distance)
      maneuver.py        impulsive Δv, J2 integration, differential correction
    scenarios/
      builder.py         synthetic constellation + engineered conjunction
      catalog.py         CelesTrak fetch + bundled snapshot fallback
      data/snapshot_tles.txt
    agent/
      tools.py           6 tool schemas + executors
      runner.py          LLM manual tool loop, limits, logging
      offline.py         deterministic fallback agent
      report.py          evidence-checked report assembly
    services/state.py    active scenario, monitor, investigation registry
  tests/
frontend/src/
  app/page.tsx           mission control layout
  components/            Globe, ObjectList, AgentPanel, ManeuverTable, Timeline, ...
  lib/api.ts, lib/types.ts, lib/orbit.ts
```

## 7. API specification (frozen contract)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{status, llm_available, data_mode}` |
| GET | `/api/scenarios` | List of scenarios |
| POST | `/api/scenarios/{id}/load` | Load a scenario, run screening, auto-trigger the agent on new events |
| GET | `/api/state` | Active scenario, epoch, horizon, objects, events, investigations |
| GET | `/api/objects/{id}` | Metadata + orbital elements |
| GET | `/api/trajectories?step_s=30` | Positions of all objects over the horizon (TEME km) |
| GET | `/api/events` | Close-approach events |
| GET | `/api/events/{id}/separation` | Separation-vs-time series near TCA |
| POST | `/api/investigations` `{event_id}` | Start the agent manually |
| GET | `/api/investigations/{id}` | Status, steps (tool calls), evidence, report |
| POST | `/api/investigations/{id}/decision` `{decision, operator, note}` | Human approval checkpoint |
| POST | `/api/maneuvers/simulate` | Manual what-if from the UI (same engine as the agent) |

## 8. Agent architecture
- **Loop:** `chat.completions.create` (OpenAI-compatible; DeepSeek by default) with 6 function tools. Each tool call is executed by deterministic Python, stored as a step with timing and an evidence ID, then returned as a `tool` message. Failures return an `error` payload so the agent can recover.
- **Guards:** ≤ 16 tool calls, 180 s wall clock, per-call input validation, a refusal and error fallback to the deterministic planner. Tools are read-only or simulation-only. There is **no actuation tool by design**.
- **Final output:** `generate_incident_report` deterministically compiles the facts. The agent then answers with a JSON recommendation (schema given in the system prompt, validated server-side) that cites evidence IDs. The server validates the citations and sets status to `awaiting_approval`.
- **Tools:** `get_satellite_info`, `get_trajectory`, `analyze_close_approach`, `simulate_maneuver`, `compare_maneuvers`, `generate_incident_report`.

## 9. Orbital physics strategy
1. **Propagation:** SGP4 (TEME frame) at 30 s on the horizon (default 6 h).
2. **Screening:** apogee/perigee filter on pairs → coarse distance grid → local minima below 25 km → `minimize_scalar` (bounded) for the precise TCA → flag if miss < 5 km (configurable). The UI also reports relative speed and radial/along/cross-track (RIC) miss components.
3. **Maneuver:** take the SGP4 state at burn time t_b and apply an impulsive Δv in RIC. Propagate with **two-body + J2** both with and without the Δv. Add that *difference* to the SGP4 baseline (differential correction keeps the model consistent with the screening). Re-refine the TCA against the secondary object. Re-screen the maneuvered orbit against the whole catalog to catch new secondary conjunctions.
4. **Honesty labels:** "screening-grade", "TLE accuracy ~1 km", "no covariance → no Pc".

## 10. Team allocation
| Dev | Owns | First deliverable (by H4) |
|---|---|---|
| 1 Frontend | layout, globe, panels, polish | Globe rendering mocked `/api/trajectories` |
| 2 Physics | propagation, screening, maneuver, scenario builder | `pytest` showing the engineered 150 m conjunction |
| 3 Agent | tools, runner, prompt, fallback agent, report validation | CLI run of the agent on a scenario event |
| 4 Backend/Integration | FastAPI, SQLite, state, scenarios endpoint, tests, demo script, deck | All endpoints returning contract-shaped stubs |

## 11. 24-hour schedule
| Hours | Milestone |
|---|---|
| 0–2 | Kickoff, freeze this API contract, repo + stubs, everyone runs the app |
| 2–8 | Parallel build. **H8 checkpoint:** real screening behind the endpoints, globe shows real orbits |
| 8–14 | Agent loop live on the conjunction scenario. Maneuver engine. Approval flow. **H14: end-to-end demo works** |
| 14–16 | Sleep shifts (2 people at a time) |
| 16–20 | Polish: RIC chart, alert animations, fallback agent, error states, tests |
| 20–22 | **Feature freeze.** Rehearse the demo 3×, record a backup video |
| 22–24 | Slides, buffer, judge Q&A prep |

## 12. Judge demo script (≈4 min)
1. **(20 s) Hook:** "There are 30,000+ tracked objects in orbit. Operators get conjunction warnings and have hours to decide."
2. **(40 s) Scenario 1, Nominal:** spin the globe, select a satellite, show altitude, velocity and elements. "All screened, no events." Point out the SIMULATED label.
3. **(60 s) Scenario 2, Conjunction:** load it → alert fires → the agent starts on its own. Narrate the live tool calls: info → trajectory → close-approach analysis (miss ≈ 150 m, 14 km/s crossing).
4. **(60 s) Scenario 3, Maneuver trade:** the agent simulates prograde, retrograde and early-burn options, catches a secondary conjunction and compares Δv against separation. Show the chart.
5. **(30 s) Explainability:** open the report. Every number cites evidence `E#`. Show the limitations section.
6. **(20 s) Human in the loop:** the operator approves → the audit record is logged. "The AI never commands anything."
7. **(10 s) Resilience:** kill the API key → the deterministic fallback agent still produces a report.

## 13. Selling points
- An agent that **investigates**: it chooses tools, reacts to results and checks for secondary conjunctions, instead of just summarizing.
- **Zero hallucinated numbers:** all physics is deterministic, and the server validates evidence citations.
- Physics that holds up to questions: SGP4 + TCA refinement + J2 differential maneuver model + RIC geometry.
- Safety by design: no actuation tool, a mandatory human approval, honest uncertainty labels.
- Works offline: fallback data and a fallback agent.

## 14. Risks & fallbacks
| Risk | Fallback |
|---|---|
| LLM API down or rate-limited | Deterministic planner runs the same tools and produces the same report schema, labeled as fallback |
| CelesTrak unreachable | Bundled TLE snapshot; synthetic scenarios need no network |
| Engineered conjunction drifts after code changes | `pytest` asserts miss < 500 m and TCA in window. Run before every commit |
| 3D performance on judge laptop | ≤ 40 objects, 30 s sampling, instanced spheres, no textures required |
| Agent loops or overruns | Step cap + wall-clock cap, then forced report generation |
| Venue Wi-Fi | Everything runs on localhost. Pre-record a backup video |

---

## Implementation roadmap (start coding now)
1. `backend`: `python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
2. `uvicorn app.main:app --reload --port 8000` → open `http://localhost:8000/docs`
3. `frontend`: `npm install && npm run dev` → open `http://localhost:3000`
4. Optional: put `DEEPSEEK_API_KEY=...` in `backend/.env` (or point `LLM_BASE_URL` at a local Ollama) to enable the LLM agent. Without it, the fallback agent runs.
5. `pytest` in `backend/` before every push.
