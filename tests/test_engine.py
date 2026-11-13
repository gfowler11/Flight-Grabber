import asyncio
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from flightgrabber import airports, engine, links
from flightgrabber.main import app
from flightgrabber.models import LegOption, Passengers, SearchRequest, Segment
from flightgrabber.providers import Provider, ProviderError

FUTURE = date.today() + timedelta(days=40)


def rt_request(**kw) -> SearchRequest:
    base = dict(
        trip_type="round-trip",
        legs=[
            {"origins": ["JFK", "EWR"], "destinations": ["LAX", "BUR"], "date": FUTURE.isoformat()},
            {"origins": ["LAX", "BUR"], "destinations": ["JFK", "EWR"], "date": (FUTURE + timedelta(days=7)).isoformat()},
        ],
        flex_days=1,
        depth="max",
        provider="demo",
    )
    base.update(kw)
    return SearchRequest(**base)


def run(req: SearchRequest, provider: Provider | None = None):
    engine._cache.clear()
    return asyncio.run(engine.run_search(engine.SearchJob(req), provider))


# --- airports ---------------------------------------------------------------


def test_airport_search_and_nearby():
    assert airports.search("JFK")[0]["code"] == "JFK"
    assert {"LHR", "LGW"} <= {a["code"] for a in airports.search("london")}
    near = {a["code"] for a in airports.nearby("JFK", 60)}
    assert {"LGA", "EWR"} <= near
    assert 3900 < airports.distance_km("JFK", "LAX") < 4100


# --- planning ---------------------------------------------------------------


def test_plan_starts_with_exact_search_and_respects_budget():
    req = rt_request(depth="quick")
    plan, possible = engine.plan_queries(req)
    assert len(plan) == engine.BUDGET["quick"] < possible
    first = {(p.query.trip, p.query.legs[0].origin, p.query.legs[0].destination, p.query.legs[0].date) for p in plan[:3]}
    assert ("round-trip", "JFK", "LAX", FUTURE) in first
    assert ("one-way", "JFK", "LAX", FUTURE) in first


def test_plan_interleaves_dates_and_airports():
    plan, _ = engine.plan_queries(rt_request(depth="deep"))
    dates = {p.query.legs[0].date for p in plan}
    pairs = {(p.query.legs[0].origin, p.query.legs[0].destination) for p in plan}
    assert len(dates) >= 3  # flexible dates searched
    assert len(pairs) >= 3  # alternative airports searched


def test_plan_skips_past_dates():
    req = rt_request()
    plan, _ = engine.plan_queries(req, today=FUTURE)
    assert all(p.query.legs[0].date >= FUTURE for p in plan)


# --- full search with demo data ---------------------------------------------


def test_round_trip_search_combines_strategies():
    res = run(rt_request())
    assert res.status == "done"
    strategies = {i.strategy for i in res.itineraries}
    assert strategies == {"round-trip-fare", "separate-tickets"}
    tags = [t for i in res.itineraries for t in i.tags]
    assert {"Best", "Cheapest", "Fastest"} <= set(tags)
    cheapest = next(i for i in res.itineraries if "Cheapest" in i.tags)
    assert cheapest.price == min(i.price for i in res.itineraries)
    assert res.insights


def test_date_grid_matches_results():
    res = run(rt_request())
    assert len(res.date_grid) >= 4
    for cell in res.date_grid:
        match = [
            i for i in res.itineraries
            if i.routes[0].date == cell.depart and i.routes[1].date == cell.ret
        ]
        assert match and min(i.price for i in match) == cell.price


def test_separate_tickets_respect_chronology():
    res = run(rt_request())
    for i in res.itineraries:
        if i.strategy == "separate-tickets":
            out, back = i.legs
            assert (back.depart - out.arrive) >= timedelta(minutes=engine.MIN_GAP_BETWEEN_LEGS_MIN)


