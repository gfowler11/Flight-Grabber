"""Rebuild flightgrabber/data/airports.csv from OurAirports (public domain).

Keeps only airports with an IATA code and scheduled passenger service.
Run: python tools/build_airports.py
"""

import csv
import io
import urllib.request
from pathlib import Path

SRC = "https://raw.githubusercontent.com/davidmegginson/ourairports-data/main/airports.csv"
OUT = Path(__file__).resolve().parent.parent / "flightgrabber" / "data" / "airports.csv"
SIZE = {"large_airport": "L", "medium_airport": "M", "small_airport": "S"}


def main() -> None:
    raw = urllib.request.urlopen(SRC, timeout=60).read().decode("utf-8")
    rows = []
    seen = set()
    for r in csv.DictReader(io.StringIO(raw)):
        code = (r["iata_code"] or "").strip().upper()
        if len(code) != 3 or not code.isalpha() or code in seen:
            continue
        if r["scheduled_service"] != "yes" or r["type"] not in SIZE:
            continue
        seen.add(code)
        rows.append([
            code,
            r["name"],
            r["municipality"],
            r["iso_country"],
            f"{float(r['latitude_deg']):.4f}",
            f"{float(r['longitude_deg']):.4f}",
            SIZE[r["type"]],
        ])
    rows.sort()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "city", "country", "lat", "lon", "size"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} airports to {OUT}")


if __name__ == "__main__":
    main()
