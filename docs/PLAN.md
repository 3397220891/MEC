# OrbitalGuard AI — Technical Plan (24 h hackathon)

> Status: the end-to-end MVP described here is **already implemented and tested** in this repo
> (`api/` + `web/`). The plan below is written so each of the four developers can pick up their
> area and polish/extend it in parallel.

## 1. Feasibility assessment

| Area | Verdict | Why |
|---|---|---|
| SGP4 screening + TCA refinement | **Feasible, done** | `sgp4` vectorised propagation; 60 real objects × 6 h screened in < 1 s. |
| Maneuver simulation | **Feasible as a labelled simplification, done** | Impulsive burn + two-body/J2 *differential correction* on top of SGP4. Credible, not flight-grade. |
| Real tool-calling agent | **Feasible, done** | OpenAI-compatible function calling (DeepSeek → Ollama fallback). Rule-based planner if no LLM. |
| 3D globe | **Feasible, done** | three.js / react-three-fiber. **CesiumJS rejected**: heavy bundle, Ion token / imagery-hosting friction, Vite plugin setup; not worth the risk in 24 h. |
| Collision probability (Pc) | **Not feasible honestly → removed** | TLEs have no covariance. We show miss distance + an *explicitly rule-of-thumb* uncertainty and say "Pc not computed" everywhere. |
| LangGraph | **Not justified → removed** | One agent, one loop, 7 tools. A 100-line loop with budgets is easier to debug and demo. |
| Next.js + TypeScript | **Swapped for the existing Vite + React kit** | Already set up and Dockerised; no SSR needed. Migrating costs hours, buys nothing for the demo. |

Biggest real risks: LLM quality/latency on the day, venue Wi-Fi, and scope creep in the UI. All three
have fallbacks (section 14).

## 2. MVP features (all implemented)

1. Scenario loader: 3 deterministic simulated scenarios + live CelesTrak catalog (bundled snapshot fallback).
2. All-pairs SGP4 screening with conservative gating, Brent TCA refinement, RTN miss components, RED/AMBER/WATCH levels.
3. Auto-trigger: RED/AMBER event involving a maneuverable object starts an investigation.
4. Agent with 7 tools; every call logged as evidence `E#` with args, result, latency, status.
5. Maneuver simulation incl. **secondary-conjunction re-screen** against every object.
6. Deterministic comparison (acceptance criteria + ranking).
7. Incident report + structured recommendation with **numeric grounding guardrail**.
8. Human approve/reject (recorded as hypothetical; optional operator token).
9. Dashboard: 3D globe, object list/detail, time controls, timeline, alerts, mission log, agent trace, comparison chart.

## 3. Postpone / remove

- **Remove:** Pc, LangGraph, user accounts, microservices, Cesium, custom ML, real-time WebSockets (1 s polling is enough).
- **Postpone (stretch, in priority order):**
  1. Draw the *post-maneuver* trajectory on the globe (needs `GET /api/maneuvers/{id}/trajectory`).
  2. Earth texture (drop NASA Blue Marble into `web/public/` and use it on the sphere).
  3. Markdown/PDF export of the incident report.
  4. Encounter-plane (B-plane) plot of the miss vector.
  5. Server-Sent Events instead of polling.

## 4. System architecture

```
┌──────────────────────── web (Vite + React, :5173) ────────────────────────┐
│ Globe (r3f)   ObjectList/Detail   Alerts+Timeline   AgentPanel(trace,chart,approval) │
└───────────────▲─────────────────────────────── polling 1–2.5 s ──────────┘
                │ REST /api/*  (Vite proxy)
┌───────────────┴──────────────── api (FastAPI, :8000) ─────────────────────┐
│ routes.py ── World (active scenario, sim clock, events) ── SQLite (db.py) │
│     │                                                                      │
│     ├─ orbital/  catalog.py (TLE fetch/validate/fallback, SGP4)            │
│     │            screening.py (grid + gate + Brent TCA, RTN)               │
│     │            maneuver.py (impulsive burn, J2 diff. correction, compare)│
│     │            synth.py + scenarios.py (deterministic simulated objects) │
│     └─ agent/    runner.py (LLM loop, budgets, rule-based fallback)        │
│                  tools.py  (7 validated tools, timeouts, evidence, guardrail)│
│                  llm.py    (kit: provider chain, retries, disk cache, log) │
└────────────────────────────────────────────────────────────────────────────┘
          CelesTrak (optional)           DeepSeek / Ollama (optional)
```

Key design rules
- **The LLM never computes numbers.** It chooses tools and parameters; physics returns numbers; the
  guardrail rejects a recommendation containing numbers that appear in no tool output.
- **Determinism:** fixed scenario epoch, deterministic IDs (event/maneuver hashes), deterministic tool
  outputs → the LLM disk cache doubles as an offline **demo replay**.
- **Nothing executes without a human**; approval is recorded as hypothetical.

