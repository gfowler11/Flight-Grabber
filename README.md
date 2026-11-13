# Flight Grabber

A personal flight finder that deep-searches every airport, date and ticket
combination for your trip, then sends you to the booking site to buy.

For each search it:

- **Checks nearby airports.** For example JFK, LaGuardia and Newark, plus whatever else you tick.
- **Checks flexible dates** (±1–3 days) and shows a price grid by date.
- **Compares ticket strategies.** For round trips it checks the round-trip fare *and* two separate one-way tickets, which are often cheaper.
- **Supports multi-city trips** (up to 5 flights).
- **Ranks results three ways:** Best (price plus the value of your time), Cheapest and Fastest.
- **Flags hassles:** tight or overnight connections, changing airports, returning to a different airport.
- **Explains its savings,** e.g. "Two one-way tickets beat the round-trip fare by $133".
- **Links to booking sites.** Every result opens Google Flights, Kayak, Skyscanner, Expedia and Momondo with the same search, so you can compare and book.

It costs nothing to run: prices come from Google Flights (which already covers
most airlines and many booking sites) through the open-source
[`fast-flights`](https://github.com/AWeirdDev/fast-flights) library.

## Run it

You need Python 3.10 or newer ([python.org](https://www.python.org/downloads/)).

- **Mac / Linux:** double-click or run `./run.sh`
- **Windows:** double-click `run.bat`

The first run installs everything. Your browser then opens at http://localhost:8000.

**On your phone:** keep it running on your computer, connect your phone to the
same Wi-Fi, and open the "On your phone" address printed in the terminal. In your
phone browser's menu, choose "Add to Home Screen" to use it like an app.

Manual start:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m flightgrabber            # http://localhost:8000
.venv/bin/python -m flightgrabber --host 0.0.0.0   # also reachable from your phone
```

## Things to know

- **Search depth** sets how many searches run: Quick ≈12, Deep ≈40, Max ≈100.
  More searches find more, but take longer, and many searches in a row can get
  your network temporarily blocked by Google. Deep is a good default.
- **Prices change.** Always confirm the price on the booking site before you pay.
- **Round-trip fares:** Google only shows return flights after you choose the
  outbound, so those results say "pick your return on the booking site".
- **If every search fails,** Google is probably blocking requests for a while, or
  has changed its page. Wait and retry, lower the depth, or update the library:
  `.venv/bin/pip install -U fast-flights`. The **Demo** data option lets you try
  the app with made-up prices in the meantime.
- Hidden-city tickets are deliberately not searched.

## Project layout

```
flightgrabber/
  main.py          web server + API
  engine.py        plans the searches, combines results, scores, finds savings
  providers/       google.py (live data), demo.py (made-up data)
  links.py         booking-site deep links
  airports.py      airport search / nearby airports (data/airports.csv)
  static/          the web app (HTML/CSS/JS, no build step)
tests/             pytest suite
tools/             build_airports.py regenerates the airport list from OurAirports
```

Run the tests: `pip install -r requirements-dev.txt && pytest`

## Ideas for later

Price tracking and drop alerts, price history ("buy now or wait"), more data
sources, and in-app booking.
