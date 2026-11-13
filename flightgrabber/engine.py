"""The deep-search engine.

One trip typed by the user becomes many provider queries:

* every combination of the chosen origin and destination airports,
* every date within the flexible window,
* for round trips, both a round-trip fare search *and* separate one-way
  searches for each direction (two one-ways are often cheaper).

Queries are prioritised (exact search first, then the variations most likely
to save money) and capped by the chosen depth so the provider doesn't block
us. Answers are combined into complete itineraries, scored and annotated.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from . import links
from .models import (
    DateCell,
    Insight,
    Itinerary,
    LegOption,
    LegView,
    RouteInfo,
    SearchProgress,
    SearchRequest,
    SearchResult,
)
from .providers import Provider, ProviderError, ProviderQuery, QueryLeg, get_provider

BUDGET = {"quick": 12, "deep": 40, "max": 100}
CACHE_TTL_S = 20 * 60
MIN_GAP_BETWEEN_LEGS_MIN = 120  # time between landing and the next leg's departure
TOP_PER_LEG = 10  # options per leg kept (per criterion) when building combinations
MAX_PARTIALS = 400  # beam width when combining multi-leg separate tickets
MAX_RESULTS = 200


# ---------------------------------------------------------------------------
# Query cache (shared across searches; refining a search doesn't refetch)
# ---------------------------------------------------------------------------

_cache: dict[tuple, tuple[float, list[LegOption]]] = {}


def _cache_get(q: ProviderQuery, provider: str) -> list[LegOption] | None:
    hit = _cache.get((provider, q.cache_key()))
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return [o.model_copy(deep=True) for o in hit[1]]
    return None


def _cache_put(q: ProviderQuery, provider: str, options: list[LegOption]) -> None:
    _cache[(provider, q.cache_key())] = (time.time(), options)
    if len(_cache) > 2000:
        cutoff = time.time() - CACHE_TTL_S
        for k in [k for k, (t, _) in _cache.items() if t < cutoff]:
            del _cache[k]


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Planned:
    query: ProviderQuery
    leg_index: int  # which trip leg a one-way query answers; -1 for round-trip fares
    priority: float


def _date_offsets(flex: int) -> list[int]:
    """0, -1, +1, -2, +2, ... closest dates first."""
    out = [0]
    for k in range(1, flex + 1):
        out += [-k, k]
    return out


def plan_queries(req: SearchRequest, today: date | None = None) -> tuple[list[Planned], int]:
    """Return (queries to run, how many were possible before the depth cap)."""
    today = today or date.today()
    base = dict(
        passengers=req.passengers,
        cabin=req.cabin,
        carry_on_bags=req.carry_on_bags,
        checked_bags=req.checked_bags,
        max_stops=req.max_stops,
        exclude_basic_economy=req.exclude_basic_economy,
        currency=req.currency,
    )
    offsets = _date_offsets(req.flex_days)
    planned: list[Planned] = []

    def one_way(i: int, o: str, d: str, day: date, prio: float) -> None:
        if day < today or o == d:
            return
        planned.append(
            Planned(ProviderQuery(legs=(QueryLeg(o, d, day),), trip="one-way", **base), i, prio)
        )

    def round_trip(o: str, d: str, d1: date, d2: date, prio: float) -> None:
        if d1 < today or d2 < d1 or o == d:
            return
        planned.append(
            Planned(
                ProviderQuery(legs=(QueryLeg(o, d, d1), QueryLeg(d, o, d2)), trip="round-trip", **base),
                -1,
                prio,
            )
        )

    legs = req.legs
    is_rt = req.trip_type == "round-trip"

    # Each candidate query gets a cost: 0 for exactly what was asked, plus 1
    # per alternative airport (ranked as the user listed them) and 1.2 per day
    # of date shift. Sorting by cost interleaves nearby airports and flexible
    # dates, so a limited budget still covers both.
    for i, leg in enumerate(legs):
        for a, o in enumerate(leg.origins):
            for b, d in enumerate(leg.destinations):
                for off in offsets:
                    one_way(i, o, d, leg.date + timedelta(days=off), a + b + 1.2 * abs(off))

    if is_rt:
        out, back = legs
        for a, o in enumerate(out.origins):
            for b, d in enumerate(out.destinations):
                for off1 in offsets:
                    for off2 in offsets:
                        if off1 and off2:
                            continue  # shift one end at a time; one-ways cover the rest
                        cost = a + b + 1.2 * (abs(off1) + abs(off2)) + 0.5
                        round_trip(
                            o, d, out.date + timedelta(days=off1), back.date + timedelta(days=off2),
                            0 if cost == 0.5 else cost,
                        )

    # De-duplicate (keep the first/highest-priority occurrence), then cap.
    seen: set[tuple] = set()
    unique: list[Planned] = []
    for p in sorted(planned, key=lambda p: (p.priority, p.leg_index < 0)):
        k = (p.leg_index, p.query.cache_key())
        if k in seen:
            continue
        seen.add(k)
        unique.append(p)
    return unique[: BUDGET[req.depth]], len(unique)


# ---------------------------------------------------------------------------
# Scoring and warnings
# ---------------------------------------------------------------------------


def leg_warnings(leg: LegOption) -> list[str]:
    w: list[str] = []
    for a, b, lay in zip(leg.segments, leg.segments[1:], leg.layovers_min):
        where = a.to_code
        if a.to_code != b.from_code:
            w.append(f"Change airports during layover ({a.to_code} → {b.from_code})")
        if lay < 50:
            w.append(f"Tight {lay}m connection in {where}")
        elif lay >= 6 * 60:
            w.append(f"Long {lay // 60}h{lay % 60:02d}m layover in {where}")
        if a.arrive.date() != b.depart.date() and lay >= 4 * 60:
            w.append(f"Overnight layover in {where}")
    return w


AIRPORT_SWITCH_COST = 40  # rough ground transport between airports


def score(
    price: float, minutes: float, stops: int, warnings: int, hour_value: float, airport_switches: int = 0
) -> float:
    """Lower is better: price plus the cost of your time and hassle."""
    return (
        price
        + hour_value * minutes / 60
        + stops * hour_value * 0.75
        + warnings * hour_value
        + airport_switches * (AIRPORT_SWITCH_COST + hour_value * 1.5)
    )


def _id(*parts) -> str:
    return hashlib.sha1(repr(parts).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Combining answers into itineraries
# ---------------------------------------------------------------------------


def _dedupe(options: Iterable[LegOption]) -> list[LegOption]:
    best: dict[tuple, LegOption] = {}
    for o in options:
        if o.price is None:
            continue
        k = o.key()
        if k not in best or (o.price < (best[k].price or 1e18)):
            best[k] = o
    return list(best.values())


def _shortlist(options: list[LegOption], hour_value: float, k: int = TOP_PER_LEG) -> list[LegOption]:
    """Cheapest, fastest and best-scored options for one leg, plus the cheapest
    per departure date and airport pair so flexible searches stay represented."""
    if len(options) <= k * 3:
        return options
    keep: dict[tuple, LegOption] = {}
    for key in (
        lambda o: o.price,
        lambda o: o.duration_min,
        lambda o: score(o.price, o.duration_min, o.stops, len(leg_warnings(o)), hour_value),
    ):
        for o in sorted(options, key=key)[:k]:
            keep[o.key()] = o
    groups: dict[tuple, LegOption] = {}
    for o in options:
        g = (o.depart.date(), o.origin, o.destination)
        if g not in groups or o.price < groups[g].price:
            groups[g] = o
    for o in groups.values():
        keep[o.key()] = o
    return list(keep.values())


def _route_label(leg: LegOption) -> str:
    return f"{leg.origin} → {leg.destination}, {leg.depart.strftime('%b %-d')}"


class Builder:
    def __init__(self, req: SearchRequest, provider: Provider):
        self.req = req
        self.provider = provider
        self.one_way: dict[int, list[LegOption]] = defaultdict(list)
        self.rt_fares: list[tuple[ProviderQuery, list[LegOption]]] = []

    def add(self, p: Planned, options: list[LegOption]) -> None:
        if p.leg_index >= 0:
            self.one_way[p.leg_index].extend(options)
        else:
            self.rt_fares.append((p.query, options))

    # -- itineraries -------------------------------------------------------

    def _make(self, strategy, routes, legs, price, booking_links, extra_warnings=(), switches=0) -> Itinerary:
        req = self.req
        known = [l for l in legs if l is not None]
        minutes = sum(l.duration_min for l in known)
        stops = sum(l.stops for l in known)
        warnings: list[str] = []
        for l in known:
            warnings += leg_warnings(l)
        warnings += list(extra_warnings)
        # Unknown legs (picked on the booking site): assume they look like the known ones.
        est_minutes = minutes * len(legs) / max(1, len(known))
        est_stops = stops * len(legs) / max(1, len(known))
        return Itinerary(
            id=_id(strategy, [l.key() if l else None for l in legs], [r.date for r in routes]),
            strategy=strategy,
            routes=routes,
            legs=[LegView.of(l) if l else None for l in legs],
            price=price,
            currency=req.currency,
            total_duration_min=minutes,
            total_stops=stops,
            best_score=round(
                score(price, est_minutes, round(est_stops), len(warnings) - switches, req.hour_value, switches), 2
            ),
            warnings=warnings,
            booking_links=booking_links,
            source=known[0].source,
        )

    def _links_for_legs(self, legs: list[LegOption], whole_trip: bool, google_url: str = ""):
        route = [(l.origin, l.destination, l.depart.date()) for l in legs]
        if whole_trip:
            return links.for_route(route, self.req.passengers, self.req.cabin, google_url)
        out = []
        for l, r in zip(legs, route):
            out += links.for_route([r], self.req.passengers, self.req.cabin, l.google_url, _route_label(l))
        return out

    def single_tickets(self) -> list[Itinerary]:
        """One-way trips: every option for the single leg."""
        out = []
        for leg in _dedupe(self.one_way.get(0, [])):
            route = [RouteInfo(origin=leg.origin, destination=leg.destination, date=leg.depart.date())]
            out.append(
                self._make("single", route, [leg], leg.price, self._links_for_legs([leg], True, leg.google_url))
            )
        return out

    def round_trip_fares(self) -> list[Itinerary]:
        out = []
        for q, options in self.rt_fares:
            back = q.legs[1]
            for leg in _dedupe(options):
                routes = [
                    RouteInfo(origin=leg.origin, destination=leg.destination, date=leg.depart.date()),
                    RouteInfo(origin=back.origin, destination=back.destination, date=back.date),
                ]
                route = [(leg.origin, leg.destination, leg.depart.date()), (back.origin, back.destination, back.date)]
                bl = links.for_route(route, self.req.passengers, self.req.cabin, leg.google_url)
                out.append(self._make("round-trip-fare", routes, [leg, None], leg.price, bl))
        return out

    def separate_tickets(self) -> list[Itinerary]:
        """Combine one-way options for every leg into complete trips."""
        n = len(self.req.legs)
        per_leg = [_shortlist(_dedupe(self.one_way.get(i, [])), self.req.hour_value) for i in range(n)]
        if any(not opts for opts in per_leg):
            return []
        hv = self.req.hour_value
        partials: list[list[LegOption]] = [[o] for o in per_leg[0]]
        for i in range(1, n):
            nxt: list[list[LegOption]] = []
            for p in partials:
                last = p[-1]
                for o in per_leg[i]:
                    if (o.depart - last.arrive).total_seconds() / 60 < MIN_GAP_BETWEEN_LEGS_MIN:
                        continue
                    nxt.append(p + [o])
            nxt.sort(key=lambda c: score(sum(l.price for l in c), sum(l.duration_min for l in c), 0, 0, hv))
            # Keep the beam diverse: best by score plus cheapest overall.
            by_price = sorted(nxt, key=lambda c: sum(l.price for l in c))
            merged: dict[tuple, list[LegOption]] = {}
            for c in nxt[:MAX_PARTIALS] + by_price[: MAX_PARTIALS // 4]:
                merged[tuple(l.key() for l in c)] = c
            partials = list(merged.values())

        if n == 2:
            # Cheapest combination for every pair of dates, so each cell of the
            # date grid has a matching result.
            def cheapest_by_date(opts):
                m: dict[date, LegOption] = {}
                for o in opts:
                    d = o.depart.date()
                    if d not in m or o.price < m[d].price:
                        m[d] = o
                return m

            a_by, b_by = cheapest_by_date(per_leg[0]), cheapest_by_date(per_leg[1])
            have = {tuple(l.key() for l in c) for c in partials}
            for a in a_by.values():
                for b in b_by.values():
                    if (b.depart - a.arrive).total_seconds() / 60 >= MIN_GAP_BETWEEN_LEGS_MIN:
                        if (a.key(), b.key()) not in have:
                            partials.append([a, b])

        out = []
        is_rt = self.req.trip_type == "round-trip"
        for combo in partials:
            extra: list[str] = []
            for a, b in zip(combo, combo[1:]):
                if b.origin != a.destination:
                    extra.append(f"Next leg leaves from {b.origin}, but you land at {a.destination}")
            if is_rt and combo[1].destination != combo[0].origin:
                extra.append(f"Returns to {combo[1].destination}, not {combo[0].origin}")
            routes = [RouteInfo(origin=l.origin, destination=l.destination, date=l.depart.date()) for l in combo]
            out.append(
                self._make(
                    "separate-tickets",
                    routes,
                    combo,
                    sum(l.price for l in combo),
                    self._links_for_legs(combo, False),
                    extra,
                    switches=len(extra),
                )
            )
        return out

    def build(self) -> tuple[list[Itinerary], list[DateCell], list[Insight]]:
        req = self.req
        if req.trip_type == "one-way":
            its = self.single_tickets()
        elif req.trip_type == "round-trip":
            its = self.round_trip_fares() + self.separate_tickets()
        else:
            its = self.separate_tickets()

        uniq: dict[str, Itinerary] = {}
        for it in its:
            if it.id not in uniq or it.price < uniq[it.id].price:
                uniq[it.id] = it
        its = sorted(uniq.values(), key=lambda i: i.best_score)

        if its:
            cheapest = min(its, key=lambda i: i.price)
            complete = [i for i in its if None not in i.legs] or its
            fastest = min(complete, key=lambda i: i.total_duration_min)
            its[0].tags.append("Best")
            cheapest.tags.append("Cheapest")
            fastest.tags.append("Fastest")
            keep = {i.id for i in its[:MAX_RESULTS]} | {cheapest.id, fastest.id}
            for group in (self._dates_of, self._airports_of):
                best: dict[tuple, Itinerary] = {}
                for i in its:
                    g = group(i)
                    if g not in best or i.price < best[g].price:
                        best[g] = i
                keep |= {i.id for i in best.values()}
            its = [i for i in its if i.id in keep]

        return its, self.date_grid(its), self.insights(its)

    @staticmethod
    def _dates_of(i: Itinerary) -> tuple:
        return tuple(r.date for r in i.routes)

    @staticmethod
    def _airports_of(i: Itinerary) -> tuple:
        return tuple((r.origin, r.destination) for r in i.routes)

    # -- extras ------------------------------------------------------------

    def date_grid(self, its: list[Itinerary]) -> list[DateCell]:
        """Cheapest itinerary per departure date (and return date)."""
        if self.req.trip_type == "multi-city":
            return []
        cells: dict[tuple, float] = {}
        for i in its:
            k = self._dates_of(i)
            cells[k] = min(cells.get(k, 1e18), i.price)
        return [
            DateCell(depart=k[0], ret=k[1] if len(k) > 1 else None, price=p) for k, p in sorted(cells.items())
        ]

    def insights(self, its: list[Itinerary]) -> list[Insight]:
        req = self.req
        if not its:
            return []
        out: list[Insight] = []
        cur = req.currency

        def money(x: float) -> str:
            return f"{cur} {x:,.0f}" if cur != "USD" else f"${x:,.0f}"

        main_o, main_d = req.legs[0].origins[0], req.legs[0].destinations[0]
        dates = [l.date for l in req.legs]

        def exact(i: Itinerary) -> bool:
            return (
                [r.date for r in i.routes] == dates
                and i.routes[0].origin == main_o
                and i.routes[0].destination == main_d
            )

        cheapest = min(its, key=lambda i: i.price)
        exact_its = [i for i in its if exact(i)]
        if exact_its:
            base = min(exact_its, key=lambda i: i.price)
            saving = base.price - cheapest.price
            if saving >= 5 and cheapest.id != base.id:
                trip = " and ".join(
                    f"{r.origin} → {r.destination} on {r.date.strftime('%a %b %-d')}" for r in cheapest.routes
                )
                out.append(
                    Insight(
                        kind="flex",
                        text=f"Flying {trip} saves {money(saving)} compared with your exact search ({money(base.price)}).",
                        saving=saving,
                    )
                )

        if req.trip_type == "round-trip":
            rt = [i for i in its if i.strategy == "round-trip-fare"]
            sep = [i for i in its if i.strategy == "separate-tickets"]
            if rt and sep:
                a, b = min(rt, key=lambda i: i.price), min(sep, key=lambda i: i.price)
                if b.price < a.price - 5:
                    out.append(
                        Insight(
                            kind="split",
                            text=f"Two one-way tickets ({money(b.price)}) beat the cheapest round-trip fare ({money(a.price)}) by {money(a.price - b.price)}.",
                            saving=a.price - b.price,
                        )
                    )
                elif a.price < b.price - 5:
                    out.append(
                        Insight(
                            kind="split",
                            text=f"A round-trip fare ({money(a.price)}) is {money(b.price - a.price)} cheaper than two one-ways ({money(b.price)}).",
                            saving=b.price - a.price,
                        )
                    )

        # Cheapest by departure airport pair.
        pairs: dict[tuple[str, str], float] = {}
        for i in its:
            k = (i.routes[0].origin, i.routes[0].destination)
            pairs[k] = min(pairs.get(k, 1e18), i.price)
        if len(pairs) > 1:
            main = pairs.get((main_o, main_d))
            (bo, bd), bp = min(pairs.items(), key=lambda kv: kv[1])
            if main is not None and (bo, bd) != (main_o, main_d) and main - bp >= 5:
                out.append(
                    Insight(
                        kind="airport",
                        text=f"{bo} → {bd} is the cheapest airport pair: from {money(bp)}, vs {money(main)} for {main_o} → {main_d}.",
                        saving=main - bp,
                    )
                )

        fastest = next((i for i in its if "Fastest" in i.tags), None)
        if fastest and fastest.price - cheapest.price > 0:
            hours = (cheapest.total_duration_min - fastest.total_duration_min) / 60
            if hours >= 1:
                out.append(
                    Insight(
                        kind="time",
                        text=f"The fastest option costs {money(fastest.price - cheapest.price)} more than the cheapest and saves {hours:.1f} hours of travel.",
                    )
                )
        return out


# ---------------------------------------------------------------------------
# Running a search
# ---------------------------------------------------------------------------


class SearchJob:
    def __init__(self, req: SearchRequest):
        self.id = uuid.uuid4().hex[:12]
        self.created = time.time()
        self.result = SearchResult(
            id=self.id,
            status="running",
            progress=SearchProgress(done=0, total=0, failed=0, message="Planning searches…"),
            request=req,
        )
        self.task: asyncio.Task | None = None


_jobs: dict[str, SearchJob] = {}


def get_job(job_id: str) -> SearchJob | None:
    return _jobs.get(job_id)


def start_search(req: SearchRequest) -> SearchJob:
    cutoff = time.time() - 3600
    for k in [k for k, j in _jobs.items() if j.created < cutoff]:
        del _jobs[k]
    job = SearchJob(req)
    _jobs[job.id] = job
    job.task = asyncio.create_task(run_search(job))
    return job


async def run_search(job: SearchJob, provider: Provider | None = None) -> SearchResult:
    req = job.result.request
    res = job.result
    provider = provider or get_provider(req.provider)
    plan, possible = plan_queries(req)
    res.progress.total = len(plan)
    res.airports_searched = sorted(
        {c for l in req.legs for c in (*l.origins, *l.destinations)}
    )
    if possible > len(plan):
        res.progress.message = (
            f"Running {len(plan)} of {possible} possible searches (raise the depth to search more)"
        )
    else:
        res.progress.message = f"Running {len(plan)} searches"

    builder = Builder(req, provider)
    sem = asyncio.Semaphore(provider.concurrency)
    errors: list[str] = []
    lock = asyncio.Lock()

    def rebuild() -> None:
        its, grid, ins = builder.build()
        res.itineraries, res.date_grid, res.insights = its, grid, ins

    async def run_one(p: Planned) -> None:
        cached = _cache_get(p.query, provider.name)
        if cached is None:
            async with sem:
                try:
                    options = await provider.search(p.query)
                except ProviderError as e:
                    options = None
                    errors.append(str(e))
                except Exception as e:  # never let one query kill the search
                    options = None
                    errors.append(f"{type(e).__name__}: {e}")
            if options is not None:
                _cache_put(p.query, provider.name, options)
        else:
            options = cached
        async with lock:
            res.progress.done += 1
            if options is None:
                res.progress.failed += 1
            else:
                builder.add(p, options)
                rebuild()

    try:
        await asyncio.gather(*(run_one(p) for p in plan))
        rebuild()
        res.errors = sorted(set(errors))[:10]
        if res.progress.failed == res.progress.total and res.progress.total:
            res.status = "error"
            res.progress.message = "Every search failed."
        else:
            res.status = "done"
            res.progress.message = f"Searched {res.progress.done - res.progress.failed} of {res.progress.total} combinations"
    except Exception as e:  # pragma: no cover - defensive
        res.status = "error"
        res.errors.append(f"{type(e).__name__}: {e}")
    return res

