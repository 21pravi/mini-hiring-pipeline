"""Builds docs/design.pdf, the design and architecture summary.

    pip install reportlab
    python docs/build_design_pdf.py --repo https://github.com/<user>/<repo>
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "screenshots"
DEFAULT_REPO = "https://github.com/21pravi/mini-hiring-pipeline"

INK = colors.HexColor("#16202A")
MUTED = colors.HexColor("#5B6873")
MIST = colors.HexColor("#E8ECEF")
LINE = colors.HexColor("#C9D1D8")
TEAL = colors.HexColor("#0E6B5E")
TEAL_BG = colors.HexColor("#E3F0EC")
AMBER = colors.HexColor("#9A6414")
BRICK = colors.HexColor("#A63D40")
BRICK_BG = colors.HexColor("#F6E7E7")
WHITE = colors.white

# Fonts: DejaVu has the arrows and symbols used below. Fall back to Helvetica/Courier
# (and ASCII stand-ins) if it isn't installed.
DEJAVU = Path("/usr/share/fonts/truetype/dejavu")
if (DEJAVU / "DejaVuSans.ttf").exists():
    pdfmetrics.registerFont(TTFont("Sans", str(DEJAVU / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("Sans-Bold", str(DEJAVU / "DejaVuSans-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("Mono", str(DEJAVU / "DejaVuSansMono.ttf")))
    pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold",
                                  italic="Sans", boldItalic="Sans-Bold")
    SANS, BOLD, MONO, UNICODE = "Sans", "Sans-Bold", "Mono", True
else:
    SANS, BOLD, MONO, UNICODE = "Helvetica", "Helvetica-Bold", "Courier", False


def t(s: str) -> str:
    if UNICODE:
        return s
    for a, b in {"→": "->", "≈": "~", "≥": ">=", "·": "-", "“": '"', "”": '"', "’": "'", "–": "-"}.items():
        s = s.replace(a, b)
    return s


# ---------------------------------------------------------------- styles
def style(name, **kw):
    base = dict(fontName=SANS, fontSize=9.5, leading=13.5, textColor=INK, alignment=TA_LEFT)
    base.update(kw)
    return ParagraphStyle(name, **base)


H1 = style("h1", fontName=BOLD, fontSize=24, leading=28, spaceAfter=4)
SUB = style("sub", fontSize=11.5, leading=15, textColor=MUTED, spaceAfter=10)
H2 = style("h2", fontName=BOLD, fontSize=14, leading=18, spaceBefore=4, spaceAfter=6)
H3 = style("h3", fontName=BOLD, fontSize=10.5, leading=14, spaceBefore=8, spaceAfter=3, textColor=TEAL)
BODY = style("body", spaceAfter=6)
SMALL = style("small", fontSize=8.5, leading=11.5)
SMALL_B = style("smallb", fontName=BOLD, fontSize=8.5, leading=11.5)
CAP = style("cap", fontSize=8, leading=10.5, textColor=MUTED, spaceBefore=3, spaceAfter=8)
CODE = style("code", fontName=MONO, fontSize=8.3, leading=11.5)


def P(text, st=BODY):
    return Paragraph(t(text), st)


def code(s):
    return f'<font name="{MONO}" color="#0E6B5E">{s}</font>'


def table(rows, widths, header=True, style_extra=()):
    data = [[c if not isinstance(c, str) else P(c, SMALL_B if (header and i == 0) else SMALL)
             for c in row] for i, row in enumerate(rows)]
    tb = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
    ]
    if header:
        cmds += [("BACKGROUND", (0, 0), (-1, 0), MIST), ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK)]
    tb.setStyle(TableStyle(cmds + list(style_extra)))
    return tb


def shot(name, width, caption=None, crop=None):
    """crop = (left, top, right, bottom) in screenshot pixels; needs Pillow, else the full image is used."""
    path = SHOTS / name
    if not path.exists():
        return [P(f"[missing screenshot: {name}]", CAP)]
    src = str(path)
    if crop:
        try:
            from PIL import Image as PILImage
            buf = io.BytesIO()
            PILImage.open(path).crop(crop).save(buf, "PNG")
            buf.seek(0)
            src = buf
        except ImportError:
            pass
    img = Image(src)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = width, width * ratio
    frame = Table([[img]], colWidths=[width])
    frame.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, LINE),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                               ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    out = [frame]
    if caption:
        out.append(P(caption, CAP))
    return out


# ---------------------------------------------------------------- diagram helpers
def box(d, x, y, w, h, lines, fill=WHITE, stroke=INK, title_color=INK, dash=None, sw=0.9):
    d.add(Rect(x, y, w, h, rx=4, ry=4, fillColor=fill, strokeColor=stroke, strokeWidth=sw,
               strokeDashArray=dash))
    n = len(lines)
    lead = 10.5
    top = y + h / 2 + (n - 1) * lead / 2 - 3
    for i, s in enumerate(lines):
        first = i == 0
        d.add(String(x + w / 2, top - i * lead, t(s), textAnchor="middle",
                     fontName=BOLD if first else SANS, fontSize=8.8 if first else 7.4,
                     fillColor=title_color if first else MUTED))


def arrow(d, x1, y1, x2, y2, color=INK, dash=None, label=None, lx=0, ly=4, sw=0.9):
    d.add(Line(x1, y1, x2, y2, strokeColor=color, strokeWidth=sw, strokeDashArray=dash))
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    size = 5
    p1 = (x2 - size * math.cos(ang - 0.4), y2 - size * math.sin(ang - 0.4))
    p2 = (x2 - size * math.cos(ang + 0.4), y2 - size * math.sin(ang + 0.4))
    d.add(Polygon([x2, y2, *p1, *p2], fillColor=color, strokeColor=color, strokeWidth=0.5))
    if label:
        d.add(String((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, t(label), textAnchor="middle",
                     fontName=SANS, fontSize=6.8, fillColor=MUTED))


def architecture_diagram(width):
    d = Drawing(width, 215)
    col_x, col_w = 240, 104
    db_x, db_w = width - 110, 110
    box(d, 0, 70, 92, 64, ["Browser", "index.html · app.js", "board · drawer", "search panel"])
    box(d, 118, 70, 92, 64, ["FastAPI", "app/main.py", "JSON + static files", "no PUT/PATCH/DELETE"])
    box(d, col_x, 172, col_w, 36, ["stages.py", "one source of rules"], fill=MIST)
    box(d, col_x, 108, col_w, 50, ["pipeline.py", "add · move · history", "409 on stale stage"],
        fill=TEAL_BG, stroke=TEAL, title_color=TEAL)
    box(d, col_x, 18, col_w, 60, ["search/", "fuzzy · parser", "engine", "rank + explain"],
        fill=TEAL_BG, stroke=TEAL, title_color=TEAL)
    box(d, db_x, 40, db_w, 120, ["SQLite", "candidates", "stage_events", "(append-only)", "allowed_transitions",
                                 "+ triggers that refuse", "edits, deletes, skips"],
        fill=MIST, stroke=INK, sw=1.2)
    mid = col_x + col_w / 2
    arrow(d, 92, 102, 118, 102, label="JSON", ly=4)
    arrow(d, 210, 114, col_x, 132)
    arrow(d, 210, 90, col_x, 56)
    arrow(d, mid, 78, mid, 108, label="reads", lx=15, ly=-3)
    arrow(d, col_x + col_w, 133, db_x, 120, label="events", ly=6)
    arrow(d, mid, 172, mid, 158)
    elbow_x = db_x + db_w / 2
    d.add(Line(col_x + col_w, 190, elbow_x, 190, strokeColor=INK, strokeWidth=0.9, strokeDashArray=(2, 2)))
    arrow(d, elbow_x, 190, elbow_x, 160, dash=(2, 2))
    d.add(String((col_x + col_w + elbow_x) / 2, 194, t("fills allowed_transitions"), textAnchor="middle",
                 fontName=SANS, fontSize=6.8, fillColor=MUTED))
    return d


def state_machine_diagram(width):
    d = Drawing(width, 118)
    names = ["Applied", "Screening", "Interview", "Offer", "Hired"]
    w, gap, y, h = 78, (width - 5 * 78) / 4, 74, 34
    xs = [i * (w + gap) for i in range(5)]
    for i, (x, n) in enumerate(zip(xs, names)):
        terminal = n == "Hired"
        box(d, x, y, w, h, [n] + (["final"] if terminal else []),
            fill=TEAL_BG if terminal else WHITE, stroke=TEAL if terminal else INK,
            title_color=TEAL if terminal else INK, sw=1.4 if terminal else 0.9)
        if i < 4:
            arrow(d, x + w, y + h / 2, xs[i + 1], y + h / 2, color=TEAL)
    rx = width / 2 - 45
    box(d, rx, 2, 90, 32, ["Rejected", "final"], fill=BRICK_BG, stroke=BRICK, title_color=BRICK, sw=1.4)
    for i in range(4):
        cx = xs[i] + w / 2
        tx = rx + 18 + i * 18
        arrow(d, cx, y, tx, 34, color=BRICK, dash=(3, 2))
    d.add(String(xs[0] + 4, 44, t("reject (from any stage before Hired)"), fontName=SANS, fontSize=6.8,
                 fillColor=BRICK))
    return d


def search_diagram(width):
    steps = [("Tokenize", ["quotes, negation", "keeps char spans"]),
             ("Parse", ["filters → clauses", "dates in her tz"]),
             ("Validate", ["contradictions", "did-you-mean"]),
             ("Evaluate", ["fuzzy names", "stage/time filters"]),
             ("Rank", ["name match,", "then wait time"]),
             ("Explain", ["reasons, errors", "empty breakdown"])]
    n = len(steps)
    w = 74
    gap = (width - n * w) / (n - 1)
    d = Drawing(width, 52)
    for i, (title, lines) in enumerate(steps):
        x = i * (w + gap)
        hl = title in ("Validate", "Explain")
        box(d, x, 2, w, 48, [title] + lines, fill=TEAL_BG if hl else WHITE,
            stroke=TEAL if hl else INK, title_color=TEAL if hl else INK)
        if i < n - 1:
            arrow(d, x + w, 26, x + w + gap, 26)
    return d


# ---------------------------------------------------------------- content
def build(out: Path, repo: str):
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=16 * mm,
                            title="Mini Hiring Pipeline — Design summary", author="Praviveek",
                            subject="Architecture and design decisions")
    W = doc.width
    s = []

    # ---- page 1: overview
    s.append(P("Mini Hiring Pipeline", H1))
    s.append(P("Design and architecture summary · Praviveek", SUB))
    link = Table([[P(f'<b>GitHub:</b> <a href="{repo}" color="#0E6B5E"><u>{repo}</u></a>', BODY)]],
                 colWidths=[W])
    link.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TEAL_BG),
                              ("LINEBEFORE", (0, 0), (0, -1), 3, TEAL),
                              ("LEFTPADDING", (0, 0), (-1, -1), 10), ("TOPPADDING", (0, 0), (-1, -1), 7),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    s += [link, Spacer(1, 10)]
    s.append(P(
        "A web app for a recruiter running one job. Candidates move Applied → Screening → Interview → "
        "Offer → Hired, one stage at a time, or are rejected before Hired. Every move is appended to an "
        "event log that the database itself refuses to change, so the history is a real audit trail. "
        "One search box takes names (typo-tolerant) and short filters, combines them, ranks the results, "
        "and tells the recruiter why a query can't work instead of showing an empty list."))
    s.append(table([
        ["", ""],
        ["Stack", "Python 3.10+, FastAPI, SQLite (stdlib), plain HTML/CSS/JS. No frontend build step."],
        ["Run", code("pip install -r requirements.txt") + " → " + code("python -m scripts.seed --reset")
         + " → " + code("uvicorn app.main:app") + " → localhost:8000"],
        ["Tests", "105 pytest tests: state machine, audit trail (including raw SQL tampering), "
                  "query parser, search ranking and explanations, HTTP API."],
        ["Repo also has", "README (run, decisions, next steps), ai-logs/, screenshots."],
    ], [28 * mm, W - 28 * mm], header=False,
        style_extra=[("FONTNAME", (0, 0), (0, -1), BOLD), ("LINEBELOW", (0, 0), (-1, 0), 0, WHITE),
                     ("TOPPADDING", (0, 0), (-1, 0), 0), ("BOTTOMPADDING", (0, 0), (-1, 0), 0)]))
    s.append(Spacer(1, 10))
    s += shot("board.png", W, "The board. Columns by stage, longest-waiting first; amber marks anyone stuck "
                              "over a week. Buttons offer only the legal next move.")

    # ---- page 2: architecture
    s.append(PageBreak())
    s.append(P("Architecture", H2))
    s.append(architecture_diagram(W))
    s.append(P("Three layers, one rule file. The browser talks JSON to a thin FastAPI layer. "
               "pipeline.py owns writes; search/ reads a projection built by pipeline.py. SQLite holds "
               "the event log and enforces the rules again with triggers.", CAP))
    s.append(table([
        ["Module", "Responsibility"],
        [code("stages.py"), "Stages and every legal (from, action, to). Used by the pipeline, the DB triggers "
                            "and the query parser, so the process is defined in one place."],
        [code("db.py"), "Schema and triggers. Opens writes with BEGIN IMMEDIATE so read-check-write is serialised."],
        [code("pipeline.py"), "Add candidate (name/email validation), move candidate, build history with "
                              "time spent in each stage. Current stage = last event."],
        [code("search/fuzzy.py"), "Accent folding, optimal string alignment distance (counts a transposition as "
                                  "one typo), prefix and typo scoring for name tokens."],
        [code("search/parser.py"), "Query language → clauses. Errors carry a message, a hint, the character span "
                                   "and, when obvious, a corrected query."],
        [code("search/engine.py"), "Filter, rank, attach reasons (“sharam” ≈ Sharma, 1 typo), explain empty results."],
        [code("main.py"), "Routes, error mapping (409 stale, 422 illegal, 400 bad query), static files."],
        [code("static/"), "Board, candidate drawer with live time-in-stage, search panel, dialogs."],
    ], [36 * mm, W - 36 * mm]))
    s.append(P("A move, end to end", H3))
    s.append(P(
        "The board sends " + code("POST /api/candidates/{id}/transitions") + " with the action and the stage "
        "the recruiter was looking at. The pipeline takes the write lock, loads the candidate, and answers "
        "409 if the stage has changed since (double-click, second tab) or 422 if the outcome is already final. "
        "Otherwise it inserts one event. The insert trigger re-checks the sequence number, the from-stage, "
        "that the move is listed in allowed_transitions, and that the timestamp isn't earlier than the last "
        "event. The response is the updated candidate and the board re-renders."))

    # ---- page 3: data model and audit trail
    s.append(PageBreak())
    s.append(P("Data model and audit trail", H2))
    s.append(state_machine_diagram(W))
    s.append(P("The state machine. Teal: advance one stage. Dashed: reject, allowed from any stage "
               "before Hired. Hired and Rejected are final.", CAP))
    s.append(table([
        ["Table", "Columns", "Rules"],
        [code("candidates"), "id, name, email (unique), created_at", "No UPDATE, no DELETE."],
        [code("stage_events"), "id, candidate_id, seq, action, from_stage, to_stage, at, note (≤ 500 chars)",
         "Append-only. UNIQUE(candidate_id, seq). Insert trigger validates the chain."],
        [code("allowed_transitions"), "from_stage, action, to_stage",
         "Filled from stages.py on first run. Read-only."],
    ], [38 * mm, 70 * mm, W - 108 * mm]))
    s.append(P("There is no stage column on candidates. The current stage is the latest event, so the "
               "board and the history can't disagree, and time in stage is now minus that event's timestamp.",
               style("n", fontSize=8.5, leading=11.5, spaceBefore=6)))
    s.append(P("Guarantees and where they are enforced", H3))
    s.append(table([
        ["Requirement", "API / pipeline", "Database", "Tested by"],
        ["History never changes", "No PUT/PATCH/DELETE routes", "Triggers abort UPDATE and DELETE",
         "Raw SQL update/delete fails"],
        ["One stage at a time", "Only next_stage() is offered", "Move must be in allowed_transitions",
         "Raw insert skipping a stage fails"],
        ["No reversing a final outcome", "422 on Hired/Rejected", "No transitions out of final stages",
         "API + raw SQL"],
        ["No lost or doubled moves", "expected_stage → 409 if stale", "seq must be max+1; from_stage must match",
         "Double-submit test"],
        ["Honest timeline", "at = max(now, last event)", "at can't precede the previous event",
         "Backdating insert fails"],
    ], [37 * mm, 42 * mm, 50 * mm, W - 129 * mm]))
    s.append(Spacer(1, 10))
    dw = 62 * mm
    drawer = shot("history.png", dw, crop=(980, 0, 1440, 530))[0]
    side = [P("The history view", H3),
            P("Opening a candidate shows the current stage and a live “time in stage” (it ticks while open), "
              "a step track with the stage where a rejected candidate stopped, and every event with its date "
              "and how long they stayed. Reasons given for Hired or Rejected are stored on the event."),
            P("The drawer says plainly that history is permanent. There is no edit button because there is "
              "nothing in the API or the database that could carry out an edit.")]
    two = Table([[drawer, side]], colWidths=[dw + 6 * mm, W - dw - 6 * mm])
    two.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                             ("RIGHTPADDING", (0, 0), (0, 0), 6 * mm), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    s.append(two)

    # ---- page 4: search
    s.append(PageBreak())
    s.append(P("Search", H2))
    s.append(P("A small query language, like Gmail's search box. Free text is a name; " + code("key:value")
               + " terms are filters; everything is ANDed; " + code("-") + " negates a filter."))
    s.append(search_diagram(W))
    s.append(Spacer(1, 8))
    s.append(table([
        ["Recruiter's question", "Query", "Seed data result"],
        ["Find Priya Sharma, typed “sharam”", code("sharam") + " / " + code("priya sharam"),
         "All four Sharmas / Priya Sharma first"],
        ["Who's in Interview right now?", code("in:interview"), "4 people"],
        ["Stuck in Screening over a week", code("in:screening for:>1w"), "4, longest first"],
        ["Moved to Interview since Monday", code("moved:interview since:monday"), "5 (Monday in her time zone)"],
        ["Reached Offer, not hired", code("reached:offer is:rejected"), "Neha Kapoor, Meera Das"],
        ["Everyone except rejected", code("-is:rejected"), "19 of 25"],
        ["Combined", code("priya -is:rejected"), "Priya Verma, Priya Sharma, Priyanka Sharma"],
    ], [52 * mm, 58 * mm, W - 110 * mm]))
    s.append(P("Ranking", H3))
    s.append(P("Name match score first (exact 1.0, prefix, then one or two typos; allowed typos scale with word "
               "length). Ties, and filter-only queries, are ordered by time in current stage, longest first, "
               "because the person waiting longest is the one the recruiter most likely needs. Each result "
               "shows why it matched."))
    s.append(P("Two kinds of “nothing”", H3))
    s.append(table([
        ["Input", "Kind", "What she sees"],
        [code("in:intervew"), "Error", "“intervew” isn't a stage. Did you mean in:interview? One-click fix."],
        [code("in:hired in:offer"), "Error", "Someone can only be in one stage at a time; use a comma for either."],
        [code("reached:hired is:rejected"), "Error", "Nobody in Rejected can have reached Hired."],
        [code("since:today before:yesterday"), "Error", "The window is empty (dates shown)."],
        [code("who is in interview right now"), "Empty", "Reads like a question; suggests in:interview."],
        [code("priyanka in:offer"), "Empty", "Each part matches someone (1 and 2) but no one matches both."],
    ], [52 * mm, 15 * mm, W - 67 * mm]))
    s.append(Spacer(1, 6))
    s.append(PageBreak())
    s.append(P("Search in the app", H2))
    s += shot("search-typo.png", W, "“priya sharam”: Priya Sharma ranks first, each result says why it matched.",
              crop=(0, 95, 1440, 500))
    s += shot("search-error.png", W, "A mistyped stage: the bad part is underlined, the hint lists valid "
                                           "stages, and the button runs the corrected query.",
              crop=(0, 95, 1440, 490))
    s += shot("search-empty.png", W, "A valid query that matches nobody: how many people each filter "
                                     "matches on its own, so she sees which one emptied the list.",
              crop=(0, 95, 1440, 500))

    # ---- page 5: decisions
    s.append(PageBreak())
    s.append(P("Decisions and trade-offs", H2))
    s.append(table([
        ["Decision", "Why", "Cost"],
        ["Event log as source of truth", "Stage and history can't drift apart; time in stage is free.",
         "Current stage needs a lookup of the last event (cheap at this size)."],
        ["Rules enforced in DB triggers too", "A bug in a future endpoint, or someone with the DB file, "
                                             "still can't rewrite history.", "Rules exist in SQL as well as Python; "
                                                                             "both read the same transition table."],
        ["expected_stage on every move", "A double-click or second tab can't skip a stage.",
         "Client must send the stage it saw; stale clients get a 409 and refresh."],
        ["Confirm only final outcomes", "Hired/Rejected can't be undone; ordinary advances stay one click.",
         "A mis-click forward is permanent until correction events exist."],
        ["Query language, not an LLM", "Predictable, testable, errors point at the exact characters, "
                                       "and the app echoes what it understood.",
         "She learns a few filters. Mitigated by example chips, syntax help and suggestions."],
        ["Fuzzy names, strict vocabulary", "Names are unknown data; stage names are a closed set where a "
                                           "guess would hide a mistake.", "“intervew” is an error (with a fix) "
                                                                          "rather than silently corrected."],
        ["Search in Python, in memory", "One job is hundreds of rows. Matching, ranking and explanations stay "
                                        "in one readable module.", "Won't scale to many jobs without compiling to SQL."],
        ["Browser time zone for dates", "“Since Monday” means her Monday, not UTC's.",
         "One more parameter on the search request."],
        ["No drag-and-drop", "Dropping a card two columns over is the easiest way to break the rules.",
         "Less tactile board."],
    ], [40 * mm, 72 * mm, W - 112 * mm]))
    s.append(P("With more time", H3))
    more = [
        ("Correction events", "record a mistaken move as a new event pointing at the old one."),
        ("Actor on every event", "auth, and who made each move."),
        ("Tamper evidence", "hash-chain events so edits made outside the app are detectable."),
        ("Natural-language front end", "an LLM translates the question into the query language; she confirms the chips; the parser still runs it."),
        ("Autocomplete", "for filter keys, stages and names."),
        ("Scale search", "compile clauses to SQL, SQLite FTS5 trigram index for names."),
        ("Richer queries", "OR across filters, parentheses, saved searches, per-stage stuck thresholds."),
        ("Multiple jobs, E2E tests (Playwright), CI, accessibility pass.", ""),
    ]
    s.append(table([[f"<b>{a}</b>" + (f" — {b}" if b else "")] for a, b in more], [W], header=False))
    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont(SANS, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, t("Mini Hiring Pipeline · Praviveek"))
    canvas.drawRightString(A4[0] - 18 * mm, 9 * mm, str(doc.page))
    canvas.restoreState()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO, help="GitHub repository URL")
    ap.add_argument("--out", default=str(HERE / "design.pdf"))
    a = ap.parse_args()
    build(Path(a.out), a.repo)
    print("wrote", a.out)
