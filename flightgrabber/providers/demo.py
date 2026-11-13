"""Made-up but realistic flights, for trying the app without a live source.

Results are deterministic for a given query, so the same search shows the same
flights. Airline names are fictional on purpose so demo data is never mistaken
for real prices.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from datetime import datetime, timedelta

from .. import airports
from ..models import LegOption, Segment
from .base import Provider, ProviderQuery, QueryLeg

AIRLINES = [
    "Aurora Air",
    "Bluejet",
    "Cascade Airlines",
    "Meridian Airways",
    "Polar Wings",
    "Sunline",
    "Redtail Air",
    "Harbor Air",
]
BUDGET = {"Bluejet", "Sunline"}
HUBS = [
    "ATL", "ORD", "DFW", "DEN", "LAX", "JFK", "SFO", "SEA", "IAH", "CLT", "PHX",
    "MIA", "BOS", "MSP", "DTW", "LHR", "CDG", "FRA", "AMS", "IST", "DXB", "DOH",
    "SIN", "HKG", "NRT", "ICN", "MAD", "YYZ", "MEX",
]
CABIN_MULT = {"economy": 1.0, "premium-economy": 1.7, "business": 3.6, "first": 5.5}
AIRCRAFT = ["Airbus A320", "Boeing 737", "Airbus A321neo", "Boeing 787", "Airbus A350", "Embraer 175"]


def _rng(*parts) -> random.Random:
    seed = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(seed[:16], 16))


def _flight_minutes(km: float) -> int:
    return int(km / 780 * 60 + 35)


class DemoProvider(Provider):
    name = "demo"
    concurrency = 8

    async def search(self, q: ProviderQuery) -> list[LegOption]:
        await asyncio.sleep(random.uniform(0.05, 0.25))  # feel like a network call
        leg = q.legs[0]
        options = self._leg_options(leg, q)
        if q.trip == "round-trip":
            ret = q.legs[1]
            rng = _rng("rt", leg, ret)
            back = self._leg_options(ret, q)
            back_avg = sum(o.price or 0 for o in back) / max(1, len(back))
            for o in options:
                # Round-trip fares are sometimes cheaper than two one-ways,
                # sometimes not: exactly what the engine should discover.
                factor = rng.uniform(0.7, 1.15)
                o.price = round(((o.price or 0) + back_avg) * factor)
                o.price_is_round_trip = True
        return options

    def search_url(self, q: ProviderQuery) -> str:
        return ""

    def _leg_options(self, leg: QueryLeg, q: ProviderQuery) -> list[LegOption]:
        rng = _rng(leg.origin, leg.destination, leg.date, q.cabin)
        km = airports.distance_km(leg.origin, leg.destination) or 1500
        n = rng.randint(7, 16)
        pax = q.passengers.total - q.passengers.infants_on_lap
        days_out = (leg.date - datetime.now().date()).days
        urgency = 1.0 + max(0, 21 - days_out) * 0.025
        weekend = 1.12 if leg.date.weekday() in (4, 6) else 1.0
        base = (45 + km * 0.10) * CABIN_MULT[q.cabin] * urgency * weekend

        hubs = [
            h for h in HUBS
            if h not in (leg.origin, leg.destination)
            and (d1 := airports.distance_km(leg.origin, h))
            and (d2 := airports.distance_km(h, leg.destination))
            and d1 + d2 < km * 1.35
            and d1 > 150 and d2 > 150
        ]

        out: list[LegOption] = []
        for i in range(n):
            airline = rng.choice(AIRLINES)
            stops = 0 if (km < 6000 and rng.random() < (0.55 if km < 2500 else 0.3)) or not hubs else rng.choice([1, 1, 1, 2])
            if q.max_stops is not None:
                stops = min(stops, q.max_stops)
            if stops and len(hubs) < stops:
                stops = len(hubs)
            route = [leg.origin, *rng.sample(hubs, stops), leg.destination]

            dep = datetime.combine(leg.date, datetime.min.time()) + timedelta(
                hours=rng.randint(5, 22), minutes=rng.choice([0, 10, 15, 25, 30, 40, 45, 55])
            )
            segs: list[Segment] = []
            t = dep
            other = rng.choice(AIRLINES)
            for j, (a, b) in enumerate(zip(route, route[1:])):
                mins = _flight_minutes(airports.distance_km(a, b) or 800) + rng.randint(-10, 25)
                # Very rough timezone shift so arrival clocks look plausible.
                pa, pb = airports.get(a), airports.get(b)
                shift = round(((pb["lon"] if pb else 0) - (pa["lon"] if pa else 0)) / 15)
                arr = t + timedelta(minutes=mins + shift * 60)
                carrier = airline if j == 0 or rng.random() < 0.7 else other
                segs.append(
                    Segment(
                        from_code=a,
                        from_name=airports.name(a),
                        to_code=b,
                        to_name=airports.name(b),
                        depart=t,
                        arrive=arr,
                        duration_min=mins,
                        aircraft=rng.choice(AIRCRAFT),
                        airline=carrier,
                    )
                )
                layover = rng.choice([40, 55, 70, 90, 120, 150, 210, 320, 480])
                t = arr + timedelta(minutes=layover)

            price = base * rng.uniform(0.75, 1.45)
            if airline in BUDGET:
                price *= 0.72
                price += 35 * q.carry_on_bags + 40 * q.checked_bags
            else:
                price += 35 * q.checked_bags * (1 if q.cabin == "economy" else 0)
            price *= {0: 1.15, 1: 0.9, 2: 0.8}[stops]
            if dep.hour < 7 or dep.hour >= 21:
                price *= 0.88  # red-eyes and dawn flights are cheaper
            if q.exclude_basic_economy and airline in BUDGET:
                price *= 1.2
            airlines = list(dict.fromkeys(s.airline for s in segs))
            out.append(
                LegOption(
                    segments=segs,
                    airlines=airlines,
                    price=round(price * pax),
                    source="demo",
                )
            )
        return out
