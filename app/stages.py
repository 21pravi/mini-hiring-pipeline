"""The pipeline's vocabulary and state machine. Everything else derives from this file."""
from __future__ import annotations

PROGRESSION: tuple[str, ...] = ("Applied", "Screening", "Interview", "Offer", "Hired")
REJECTED = "Rejected"
ALL_STAGES: tuple[str, ...] = PROGRESSION + (REJECTED,)
ACTIVE: tuple[str, ...] = PROGRESSION[:-1]          # still in play
TERMINAL: frozenset[str] = frozenset({"Hired", REJECTED})

# Every legal (from_stage, action, to_stage). The DB insert trigger checks against these
# same rows, so the rule lives in one place and is enforced twice.
TRANSITIONS: tuple[tuple[str | None, str, str], ...] = (
    ((None, "applied", "Applied"),)
    + tuple((a, "advanced", b) for a, b in zip(PROGRESSION, PROGRESSION[1:]))
    + tuple((s, "rejected", REJECTED) for s in ACTIVE)
)


def next_stage(stage: str) -> str | None:
    if stage in TERMINAL:
        return None
    return PROGRESSION[PROGRESSION.index(stage) + 1]


def could_have_reached(current: str, target: str) -> bool:
    """Is it *possible* for someone now in `current` to have passed through `target`?"""
    if target == "Applied":
        return True
    if target == REJECTED:
        return current == REJECTED
    if current == REJECTED:
        return target != "Hired"
    return PROGRESSION.index(current) >= PROGRESSION.index(target)


def must_have_reached(current: str, target: str) -> bool:
    """Is it *certain* that someone now in `current` passed through `target`?"""
    if target == "Applied" or current == target:
        return True
    if current in PROGRESSION and target in PROGRESSION:
        return PROGRESSION.index(current) >= PROGRESSION.index(target)
    return False
