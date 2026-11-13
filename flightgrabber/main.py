"""FastAPI app: JSON API plus the static web UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, airports, engine
from .models import SearchRequest, SearchResult

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Flight Grabber", version=__version__)


@app.get("/api/airports")
def airport_search(q: str = Query(..., min_length=1, max_length=60)) -> list[dict]:
    return airports.search(q)


@app.get("/api/airports/{code}/nearby")
def airport_nearby(code: str, radius_km: float = Query(150, ge=10, le=500)) -> list[dict]:
    if not airports.get(code):
        raise HTTPException(404, f"Unknown airport {code}")
    return airports.nearby(code, radius_km)


@app.post("/api/search")
async def search(req: SearchRequest) -> dict:
    job = engine.start_search(req)
    return {"id": job.id}


@app.get("/api/search/{job_id}")
def search_status(job_id: str) -> SearchResult:
    job = engine.get_job(job_id)
    if not job:
        raise HTTPException(404, "Search not found (it may have expired)")
    return job.result


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/", StaticFiles(directory=STATIC), name="static")
