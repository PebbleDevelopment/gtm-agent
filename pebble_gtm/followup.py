"""Follow-up agent.

Watches every org from first touch to live on Pebble and proposes the next step with a reason.

Before signup: replied → answer it today. Un-replied → touch 2 after 4 quiet days, touch 3 after 7 more,
cold after 14. A cold org comes back only when a new signal shows up. Venues and civic groups with a live
customer nearby get an intro ask before any cold message.

After signup: each Pebble milestone has a deadline. Signed up with no event after 2 days, an event with no
applications open after 3, applications open with no payment after 7: each gets a nudge. Once live, the org
gets a referral ask, because live customers are the best source of the next prospects.

morning() is the proactive part. It runs once a day, does the work (checks Gmail for replies, prospects,
researches, sends orgs missing an email to Clay, pre-drafts),
and returns the message that goes to Aaron's WhatsApp. It only ever drafts. Approving is up to Aaron.
"""

from datetime import date, timedelta

from . import db, llm, research, tracking

WAIT_DAYS = {1: 4, 2: 7, 3: 14}   # days of silence after touch N before the next step
MAX_TOUCHES = 3
ACTIVATION_DAYS = {"event_created": 2, "apps_open": 3, "first_payment": 7}   # days allowed to reach each milestone
REFERRAL_EVERY_DAYS = 60


def _days(since, today):
    return (today - date.fromisoformat(since[:10])).days


def propose_for(conn, org, today):
    """Return one proposal dict for an org, or None if nothing to do."""
    p = conn.execute("SELECT * FROM pipeline WHERE org_id = ?", (org["id"],)).fetchone()
    if p["stage"] in ("lost", "meeting"):
        return None
    drafts = conn.execute("SELECT * FROM drafts WHERE org_id = ? ORDER BY id", (org["id"],)).fetchall()
    open_drafts = [d for d in drafts if d["status"] in ("draft", "approved")]
    prop = lambda action, reason, bucket, due=today: {
        "org_id": org["id"], "org": org["name"], "action": action, "reason": reason,
        "bucket": bucket, "due": due.isoformat()}

    if open_drafts:
        d = open_drafts[-1]
        if d["status"] == "draft":
            return prop(f"Approve draft #{d['id']} (`ok {d['id']}`)", f"{d['purpose']} drafted {_days(d['created_at'], today)}d ago",
                        "waiting on you")
        return prop(f"Send approved draft #{d['id']} yourself, then `sent {d['id']}`", "approved but not marked sent",
                    "waiting on you")

    # After signup: push toward the next milestone.
    if p["stage"] in ("signed_up", "onboarding", "live"):
        m = tracking.milestones(conn, org["id"])
        if p["stage"] == "live":
            last_ask = conn.execute("""SELECT MAX(created_at) FROM drafts WHERE org_id = ? AND purpose = 'referral_ask'""",
                                    (org["id"],)).fetchone()[0]
            if last_ask is None or _days(last_ask, today) >= REFERRAL_EVERY_DAYS:
                return prop("Draft a referral ask", "live on Pebble; live customers are the warmest source of the next ones",
                            "activation")
            return None
        nxt = next(x for x in db.MILESTONES if x not in m)
        prev = db.MILESTONES[db.MILESTONES.index(nxt) - 1]
        stuck = _days(m[prev], today)
        last_nudge = conn.execute("""SELECT MAX(created_at) FROM drafts WHERE org_id = ? AND purpose = 'activation'""",
                                  (org["id"],)).fetchone()[0]
        if stuck >= ACTIVATION_DAYS[nxt] and (last_nudge is None or last_nudge[:10] < m[prev]):
            return prop(f"Draft an activation nudge toward `{nxt}`", f"{prev} {stuck}d ago, no {nxt} yet", "activation")
        return None

    sent = [d for d in drafts if d["status"] == "sent" and d["purpose"] in ("outreach", "reply")]
    intro = next((d for d in reversed(drafts) if d["purpose"] == "intro_ask" and d["status"] == "sent"), None)
    if intro and not sent:
        if intro["reply_kind"] == "positive":
            return prop("Intro is on: draft the first message", "customer agreed to introduce you", "replied")
        if intro["reply_kind"] is None and _days(intro["sent_at"], today) < 7:
            return None   # give the customer a week before going direct
    if sent:
        last = sent[-1]
        if last["reply_kind"] == "negative":
            return None
        if last["reply_kind"] == "positive":
            return prop("Book the call: `draft` offers times", f"replied positive {_days(last['replied_at'], today)}d ago",
                        "replied")
        if last["reply_kind"] == "neutral":
            note = conn.execute("SELECT detail FROM activity WHERE org_id = ? AND action = 'reply' ORDER BY id DESC LIMIT 1",
                                (org["id"],)).fetchone()
            q = f': "{note["detail"].split(": ", 1)[-1]}"' if note else ""
            return prop("Answer their question: `draft` writes it", f"replied with a question{q}", "replied")

        quiet = _days(last["sent_at"], today)
        if p["stage"] == "cold":
            new = conn.execute("SELECT * FROM signals WHERE org_id = ? AND seen_on > ? ORDER BY seen_on DESC LIMIT 1",
                               (org["id"], last["sent_at"][:10])).fetchone()
            if new:
                return prop("Re-open with a fresh first message",
                            f"went cold after {last['touch']} touches; new signal {new['seen_on']}: {new['detail']}", "cold")
            return None
        wait = WAIT_DAYS[min(last["touch"], MAX_TOUCHES)]
        due = date.fromisoformat(last["sent_at"][:10]) + timedelta(days=wait)
        if last["touch"] >= MAX_TOUCHES:
            if quiet >= wait:
                tracking.set_stage(conn, org["id"], "cold")
                db.log(conn, "followup", "cold", org["id"], f"no reply {quiet}d after touch {last['touch']}")
                return prop("Marked cold, no action", f"{MAX_TOUCHES} touches, {quiet}d silent", "cold")
            return None
        if quiet >= wait:
            return prop(f"Draft touch {last['touch'] + 1}",
                        f"touch {last['touch']} sent {quiet}d ago, no reply (rule: {wait}d)", "un-replied")
        return prop(f"Wait, then touch {last['touch'] + 1}", f"touch {last['touch']} sent {quiet}d ago", "un-replied", due)

    brief = research.latest_brief(conn, org["id"])
    if brief is None:
        return prop("Research", f"found via {org['source']}" if org["source"] != "seed" else "no brief yet", "new")
    if research.fit_score(org) < 50:
        return None
    warm = research.warm_path(conn, org)
    if warm and not intro:
        return prop(f"Ask {warm['name']} for an intro", f"live customer in {warm['city']}, {warm['state']}", "new")
    if not org["email"] or not org["decision_maker"] or org["email_status"] == "invalid":
        from . import clay
        if clay.needs_enrichment(conn, org):
            missing = "decider" if not org["decision_maker"] else "email" if not org["email"] else "a working email"
            return prop(f"Find the decider's email: `enrich {org['id']}`", f"missing {missing}", "new")
        return None   # waiting on Clay
    if brief["why_now"]:
        return prop("Draft first message", f"trigger: {brief['why_now'].split(' Why it matters')[0]}", "new")
    return None   # researched, no trigger: HOLD until a signal appears