def test_one_way_and_multi_city():
    one = run(SearchRequest(
        trip_type="one-way",
        legs=[{"origins": ["SFO"], "destinations": ["SEA"], "date": FUTURE.isoformat()}],
        provider="demo",
        flex_days=2,
    ))
    assert one.itineraries and all(i.strategy == "single" for i in one.itineraries)
    assert len(one.date_grid) == 5

    multi = run(SearchRequest(
        trip_type="multi-city",
        legs=[
            {"origins": ["JFK"], "destinations": ["LHR"], "date": FUTURE.isoformat()},
            {"origins": ["LHR"], "destinations": ["CDG"], "date": (FUTURE + timedelta(days=4)).isoformat()},
            {"origins": ["CDG"], "destinations": ["JFK"], "date": (FUTURE + timedelta(days=9)).isoformat()},
        ],
        provider="demo",
    ))
    assert multi.itineraries
    assert all(len(i.legs) == 3 for i in multi.itineraries)


class FailingProvider(Provider):
    name = "failing"

    async def search(self, q):
        raise ProviderError("blocked")


def test_all_failures_reported():
    res = run(rt_request(depth="quick"), FailingProvider())
    assert res.status == "error"
    assert res.progress.failed == res.progress.total
    assert res.errors == ["blocked"]


# --- validation & scoring ---------------------------------------------------


def test_request_validation():
    with pytest.raises(ValueError):
        SearchRequest(trip_type="round-trip", legs=[{"origins": ["JFK"], "destinations": ["LAX"], "date": FUTURE.isoformat()}])
    with pytest.raises(ValueError):
        Passengers(adults=1, infants_on_lap=2)
    with pytest.raises(ValueError):
        SearchRequest(trip_type="one-way", legs=[{"origins": ["J1"], "destinations": ["LAX"], "date": FUTURE.isoformat()}])


def _leg(layover_min: int) -> LegOption:
    t = datetime(2030, 1, 1, 8, 0)
    s1 = Segment(from_code="AAA", to_code="BBB", depart=t, arrive=t + timedelta(hours=2), duration_min=120)
    t2 = s1.arrive + timedelta(minutes=layover_min)
    s2 = Segment(from_code="BBB", to_code="CCC", depart=t2, arrive=t2 + timedelta(hours=1), duration_min=60)
    return LegOption(segments=[s1, s2], airlines=["X"], price=100, source="demo")


def test_leg_warnings_and_duration():
    assert any("Tight" in w for w in engine.leg_warnings(_leg(35)))
    assert any("Long" in w for w in engine.leg_warnings(_leg(7 * 60)))
    assert engine.leg_warnings(_leg(90)) == []
    assert _leg(90).duration_min == 120 + 90 + 60


# --- links --------------------------------------------------------------------


def test_booking_links():
    route = [("JFK", "LAX", date(2030, 3, 1)), ("LAX", "JFK", date(2030, 3, 8))]
    pax = Passengers(adults=2, children=1)
    ls = {l.site: l.url for l in links.for_route(route, pax, "business", "https://google.example")}
    assert ls["Kayak"] == "https://www.kayak.com/flights/JFK-LAX/2030-03-01/2030-03-08/business/2adults/children-11?sort=bestflight_a"
    assert "/jfk/lax/300301/300308/" in ls["Skyscanner"] and "adultsv2=2" in ls["Skyscanner"]
    assert "trip=roundtrip" in ls["Expedia"] and "leg2=" in ls["Expedia"]
    assert ls["Google Flights"] == "https://google.example"
    multi = links.for_route(route + [("JFK", "BOS", date(2030, 3, 10))], Passengers(), "economy")
    assert "Skyscanner" not in {l.site for l in multi}


# --- API ------------------------------------------------------------------------


def test_api_end_to_end():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["ok"]
        assert client.get("/api/airports", params={"q": "paris"}).json()[0]["code"] in ("CDG", "ORY")
        assert client.get("/api/airports/ZZZ/nearby").status_code == 404
        bad = client.post("/api/search", json={"trip_type": "one-way", "legs": []})
        assert bad.status_code == 422
        r = client.post("/api/search", json=rt_request(depth="quick").model_dump(mode="json"))
        job_id = r.json()["id"]
        for _ in range(100):
            res = client.get(f"/api/search/{job_id}").json()
            if res["status"] != "running":
                break
            asyncio.run(asyncio.sleep(0.1))
        assert res["status"] == "done" and res["itineraries"]
        assert client.get("/").status_code == 200
        assert client.get("/app.js").status_code == 200
