# OrbitalGuard AI

OrbitalGuard AI helps low-Earth-orbit satellite operators triage close approaches, compare avoidance maneuvers, and record a human decision. The console is advisory only and has no spacecraft command interface.

## Run locally

Requirements: Python 3.11+, Node.js 20+, and a browser with WebGL. Clone the repository and open two terminals at its root. The first dependency installation and frontend build need internet access. The default simulated mission runs without a model key after setup.

**Terminal 1 — backend (Windows PowerShell)**

~~~powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

On macOS/Linux, use `.venv/bin/python` for the last two commands.

**Terminal 2 — frontend**

~~~sh
cd frontend
npm ci
npm run dev
~~~

Open [Mission Control](http://localhost:3000) and enter **Ruipeng Lin** as the operator name. The four roster names select demo identities without passwords. Anyone who knows a roster name can use that identity, so run this demo locally with simulated data.

## Three-minute demo

1. Open **Alert queue**. The default scenario tracks 10 simulated objects and surfaces two conjunctions: **RED**, OGX-3 KESTREL × ORION-9, at about **0.123 km**, and **AMBER**, OGX-2 VEGA × DEB FRAG-5021, at about **1.173 km**.
2. Review the RED alert in **Collision Assistant**. Compare the baseline miss distance with the predicted miss distance, maneuver Δv, and secondary alerts. Rotate the globe and inspect the highlighted path. Expand **Investigation details** to inspect the evidence behind the recommendation.
3. In **Agent Group Chat**, ask the event's Sentinel “Which maneuver looks best?” Then select **Director** and ask “Which alert should I review first?” The Sentinel handles its event. The Director prioritizes open alerts across events.
4. Under **Review pending decision**, record an approval or rejection. The console displays the decision and SQLite records the operator and timestamp. The simulation issues no uplink. Collapse the Alert Queue, Agent Group Chat, or Mission Timeline to free space around the globe.

## Architecture and data

| Layer | Responsibility |
| --- | --- |
| Next.js and React Three Fiber | Interactive Earth, orbit tracks, alert queue, event review, and streaming chat |
| FastAPI and SQLite | Mission state, investigation evidence, operator sessions, and decision audit |
| SGP4 and NumPy/SciPy | Propagation, sampled all-pairs screening, closest-approach refinement, maneuver propagation, and secondary screening |
| DeepSeek or deterministic fallback | Calls analysis tools and produces a structured, evidence-linked recommendation. The backend checks citations and reported numbers against tool results. |

The shipped demo uses fictional targets and a fixed simulation epoch. To try real catalog data, sign in at `localhost:3000`, then open the [local API docs](http://localhost:8000/docs), execute `POST /api/scenarios/live/load`, and refresh the mission console. This mode uses [CelesTrak general perturbations data](https://celestrak.org/NORAD/elements/) and caps the visual catalog at 120 objects. When fetching fails, it uses the bundled snapshot and identifies that source in the console. These are point-estimate conjunctions. The app does not calculate collision probability, since TLEs do not supply covariance. The impulsive maneuver model is intended for simulation.

The browser interpolates positions between trajectory samples. It requests a 30-second grid for the small demo and a 60-second grid for catalogs over 40 objects. Invalid propagation samples are transmitted as missing positions.

## Optional model setup

Copy `backend/.env.example` to `backend/.env`, configure `DEEPSEEK_API_KEY` and `ORBITALGUARD_MODEL`, then restart the backend. Set `LLM_BASE_URL` and `LLM_API_KEY` for another OpenAI-compatible endpoint. With no key or when the provider fails, the console can use the labeled deterministic fallback. Set `ORBITALGUARD_AGENT_MODE=offline` to force that path.

## Verification

- In `backend`, run `.\.venv\Scripts\python.exe -m pytest -q` (macOS/Linux: `.venv/bin/python -m pytest -q`).
- In `frontend`, run `npm run build` for the production compile and type check.
- Visit [backend health](http://127.0.0.1:8000/api/health) if the frontend cannot connect. It reports the loaded scenario, data source, and whether an LLM is configured.

## Sources and repository history

- Orbit propagation uses the [Python SGP4 package](https://pypi.org/project/sgp4/).
- The sign-in background is [NASA image ART002E008487](https://images.nasa.gov/details/art002e008487).
- The repository contains a pre-existing starter kit in commits `69a2151` and `0711b17`. The OrbitalGuard implementation begins at `dd79560`. The starter kit was removed from the current tree in `97f28b7`.
- [Architecture and development notes](docs/PLAN.md).