ORDER = {"replied": 0, "waiting on you": 1, "activation": 2, "un-replied": 3, "cold": 4, "new": 5}


def run(conn, include_waiting=False):
    today = db.today()
    out = []
    for org in conn.execute("SELECT * FROM orgs ORDER BY id").fetchall():
        p = propose_for(conn, org, today)
        if p is None:
            continue
        tracking.set_next(conn, org["id"], p["action"], p["due"])
        if include_waiting or p["due"] <= today.isoformat():
            out.append(p)
    conn.commit()
    return sorted(out, key=lambda p: (ORDER[p["bucket"]], p["due"]))


def morning(conn, prospect_limit=3, research_limit=4, draft_limit=6):
    """The daily proactive pass. Does the work, then returns the WhatsApp message for Aaron."""
    from . import clay, draft, gmail, prospect
    use_web = llm.available()
    did = []

    if gmail.configured():                        # 0. catch up on replies first
        try:
            did += gmail.check(conn)
        except Exception as e:
            did.append(f"couldn't check Gmail ({type(e).__name__})")

    if use_web:                                   # 1. find new orgs
        try:
            added, _ = prospect.run(conn, limit=prospect_limit)
            if added:
                did.append(f"found {len(added)} new prospects: " + ", ".join(f"{n} (#{i})" for i, n, _ in added))
        except Exception as e:
            did.append(f"prospecting failed ({type(e).__name__})")

    props = run(conn)                             # 2. research the ones with no brief
    for p in [p for p in props if p["action"] == "Research"][:research_limit]:
        research.run(conn, p["org_id"], web=use_web)
        did.append(f"researched {p['org']}")

    if clay.configured():                         # 3. send orgs with no usable email to Clay
        asked = []
        for p in [p for p in run(conn) if p["action"].startswith("Find the decider")]:
            clay.request(conn, p["org_id"])
            asked.append(p["org"])
        if asked:
            did.append(f"sent {len(asked)} to Clay for emails ({', '.join(asked)})")

    props = run(conn)                             # 4. pre-draft everything that needs a message
    drafted = []
    for p in props:
        if len(drafted) >= draft_limit:
            break
        a = p["action"]
        if a.startswith(("Draft", "Book the call", "Answer their question", "Re-open", "Intro is on", "Ask ")):
            d, _ = draft.run(conn, p["org_id"], intro=a.startswith("Ask "))
            if d:
                drafted.append(f"#{d['id']} {p['org']} ({d['purpose']})")

    props = run(conn)
    replied = [p for p in props if p["bucket"] == "replied"]
    waiting = [p for p in props if p["bucket"] == "waiting on you"]
    n = {r["stage"]: r["c"] for r in conn.execute("SELECT stage, COUNT(*) AS c FROM pipeline GROUP BY stage")}
    lines = [f"Morning Aaron. {n.get('live', 0)} live on Pebble, {n.get('onboarding', 0) + n.get('signed_up', 0)} onboarding, "
             f"{n.get('replied', 0) + n.get('meeting', 0)} in conversation, {n.get('contacted', 0)} waiting on a reply."]
    if did:
        lines.append("Since yesterday:\n" + "\n".join(f"• {d}" for d in did))
    if drafted:
        lines.append("Drafts ready for you (`show <#>`, `ok <#>` to approve):\n" + "\n".join(f"• {d}" for d in drafted))
    if replied:
        lines.append("Replies waiting: " + ", ".join(p["org"] for p in replied))
    if len(waiting) > len(drafted):
        lines.append(f"{len(waiting)} drafts waiting on you in total.")
    if not (did or drafted or replied):
        lines.append("Nothing urgent. Say `prospect <where>` if you want me to go find more.")
    return "\n\n".join(lines)


def metric(conn):
    """Reply rate by touch: of messages sent as touch N, how many got a reply."""
    rows = conn.execute("""SELECT touch, COUNT(*) AS sent, SUM(replied_at IS NOT NULL) AS replied
                           FROM drafts WHERE status = 'sent' AND purpose IN ('outreach','reply') AND touch > 0
                           GROUP BY touch ORDER BY touch""").fetchall()
    return [(r["touch"], r["replied"], r["sent"]) for r in rows]
