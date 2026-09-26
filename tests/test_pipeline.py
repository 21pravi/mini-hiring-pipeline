import pytest

from app import pipeline
from app.pipeline import Conflict, InvalidInput, InvalidTransition
from app.stages import ACTIVE

from .conftest import NOW, ago, make


def test_new_candidate_starts_in_applied_with_one_event(conn):
    c = pipeline.add_candidate(conn, "  Priya   Sharma ", "Priya@Example.com", now=NOW)
    assert (c.name, c.email, c.stage) == ("Priya Sharma", "priya@example.com", "Applied")
    assert [(e.action, e.from_stage, e.to_stage) for e in c.events] == [("applied", None, "Applied")]


def test_advance_moves_exactly_one_stage_all_the_way_to_hired(conn):
    c = make(conn, "A", 10)
    seen = [c.stage]
    for _ in range(4):
        c = pipeline.transition(conn, c.id, "advance", c.stage, now=NOW)
        seen.append(c.stage)
    assert seen == ["Applied", "Screening", "Interview", "Offer", "Hired"]
    assert [e.seq for e in c.events] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("stage", ACTIVE)
def test_can_reject_from_any_active_stage(conn, stage):
    c = make(conn, "A", 10)
    while c.stage != stage:
        c = pipeline.transition(conn, c.id, "advance", c.stage, now=NOW)
    c = pipeline.transition(conn, c.id, "reject", stage, note="no", now=NOW)
    assert c.stage == "Rejected"
    assert (c.events[-1].from_stage, c.events[-1].note) == (stage, "no")


@pytest.mark.parametrize("action", ["advance", "reject"])
def test_final_outcomes_cannot_be_changed(conn, action):
    hired = make(conn, "H", 10, advances=[9, 8, 7, 6])
    rejected = make(conn, "R", 10, reject=9)
    for c in (hired, rejected):
        with pytest.raises(InvalidTransition):
            pipeline.transition(conn, c.id, action, c.stage, now=NOW)
        assert len(pipeline.load_one(conn, c.id).events) == len(c.events)


def test_stale_expected_stage_is_refused_and_nothing_is_recorded(conn):
    c = make(conn, "A", 10, advances=[5])  # now in Screening
    with pytest.raises(Conflict):
        pipeline.transition(conn, c.id, "advance", "Applied", now=NOW)
    assert pipeline.load_one(conn, c.id).stage == "Screening"


def test_double_click_cannot_skip_a_stage(conn):
    c = make(conn, "A", 10)
    pipeline.transition(conn, c.id, "advance", "Applied", now=NOW)      # first click
    with pytest.raises(Conflict):
        pipeline.transition(conn, c.id, "advance", "Applied", now=NOW)  # second click, same view
    assert pipeline.load_one(conn, c.id).stage == "Screening"


def test_unknown_action_is_rejected(conn):
    c = make(conn, "A", 1)
    with pytest.raises(InvalidInput):
        pipeline.transition(conn, c.id, "hire", "Applied", now=NOW)


def test_history_durations_and_time_in_stage(conn):
    c = make(conn, "A", 10, advances=[7, 2])
    d = pipeline.detail(c, NOW)
    assert [h["to_stage"] for h in d["history"]] == ["Applied", "Screening", "Interview"]
    assert [h["duration_seconds"] // 86400 for h in d["history"]] == [3, 5, 2]
    assert [h["current"] for h in d["history"]] == [False, False, True]
    assert d["time_in_stage_seconds"] == 2 * 86400
    assert d["next_stage"] == "Offer"


def test_clock_going_backwards_does_not_break_the_chain(conn):
    c = make(conn, "A", 1)
    c = pipeline.transition(conn, c.id, "advance", "Applied", now=ago(3))  # earlier than applied
    assert c.events[-1].at == c.events[-2].at


@pytest.mark.parametrize("name,email", [("", None), ("   ", None), ("x" * 101, None), ("A", "not-an-email")])
def test_invalid_candidate_input(conn, name, email):
    with pytest.raises(InvalidInput):
        pipeline.add_candidate(conn, name, email, now=NOW)


def test_duplicate_email_is_a_conflict(conn):
    pipeline.add_candidate(conn, "A", "a@example.com", now=NOW)
    with pytest.raises(Conflict):
        pipeline.add_candidate(conn, "B", "A@example.com", now=NOW)
