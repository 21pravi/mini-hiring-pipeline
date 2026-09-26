"""Domain layer: the only code that writes to the audit trail.

Current stage is never stored. It is the `to_stage` of a candidate's latest event, so the
history and the "current state" can't disagree.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import db
from .clock import from_iso, to_iso, utcnow
from .stages import PROGRESSION, REJECTED, TERMINAL, next_stage


class PipelineError(Exception):
    status = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(PipelineError):
    status = 404


class Conflict(PipelineError):
    status = 409


class InvalidTransition(PipelineError):
    status = 422


class InvalidInput(PipelineError):
    status = 422


@dataclass(frozen=True)
class Event:
    seq: int
    action: str
    from_stage: str | None
    to_stage: str
    at: datetime
    note: str | None


@dataclass(frozen=True)
class Candidate:
    id: int
    name: str
    email: str | None
    created_at: datetime
    events: tuple[Event, ...]

    @property
    def stage(self) -> str:
        return self.events[-1].to_stage

    @property
    def entered_stage_at(self) -> datetime:
        return self.events[-1].at

    def time_in_stage(self, now: datetime) -> timedelta:
        return max(now - self.entered_stage_at, timedelta(0))


# ---------- reads ----------

def _row_to_event(r: sqlite3.Row) -> Event:
    return Event(r["seq"], r["action"], r["from_stage"], r["to_stage"], from_iso(r["at"]), r["note"])


def load_all(conn: sqlite3.Connection) -> list[Candidate]:
    events: dict[int, list[Event]] = {}
    for r in conn.execute("SELECT * FROM stage_events ORDER BY candidate_id, seq"):
        events.setdefault(r["candidate_id"], []).append(_row_to_event(r))
    return [
        Candidate(r["id"], r["name"], r["email"], from_iso(r["created_at"]), tuple(events.get(r["id"], ())))
        for r in conn.execute("SELECT * FROM candidates ORDER BY id")
        if r["id"] in events
    ]


def load_one(conn: sqlite3.Connection, candidate_id: int) -> Candidate:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    if row is None:
        raise NotFound(f"No candidate with id {candidate_id}.")
    evs = tuple(_row_to_event(r) for r in conn.execute(
        "SELECT * FROM stage_events WHERE candidate_id = ? ORDER BY seq", (candidate_id,)))
    return Candidate(row["id"], row["name"], row["email"], from_iso(row["created_at"]), evs)


# ---------- writes ----------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def add_candidate(conn: sqlite3.Connection, name: str, email: str | None = None,
                  now: datetime | None = None) -> Candidate:
    now = now or utcnow()
    name = " ".join((name or "").split())
    if not name:
        raise InvalidInput("Name is required.")
    if len(name) > 100:
        raise InvalidInput("Name must be 100 characters or fewer.")
    email = (email or "").strip().lower() or None
    if email and not _EMAIL.match(email):
        raise InvalidInput(f"“{email}” doesn't look like an email address.")

    with db.transaction(conn):
        if email:
            dup = conn.execute("SELECT name FROM candidates WHERE email = ?", (email,)).fetchone()
            if dup:
                raise Conflict(f"{dup['name']} already applied with {email}.")
        cur = conn.execute("INSERT INTO candidates (name, email, created_at) VALUES (?, ?, ?)",
                           (name, email, to_iso(now)))
        cid = cur.lastrowid
        conn.execute(
            "INSERT INTO stage_events (candidate_id, seq, action, from_stage, to_stage, at) "
            "VALUES (?, 1, 'applied', NULL, 'Applied', ?)", (cid, to_iso(now)))
    return load_one(conn, cid)


def transition(conn: sqlite3.Connection, candidate_id: int, action: str, expected_stage: str,
               note: str | None = None, now: datetime | None = None) -> Candidate:
    """Advance one stage or reject.

    `expected_stage` is the stage the caller saw. If someone else moved the candidate in the
    meantime, we refuse instead of applying the action to a state the caller never saw; this
    is what stops a double-click from skipping a stage.
    """
    now = now or utcnow()
    note = (note or "").strip() or None
    if note and len(note) > 500:
        raise InvalidInput("Notes must be 500 characters or fewer.")
    if action not in ("advance", "reject"):
        raise InvalidInput("Action must be “advance” or “reject”.")

    with db.transaction(conn):
        c = load_one(conn, candidate_id)
        current = c.stage
        if expected_stage != current:
            raise Conflict(f"{c.name} is now in {current}, not {expected_stage}. "
                           "The board was out of date; it has been refreshed.")
        if current in TERMINAL:
            outcome = "hired" if current == "Hired" else "rejected"
            raise InvalidTransition(f"{c.name} was {outcome}. Final outcomes can't be changed.")
        if action == "advance":
            to_stage, recorded = next_stage(current), "advanced"
        else:
            to_stage, recorded = REJECTED, "rejected"
        last = c.events[-1]
        at = max(now, last.at)  # never let a clock step backwards break the chain
        try:
            conn.execute(
                "INSERT INTO stage_events (candidate_id, seq, action, from_stage, to_stage, at, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (candidate_id, last.seq + 1, recorded, current, to_stage, to_iso(at), note))
        except sqlite3.IntegrityError as exc:  # the DB backstop disagreed with us
            raise Conflict(f"Could not record the move: {exc}.") from exc
    return load_one(conn, candidate_id)


# ---------- serialisation ----------

def summary(c: Candidate, now: datetime) -> dict:
    nxt = next_stage(c.stage)
    return {
        "id": c.id,
        "name": c.name,
        "email": c.email,
        "stage": c.stage,
        "next_stage": nxt,
        "can_reject": c.stage not in TERMINAL,
        "entered_stage_at": to_iso(c.entered_stage_at),
        "time_in_stage_seconds": int(c.time_in_stage(now).total_seconds()),
        "created_at": to_iso(c.created_at),
        "furthest_stage": max((e.to_stage for e in c.events if e.to_stage in PROGRESSION),
                              key=PROGRESSION.index),
    }


def detail(c: Candidate, now: datetime) -> dict:
    history = []
    for i, e in enumerate(c.events):
        end = c.events[i + 1].at if i + 1 < len(c.events) else None
        history.append({
            "seq": e.seq,
            "action": e.action,
            "from_stage": e.from_stage,
            "to_stage": e.to_stage,
            "at": to_iso(e.at),
            "note": e.note,
            "duration_seconds": int(((end or now) - e.at).total_seconds()),
            "current": end is None,
        })
    return {**summary(c, now), "history": history}
