from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.search.parser import QueryError, describe, parse, tokenize

from .conftest import NOW  # Saturday 26 Sep 2026 12:00 UTC

IST = ZoneInfo("Asia/Kolkata")


def p(q, tz=IST, now=NOW):
    return parse(q, now, tz)


def err(q):
    with pytest.raises(QueryError) as e:
        p(q)
    return e.value


# ---- structure ----

def test_free_text_becomes_one_text_clause():
    (c,) = p("Priya  SHARAM").clauses
    assert (c.kind, c.terms) == ("text", ("priya", "sharam"))


def test_filler_words_are_optional():
    q = p("find priya sharma")
    assert q.clauses[0].terms == ("priya", "sharma") and q.clauses[0].optional == ("find",)
    assert "find" in q.notes[0]


@pytest.mark.parametrize("q", ["stage:interview", "in:interview", "is:interview", "in:Interview",
                               "in:int", "in: interview", 'in:"interview"', "is:interviewing"])
def test_stage_aliases_and_forgiving_input(q):
    (c,) = p(q).clauses
    assert (c.kind, set(c.stages), c.negate) == ("stage", {"Interview"}, False)


def test_comma_means_or_and_groups_expand():
    assert set(p("in:screening,interview").clauses[0].stages) == {"Screening", "Interview"}
    assert set(p("is:active").clauses[0].stages) == {"Applied", "Screening", "Interview", "Offer"}
    assert set(p("is:closed").clauses[0].stages) == {"Hired", "Rejected"}


def test_negation():
    (c,) = p("-is:rejected").clauses
    assert c.negate and set(c.stages) == {"Rejected"}
    assert describe(c, IST) == "Current stage is not Rejected"


@pytest.mark.parametrize("value,op,days", [(">1w", ">", 7), (">=10d", ">=", 10), ("<36h", "<", 1.5),
                                           ("7days", ">=", 7), ("<=2weeks", "<=", 14)])
def test_durations(value, op, days):
    (c,) = p(f"for:{value}").clauses
    assert (c.op, c.seconds) == (op, days * 86400)


def test_since_weekday_is_the_most_recent_one_in_the_recruiters_timezone():
    (c,) = p("moved:interview since:monday").clauses
    assert c.since == datetime(2026, 9, 21, 0, 0, tzinfo=IST)
    assert describe(c, IST) == "Moved to Interview since Mon 21 Sep"


def test_since_today_weekday_means_this_morning():
    (c,) = p("moved:offer since:saturday").clauses
    assert c.since == datetime(2026, 9, 26, 0, 0, tzinfo=IST)


def test_timezone_changes_what_monday_means():
    late_sunday_utc = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)  # already Monday in IST
    ist = parse("moved:offer since:monday", late_sunday_utc, IST).clauses[0].since
    utc = parse("moved:offer since:monday", late_sunday_utc, ZoneInfo("UTC")).clauses[0].since
    assert ist.date().isoformat() == "2026-09-28" and utc.date().isoformat() == "2026-09-21"


@pytest.mark.parametrize("value", ["yesterday", "3d", "2026-09-20", "today"])
def test_other_date_forms(value):
    assert p(f"moved:screening since:{value}").clauses[0].since is not None


def test_before_and_since_make_a_window():
    (c,) = p("moved:screening since:2026-09-01 before:2026-09-15").clauses
    assert c.since < c.before


def test_tokenize_keeps_spans_for_highlighting():
    toks = tokenize('priya -is:rejected "x y"')
    assert [(t.start, t.end) for t in toks] == [(0, 5), (6, 18), (19, 24)]


# ---- errors that explain themselves ----

@pytest.mark.parametrize("q,message,hint", [
    ("stag:interview", "isn't a filter", "Did you mean stage:"),
    ("in:intervew", "isn't a stage", "Did you mean in:interview"),
    ("in:xyz", "isn't a stage", "Stages are"),
    ("in:o", "could be", "Type a little more"),
    ("in:", "needs a value", "in:interview"),
    ("for:7x", "isn't a length of time", "for:>1w"),
    ("since:monday", "needs to follow a moved:", "moved:interview since:monday"),
    ("moved:interview since:someday", "isn't a date", "weekday"),
    ("moved:interview since:2027-01-01", "in the future", None),
    ("moved:interview since:friday before:monday", "window is empty", None),
    ("reached:active", "specific stage", None),
    ("-priya", "only works in front of a filter", "-is:rejected"),
    ('"priya', "no closing quote", None),
])
def test_bad_input_is_explained(q, message, hint):
    e = err(q)
    assert message in e.message
    if hint:
        assert hint in e.hint
    assert e.span is not None


@pytest.mark.parametrize("q,message", [
    ("in:hired in:rejected", "one stage at a time"),
    ("is:rejected -is:rejected", "both included and excluded"),
    ("-is:active -is:closed", "exclude every stage"),
    ("in:applied reached:offer", "Nobody in Applied can have reached Offer"),
    ("in:offer -reached:interview", "excludes everyone"),
    ("-reached:applied", "excludes everyone"),
    ("for:>2w for:<1w", "no possible length of time"),
])
def test_impossible_combinations_are_explained_not_silently_empty(q, message):
    assert message in err(q).message


def test_possible_combinations_are_allowed():
    for q in ["reached:offer is:rejected", "in:rejected reached:interview", "for:>2d for:<1w",
              "-is:rejected in:screening,interview", "moved:interview since:monday -is:hired"]:
        p(q)


@pytest.mark.parametrize("q,fix", [
    ("priya in:intervew", "priya in:interview"),
    ("stag:screening for:>1w", "stage:screening for:>1w"),
    ("-is:rejectd", "-is:rejected"),
    ("in:screening,intervew", "in:screening,interview"),
])
def test_typos_in_the_vocabulary_come_with_a_one_click_fix(q, fix):
    assert err(q).to_dict()["fix"] == fix


def test_no_fix_when_there_is_no_good_guess():
    assert err("in:xyz").to_dict()["fix"] is None
