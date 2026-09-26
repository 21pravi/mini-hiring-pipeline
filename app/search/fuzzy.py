"""Typo-tolerant name matching.

Names are data we've never seen, so they get fuzzy matching. The query vocabulary (stage
names, filter keys) is small and fixed, so it gets strict matching with "did you mean"
suggestions instead; see parser.py.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

_WORD = re.compile(r"[^\W_]+")


def fold(text: str) -> str:
    """Lowercase and strip accents so 'José' matches 'jose'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def words(text: str) -> list[str]:
    return _WORD.findall(fold(text))


def name_tokens(name: str) -> list[tuple[str, str]]:
    """[(normalised token, the word as written)] e.g. 'Anne-Marie' -> [('anne','Anne-Marie'), ('marie', ...)]"""
    out = []
    for written in name.split():
        for tok in words(written):
            out.append((tok, written))
    return out


def osa_distance(a: str, b: str, limit: int | None = None) -> int:
    """Optimal string alignment distance: Levenshtein plus adjacent transpositions.

    Transpositions matter here: 'sharam' -> 'sharma' is one swap, which plain
    Levenshtein would count as two edits.
    """
    if limit is not None and abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[len(b)]


def allowed_typos(length: int) -> int:
    """Short terms must be exact: 'al' shouldn't match every two-letter name."""
    if length <= 2:
        return 0
    if length <= 5:
        return 1
    return 2


@dataclass(frozen=True)
class TermMatch:
    score: float          # 0 means no match
    kind: str             # exact | prefix | typo | typo-prefix | none
    typos: int = 0


NO_MATCH = TermMatch(0.0, "none")


def match_term(term: str, token: str) -> TermMatch:
    """Score one query term against one name token. Exact > prefix > typo > typo in a prefix."""
    if term == token:
        return TermMatch(1.0, "exact")
    if token.startswith(term):
        # 'priya' vs 'priyanka' is a weaker match than 'sharm' vs 'sharma'.
        return TermMatch(0.7 + 0.2 * len(term) / len(token), "prefix")
    k = allowed_typos(len(term))
    if k == 0:
        return NO_MATCH
    d = osa_distance(term, token, k)
    if d <= k:
        return TermMatch(0.75 if d == 1 else 0.55, "typo", d)
    if len(token) > len(term) >= 4:  # a typo in a name that's still being typed: 'priyq' -> 'priyanka'
        d = osa_distance(term, token[: len(term)], k)
        if d <= k:
            return TermMatch(0.5 if d == 1 else 0.4, "typo-prefix", d)
    return NO_MATCH


def closest(word: str, options: Iterable[str], max_distance: int = 2) -> str | None:
    """Best 'did you mean' suggestion from a fixed vocabulary, or None."""
    best, best_d = None, max_distance + 1
    for opt in options:
        d = osa_distance(word, opt, max_distance)
        if d < best_d or (d == best_d and best is not None and len(opt) < len(best)):
            best, best_d = opt, d
    if best is None or best_d > max_distance or best_d >= len(word):
        return None
    return best
