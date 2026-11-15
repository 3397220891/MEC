"""Real orbital data: CelesTrak GP (TLE) with bundled snapshot fallback."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sgp4.api import Satrec

from ..physics.propagation import Epoch, TrackedObject

log = logging.getLogger(__name__)

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle"
SNAPSHOT = Path(__file__).parent / "data" / "snapshot_tles.txt"
MAX_OBJECTS = 120


def _valid_tle_line(line: str, n: str) -> bool:
    if len(line) < 69 or not line.startswith(n + " "):
        return False
    digits = [int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line[:68]]
    return sum(digits) % 10 == int(line[68])


def parse_tle_text(text: str, source: str) -> tuple[list[TrackedObject], list[str]]:
    """Parse 3-line TLE text, validating checksums and SGP4 initialisation."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    objs, rejected = [], []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        i += 3
        if not (_valid_tle_line(l1, "1") and _valid_tle_line(l2, "2")):
            rejected.append(f"{name}: checksum/format")
            continue
        sat = Satrec.twoline2rv(l1, l2)
        if sat.error != 0:
            rejected.append(f"{name}: sgp4 init error {sat.error}")
            continue
        kind = "ROCKET_BODY" if "R/B" in name else ("DEBRIS" if "DEB" in name else "PAYLOAD")
        objs.append(
            TrackedObject(
                id=f"NORAD-{sat.satnum}",
                name=name,
                kind=kind,
                source=source,
                satrec=sat,
                maneuverable=False,  # unknown for public objects: never assume
                operator="Unknown (public catalog)",
                norad_id=int(sat.satnum),
                extra={"tle_line1": l1, "tle_line2": l2},
            )
        )
        if len(objs) >= MAX_OBJECTS:
            break
    return objs, rejected


def load_real_catalog(timeout_s: float = 6.0) -> tuple[Epoch, list[TrackedObject], dict]:
    """Try CelesTrak live; fall back to the bundled snapshot. Epoch = now (UTC, minute)."""
    info: dict = {"url": CELESTRAK_URL}
    text, source = None, "CELESTRAK_SNAPSHOT"
    try:
        r = httpx.get(CELESTRAK_URL, timeout=timeout_s, headers={"User-Agent": "OrbitalGuard-hackathon"})
        r.raise_for_status()
        if "1 " in r.text and len(r.text) > 500:
            text, source = r.text, "CELESTRAK_LIVE"
    except Exception as exc:  # network down, rate limit, etc.
        info["fetch_error"] = f"{type(exc).__name__}: {exc}"
        log.warning("CelesTrak unavailable, using snapshot: %s", exc)
    if text is None:
        text = SNAPSHOT.read_text()
    objs, rejected = parse_tle_text(text, source)
    info.update({"source": source, "count": len(objs), "rejected": rejected})
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return Epoch(now), objs, info
