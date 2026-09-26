from zoneinfo import ZoneInfo

import pytest

from app import pipeline
from app.search.engine import run
from app.search.parser import parse

from .conftest import NOW, make

IST = ZoneInfo("Asia/Kolkata")
# NOW is Sat 26 Sep 12:00 UTC; "since:monday" in IST is Mon 21 Sep 00:00 IST (Sun 20 Sep 18:30 UTC).


@pytest.fixture
def people(conn):
    make(conn, "Priya Sharma", 12, advances=[9, 2])                 # Interview, moved Thu
    make(conn, "Priyanka Sharma", 15, advances=[11])                # Screening 11d
    make(conn, "Pooja Sharma", 3)                                   # Applied
    make(conn, "Priya Verma", 20, advances=[16, 10, 5])             # Offer
    make(conn, "Arjun Mehta", 30, advances=[25, 18, 12, 6])         # Hired
    make(conn, "Neha Kapoor", 28, advances=[24, 17, 9], reject=4)   # reached Offer, rejected
    make(conn, "Rahul Nair", 22, advances=[19], reject=14)          # rejected at Screening
    make(conn, "Ananya Iyer", 10, advances=[8])                     # Screening 8d
    make(conn, "Karthik Reddy", 9, advances=[4])                    # Screening 4d
    make(conn, "Vikram Rao", 12, advances=[11, 7])                  # Interview, moved before Monday
    make(conn, "Nikhil Sharma", 9, advances=[8, 3], reject=1)       # moved to Interview Wed, then rejected
    make(conn, "José Álvarez", 6, advances=[4.5])                   # Screening
    return pipeline.load_all(conn)


def names(people, q):
    out = run(parse(q, NOW, IST), people)
    return [h.candidate.name for h in out.hits]


# ---- the brief's questions ----

def test_find_priya_sharma_with_a_typo(people):
    assert "Priya Sharma" in names(people, "sharam")
    assert names(people, "priya sharam")[0] == "Priya Sharma"
    assert names(people, "Find Priya Sharma")[0] == "Priya Sharma"


def test_whos_in_interview_right_now(people):
    assert set(names(people, "in:interview")) == {"Priya Sharma", "Vikram Rao"}


def test_stuck_in_screening_for_more_than_a_week_longest_first(people):
    assert names(people, "in:screening for:>1w") == ["Priyanka Sharma", "Ananya Iyer"]


def test_moved_to_interview_since_monday(people):
    # Nikhil moved to Interview on Wednesday and was later rejected; he still moved since Monday.
    assert set(names(people, "moved:interview since:monday")) == {"Priya Sharma", "Nikhil Sharma"}


def test_reached_offer_but_not_hired(people):
    assert names(people, "reached:offer is:rejected") == ["Neha Kapoor"]
    # The looser reading also counts offers still pending.
    assert set(names(people, "reached:offer -is:hired")) == {"Neha Kapoor", "Priya Verma"}


def test_everyone_except_rejected(people):
    got = set(names(people, "-is:rejected"))
    assert "Neha Kapoor" not in got and "Rahul Nair" not in got and len(got) == len(people) - 3


def test_combining_text_and_filters(people):
    # Equal name scores, so the one waiting longest in their stage comes first.
    assert names(people, "sharma -is:rejected in:screening,interview") == ["Priyanka Sharma", "Priya Sharma"]
    assert names(people, "sharam is:active for:>1w") == ["Priyanka Sharma"]


# ---- ranking ----

def test_exact_beats_prefix_beats_typo(people):
    out = run(parse("priya", NOW, IST), people)
    scores = {h.candidate.name: h.score for h in out.hits}
    assert scores["Priya Sharma"] > scores["Priyanka Sharma"]
    assert names(people, "priya") == ["Priya Verma", "Priya Sharma", "Priyanka Sharma"]


def test_accents_are_ignored(people):
    assert names(people, "jose alvarez") == ["José Álvarez"]


def test_short_terms_are_not_fuzzy(people):
    assert names(people, "xa") == []


def test_reasons_explain_each_hit(people):
    hit = run(parse("sharam in:screening for:>1w", NOW, IST), people).hits[0]
    assert any("typo" in r for r in hit.reasons) and any("In Screening for" in r for r in hit.reasons)


# ---- empty results are explained ----

def test_sentence_shaped_query_gets_a_suggestion(people):
    out = run(parse("who's in interview right now", NOW, IST), people)
    assert out.hits == [] and "in:interview" in out.empty["suggestions"]
    assert "plain English" in out.empty["message"]


def test_unknown_name_suggests_nearest_names(people):
    out = run(parse("zzqx", NOW, IST), people)
    assert "close to" in out.empty["message"] and len(out.empty["suggestions"]) == 3


def test_empty_combination_says_which_part_matched_nobody(people):
    out = run(parse("in:offer for:>3w", NOW, IST), people)
    assert "more than 3 weeks" in out.empty["message"]
    assert [b["matches"] for b in out.empty["breakdown"]] == [1, 0]


def test_empty_when_each_part_matches_someone(people):
    out = run(parse("in:applied for:>1w", NOW, IST), people)
    assert "no one matches all of them together" in out.empty["message"]


def test_empty_query_returns_everyone(people):
    assert len(names(people, "")) == len(people)
