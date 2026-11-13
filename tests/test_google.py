from datetime import date

from fast_flights.model import Airport, CarbonEmission, Flights, SimpleDatetime, SingleFlight

from flightgrabber.models import Passengers
from flightgrabber.providers import GoogleFlightsProvider, ProviderQuery, QueryLeg


def q(trip="one-way"):
    legs = (QueryLeg("JFK", "LAX", date(2030, 3, 1)),)
    if trip == "round-trip":
        legs += (QueryLeg("LAX", "JFK", date(2030, 3, 8)),)
    return ProviderQuery(legs=legs, trip=trip, passengers=Passengers(adults=2), cabin="business", checked_bags=1)


def test_builds_google_url():
    url = GoogleFlightsProvider().search_url(q("round-trip"))
    assert url.startswith("https://www.google.com/travel/flights/search?tfs=")
    assert "curr=USD" in url


def test_converts_results():
    f = Flights(
        type="multi",
        price=812,
        airlines=["Delta", "Alaska"],
        flights=[
            SingleFlight(Airport("John F. Kennedy", "JFK"), Airport("Salt Lake City", "SLC"),
                         SimpleDatetime((2030, 3, 1), (8, 5)), SimpleDatetime((2030, 3, 1), (11, 0)), 295, "A321"),
            SingleFlight(Airport("Salt Lake City", "SLC"), Airport("Los Angeles", "LAX"),
                         SimpleDatetime((2030, 3, 1), (12, 30)), SimpleDatetime((2030, 3, 1), (13, 45)), 135, "737"),
        ],
        carbon=CarbonEmission(1, 1),
    )
    leg = GoogleFlightsProvider._convert(f, q("round-trip"), "u")
    assert leg.price == 812 and leg.price_is_round_trip
    assert [s.airline for s in leg.segments] == ["Delta", "Alaska"]
    assert leg.stops == 1 and leg.layovers_min == [90]
    assert leg.duration_min == 295 + 90 + 135
