# OrbitalGuard AI

An intelligent agent for satellite collision risk analysis and mission decision support. **Advisory only:** the system has no capability to command any spacecraft.

- **Physics (deterministic):** SGP4 propagation → all-pairs screening → TCA refinement → impulsive Δv maneuver model (SGP4 baseline + two-body/J2 differential correction) → re-screening for secondary conjunctions.
- **Agent:** an LLM (DeepSeek by default, or any OpenAI-compatible API such as a local Ollama model) runs a tool-use loop over 6 analysis tools. Every tool result becomes evidence `E#`. The final recommendation is structured JSON, and the server validates its citations and checks its numbers against the cited tool outputs.
- **Fallback:** without an API key, or if the LLM fails, a deterministic planner runs the same tools and produces the same report schema, clearly labeled.
- **Human in the loop:** approve/reject is recorded in SQLite as an audit record only.

See [docs/PLAN.md](docs/PLAN.md) for the full architecture, team split, schedule and demo script.

## Run it

```bash
# 1. Backend (Python 3.11+)
cd backend
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # optional: set DEEPSEEK_API_KEY to enable the LLM agent
uvicorn app.main:app --port 8000  # API docs: http://localhost:8000/docs

# 2. Frontend (Node 20+), in a second terminal
cd frontend
npm install
npm run dev                       # http://localhost:3000  (proxies /api → :8000)

# 3. Tests (guards the demo scenarios)
cd backend && pytest -q
```

## Demo scenarios
| Button | Data | What happens |
|---|---|---|
| 1 · Normal Operations | Simulated | 16 objects, 6 h screening, no events |
| 2 · Potential Collision Event | Simulated | AMBER, about 1.4 km miss at T+1h52 → agent investigates → typically *monitor & request tracking* |
| 3 · Maneuver Evaluation | Simulated | RED, about 0.12 km miss at T+3h05 → agent runs a maneuver trade study → *prepare maneuver for approval* |
| 4 · Real Catalog | CelesTrak live, else bundled snapshot | About 120 real objects screened from "now" |

## Configuration (`backend/.env`)
| Variable | Default | Meaning |
|---|---|---|
| `DEEPSEEK_API_KEY` (or `LLM_API_KEY`) | — | Enables the LLM agent; without it the fallback planner runs |
| `LLM_BASE_URL` | `https://api.deepseek.com` | Any OpenAI-compatible endpoint; `http://localhost:11434/v1` for Ollama |
| `ORBITALGUARD_MODEL` | `deepseek-flash` | Agent model (check the exact name in the provider console) |
| `ORBITALGUARD_AGENT_MODE` | `auto` | `offline` forces the fallback planner |
| `SCREEN_THRESHOLD_KM` / `RED_THRESHOLD_KM` / `AMBER_THRESHOLD_KM` | 5 / 1 / 2.5 | Screening and severity thresholds |

## Honest limitations
Screening uses SGP4 point estimates, so it is not a collision probability (Pc). TLEs carry no covariance. The maneuver model is impulsive and demonstration-grade. Simulated objects are fictional and labeled `SIMULATED` everywhere.
