"""Evaluate a parsed query against candidates, rank the hits, and explain empty results."""
from __future__ import annotations

from dataclasses import dataclass
from ..pipeline import Candidate
from .fuzzy import NO_MATCH, match_term, name_tokens, osa_distance
from .parser import WEEKDAYS, Clause, ParsedQuery, _fmt, describe


@dataclass
class Hit:
    candidate: Candidate
    score: float
    reasons: list[str]


@dataclass
class Outcome:
    hits: list[Hit]
    empty: dict | None


def human_duration(secs: float) -> str:
    if secs < 3600:
        m = int(secs // 60)
        return f"{m} minute{'s' if m != 1 else ''}"
    if secs < 2 * 86400:
        h = int(secs // 3600)
        return f"{h} hour{'s' if h != 1 else ''}"
    d, h = int(secs // 86400), int(secs % 86400 // 3600)
    return f"{d} days, {h} hour{'s' if h != 1 else ''}" if d < 10 and h else f"{d} days"


# ---------------- matching ----------------

def _text_match(c: Clause, cand: Candidate) -> tuple[float, str | None]:
    tokens = name_tokens(cand.name)
    total, counted, notes = 0.0, 0, []
    for term, required in [(t, True) for t in c.terms] + [(t, False) for t in c.optional]:
        best, written = NO_MATCH, ""
        for norm, as_written in tokens:
            m = match_term(term, norm)
            if m.score > best.score:
                best, written = m, as_written
        if best.score == 0:
            if required:
                return 0.0, None
            continue
        total, counted = total + best.score, counted + 1
        if best.kind in ("typo", "typo-prefix"):
            notes.append(f"“{term}” ≈ {written} ({best.typos} typo{'s' if best.typos > 1 else ''})")
    for e in c.emails:
        if not cand.email or not cand.email.startswith(e):
            return 0.0, None
        total, counted = total + 1.0, counted + 1
    if counted == 0:
        return 0.0, None
    score = total / counted
    # A name the query fully accounts for beats one with extra words:
    # 'priya sharma' puts Priya Sharma above Priya Sharma Kapoor.
    score -= 0.01 * max(0, len({t for t, _ in tokens}) - counted)
    return max(score, 0.01), ("Name: " + "; ".join(notes)) if notes else None


def _compare(value: float, op: str, target: float) -> bool:
    return {">": value > target, ">=": value >= target, "<": value < target, "<=": value <= target}[op]


def match_clause(c: Clause, cand: Candidate, p: ParsedQuery) -> tuple[bool, float, str | None]:
    """Returns (matched, score contribution, human reason)."""
    if c.kind == "text":
        score, reason = _text_match(c, cand)
        return score > 0, score, reason

    reason = None
    if c.kind == "stage":
        ok = cand.stage in c.stages
    elif c.kind == "for":
        secs = cand.time_in_stage(p.now).total_seconds()
        ok = _compare(secs, c.op, c.seconds)
        reason = f"In {cand.stage} for {human_duration(secs)}"
    else:  # reached
        hits = [e for e in cand.events if e.to_stage in c.stages
                and (c.since is None or e.at >= c.since) and (c.before is None or e.at < c.before)]
        ok = bool(hits)
        if hits:
            e = hits[-1]
            verb = "Moved to" if (c.since or c.before) else "Reached"
            reason = f"{verb} {e.to_stage} on {_fmt(e.at, p.tz)}"
    if c.negate:
        return (not ok), 1.0, None
    return ok, 1.0, reason


# ---------------- search ----------------

def run(p: ParsedQuery, candidates: list[Candidate]) -> Outcome:
    hits: list[Hit] = []
    for cand in candidates:
        score, reasons, ok = 1.0, [], True
        for c in p.clauses:
            matched, s, reason = match_clause(c, cand, p)
            if not matched:
                ok = False
                break
            if c.kind == "text":
                score = s
            if reason and reason not in reasons:
                reasons.append(reason)
        if ok:
            hits.append(Hit(cand, round(score, 4), reasons))

    # Best name match first; among equals, whoever has waited longest in their stage,
    # since that's who most likely needs the recruiter's attention.
    hits.sort(key=lambda h: (-h.score, -h.candidate.time_in_stage(p.now).total_seconds(),
                             h.candidate.name.lower()))
    empty = None if hits or p.is_empty else explain_empty(p, candidates)
    return Outcome(hits, empty)


# ---------------- explaining empty results ----------------

_HINTS = {
    "applied": "in:applied", "screening": "in:screening", "screen": "in:screening",
    "interview": "in:interview", "interviews": "in:interview", "interviewing": "in:interview",
    "offer": "reached:offer", "offers": "reached:offer", "offered": "reached:offer",
    "hired": "is:hired", "hire": "is:hired", "rejected": "is:rejected", "reject": "is:rejected",
    "stuck": "is:active for:>1w", "waiting": "is:active for:>1w", "week": "for:>1w", "weeks": "for:>1w",
    "moved": "moved:interview since:monday", "since": "moved:interview since:monday",
    "except": "-is:rejected", "excluding": "-is:rejected", "active": "is:active",
}


def _vocab_suggestions(terms: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for t in terms:
        s = _HINTS.get(t) or (f"moved:interview since:{t}" if t in WEEKDAYS else None)
        if s and s not in out:
            out.append(s)
    return out


def _nearest_names(terms: tuple[str, ...], candidates: list[Candidate], k: int = 3) -> list[str]:
    def distance(cand: Candidate) -> float:
        toks = [t for t, _ in name_tokens(cand.name)] or [""]
        return sum(min(osa_distance(term, t) for t in toks) for term in terms) / max(len(terms), 1)
    return [c.name for c in sorted(candidates, key=distance)[:k]]


def explain_empty(p: ParsedQuery, candidates: list[Candidate]) -> dict:
    if not candidates:
        return {"message": "There are no candidates yet. Add one to get started.",
                "breakdown": [], "suggestions": []}

    breakdown = [{"clause": describe(c, p.tz),
                  "matches": sum(match_clause(c, x, p)[0] for x in candidates)} for c in p.clauses]
    text = next((c for c in p.clauses if c.kind == "text"), None)
    suggestions = _vocab_suggestions(text.terms + text.optional) if text else []
    sentence_like = bool(text) and bool(suggestions)

    if len(p.clauses) == 1 and text:
        if sentence_like:
            message = ("That reads like a question in plain English. The search box takes names "
                       "and filters, so none of the names matched. One of these may be what you meant.")
        else:
            words = " ".join(text.terms) or " ".join(text.emails)
            message = f"No one's name is close to “{words}”."
            suggestions = _nearest_names(text.terms, candidates) if text.terms else []
    elif len(p.clauses) == 1:
        message = f"No one matches “{breakdown[0]['clause']}” right now."
    else:
        zero = [b["clause"] for b in breakdown if b["matches"] == 0]
        if zero:
            message = (f"No one matches “{zero[0]}”, so the combination can't match anyone either.")
        else:
            message = "Each part matches someone on its own, but no one matches all of them together."
    return {"message": message, "breakdown": breakdown, "suggestions": suggestions[:4]}
