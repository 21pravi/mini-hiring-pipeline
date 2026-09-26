from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db, pipeline

# Saturday 26 Sep 2026, 12:00 UTC. Fixed so weekday maths is deterministic.
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()


def make(conn, name, applied, advances=(), reject=None, email=None, note=None):
    """Create a candidate with a back-dated history. Days are 'days before NOW'."""
    c = pipeline.add_candidate(conn, name, email, now=ago(applied))
    for d in advances:
        c = pipeline.transition(conn, c.id, "advance", c.stage, now=ago(d))
    if reject is not None:
        c = pipeline.transition(conn, c.id, "reject", c.stage, note=note, now=ago(reject))
    return c