## 5. Libraries

Backend: `fastapi`, `uvicorn`, `pydantic` v2, `numpy`, `scipy` (solve_ivp, minimize_scalar, least_squares),
`sgp4`, `httpx`, `openai` (OpenAI-compatible client for DeepSeek/Ollama), `pytest`. SQLite via stdlib.
Frontend: `react` 19, `three`, `@react-three/fiber` 9, `@react-three/drei` 10, `recharts` 3.

## 6. Directory structure

```
api/
  app/
    main.py            FastAPI app, kit endpoints (/api/health, /api/chat), mounts routes
    routes.py          OrbitalGuard endpoints (contract: docs/API.md)
    world.py           active scenario state + screening orchestration
    db.py              SQLite: log, investigations, steps, maneuvers
    scenarios.py       scenario catalogue + builders
    llm.py             provider chain + chat_tools() (function calling, cached)
    orbital/
      timeutil.py      JD, GMST, TEME→ECEF→geodetic, RTN
      catalog.py       SpaceObject, TLE parse/validate, CelesTrak + snapshot fallback
      screening.py     screening, TCA refinement, closest_approach()
      maneuver.py      ManeuverPlan, simulate_maneuver(), compare_maneuvers()
      synth.py         simulated-object builders (crossing solver)
    agent/
      tools.py         tool schemas, arg models, execute(), grounding guardrail
      runner.py        investigation loop, budgets, rule-based fallback
  data/tle_snapshot.tle (+ .meta.json)   real CelesTrak snapshot for offline use
  tests/               test_orbital.py, test_agent.py, test_llm.py (kit)
web/src/
  App.jsx              layout, state, polling, alerts, timeline, log
  api.js               fetch wrapper + formatters
  components/Globe.jsx       3D scene (Earth rotates by GMST, TEME positions)
  components/AgentPanel.jsx  trace, recommendation, comparison chart, approval
```

## 7. API endpoints

See `docs/API.md`. Summary: `GET /scenarios`, `POST /scenarios/{id}/load`, `GET /status`, `POST /screen`,
`GET /objects`, `GET /objects/{id}`, `GET /objects/{id}/trajectory`, `GET /trajectories`, `GET /events`,
`GET /logs`, `POST /maneuvers/simulate`, `POST /investigations`, `GET /investigations[/{id}]`,
`POST /investigations/{id}/cancel`, `POST /investigations/{id}/decision`.

## 8. Agent architecture & tools

Loop (`agent/runner.py`): system prompt + briefing → model turn → execute every tool call (validated,
25 s timeout, logged as `E#`) → feed results back → repeat until `submit_recommendation` succeeds.
Budgets: 24 model turns, 240 s wall time, 2 nudges if the model stops early. LLM failure mid-run →
switch to the rule-based planner (labelled in UI and DB).

| Tool | Does | Guards |
|---|---|---|
| `get_satellite_info` | elements, state, source, element age/freshness | unknown id → error to model |
| `get_trajectory` | ≤24 SGP4 samples + stats | window ≤ 720 min |
| `analyze_close_approach` | TCA, miss, RTN, v_rel, ages, indicative uncertainty, miss/uncertainty ratio | explicitly "Pc not computed" |
| `simulate_maneuver` | new miss/TCA, Δv, propellant, Δa, **secondary re-screen** | maneuverable only, ‖Δv‖ ≤ 2 m/s, burn ≥ 10 min after now and ≥ 1 min before TCA |
| `compare_maneuvers` | criteria + ranking (miss ≥ threshold, no new/worsened RED/AMBER, min Δv, latest burn) | ids must belong to this investigation |
| `generate_incident_report` | deterministic evidence package (event, objects, freshness, steps, options, comparison, limitations) | — |
| `submit_recommendation` | structured decision for human review | report must exist; maneuver must be "acceptable"; evidence refs must exist; **numbers must be grounded** (2 rejections, then accepted with a visible warning) |

## 9. Orbital physics strategy

- **Propagation:** SGP4 (TEME frame) via `SatrecArray` on a 10 s grid.
- **Screening:** sampled local minima per pair, gated by `sqrt(thr² + (v_rel·Δt/2)²)` (straight-line
  bound, so the grid cannot hide an event), then bounded Brent search on exact SGP4 states (1 ms
  tolerance). Co-orbiting pairs (v_rel < 100 m/s: docked modules, proximity ops) are reported separately.
- **Maneuver:** impulsive Δv in RTN at burn time; both burned and unburned states integrated with
  two-body + J2 (DOP853, rtol 1e-11); the *difference* is added to the SGP4 nominal, so the "no maneuver"
  option reproduces the baseline exactly and common model errors cancel.
- **Demo objects:** a least-squares solver places a simulated object so its SGP4 trajectory passes a
  chosen miss distance from a target at a chosen time, crossing at perigee/apogee so the encounter does
  not repeat each orbit. Scenario 3 places a second object on the path a naive prograde burn would take.
