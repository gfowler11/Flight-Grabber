"""Live prices from Google Flights, via the open-source ``fast-flights`` library.

Google Flights already aggregates most airlines and many online travel
agencies, so it is the broadest free source available. It is unofficial: if
Google changes its page, this provider fails until ``fast-flights`` is updated.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime

from ..models import LegOption, Segment
from .base import Provider, ProviderError, ProviderQuery


class GoogleFlightsProvider(Provider):
    name = "google"
    # Keep this low: many parallel requests from one IP get rate limited.
    concurrency = 3

    def _build(self, q: ProviderQuery):
        from fast_flights import FlightQuery, Passengers, create_query

        p = q.passengers
        return create_query(
            flights=[
                FlightQuery(
                    date=leg.date.isoformat(),
                    from_airport=leg.origin,
                    to_airport=leg.destination,
                )
                for leg in q.legs
            ],
            seat=q.cabin,
            trip=q.trip,
            passengers=Passengers(
                adults=p.adults,
                children=p.children,
                infants_in_seat=p.infants_in_seat,
                infants_on_lap=p.infants_on_lap,
            ),
            language="en-US",
            currency=q.currency,
            max_stops=q.max_stops,
            carry_on_bags=q.carry_on_bags,
            checked_bags=q.checked_bags,
            exclude_basic_economy=q.exclude_basic_economy,
        )

    def search_url(self, q: ProviderQuery) -> str:
        try:
            return self._build(q).url()
        except Exception:
            return ""

    async def search(self, q: ProviderQuery) -> list[LegOption]:
        # A little jitter so a burst of queries looks less like a bot.
        await asyncio.sleep(random.uniform(0.2, 0.8))
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._search_sync, q), timeout=45)
        except ProviderError:
            raise
        except asyncio.TimeoutError as e:
            raise ProviderError("Google Flights timed out") from e
        except Exception as e:  # network errors, parse errors, blocked pages
            raise ProviderError(f"Google Flights query failed: {type(e).__name__}: {e}") from e

    def _search_sync(self, q: ProviderQuery) -> list[LegOption]:
        from fast_flights import FlightsNotFound, get_flights

        query = self._build(q)
        url = query.url()
        try:
            results = get_flights(query)
        except FlightsNotFound:
            return []
        except AttributeError as e:
            # The results script tag was missing: usually a consent page,
            # a CAPTCHA, or Google changed its markup.
            raise ProviderError(
                "Google Flights returned an unexpected page (blocked, consent screen or layout change)"
            ) from e

        options: list[LegOption] = []
        for f in results:
            try:
                options.append(self._convert(f, q, url))
            except Exception:
                continue  # skip a single malformed result rather than the whole query
        return options

    @staticmethod
    def _convert(f, q: ProviderQuery, url: str) -> LegOption:
        airlines = [a for a in (f.airlines or []) if a]
        segments: list[Segment] = []
        for i, s in enumerate(f.flights):
            dy, dm, dd = s.departure.date
            ay, am, ad = s.arrival.date
            dep = datetime(dy, dm, dd, *s.departure.time)
            arr = datetime(ay, am, ad, *s.arrival.time)
            if len(airlines) == len(f.flights):
                airline = airlines[i]
            elif len(airlines) == 1:
                airline = airlines[0]
            else:
                airline = ""
            duration = s.duration or int((arr - dep).total_seconds() // 60)
            segments.append(
                Segment(
                    from_code=s.from_airport.code,
                    from_name=s.from_airport.name or "",
                    to_code=s.to_airport.code,
                    to_name=s.to_airport.name or "",
                    depart=dep,
                    arrive=arr,
                    duration_min=duration,
                    aircraft=s.plane_type or "",
                    airline=airline,
                )
            )
        if not segments:
            raise ValueError("result without flights")
        price = float(f.price) if f.price else None
        return LegOption(
            segments=segments,
            airlines=airlines,
            price=price,
            source="google",
            google_url=url,
            price_is_round_trip=q.trip == "round-trip",
        )
