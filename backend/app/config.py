from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


MODEL = os.getenv("ORBITALGUARD_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("ORBITALGUARD_EFFORT", "medium")
AGENT_MODE = os.getenv("ORBITALGUARD_AGENT_MODE", "auto")  # auto | llm | offline
DB_PATH = Path(os.getenv("ORBITALGUARD_DB", Path(__file__).resolve().parent.parent / "orbitalguard.db"))

SCREEN_THRESHOLD_KM = _f("SCREEN_THRESHOLD_KM", 5.0)  # report/flag events inside this volume
RED_KM = _f("RED_THRESHOLD_KM", 1.0)
AMBER_KM = _f("AMBER_THRESHOLD_KM", 2.5)
DEFAULT_TARGET_MISS_KM = _f("TARGET_MISS_KM", 2.0)

AGENT_MAX_TOOL_CALLS = int(_f("AGENT_MAX_TOOL_CALLS", 16))
AGENT_WALL_CLOCK_S = _f("AGENT_WALL_CLOCK_S", 180)
TOOL_TIMEOUT_S = _f("TOOL_TIMEOUT_S", 30)

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")


def llm_configured() -> bool:
    if AGENT_MODE == "offline":
        return False
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


def severity(miss_km: float) -> str:
    if miss_km < RED_KM:
        return "RED"
    if miss_km < AMBER_KM:
        return "AMBER"
    return "WATCH"
