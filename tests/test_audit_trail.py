"""The storage layer defends the audit trail even if application code is bypassed."""
import sqlite3

import pytest

from app.clock import to_iso

from .conftest import NOW, ago, make


def raw_insert(conn, cid, seq, action, frm, to, at=NOW):
    conn.execute("INSERT INTO stage_events (candidate_id, seq, action, from_stage, to_stage, at) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (cid, seq, action, frm, to, to_iso(at)))


def test_events_cannot_be_updated(conn):
    make(conn, "A", 5)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE stage_events SET to_stage = 'Hired'")


def test_events_cannot_be_deleted(conn):
    make(conn, "A", 5)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM stage_events")


def test_candidates_cannot_be_changed_or_deleted(conn):
    make(conn, "A", 5)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE candidates SET name = 'B'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM candidates")


def test_raw_insert_cannot_skip_a_stage(conn):
    c = make(conn, "A", 5)
    with pytest.raises(sqlite3.IntegrityError, match="not allowed"):
        raw_insert(conn, c.id, 2, "advanced", "Applied", "Interview")


def test_raw_insert_cannot_reverse_a_final_outcome(conn):
    c = make(conn, "A", 5, reject=4)
    with pytest.raises(sqlite3.IntegrityError, match="not allowed"):
        raw_insert(conn, c.id, 3, "advanced", "Rejected", "Applied")


def test_raw_insert_must_start_from_the_current_stage(conn):
    c = make(conn, "A", 5, advances=[4])  # in Screening
    with pytest.raises(sqlite3.IntegrityError, match="current stage"):
        raw_insert(conn, c.id, 3, "advanced", "Applied", "Screening")


def test_raw_insert_cannot_leave_a_gap_or_rewrite_a_seq(conn):
    c = make(conn, "A", 5)
    with pytest.raises(sqlite3.IntegrityError, match="seq"):
        raw_insert(conn, c.id, 3, "advanced", "Applied", "Screening")
    with pytest.raises(sqlite3.IntegrityError, match="seq"):
        raw_insert(conn, c.id, 1, "applied", None, "Applied")


def test_raw_insert_cannot_backdate(conn):
    c = make(conn, "A", 5)
    with pytest.raises(sqlite3.IntegrityError, match="before the previous"):
        raw_insert(conn, c.id, 2, "advanced", "Applied", "Screening", at=ago(6))


def test_pipeline_rules_table_is_fixed(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE allowed_transitions SET to_stage = 'Hired'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM allowed_transitions")
