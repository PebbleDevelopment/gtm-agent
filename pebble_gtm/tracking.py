"""Tracking agent.

Keeps the pipeline table honest: stage, last contact, next action, outcome, all the way
from "found" to "live on Pebble". Stages are derived from what actually happened:
briefs, drafts, sends and replies before signup, then Pebble milestones after it.
Only Aaron sets meeting and lost.

Funnel: new → researched → drafted → contacted → replied → meeting → signed_up → onboarding → live
"""

from datetime import timedelta

from . import db

MANUAL = {"meeting", "lost"}

# Minutes the same work takes by hand. These drive the hours-saved estimate; change them if they're wrong for you.
MINUTES_BY_HAND = {"prospect": 10, "brief": 25, "draft": 12, "followup_per_thread": 3, "pipeline_upkeep_weekly": 15}


def set_stage(conn, org_id, stage, outcome=None):
    conn.execute("UPDATE pipeline SET stage = ?, outcome = COALESCE(?, outcome) WHERE org_id = ?", (stage, outcome, org_id))
    db.log(conn, "tracking", "stage", org_id, stage + (f" ({outcome})" if outcome else ""))


def set_next(conn, org_id, action, due):
    conn.execute("UPDATE pipeline SET next_action = ?, next_action_due = ? WHERE org_id = ?", (action, due, org_id))


def milestones(conn, org_id):
    return {r["milestone"]: r["at"] for r in conn.execute("SELECT * FROM milestones WHERE org_id = ?", (org_id,))}


def mark_milestone(conn, org_id, milestone, at=None):
    if milestone not in db.MILESTONES:
        raise ValueError(f"Milestone must be one of {', '.join(db.MILESTONES)}")
    conn.execute("INSERT OR IGNORE INTO milestones (org_id, milestone, at) VALUES (?, ?, ?)",
                 (org_id, milestone, at or db.today().isoformat()))
    db.log(conn, "pebble", milestone, org_id)
    stage = refresh_stage(conn, org_id)
    conn.commit()
    return stage


def refresh_stage(conn, org_id):
    current = conn.execute("SELECT stage FROM pipeline WHERE org_id = ?", (org_id,)).fetchone()["stage"]
    m = milestones(conn, org_id)
    drafts = conn.execute("SELECT * FROM drafts WHERE org_id = ? AND via_org_id IS NULL ORDER BY id", (org_id,)).fetchall()
    outreach = [d for d in drafts if d["status"] == "sent" and d["purpose"] in ("outreach", "reply")]
    has_brief = conn.execute("SELECT 1 FROM briefs WHERE org_id = ?", (org_id,)).fetchone()

    if "first_payment" in m:
        stage = "live"
    elif "event_created" in m or "apps_open" in m:
        stage = "onboarding"
    elif "signed_up" in m:
        stage = "signed_up"
    elif current in MANUAL:
        stage = current
    elif any(d["reply_kind"] == "negative" for d in outreach):
        stage = "lost"
    elif outreach and outreach[-1]["replied_at"]:
        stage = "replied"
    elif any(d["status"] in ("draft", "approved") for d in drafts):
        stage = "drafted"
    elif current == "cold":
        stage = "cold"
    elif outreach:
        stage = "contacted"
    elif has_brief:
        stage = "researched"
    else:
        stage = "new"

    sent = [d["sent_at"] for d in drafts if d["status"] == "sent"]
    last = max(sent, default=None)
    conn.execute("UPDATE pipeline SET stage = ?, last_contact = ? WHERE org_id = ?", (stage, last and last[:10], org_id))
    if stage == "lost":
        conn.execute("UPDATE pipeline SET outcome = COALESCE(outcome, 'said no'), next_action = NULL WHERE org_id = ?", (org_id,))
    if stage != current:
        db.log(conn, "tracking", "stage", org_id, f"{current} -> {stage}")
    return stage


def mark_reply(conn, org_id, kind, note=""):
    d = conn.execute("""SELECT * FROM drafts WHERE (org_id = ? OR via_org_id = ?) AND status = 'sent'
                        ORDER BY sent_at DESC, id DESC LIMIT 1""", (org_id, org_id)).fetchone()
    if d is None:
        return None
    conn.execute("UPDATE drafts SET replied_at = ?, reply_kind = ? WHERE id = ?", (db.today().isoformat(), kind, d["id"]))
    db.log(conn, "aaron", "reply", d["org_id"], f"{kind}: {note}" if note else kind)
    stage = refresh_stage(conn, d["org_id"])
    conn.commit()
    return stage, d["org_id"]


def table(conn):
    order = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(
        ["replied", "meeting", "signed_up", "onboarding", "drafted", "contacted", "researched", "new", "cold", "live", "lost"]))
    return conn.execute(f"""SELECT o.id, o.name, o.segment, o.source, p.stage, p.last_contact, p.next_action,
                                   p.next_action_due, p.outcome
                            FROM pipeline p JOIN orgs o ON o.id = p.org_id
                            ORDER BY CASE p.stage {order} ELSE 99 END, o.id""").fetchall()


def funnel(conn):
    """How many orgs have reached each stage or beyond. The number that matters is `live`."""
    counts = {s: 0 for s in db.STAGES}
    for r in conn.execute("SELECT stage, COUNT(*) AS n FROM pipeline GROUP BY stage"):
        counts[r["stage"]] = r["n"]
    reached = {}
    path = ["contacted", "replied", "meeting", "signed_up", "onboarding", "live"]
    for i, s in enumerate(path):
        reached[s] = sum(counts[x] for x in path[i:])
    # lost/cold orgs were contacted too
    reached["contacted"] += counts["lost"] + counts["cold"]
    reached["total"] = sum(counts.values())
    return reached


def metric(conn):
    """Estimated hours saved per week, from the last 7 days of agent activity."""
    since = (db.today() - timedelta(days=7)).isoformat()
    count = lambda agent, action: conn.execute(
        "SELECT COUNT(*) FROM activity WHERE agent = ? AND action = ? AND at >= ?", (agent, action, since)).fetchone()[0]
    prospects, briefs, drafts = count("prospect", "add"), count("research", "brief"), count("draft", "draft")
    threads = conn.execute("""SELECT COUNT(*) FROM pipeline
                              WHERE stage IN ('contacted','replied','drafted','signed_up','onboarding')""").fetchone()[0]
    m = MINUTES_BY_HAND
    minutes = (prospects * m["prospect"] + briefs * m["brief"] + drafts * m["draft"]
               + threads * m["followup_per_thread"] + m["pipeline_upkeep_weekly"])
    return {"hours": round(minutes / 60, 1), "prospects": prospects, "briefs": briefs, "drafts": drafts, "threads": threads}
