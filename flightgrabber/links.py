"""Deep links that open the same search on popular booking sites.

These let you check a price on several sites in one click. They open a
search for the airports, dates, passengers and cabin; you then pick the
flight (airline and times are shown on each result card).
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from .models import BookingLink, Cabin, Passengers

Route = list[tuple[str, str, date]]  # (origin, destination, date) per leg


def _is_round_trip(route: Route) -> bool:
    return len(route) == 2 and route[0][0] == route[1][1] and route[0][1] == route[1][0]


def kayak_style(base: str, route: Route, pax: Passengers, cabin: Cabin) -> str:
    """Kayak and Momondo share this URL format."""
    parts: list[str] = []
    if _is_round_trip(route):
        (o, d, d1), (_, _, d2) = route
        parts += [f"{o}-{d}", d1.isoformat(), d2.isoformat()]
    else:
        for o, d, dt in route:
            parts += [f"{o}-{d}", dt.isoformat()]
    cabin_part = {"economy": "", "premium-economy": "premium", "business": "business", "first": "first"}[cabin]
    if cabin_part:
        parts.append(cabin_part)
    if pax.adults != 1 or pax.children or pax.infants_in_seat or pax.infants_on_lap:
        parts.append(f"{pax.adults}adults")
    kids = ["11"] * pax.children + ["1S"] * pax.infants_in_seat + ["1L"] * pax.infants_on_lap
    if kids:
        parts.append("children-" + "-".join(kids))
    section = "flight-search" if "momondo" in base else "flights"
    return f"{base}/{section}/" + "/".join(parts) + "?sort=bestflight_a"


def skyscanner(route: Route, pax: Passengers, cabin: Cabin) -> str | None:
    if len(route) == 1:
        o, d, d1 = route[0]
        path = f"{o.lower()}/{d.lower()}/{d1.strftime('%y%m%d')}/"
        rtn = 0
    elif _is_round_trip(route):
        (o, d, d1), (_, _, d2) = route
        path = f"{o.lower()}/{d.lower()}/{d1.strftime('%y%m%d')}/{d2.strftime('%y%m%d')}/"
        rtn = 1
    else:
        return None  # Skyscanner multi-city URLs aren't stable enough to deep-link
    cabin_q = {"economy": "economy", "premium-economy": "premiumeconomy", "business": "business", "first": "first"}[cabin]
    ages = ["8"] * pax.children + ["1"] * (pax.infants_in_seat + pax.infants_on_lap)
    q = f"adultsv2={pax.adults}&cabinclass={cabin_q}&rtn={rtn}"
    if ages:
        q += "&childrenv2=" + quote("|".join(ages))
    return f"https://www.skyscanner.com/transport/flights/{path}?{q}"


def expedia(route: Route, pax: Passengers, cabin: Cabin) -> str:
    if len(route) == 1:
        trip = "oneway"
    elif _is_round_trip(route):
        trip = "roundtrip"
    else:
        trip = "multi"
    legs = [
        f"leg{i}=" + quote(f"from:{o},to:{d},departure:{dt.strftime('%m/%d/%Y')}TANYT", safe=":,/")
        for i, (o, d, dt) in enumerate(route, start=1)
    ]
    passengers = f"adults:{pax.adults}"
    kid_ages = ["8"] * pax.children + ["1"] * pax.infants_in_seat
    if kid_ages:
        passengers += f",children:{len(kid_ages)}[{';'.join(kid_ages)}]"
    if pax.infants_on_lap:
        passengers += ",infantinlap:Y"
    cabin_q = {"economy": "economy", "premium-economy": "premium", "business": "business", "first": "first"}[cabin]
    return (
        f"https://www.expedia.com/Flights-Search?trip={trip}&"
        + "&".join(legs)
        + f"&passengers={quote(passengers, safe=':,[];')}&options=cabinclass:{cabin_q}&mode=search"
    )


def for_route(
    route: Route,
    pax: Passengers,
    cabin: Cabin,
    google_url: str = "",
    label: str = "",
) -> list[BookingLink]:
    links: list[BookingLink] = []
    if google_url:
        links.append(BookingLink(site="Google Flights", url=google_url, label=label))
    links.append(BookingLink(site="Kayak", url=kayak_style("https://www.kayak.com", route, pax, cabin), label=label))
    sky = skyscanner(route, pax, cabin)
    if sky:
        links.append(BookingLink(site="Skyscanner", url=sky, label=label))
    links.append(BookingLink(site="Expedia", url=expedia(route, pax, cabin), label=label))
    links.append(BookingLink(site="Momondo", url=kayak_style("https://www.momondo.com", route, pax, cabin), label=label))
    return links
