"""Load demo candidates with realistic, back-dated histories.

    python -m scripts.seed           # add demo data to the current database
    python -m scripts.seed --reset   # start from a fresh database file first

--reset replaces the file rather than deleting rows: the triggers forbid deletes,
and the seed script gets no special exemption.
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from app import db, pipeline
from app.clock import utcnow

# name, email, applied (days ago), each advance (days ago), rejection (days ago, reason)
PEOPLE = [
    ("Priya Sharma", "priya.sharma@example.com", 12, [9, 2], None),
    ("Priyanka Sharma", "priyanka.s@example.com", 15, [11], None),
    ("Pooja Sharma", "pooja.sharma@example.com", 3, [], None),
    ("Priya Verma", "priya.verma@example.com", 20, [16, 10, 5], None),
    ("Arjun Mehta", "arjun.mehta@example.com", 30, [25, 18, 12, 6], None),
    ("Neha Kapoor", "neha.kapoor@example.com", 28, [24, 17, 9], (4, "Declined the offer; accepted a counter-offer")),
    ("Rahul Nair", "rahul.nair@example.com", 22, [19], (14, "SQL depth below the bar")),
    ("Ananya Iyer", "ananya.iyer@example.com", 10, [8], None),
    ("Vikram Rao", "vikram.rao@example.com", 6, [5, 0.3], None),
    ("Sneha Patel", "sneha.patel@example.com", 14, [13, 7, 1], None),
    ("Karthik Reddy", "karthik.reddy@example.com", 9, [4], None),
    ("Meera Das", "meera.das@example.com", 18, [16, 12, 7], (2, "Offer withdrawn after the headcount freeze")),
    ("Aditya Mohanty", "aditya.mohanty@example.com", 5, [], None),
    ("Fatima Khan", "fatima.khan@example.com", 25, [21, 15], (11, "Panel said no on system design")),
    ("Rohan Gupta", "rohan.gupta@example.com", 2, [], None),
    ("Ishita Banerjee", "ishita.b@example.com", 16, [14], None),
    ("Siddharth Joshi", "sid.joshi@example.com", 11, [9, 3], None),
    ("Lakshmi Menon", "lakshmi.menon@example.com", 7, [6, 1], None),
    ("Daniel Fernandes", "daniel.f@example.com", 19, [], (17, "Needs visa sponsorship; role can't sponsor")),
    ("Zoya Ahmed", "zoya.ahmed@example.com", 4, [1], None),
    ("Aman Verma", "aman.verma@example.com", 13, [10, 6, 3, 0.5], None),
    ("Tanvi Kulkarni", "tanvi.k@example.com", 1, [], None),
    ("Harsh Agarwal", "harsh.agarwal@example.com", 8, [7.5], None),
    ("Nikhil Sharma", "nikhil.sharma@example.com", 9, [8, 5], (1, "Chose to stay with current team")),
    ("José Álvarez", "jose.alvarez@example.com", 6, [4.5], None),
]


def seed(conn) -> int:
    now = utcnow()
    ago = lambda days: now - timedelta(days=days)  # noqa: E731
    added = 0
    for name, email, applied, advances, rejection in PEOPLE:
        if conn.execute("SELECT 1 FROM candidates WHERE email = ?", (email,)).fetchone():
            continue
        c = pipeline.add_candidate(conn, name, email, now=ago(applied))
        for days in advances:
            c = pipeline.transition(conn, c.id, "advance", c.stage, now=ago(days))
        if rejection:
            days, why = rejection
            pipeline.transition(conn, c.id, "reject", c.stage, note=why, now=ago(days))
        added += 1
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset", action="store_true", help="replace the database file before seeding")
    args = ap.parse_args()
    path = db.resolve_path()
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            p = path.with_name(path.name + suffix)
            if p.exists():
                p.unlink()
    conn = db.connect(path)
    db.init_db(conn)
    print(f"Added {seed(conn)} candidates to {path}")


if __name__ == "__main__":
    main()