- **Validated by tests:** dense brute-force agreement < 1 m, zero-Δv identity, prograde raises SMA,
  physical altitudes, scenario expectations.

## 10. Task allocation (from now on)

| Dev | Owns | Next tasks |
|---|---|---|
| 1 Frontend | `web/` | Earth texture; post-maneuver trajectory overlay; report view/export; polish for projector (font sizes); loading states |
| 2 Physics | `api/app/orbital/` | `GET /maneuvers/{id}/trajectory`; B-plane plot data; optional normal-direction burns in rule-based planner; more scenario variety |
| 3 Agent | `api/app/agent/`, `llm.py` | Tune prompt on DeepSeek with real runs; record a cache replay of each scenario; test Ollama fallback; evaluate grounding false positives |
| 4 Backend/QA | `routes.py`, `world.py`, `db.py`, Docker, tests | Docker compose run-through on a clean machine; SSE (optional); README/demo script rehearsal; failure drills (Wi-Fi off, no key) |

## 11. 24-hour schedule (remaining time, adapt to your start time)

| Hours | Goal |
|---|---|
| 0–1 | Everyone runs the stack locally (README), reads this plan, gets `pytest` green. Add `DEEPSEEK_API_KEY`. |
| 1–6 | First real LLM investigations (Dev 3); stretch #1 maneuver overlay (Dev 1+2); Docker check (Dev 4). |
| 6–10 | Fix agent behaviour seen in real runs; UI polish; second stretch item. **Feature freeze at hour 12.** |
| 10–12 | Record LLM cache replays for scenarios 2 and 3 (commit `api/data/cache` copy to a USB stick, not git). |
| 12–18 | Bug-fix only. Rehearse the demo 3×; time it. Failure drills. |
| 18–22 | Slides (architecture diagram, guardrail story, honesty about limits). Sleep in shifts. |
| 22–24 | Final rehearsal on the presentation laptop, offline. |

## 12. Demo script for judges (~5 min)

1. **Hook (20 s):** "A 162 m miss at 11 km/s — would you burn? And which burn?" Show scenario 1:
   10 objects, *screened in ~40 ms, nothing flagged*. Point out the "SIMULATED DATA" badge.
2. **Real data (30 s):** Live catalog — 60 real CelesTrak objects; docked ISS/CSS modules recognised as
   co-orbiting, not conjunctions. Shows the engine is not a toy.
3. **Detection (30 s):** Scenario 3. Alerts table: RED 162 m with FRAG-417, AMBER 1.48 km with CUBESAT-CHARLIE.
   Mission log: monitor auto-triggers the investigation.
4. **Agent at work (90 s):** Right panel streams reasoning + tool calls (E1…). Highlight: it checks element
   age and uncertainty; it tries prograde and retrograde; **a prograde burn fixes the debris but
   creates a RED 300 m approach with CHARLIE** — caught by the secondary re-screen; it scales up Δv when
   5 cm/s isn't enough.
5. **Decision support (60 s):** Comparison chart vs the 1 km threshold; recommendation with cited evidence
   and "numeric grounding passed". Open an E# step to show raw JSON. Open limitations: no Pc, simplified model.
6. **Human in the loop (20 s):** Approve → "recorded as hypothetical, no command capability". Jump to TCA on the globe.
7. **Resilience (30 s):** Toggle Wi-Fi off / remove key → scenario still runs (cache replay or rule-based mode, labelled).

## 13. Selling points

- Real agent: model-chosen tool sequence, parameter search, adaptation after failed options — visible as an evidence trail.
- **Numeric grounding guardrail**: recommendations cannot contain numbers the tools didn't produce.
- **Secondary-conjunction check**: the scenario judges remember — fixing one collision can cause another.
- Honest physics: correct SGP4 screening, provable no-miss gating, differential-correction maneuver model, explicit "no Pc".
- Works offline: snapshot data, LLM cache replay, rule-based fallback — each labelled.
- Safety by design: human approval, no command path, input validation, timeouts, budgets, optional operator token.

## 14. Risks & fallbacks

| Risk | Fallback |
|---|---|
| LLM slow/unavailable on stage | Cache replay (same scenario → same tool outputs → cached turns); else rule-based planner (auto). |
| LLM loops or stops early | Turn/time budgets, nudges, mid-run switch to rule-based. |
| LLM invents numbers | Grounding guardrail; warning shown if it persists. |
| CelesTrak down / no Wi-Fi | Bundled snapshot, replayed at its fetch time; badge shows SNAPSHOT. |
| WebGL weak on projector laptop | Lower `Stars` count; it still renders with software GL (tested headless). |
| Judges ask about accuracy | Point to limitations section + tests; we screen, we don't certify. |
| Merge conflicts | Ownership table in §10; API contract frozen in docs/API.md. |
