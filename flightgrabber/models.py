"""Data models shared by the API, the search engine and the providers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Cabin = Literal["economy", "premium-economy", "business", "first"]
TripType = Literal["one-way", "round-trip", "multi-city"]
Depth = Literal["quick", "deep", "max"]
ProviderName = Literal["google", "demo"]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class Passengers(BaseModel):
    adults: int = Field(1, ge=0, le=9)
    children: int = Field(0, ge=0, le=8)
    infants_in_seat: int = Field(0, ge=0, le=8)
    infants_on_lap: int = Field(0, ge=0, le=8)

    @model_validator(mode="after")
    def _check(self) -> "Passengers":
        total = self.adults + self.children + self.infants_in_seat + self.infants_on_lap
        if total < 1:
            raise ValueError("At least one passenger is required")
        if total > 9:
            raise ValueError("At most 9 passengers")
        if self.infants_on_lap > self.adults:
            raise ValueError("Each lap infant needs an adult")
        if self.adults == 0 and (self.children or self.infants_in_seat):
            raise ValueError("Children and infants must travel with an adult")
        return self

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants_in_seat + self.infants_on_lap


class LegRequest(BaseModel):
    """One leg of the trip as the user typed it.

    ``origins`` / ``destinations`` hold one or more IATA codes: the main airport
    plus any nearby airports the user ticked.
    """

    origins: list[str] = Field(min_length=1, max_length=6)
    destinations: list[str] = Field(min_length=1, max_length=6)
    date: date

    @field_validator("origins", "destinations")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for code in v:
            code = code.strip().upper()
            if len(code) != 3 or not code.isalpha():
                raise ValueError(f"'{code}' is not a 3-letter airport code")
            if code not in out:
                out.append(code)
        return out


class SearchRequest(BaseModel):
    trip_type: TripType = "round-trip"
    legs: list[LegRequest] = Field(min_length=1, max_length=5)
    passengers: Passengers = Passengers()
    cabin: Cabin = "economy"
    carry_on_bags: int = Field(0, ge=0, le=1)
    checked_bags: int = Field(0, ge=0, le=2)
    max_stops: int | None = Field(None, ge=0, le=2)
    exclude_basic_economy: bool = False
    flex_days: int = Field(0, ge=0, le=3)
    depth: Depth = "deep"
    currency: str = "USD"
    provider: ProviderName = "google"
    # How much one hour of travel time is "worth" to the traveller, used to
    # rank "best" options (price + time cost).
    hour_value: float = Field(25.0, ge=0, le=500)

    @model_validator(mode="after")
    def _check(self) -> "SearchRequest":
        if self.trip_type == "one-way" and len(self.legs) != 1:
            raise ValueError("A one-way trip has exactly one leg")
        if self.trip_type == "round-trip" and len(self.legs) != 2:
            raise ValueError("A round trip has exactly two legs")
        if self.trip_type == "multi-city" and len(self.legs) < 2:
            raise ValueError("A multi-city trip needs at least two legs")
        for a, b in zip(self.legs, self.legs[1:]):
            if b.date < a.date:
                raise ValueError("Each leg must depart on or after the previous leg")
        self.currency = self.currency.upper()
        return self


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class Segment(BaseModel):
    """A single takeoff-to-landing flight."""

    from_code: str
    from_name: str = ""
    to_code: str
    to_name: str = ""
    depart: datetime  # local time at the departure airport
    arrive: datetime  # local time at the arrival airport
    duration_min: int
    aircraft: str = ""
    airline: str = ""


class LegOption(BaseModel):
    """One way of flying one leg, as returned by a provider."""

    segments: list[Segment]
    airlines: list[str]
    price: float | None  # total for all passengers; None if unknown
    source: ProviderName
    google_url: str = ""
    # When this option came from a round-trip fare search, the price covers
    # the whole round trip, not just this leg.
    price_is_round_trip: bool = False

    @property
    def origin(self) -> str:
        return self.segments[0].from_code

    @property
    def destination(self) -> str:
        return self.segments[-1].to_code

    @property
    def depart(self) -> datetime:
        return self.segments[0].depart

    @property
    def arrive(self) -> datetime:
        return self.segments[-1].arrive

    @property
    def stops(self) -> int:
        return len(self.segments) - 1

    @property
    def layovers_min(self) -> list[int]:
        return [
            int((b.depart - a.arrive).total_seconds() // 60)
            for a, b in zip(self.segments, self.segments[1:])
        ]

    @property
    def duration_min(self) -> int:
        return sum(s.duration_min for s in self.segments) + sum(self.layovers_min)

    def key(self) -> tuple:
        """Identity used to de-duplicate the same flights seen twice."""
        return tuple(
            (s.from_code, s.to_code, s.depart.isoformat(), s.airline) for s in self.segments
        )


class LegView(BaseModel):
    """A leg option as shown to the user, with derived fields filled in."""

    origin: str
    destination: str
    depart: datetime
    arrive: datetime
    duration_min: int
    stops: int
    layovers_min: list[int]
    airlines: list[str]
    segments: list[Segment]
    price: float | None
    google_url: str = ""

    @classmethod
    def of(cls, leg: LegOption) -> "LegView":
        return cls(
            origin=leg.origin,
            destination=leg.destination,
            depart=leg.depart,
            arrive=leg.arrive,
            duration_min=leg.duration_min,
            stops=leg.stops,
            layovers_min=leg.layovers_min,
            airlines=leg.airlines,
            segments=leg.segments,
            price=None if leg.price_is_round_trip else leg.price,
            google_url=leg.google_url,
        )


class BookingLink(BaseModel):
    site: str
    url: str
    # Which part of the trip this link books, e.g. "JFK → LAX" for one of
    # several separate tickets. Empty when it covers the whole trip.
    label: str = ""


TicketStrategy = Literal["single", "round-trip-fare", "separate-tickets"]


class RouteInfo(BaseModel):
    origin: str
    destination: str
    date: date


class Itinerary(BaseModel):
    id: str
    strategy: TicketStrategy
    routes: list[RouteInfo]
    # One entry per route. ``None`` means the flight for that leg is picked on
    # the booking site (e.g. the return of a round-trip fare, which Google only
    # reveals after you choose the outbound).
    legs: list[LegView | None]
    price: float
    currency: str
    total_duration_min: int
    total_stops: int
    best_score: float = 0.0
    tags: list[str] = []
    warnings: list[str] = []
    booking_links: list[BookingLink] = []
    source: ProviderName


class DateCell(BaseModel):
    depart: date
    ret: date | None = None
    price: float


class SearchProgress(BaseModel):
    done: int
    total: int
    failed: int
    message: str = ""


class Insight(BaseModel):
    kind: str
    text: str
    saving: float = 0.0


class SearchResult(BaseModel):
    id: str
    status: Literal["running", "done", "error"]
    progress: SearchProgress
    itineraries: list[Itinerary] = []
    date_grid: list[DateCell] = []
    airports_searched: list[str] = []
    errors: list[str] = []
    insights: list[Insight] = []
    request: SearchRequest
