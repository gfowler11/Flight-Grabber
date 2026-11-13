"""Airport lookup: autocomplete, nearby airports and great-circle distance."""

from __future__ import annotations

import csv
import math
from functools import lru_cache
from pathlib import Path

# Airports with scheduled passenger service, from OurAirports (public domain).
# Regenerate with tools/build_airports.py.
DATA = Path(__file__).parent / "data" / "airports.csv"
_SIZE_RANK = {"L": 0, "M": 1, "S": 2}


@lru_cache(maxsize=1)
def _db() -> dict[str, dict]:
    out: dict[str, dict] = {}
    with DATA.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            r["lat"], r["lon"] = float(r["lat"]), float(r["lon"])
            out[r["code"]] = r
    return out


def get(code: str) -> dict | None:
    return _db().get(code.upper())


def name(code: str) -> str:
    a = get(code)
    return a["name"] if a else code


def city(code: str) -> str:
    a = get(code)
    return (a["city"] or a["name"]) if a else code


def is_major(a: dict) -> bool:
    return a["size"] == "L"


def distance_km(a: str, b: str) -> float | None:
    pa, pb = get(a), get(b)
    if not pa or not pb:
        return None
    return haversine_km(pa["lat"], pa["lon"], pb["lat"], pb["lon"])


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _view(a: dict, distance: float | None = None) -> dict:
    out = {
        "code": a["code"],
        "name": a["name"],
        "city": a["city"] or a["name"],
        "country": a["country"],
        "major": is_major(a),
    }
    if distance is not None:
        out["distance_km"] = round(distance)
    return out


def search(query: str, limit: int = 10) -> list[dict]:
    """Autocomplete by code, city or airport name."""
    q = query.strip().lower()
    if not q:
        return []
    scored: list[tuple[int, str, dict]] = []
    for code, a in _db().items():
        n, c = a["name"].lower(), a["city"].lower()
        if code.lower() == q:
            s = 0
        elif c == q:
            s = 1
        elif c.startswith(q):
            s = 2
        elif n.startswith(q) or code.lower().startswith(q):
            s = 3
        elif q in n or q in c:
            s = 4
        else:
            continue
        scored.append((s * 3 + _SIZE_RANK[a["size"]], a["name"], a))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [_view(a) for _, _, a in scored[:limit]]


def nearby(code: str, radius_km: float = 150, limit: int = 8) -> list[dict]:
    """Airports within ``radius_km`` of ``code`` (excluding itself), largest then nearest first."""
    base = get(code)
    if not base:
        return []
    found: list[tuple[float, dict]] = []
    for other_code, a in _db().items():
        if other_code == base["code"]:
            continue
        d = haversine_km(base["lat"], base["lon"], a["lat"], a["lon"])
        if d <= radius_km:
            found.append((d, a))
    # Bigger airports first, then by distance.
    found.sort(key=lambda t: (_SIZE_RANK[t[1]["size"]], t[0]))
    return [_view(a, d) for d, a in found[:limit]]
