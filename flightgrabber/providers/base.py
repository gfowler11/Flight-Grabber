"""Provider interface.

A provider answers one *query*: either a one-way search for a single leg, or a
round-trip fare search for an outbound + return pair. The search engine fans a
user's trip out into many such queries and combines the answers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from ..models import Cabin, LegOption, Passengers


class ProviderError(Exception):
    """A query failed (blocked, timed out, unparseable page, ...)."""


@dataclass(frozen=True)
class QueryLeg:
    origin: str
    destination: str
    date: date


@dataclass(frozen=True)
class ProviderQuery:
    legs: tuple[QueryLeg, ...]
    trip: Literal["one-way", "round-trip"]
    passengers: Passengers = field(default_factory=Passengers, compare=False, hash=False)
    cabin: Cabin = "economy"
    carry_on_bags: int = 0
    checked_bags: int = 0
    max_stops: int | None = None
    exclude_basic_economy: bool = False
    currency: str = "USD"

    def cache_key(self) -> tuple:
        p = self.passengers
        return (
            self.legs,
            self.trip,
            (p.adults, p.children, p.infants_in_seat, p.infants_on_lap),
            self.cabin,
            self.carry_on_bags,
            self.checked_bags,
            self.max_stops,
            self.exclude_basic_economy,
            self.currency,
        )


class Provider:
    name: str = "base"
    # How many queries may run at once against this provider.
    concurrency: int = 4

    async def search(self, q: ProviderQuery) -> list[LegOption]:
        """Return options for the query.

        One-way: options for ``q.legs[0]``.
        Round-trip: outbound options (``q.legs[0]``) whose price is the whole
        round-trip fare (``price_is_round_trip=True``).
        """
        raise NotImplementedError

    def search_url(self, q: ProviderQuery) -> str:
        return ""
