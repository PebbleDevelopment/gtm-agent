"""Shared SQLite store. Every agent reads and writes here; nothing else holds state."""

import os
import sqlite3
from datetime import date, datetime

DB_PATH = os.environ.get("PEBBLE_DB", os.path.join(os.path.dirname(__file__), "..", "pebble.db"))

# The funnel, in order. Everything before signed_up is prospecting; everything after is activation.
STAGES = ["new", "researched", "drafted", "contacted", "replied", "meeting",
          "signed_up", "onboarding", "live", "lost", "cold"]

# Activation milestones inside Pebble. live = first vendor payment collected through Pebble.
MILESTONES = ["signed_up", "event_created", "apps_open", "first_payment"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS orgs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    segment TEXT NOT NULL,          -- multi_festival | venue | tourism | chamber | econ_dev | single_event
    city TEXT, state TEXT,
    events_per_year INTEGER,
    flagship_event TEXT,
    vendors_per_event INTEGER,
    decision_maker TEXT,
    decision_maker_title TEXT,
    email TEXT,
    email_status TEXT DEFAULT 'unknown',   -- unknown | valid | risky | invalid (from Clay or a bounce)
    website TEXT,
    instagram TEXT,                 -- handle without the @
    current_tools TEXT,
    source TEXT DEFAULT 'seed',     -- seed | aaron | prospect agent query that found it
    source_url TEXT,
    synthetic INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    seen_on TEXT NOT NULL,          -- ISO date
    kind TEXT NOT NULL,             -- vendor_apps_open | new_event | tool_pain | hiring | grant | venue_booking
    detail TEXT NOT NULL,
    source TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    created_at TEXT NOT NULL,
    fit_score INTEGER,
    why_now TEXT,                   -- NULL means no trigger: hold, don't reach out
    why_now_signal_id INTEGER,
    body TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES orgs(id),   -- the org this thread is about
    via_org_id INTEGER REFERENCES orgs(id),        -- set when the message goes to a customer asking for an intro
    brief_id INTEGER REFERENCES briefs(id),
    touch INTEGER NOT NULL,         -- 0 = intro ask or activation nudge, 1 = first message, 2+ = follow-ups
    purpose TEXT DEFAULT 'outreach',-- outreach | reply | intro_ask | activation | referral_ask
    body TEXT NOT NULL,
    rationale TEXT,                 -- one line for Aaron: why this angle
    status TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | sent | discarded
    edited INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    sent_at TEXT,
    replied_at TEXT,
    reply_kind TEXT                 -- positive | neutral | negative
);

CREATE TABLE IF NOT EXISTS pipeline (
    org_id INTEGER PRIMARY KEY REFERENCES orgs(id),
    stage TEXT NOT NULL DEFAULT 'new',
    last_contact TEXT,
    next_action TEXT,
    next_action_due TEXT,
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS milestones (
    org_id INTEGER NOT NULL REFERENCES orgs(id),
    milestone TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (org_id, milestone)
);

CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,             -- preference | voice_edit | voice_sample | fact
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    role TEXT NOT NULL,             -- user | assistant
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox_seen (
    message_id TEXT PRIMARY KEY,    -- Gmail messages already processed
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    agent TEXT NOT NULL,            -- prospect | research | draft | followup | tracking | assistant | aaron | pebble | clay | gmail | instagram
    action TEXT NOT NULL,
    org_id INTEGER,
    detail TEXT
);
"""

TABLES = ["activity", "kv", "inbox_seen", "conversation", "memory", "milestones", "pipeline", "drafts", "briefs", "signals", "orgs"]


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    # Older stores predate these columns; add them in place.
    if "email_status" not in [r[1] for r in conn.execute("PRAGMA table_info(orgs)")]:
        conn.execute("ALTER TABLE orgs ADD COLUMN email_status TEXT DEFAULT 'unknown'")
    if "instagram" not in [r[1] for r in conn.execute("PRAGMA table_info(orgs)")]:
        conn.execute("ALTER TABLE orgs ADD COLUMN instagram TEXT")
    if "rationale" not in [r[1] for r in conn.execute("PRAGMA table_info(drafts)")]:
        conn.execute("ALTER TABLE drafts ADD COLUMN rationale TEXT")
    return conn


def reset(conn):
    for t in TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.executescript(SCHEMA)


def today():
    return date.fromisoformat(os.environ["PEBBLE_TODAY"]) if os.environ.get("PEBBLE_TODAY") else date.today()


def now():
    return datetime.now().isoformat(timespec="seconds")


def log(conn, agent, action, org_id=None, detail=None):
    conn.execute(
        "INSERT INTO activity (at, agent, action, org_id, detail) VALUES (?, ?, ?, ?, ?)",
        (now(), agent, action, org_id, detail),
    )


def find_org(conn, ref):
    """Look up an org by id or by a fragment of its name."""
    ref = str(ref).strip().lstrip("#")
    if ref.isdigit():
        return conn.execute("SELECT * FROM orgs WHERE id = ?", (int(ref),)).fetchone()
    rows = conn.execute("SELECT * FROM orgs WHERE name LIKE ? ORDER BY id", (f"%{ref}%",)).fetchall()
    return rows[0] if rows else None


def add_org(conn, fields, source, source_url=None, synthetic=0):
    cols = ["name", "segment", "city", "state", "events_per_year", "flagship_event", "vendors_per_event",
            "decision_maker", "decision_maker_title", "email", "website", "instagram", "current_tools"]
    vals = [fields.get(c) for c in cols]
    org_id = conn.execute(
        f"INSERT INTO orgs ({', '.join(cols)}, source, source_url, synthetic) VALUES ({', '.join('?' * len(cols))}, ?, ?, ?)",
        vals + [source, source_url, synthetic]).lastrowid
    conn.execute("INSERT INTO pipeline (org_id, stage) VALUES (?, 'new')", (org_id,))
    return org_id


def get_kv(conn, key, default=None):
    r = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_kv(conn, key, value):
    conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def remember(conn, kind, text):
    conn.execute("INSERT INTO memory (at, kind, text) VALUES (?, ?, ?)", (now(), kind, text))


def recall(conn, kind, limit=8):
    return [r["text"] for r in conn.execute(
        "SELECT text FROM memory WHERE kind = ? ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()]
