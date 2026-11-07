"""Quality checks for briefs and drafts.

The evals run these against fixtures, and the draft agent runs the draft checks
on every Claude-polished draft before it reaches Aaron. Each function returns a
list of problems; an empty list means it passed.
"""

import re
from datetime import date

from .research import FRESH_DAYS, ICP_SEGMENTS

MAX_DRAFT_WORDS = 90
MAX_BRIEF_WORDS = 180

# Numbers a draft may use without the brief backing them: 10% fee, $249 Pro, 15/20-minute calls.
ALLOWED_NUMBERS = {"10", "15", "20", "249"}

BANNED = [
    "hope this finds you", "hope you're well", "circle back", "touch base", "synergy", "leverage",
    "game-changer", "game changer", "revolutionary", "cutting-edge", "best-in-class", "seamless",
    "just checking in", "quick question", "i wanted to reach out", "excited to", "unlock",
]

PLACEHOLDER = re.compile(r"\{|\}|\[[A-Za-z _]+\]|<[a-z_ ]+>|TODO|XXX|lorem", re.IGNORECASE)


def words(text):
    return re.findall(r"[\w$%'&-]+", text)


def numbers(text):
    return set(re.findall(r"\d+", text.replace(",", "")))


def why_now_line(brief_body):
    for line in brief_body.splitlines():
        if line.startswith("Why now:"):
            return line[len("Why now:"):].strip()
    return ""


def draft_problems(text, org, brief_body):
    if not text or not text.strip():
        return ["empty draft"]
    problems = []
    n = len(words(text))
    if n > MAX_DRAFT_WORDS:
        problems.append(f"too long ({n} words > {MAX_DRAFT_WORDS})")
    first = org["decision_maker"].split()[0]
    if not text.strip().startswith(f"Hi {first},"):
        problems.append(f"doesn't open with 'Hi {first},'")
    if not re.search(r"\nAaron(\nPebble)?\s*$", text.strip()):
        problems.append("not signed 'Aaron'")
    q = text.count("?")
    if q != 1:
        problems.append(f"{q} questions, needs exactly one ask")
    hits = [b for b in BANNED if b in text.lower()]
    if hits:
        problems.append(f"banned phrases: {', '.join(hits)}")
    if PLACEHOLDER.search(text):
        problems.append("unfilled placeholder")
    unsupported = numbers(text) - numbers(brief_body) - ALLOWED_NUMBERS
    if unsupported:
        problems.append(f"numbers not in the brief: {', '.join(sorted(unsupported))}")
    anchors = [org["flagship_event"].lower(), org["name"].lower()]
    why = why_now_line(brief_body).lower()
    why_words = words(why.split("(")[0])
    anchors += [" ".join(why_words[i:i + 4]) for i in range(max(0, len(why_words) - 3))]
    if not any(a and a in text.lower() for a in anchors):
        problems.append("nothing specific to this org (no event name, org name or trigger)")
    return problems


def brief_problems(brief, org, signals, today):
    body = brief["body"]
    problems = []
    if f"{org['events_per_year']} events/yr" not in body:
        problems.append("missing events per year")
    if org["decision_maker"] not in body or org["decision_maker_title"] not in body:
        problems.append("missing decision maker name and title")
    if "Recent signals:" not in body:
        problems.append("missing recent signals section")
    if "Angle:" not in body:
        problems.append("missing Pebble angle")
    n = len(words(body))
    if n > MAX_BRIEF_WORDS:
        problems.append(f"too long ({n} words > {MAX_BRIEF_WORDS})")

    # Every signal the brief lists must exist and be fresh. No invented signals.
    real = {s["detail"] for s in signals}
    for line in body.splitlines():
        m = re.match(r"\s+- (\d{4}-\d{2}-\d{2}): (.+?) \[", line)
        if m and m.group(2) not in real:
            problems.append(f"lists a signal that isn't in the data: {m.group(2)[:50]}")

    fresh = [s for s in signals if (today - date.fromisoformat(s["seen_on"])).days <= FRESH_DAYS]
    in_icp = org["segment"] in ICP_SEGMENTS
    if not in_icp:
        if brief["why_now"]:
            problems.append("gave an outreach reason for an org outside the ICP")
        if "SKIP" not in body:
            problems.append("off-ICP org not marked SKIP")
    elif not fresh:
        if brief["why_now"]:
            problems.append("invented a why-now with no fresh signal")
        if "HOLD" not in body:
            problems.append("no fresh signal but not marked HOLD")
    else:
        if not brief["why_now"]:
            problems.append("fresh signal exists but no why-now given")
        elif not any(s["detail"].lower() in brief["why_now"].lower() for s in fresh):
            problems.append("why-now doesn't cite a specific fresh signal")
    return problems
