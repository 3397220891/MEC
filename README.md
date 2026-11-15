# OrbitalGuard AI

An autonomous collision-investigation agent for satellite operations. Screening detects close
approaches with SGP4, an LLM agent investigates them by calling deterministic physics tools, compares
hypothetical avoidance maneuvers (including the conjunctions a burn could *create*), and hands an
evidence-cited recommendation to a human for approval.

**Decision support only.** No spacecraft command capability. No collision probability is claimed (TLEs carry
no covariance). Demo scenarios are simulated and labelled.

## Run locally

```bash
# backend (Python 3.12)
cd api
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000 --reload --reload-dir app

# frontend (Node 22)
cd web
npm install
npm run dev            # http://localhost:5173  (proxies /api to :8000)
```

Or `cp .env.example .env && docker compose up --build`.

LLM: set `DEEPSEEK_API_KEY` (and/or `LOCAL_BASE_URL` for Ollama) in the environment or `.env`. Without a
provider the agent runs its labelled rule-based planner; everything else works unchanged.

## Test

```bash
cd api && python -m pytest -q
```

## Docs

- `docs/PLAN.md`: architecture, feasibility, task split, schedule, demo script, risks
- `docs/API.md`: REST contract
