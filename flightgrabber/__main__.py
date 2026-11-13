"""Run with: python -m flightgrabber [--host 0.0.0.0] [--port 8000]"""

import argparse
import socket

import uvicorn


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Flight Grabber")
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open it from your phone on the same Wi-Fi")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    print(f"\n  Flight Grabber running at http://localhost:{args.port}")
    if args.host == "0.0.0.0" and (ip := _lan_ip()):
        print(f"  On your phone (same Wi-Fi): http://{ip}:{args.port}")
    print()
    uvicorn.run("flightgrabber.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
