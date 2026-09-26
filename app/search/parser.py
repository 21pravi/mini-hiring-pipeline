"""The search box language.

    priya sharam                      names, typo-tolerant
    in:interview                      current stage (aliases: stage:, is:)
    in:screening for:>1w              time in current stage
    moved:interview since:monday      entered a stage within a window (alias: reached:)
    reached:offer is:rejected         got to Offer, then rejected
    -is:rejected                      '-' negates any filter

Terms are ANDed. Commas OR values inside one filter: in:screening,interview.

Every failure raises QueryError with a message, a hint, and the character span of the
offending token, so the UI can underline exactly what didn't make sense.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo

from ..stages import ACTIVE, ALL_STAGES, PROGRESSION, REJECTED, could_have_reached, must_have_reached
from .fuzzy import closest, fold, words


class QueryError(Exception):
    def __init__(self, message: str, hint: str | None = None, span: tuple[int, int] | None = None,
                 replacement: str | None = None):
        super().__init__(message)
        self.message, self.hint, self.span = message, hint, span
        self.replacement = replacement   # what the bad token probably should have been
        self.fix: str | None = None      # the whole query with that replacement applied

    def to_dict(self) -> dict:
        return {"message": self.message, "hint": self.hint,
                "span": list(self.span) if self.span else None, "fix": self.fix}


# ---------------- vocabulary ----------------

KEYS = {
    "stage": "stage", "in": "stage", "is": "stage", "status": "stage",
    "reached": "reached", "moved": "reached", "entered": "reached",
    "for": "for",
    "since": "since", "after": "since",
    "before": "before",
    "name": "name",
}

STAGE_WORDS: dict[str, tuple[str, ...]] = {
    "applied": ("Applied",), "screening": ("Screening",), "interview": ("Interview",),
    "offer": ("Offer",), "hired": ("Hired",), "rejected": (REJECTED,),
    "interviewing": ("Interview",), "offered": ("Offer",),
    "active": ACTIVE, "open": ACTIVE, "closed": ("Hired", REJECTED),
}
GROUP_WORDS = {"active", "open", "closed"}

UNITS = {
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
}
WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_DURATION = re.compile(r"^(>=|<=|>|<)?(\d+(?:\.\d+)?)([a-z]+)$")
_RELATIVE = re.compile(r"^(\d+(?:\.\d+)?)([a-z]+)$")
# Words people type around a name ("find priya sharma"). They count if they match a name,
# and are skipped if they don't, so a sentence-shaped query still finds the person.
FILLER = frozenset("""find search show list get me all who whos s is are in the a an right now
currently please candidate candidates people person named called with name for of""".split())
OP_WORDS = {">": "more than", ">=": "at least", "<": "less than", "<=": "at most"}


# ---------------- AST ----------------

@dataclass(frozen=True)
class Clause:
    kind: str                                  # text | stage | reached | for
    negate: bool = False
    span: tuple[int, int] = (0, 0)
    stages: frozenset[str] = frozenset()
    terms: tuple[str, ...] = ()                # text: name words that must match
    optional: tuple[str, ...] = ()             # text: filler words ('find', 'who') that may be ignored
    emails: tuple[str, ...] = ()               # text: anything containing '@'
    op: str | None = None                      # for: > >= < <=
    seconds: float | None = None
    since: datetime | None = None              # reached: window start (inclusive)
    before: datetime | None = None             # reached: window end (exclusive)


@dataclass
class ParsedQuery:
    raw: str
    clauses: list[Clause]
    now: datetime
    tz: tzinfo
    notes: list[str] = field(default_factory=list)   # how ambiguous bits were read

    @property
    def is_empty(self) -> bool:
        return not self.clauses

    def describe(self, clause: Clause) -> str:
        return describe(clause, self.tz)


# ---------------- tokenizer ----------------

@dataclass
class Token:
    start: int
    end: int
    negate: bool
    key: str | None
    value: str


def tokenize(q: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(q)
    while i < n:
        if q[i].isspace():
            i += 1
            continue
        start, negate = i, False
        if q[i] == "-" and i + 1 < n and not q[i + 1].isspace():
            negate, i = True, i + 1
        key = None
        m = re.match(r"([A-Za-z_]+):", q[i:])
        if m:
            key, i = m.group(1).lower(), i + m.end()
        if i < n and q[i] == '"':
            j = q.find('"', i + 1)
            if j == -1:
                raise QueryError("There's an opening quote with no closing quote.",
                                 'Close it, e.g. "priya sharma".', (i, n))
            value, i = q[i + 1:j], j + 1
        else:
            j = i
            while j < n and not q[j].isspace():
                j += 1
            value, i = q[i:j], j
        tokens.append(Token(start, i, negate, key, value))

    # Be forgiving about 'in: interview' (a space after the colon).
    merged: list[Token] = []
    for tok in tokens:
        prev = merged[-1] if merged else None
        if prev and prev.key and prev.value == "" and tok.key is None and not tok.negate:
            prev.value, prev.end = tok.value, tok.end
            continue
        merged.append(tok)
    return merged


# ---------------- value parsers ----------------

def _stage_set(tok: Token, allow_groups: bool) -> frozenset[str]:
    vocab = {w: s for w, s in STAGE_WORDS.items() if allow_groups or w not in GROUP_WORDS}
    names = "applied, screening, interview, offer, hired, rejected"
    if allow_groups:
        names += " (or active, closed)"
    out: set[str] = set()
    for part in tok.value.split(","):
        w = fold(part.strip())
        if not w:
            raise QueryError(f"“{tok.key}:{tok.value}” has an empty stage in it.",
                             "Separate stages with commas and no gaps, e.g. in:screening,interview.",
                             (tok.start, tok.end))
        if w in vocab:
            out.update(vocab[w])
            continue
        prefixed = {k: vocab[k] for k in vocab if k.startswith(w)}
        if len(set(prefixed.values())) == 1:
            out.update(next(iter(prefixed.values())))
            continue
        if prefixed:
            raise QueryError(f"“{part}” could be {', '.join(sorted(prefixed))}.",
                             "Type a little more of the stage name.", (tok.start, tok.end))
        if not allow_groups and w in GROUP_WORDS:
            raise QueryError(f"{tok.key}: needs a specific stage, not “{w}”.",
                             f"Try {tok.key}:offer or {tok.key}:interview.", (tok.start, tok.end))
        guess = closest(w, vocab)
        hint = f"Did you mean {tok.key}:{guess}? " if guess else ""
        fixed = None
        if guess:
            parts = [guess if p.strip() == part.strip() else p for p in tok.value.split(",")]
            fixed = f"{'-' if tok.negate else ''}{tok.key}:{','.join(parts)}"
        raise QueryError(f"“{part}” isn't a stage.", f"{hint}Stages are {names}.", (tok.start, tok.end), fixed)
    return frozenset(out)


def _duration(tok: Token) -> tuple[str, float]:
    m = _DURATION.match(tok.value.replace(" ", "").lower())
    if not m or m.group(3) not in UNITS:
        raise QueryError(f"“{tok.value}” isn't a length of time.",
                         "Use a number and a unit, e.g. for:>1w, for:>=10d, for:<36h.",
                         (tok.start, tok.end))
    op = m.group(1) or ">="
    return op, float(m.group(2)) * UNITS[m.group(3)]


def _midnight(d: date, tz: tzinfo) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=tz)


def parse_point_in_time(tok: Token, now: datetime, tz: tzinfo) -> datetime:
    v = tok.value.strip().lower()
    today = now.astimezone(tz).date()
    if v == "today":
        return _midnight(today, tz)
    if v == "yesterday":
        return _midnight(today - timedelta(days=1), tz)
    if v in WEEKDAYS:  # the most recent such day, today included
        return _midnight(today - timedelta(days=(today.weekday() - WEEKDAYS[v]) % 7), tz)
    m = _RELATIVE.match(v)
    if m and m.group(2) in UNITS:
        return now - timedelta(seconds=float(m.group(1)) * UNITS[m.group(2)])
    try:
        return _midnight(date.fromisoformat(v), tz)
    except ValueError:
        pass
    raise QueryError(f"“{tok.value}” isn't a date I can read.",
                     "Try a weekday (monday), today, yesterday, 3d (three days ago), or 2026-09-21.",
                     (tok.start, tok.end))


# ---------------- parser ----------------

def parse(q: str, now: datetime, tz: tzinfo) -> ParsedQuery:
    try:
        return _parse(q, now, tz)
    except QueryError as exc:
        if exc.replacement and exc.span:
            exc.fix = q[:exc.span[0]] + exc.replacement + q[exc.span[1]:]
        raise


def _parse(q: str, now: datetime, tz: tzinfo) -> ParsedQuery:
    clauses: list[Clause] = []
    terms: list[str] = []
    emails: list[str] = []
    text_span: list[int] = []
    last_reached_idx: int | None = None
    fors = 0

    for tok in tokenize(q):
        span = (tok.start, tok.end)
        if tok.key is None or tok.key == "name":
            if tok.negate:
                raise QueryError("“-” only works in front of a filter.",
                                 "To leave people out, negate a filter, e.g. -is:rejected.", span)
            if "@" in tok.value:
                emails.append(tok.value.strip().lower())
            else:
                terms.extend(words(tok.value))
            text_span = [min(text_span[0], tok.start) if text_span else tok.start, tok.end]
            continue

        key = KEYS.get(tok.key)
        if key is None:
            guess = closest(tok.key, KEYS)
            hint = f"Did you mean {guess}:? " if guess else ""
            fixed = f"{'-' if tok.negate else ''}{guess}:{tok.value}" if guess else None
            raise QueryError(f"“{tok.key}:” isn't a filter.",
                             f"{hint}Filters are in:, moved:, reached:, for:, since:, before:.", span, fixed)
        if tok.value.strip() == "":
            example = {"stage": "in:interview", "reached": "moved:interview", "for": "for:>1w",
                       "since": "since:monday", "before": "before:2026-09-01"}[key]
            raise QueryError(f"“{tok.key}:” needs a value.", f"For example {example}.", span)

        if key == "stage":
            clauses.append(Clause("stage", tok.negate, span, stages=_stage_set(tok, True)))
        elif key == "reached":
            clauses.append(Clause("reached", tok.negate, span, stages=_stage_set(tok, False)))
            last_reached_idx = len(clauses) - 1
        elif key == "for":
            op, secs = _duration(tok)
            clauses.append(Clause("for", tok.negate, span, op=op, seconds=secs))
            fors += 1
        else:  # since / before modify the moved:/reached: filter right before them
            if last_reached_idx is None:
                raise QueryError(f"{tok.key}: needs to follow a moved: filter so it knows which move to date.",
                                 f"For example moved:interview {tok.key}:{tok.value}.", span)
            if tok.negate:
                other = "before" if key == "since" else "since"
                raise QueryError(f"“-{tok.key}:” isn't supported.", f"Use {other}:{tok.value} instead.", span)
            when = parse_point_in_time(tok, now, tz)
            target = clauses[last_reached_idx]
            if key == "since":
                if target.since is not None:
                    raise QueryError("That move already has a since: date.", "Keep one since: per moved: filter.", span)
                if when > now:
                    raise QueryError(f"since:{tok.value} is in the future ({_fmt(when, tz)}).",
                                     "Nothing can have moved after now.", span)
                target = replace(target, since=when)
            else:
                if target.before is not None:
                    raise QueryError("That move already has a before: date.", "Keep one before: per moved: filter.", span)
                target = replace(target, before=when)
            if target.since and target.before and target.since >= target.before:
                raise QueryError(f"The window is empty: since {_fmt(target.since, tz)} "
                                 f"but before {_fmt(target.before, tz)}.",
                                 "Put the since: date earlier than the before: date.", span)
            clauses[last_reached_idx] = replace(target, span=(target.span[0], tok.end))

    notes: list[str] = []
    if terms or emails:
        required = [t for t in terms if t not in FILLER]
        optional = [t for t in terms if t in FILLER]
        if not required and not emails:          # 'show' on its own is still a name search
            required, optional = terms, []
        if optional:
            notes.append(f"Not requiring {', '.join(f'“{w}”' for w in optional)} to be in the name.")
        clauses.insert(0, Clause("text", False, (text_span[0], text_span[1]),
                                 terms=tuple(required), optional=tuple(optional), emails=tuple(emails)))

    parsed = ParsedQuery(q, clauses, now, tz, notes)
    _check_contradictions(parsed)
    return parsed


# ---------------- semantic checks ----------------

def _check_contradictions(p: ParsedQuery) -> None:
    """Catch queries that can never match anyone, and say why, instead of returning nothing."""
    allowed, excluded, included = set(ALL_STAGES), set(), False
    for c in p.clauses:
        if c.kind != "stage":
            continue
        if c.negate:
            excluded |= c.stages
            if not allowed - c.stages:
                if included:
                    raise QueryError(f"{_or(allowed)} is both included and excluded.",
                                     "Keep either the filter or its - version, not both.", c.span)
                raise QueryError("Together, these filters exclude every stage.",
                                 "Remove one of the - filters.", c.span)
            allowed -= c.stages
            continue
        included = True
        if not allowed & c.stages:
            if c.stages <= excluded:
                raise QueryError(f"{_or(c.stages)} is both included and excluded.",
                                 "Keep either the filter or its - version, not both.", c.span)
            raise QueryError(f"Someone can only be in one stage at a time, so nobody is in "
                             f"{_or(c.stages)} and {_or(allowed)} together.",
                             "To match either stage, use a comma: in:screening,interview.", c.span)
        allowed &= c.stages

    who = "anyone" if allowed == set(ALL_STAGES) else f"anyone in {_or(allowed)}"
    for c in p.clauses:
        if c.kind != "reached":
            continue
        if not c.negate and not any(could_have_reached(s, r) for s in allowed for r in c.stages):
            raise QueryError(f"Nobody in {_or(allowed)} can have reached {_or(c.stages)}.",
                             "Stages only move forward; check the order of the filters.", c.span)
        if c.negate and c.since is None and c.before is None and \
                all(any(must_have_reached(s, r) for r in c.stages) for s in allowed):
            raise QueryError(f"This excludes everyone: {who} has already been through {_or(c.stages)}.",
                             "Remove the - to find people who did reach it.", c.span)

    lo, hi = 0.0, float("inf")
    for c in p.clauses:
        if c.kind == "for" and not c.negate:
            if c.op in (">", ">="):
                lo = max(lo, c.seconds)
            else:
                hi = min(hi, c.seconds)
            if lo > hi or (lo == hi and c.op in (">", "<")):
                raise QueryError("These for: filters leave no possible length of time.",
                                 "Make the lower bound smaller than the upper one, e.g. for:>2d for:<1w.",
                                 c.span)


# ---------------- descriptions ----------------

def _or(stages) -> str:
    ordered = [s for s in ALL_STAGES if s in stages]
    if set(ordered) == set(ACTIVE):
        return "an active stage"
    return " or ".join(ordered)


def _fmt(dt: datetime, tz: tzinfo) -> str:
    local = dt.astimezone(tz)
    day = f"{local:%a} {local.day} {local:%b}"
    return day if (local.hour, local.minute) == (0, 0) else f"{day}, {local:%H:%M}"


def humanize_seconds(secs: float) -> str:
    if secs % 604800 == 0:
        n, unit = secs / 604800, "week"
    elif secs % 86400 == 0:
        n, unit = secs / 86400, "day"
    else:
        n, unit = secs / 3600, "hour"
    n_str = f"{n:g}"
    return f"{n_str} {unit}{'' if n == 1 else 's'}"


def describe(c: Clause, tz: tzinfo) -> str:
    if c.kind == "text":
        parts = []
        if c.terms:
            parts.append(f"Name is like “{' '.join(c.terms)}”")
        if c.emails:
            parts.append(f"Email starts with {', '.join(c.emails)}")
        return "; ".join(parts)
    if c.kind == "stage":
        if set(c.stages) == set(ACTIVE):
            return "Not in the pipeline anymore" if c.negate else "Still in the pipeline"
        return f"Current stage is {'not ' if c.negate else ''}{_or(c.stages)}"
    if c.kind == "for":
        op = {">": "<=", ">=": "<", "<": ">=", "<=": ">"}[c.op] if c.negate else c.op
        return f"In current stage for {OP_WORDS[op]} {humanize_seconds(c.seconds)}"
    # reached
    windowed = c.since is not None or c.before is not None
    text = f"{_or(c.stages)}"
    if c.since:
        text += f" since {_fmt(c.since, tz)}"
    if c.before:
        text += f" before {_fmt(c.before, tz)}"
    if windowed:
        return f"Didn't move to {text}" if c.negate else f"Moved to {text}"
    return f"Never reached {text}" if c.negate else f"Reached {text}"
