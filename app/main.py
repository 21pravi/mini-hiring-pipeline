"""HTTP layer. Thin on purpose: validation and rules live in pipeline.py and search/."""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterator, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, pipeline
from .clock import utcnow
from .search.engine import run
from .search.parser import QueryError, describe, parse
from .stages import ACTIVE, ALL_STAGES, PROGRESSION, TERMINAL

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


class NewCandidate(BaseModel):
    name: str = Field(max_length=200)
    email: str | None = Field(default=None, max_length=200)


class TransitionRequest(BaseModel):
    action: Literal["advance", "reject"]
    expected_stage: str
    note: str | None = Field(default=None, max_length=1000)


def create_app(db_path: str | None = None) -> FastAPI:
    path = db.resolve_path(db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        conn = db.connect(path)
        db.init_db(conn)
        conn.close()
        yield

    app = FastAPI(title="Mini Hiring Pipeline", lifespan=lifespan)

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = db.connect(path)
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(pipeline.PipelineError)
    async def pipeline_error(_: Request, exc: pipeline.PipelineError):
        return JSONResponse(status_code=exc.status, content={"error": {"message": exc.message}})

    @app.get("/api/stages")
    def stages():
        return {"all": ALL_STAGES, "progression": PROGRESSION, "active": ACTIVE,
                "terminal": sorted(TERMINAL, key=ALL_STAGES.index)}

    @app.get("/api/candidates")
    def list_candidates(conn: sqlite3.Connection = Depends(get_conn)):
        now = utcnow()
        return [pipeline.summary(c, now) for c in pipeline.load_all(conn)]

    @app.post("/api/candidates", status_code=201)
    def add_candidate(body: NewCandidate, conn: sqlite3.Connection = Depends(get_conn)):
        c = pipeline.add_candidate(conn, body.name, body.email)
        return pipeline.detail(c, utcnow())

    @app.get("/api/candidates/{candidate_id}")
    def get_candidate(candidate_id: int, conn: sqlite3.Connection = Depends(get_conn)):
        return pipeline.detail(pipeline.load_one(conn, candidate_id), utcnow())

    @app.post("/api/candidates/{candidate_id}/transitions")
    def move_candidate(candidate_id: int, body: TransitionRequest,
                       conn: sqlite3.Connection = Depends(get_conn)):
        c = pipeline.transition(conn, candidate_id, body.action, body.expected_stage, body.note)
        return pipeline.detail(c, utcnow())

    @app.get("/api/search")
    def search(q: str = "", tz: str = "UTC", conn: sqlite3.Connection = Depends(get_conn)):
        now = utcnow()
        try:
            zone = ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            zone, tz = ZoneInfo("UTC"), "UTC"
        candidates = pipeline.load_all(conn)
        try:
            parsed = parse(q, now, zone)
        except QueryError as exc:
            return JSONResponse(status_code=400, content={"query": q, "error": exc.to_dict()})
        outcome = run(parsed, candidates)
        return {
            "query": q,
            "timezone": tz,
            "interpretation": [describe(c, zone) for c in parsed.clauses],
            "notes": parsed.notes,
            "count": len(outcome.hits),
            "results": [{**pipeline.summary(h.candidate, now), "score": h.score, "reasons": h.reasons}
                        for h in outcome.hits],
            "empty": outcome.empty,
        }

    # Mounted last so it never shadows /api routes.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
