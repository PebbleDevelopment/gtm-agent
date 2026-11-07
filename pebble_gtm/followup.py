"""Follow-up agent.

Watches every org from first touch to live on Pebble and proposes the next step, with the reason in plain words.

Before signup: a reply gets answered today. A quiet thread gets a second note after 4 days, a friendly close-out
7 days after that, and goes cold 14 days later. A cold org comes back only when it has new news. A "not now" is
a date to come back (from the playbook), not a no. Venues and civic groups near a live customer get an intro ask
before any cold note.

After signup: each Pebble step has a deadline. Signed up with no event after 2 days, an event with no applications
open after 3, applications open with no payment after 7: each gets a nudge offering to do that step for them. Once
live, the org gets a referral ask, because live customers are the best source of the next ones.

morning() is the proactive part. Once a day it does the work (checks Gmail, prospects, researches, sends orgs
missing an email to Clay, pre-drafts) and writes Aaron's morning WhatsApp. It only ever drafts. Approving is up to Aaron.
"""

import json
import os
from datetime import date, timedelta

from . import db, llm, playbook, research, tracking

WAIT_DAYS = {1: 4, 2: 7, 3: 14}   # days of silence after note N before the next step
MAX_TOUCHES = 3
ACTIVATION_DAYS = {"event_created": 2, "apps_open": 3, "first_payment": 7}   # days allowed to reach each step
REFERRAL_EVERY_DAYS = 60
STEP_WORDS = {"signed_up": "signed up", "event_created": "set up their first event",
              "apps_open": "opened vendor applications", "first_payment": "taken a first vendor payment"}
NOTE_WORDS = {1: "first note", 2: "second note", 3: "close-out note"}

# Proposal kinds that the morning pass drafts a message for.
DRAFTABLE = {"book", "answer", "intro_on", "nudge", "referral", "intro_ask", "revisit", "reopen", "follow_up", "first"}


def _days(since, today):
    return (today - date.fromisoformat(since[:10])).days


def _ago(n):
    return "today" if n == 0 else "yesterday" if n == 1 else f"{n} days ago"


def propose_for(conn, org, today):
    """One proposal for an org, or None if nothing to do. Every proposal has a kind, an action and a reason."""
    p = conn.execute("SELECT * FROM pipeline WHERE org_id = ?", (org["id"],)).fetchone()
    prop = lambda kind, action, reason, bucket, due=today: {
        "org_id": org["id"], "org": org["name"], "kind": kind, "action": action, "reason": reason,
        "bucket": bucket, "due": due.isoformat()}
    if p["stage"] == "meeting":
        return None
    if p["stage"] == "lost":
        return _revisit(conn, org, today, prop)

    drafts = conn.execute("SELECT * FROM drafts WHERE org_id = ? ORDER BY id", (org["id"],)).fetchall()
    open_drafts = [d for d in drafts if d["status"] in ("draft", "approved")]
    if open_drafts:
        d = open_drafts[-1]
        kind = playbook.PURPOSE_WORDS.get(d["purpose"], "note")
        if d["status"] == "draft":
            return prop("approve", f"approve the {kind} (`ok {d['id']}`)", f"ready {_ago(_days(d['created_at'], today))}",
                        "waiting on you")
        return prop("send", f"send the {kind} you approved", "approved but not sent yet", "waiting on you")

    # After signup: push toward the next step.
    if p["stage"] in ("signed_up", "onboarding", "live"):
        m = tracking.milestones(conn, org["id"])
        if p["stage"] == "live":
            last_ask = conn.execute("SELECT MAX(created_at) FROM drafts WHERE org_id = ? AND purpose = 'referral_ask'",
                                    (org["id"],)).fetchone()[0]
            if last_ask is None or _days(last_ask, today) >= REFERRAL_EVERY_DAYS:
                return prop("referral", "ask who else they'd introduce you to", "live and happy", "activation")
            return None
        nxt = next(x for x in db.MILESTONES if x not in m)
        prev = db.MILESTONES[db.MILESTONES.index(nxt) - 1]
        stuck = _days(m[prev], today)
        last_nudge = conn.execute("SELECT MAX(created_at) FROM drafts WHERE org_id = ? AND purpose = 'activation'",
                                  (org["id"],)).fetchone()[0]
        if stuck >= ACTIVATION_DAYS[nxt] and (last_nudge is None or last_nudge[:10] < m[prev]):
            help_with = {"event_created": "set up their first event", "apps_open": "open vendor applications",
                         "first_payment": "get their first vendor payment"}[nxt]
            return prop("nudge", f"help them {help_with}",
                        f"{STEP_WORDS[prev]} {_ago(stuck)} and haven't {STEP_WORDS[nxt]} yet", "activation")
        return None

    sent = [d for d in drafts if d["status"] == "sent" and d["purpose"] in ("outreach", "reply")]
    intro = next((d for d in reversed(drafts) if d["purpose"] == "intro_ask" and d["status"] == "sent"), None)
    if intro and not sent:
        if intro["reply_kind"] == "positive":
            return prop("intro_on", "send the first note now that the intro is on", "your customer said they'd introduce you", "replied")
        if intro["reply_kind"] is None and _days(intro["sent_at"], today) < 7:
            return None   # give the customer a week before going direct
    if sent:
        last = sent[-1]
        if last["reply_kind"] == "negative":
            return None
        if last["reply_kind"] == "positive":
            return prop("book", "book the call", f"said yes {_ago(_days(last['replied_at'], today))}", "replied")
        if last["reply_kind"] == "neutral":
            note = conn.execute("SELECT detail FROM activity WHERE org_id = ? AND action = 'reply' ORDER BY id DESC LIMIT 1",
                                (org["id"],)).fetchone()
            q = f'asked: "{note["detail"].split(": ", 1)[-1]}"' if note else "asked a question"
            return prop("answer", "answer their question", q, "replied")

        quiet = _days(last["sent_at"], today)
        said = NOTE_WORDS.get(last["touch"], "last note")
        if p["stage"] == "cold":
            new = conn.execute("SELECT * FROM signals WHERE org_id = ? AND seen_on > ? ORDER BY seen_on DESC LIMIT 1",
                               (org["id"], last["sent_at"][:10])).fetchone()
            if new:
                return prop("reopen", "write a fresh first note", f"went quiet, but now: {new['detail']}", "cold")
            return None
        wait = WAIT_DAYS[min(last["touch"], MAX_TOUCHES)]
        due = date.fromisoformat(last["sent_at"][:10]) + timedelta(days=wait)
        if last["touch"] >= MAX_TOUCHES:
            if quiet >= wait:
                tracking.set_stage(conn, org["id"], "cold")
                db.log(conn, "followup", "cold", org["id"], f"no reply {quiet}d after touch {last['touch']}")
                return prop("cold", "let it rest", "no reply to three notes; I'll flag them if they have news", "cold")
            return None
        if quiet >= wait:
            nxt = "send a friendly close-out" if last["touch"] == 2 else "send a follow-up with something new"
            return prop("follow_up", nxt, f"quiet for {quiet} days since your {said}", "un-replied")
        return prop("wait", f"give it until {due:%b %-d}", f"{said} went out {_ago(quiet)}", "un-replied", due)

    brief = research.latest_brief(conn, org["id"])
    if brief is None:
        found = f"the prospect agent found them ({org['source'].removeprefix('prospect: ')})" if org["source"].startswith("prospect") else "haven't looked into them yet"
        return prop("research", "look into them", found, "new")
    if research.fit_score(org) < 50:
        return None
    warm = research.warm_path(conn, org)
    if warm and not intro:
        return prop("intro_ask", f"ask {playbook.who(warm)} for an intro", f"they're a live customer in {warm['city']}", "new")
    if not org["email"] or not org["decision_maker"] or org["email_status"] == "invalid":
        from . import clay
        if clay.needs_enrichment(conn, org):
            missing = "who decides" if not org["decision_maker"] else "an email" if not org["email"] else "a working email"
            return prop("enrich", f"find their email (`enrich {org['id']}`)", f"good fit, but I don't have {missing}", "new")
        return None   # waiting on Clay
    if brief["why_now"]:
        return prop("first", "send a first note", brief["why_now"].split(" (")[0], "new")
    return None   # researched, no trigger: hold until there's news


def _revisit(conn, org, today, prop):
    """A 'not now' is a date, not a no. Come back when the playbook says to."""
    from . import draft
    objection = draft.last_objection(conn, org["id"])
    rule = playbook.OBJECTIONS.get(objection or "", {})
    if not rule.get("revisit_days"):
        return None
    last = conn.execute("SELECT MAX(COALESCE(replied_at, sent_at)) FROM drafts WHERE org_id = ? AND status = 'sent'",
                        (org["id"],)).fetchone()[0]
    if not last or conn.execute("SELECT 1 FROM drafts WHERE org_id = ? AND status IN ('draft','approved')",
                                (org["id"],)).fetchone():
        return None
    due = date.fromisoformat(last[:10]) + timedelta(days=rule["revisit_days"])
    reason = {"existing_tool": "they were locked into another tool", "timing": "the timing wasn't right"}.get(objection, "not now")
    return prop("revisit", "check back in", f"they said not now ({reason})", "cold", due)


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


# New organizers to add per day, from Instagram and the web together. Override with DAILY_LEADS in .env.
DAILY_LEADS = 5


def morning(conn, leads=None, research_limit=6, draft_limit=8):
    """The daily proactive pass. Does the work, then returns the WhatsApp message for Aaron."""
    from . import clay, draft, gmail, instagram, prospect
    use_web = llm.available()
    leads = leads if leads is not None else int(os.environ.get("DAILY_LEADS", DAILY_LEADS))
    did = []

    if gmail.configured():                        # 1. catch up on replies first
        try:
            notes = gmail.check(conn)
            if notes:
                did.append(f"picked up {len(notes)} new {'email' if len(notes) == 1 else 'emails'} from the pipeline")
        except Exception as e:
            did.append(f"couldn't reach Gmail ({type(e).__name__})")

    if instagram.configured():                    # 2. news from organizers' Instagram, and new ones by hashtag
        try:
            news = instagram.watch(conn)
            if news:
                did.append("spotted fresh Instagram posts from " + ", ".join(n["org"] for n in news))
            posts, added = instagram.discover(conn, limit=min(3, leads))
            leads -= len(added)
            if added:
                did.append(f"found {', '.join(n for _, n, _ in added)} through Instagram vendor calls")
            elif posts and not use_web:
                did.append(f"saw {len(posts)} new vendor calls on Instagram, but working out who posted them needs Claude")
        except Exception as e:
            did.append(f"couldn't reach Instagram ({type(e).__name__})")

    if use_web and leads > 0:                     # 3. find new orgs, up to the daily total
        try:
            added, _ = prospect.run(conn, limit=leads)
            if added:
                did.append("found " + ", ".join(n for _, n, _ in added))
        except Exception as e:
            did.append(f"couldn't finish prospecting ({type(e).__name__})")

    props = run(conn)                             # 4. research the ones with no brief
    looked = [p["org"] for p in props if p["kind"] == "research"][:research_limit]
    for p in [p for p in props if p["kind"] == "research"][:research_limit]:
        research.run(conn, p["org_id"], web=use_web)
    if looked:
        did.append("looked into " + ", ".join(looked))

    if clay.configured():                         # 5. send orgs with no usable email to Clay
        asked = [p for p in run(conn) if p["kind"] == "enrich"]
        for p in asked:
            clay.request(conn, p["org_id"])
        if asked:
            did.append(f"asked Clay for emails at {', '.join(p['org'] for p in asked)}")

    drafted = []                                  # 6. pre-draft everything that needs a message
    for p in run(conn):
        if len(drafted) >= draft_limit:
            break
        if p["kind"] in DRAFTABLE:
            d, _ = draft.run(conn, p["org_id"], intro=p["kind"] == "intro_ask")
            if d:
                drafted.append({"id": d["id"], "org_id": p["org_id"], "who": d["to"], "kind": p["kind"],
                                "reason": p["reason"], "org": p["org"]})

    return brief(conn, run(conn), did, drafted)


# ---- The morning message: what matters, in Aaron's terms ----

def _line(conn, d):
    """One human sentence for a draft that's ready, in priority order."""
    org = conn.execute("SELECT * FROM orgs WHERE id = ?", (d["org_id"],)).fetchone()
    name, k, r, ok = playbook.who(org), d["kind"], d["reason"], f" (ok {d['id']})"
    first = playbook.first(org["decision_maker"])
    return {
        "book": f"{name} said yes. Lock in a time while it's warm; I offered two{ok}.",
        "answer": f"{name} {_asked(r)}, so I drafted a straight answer{ok}.",
        "intro_on": f"The intro to {name} is on, so I wrote the first note leading with it{ok}.",
        "nudge": f"{org['name']} {r}. I offered to do it for {first}{ok}.",
        "referral": f"{org['name']} is live and happy, the best moment to ask who else they know{ok}.",
        "intro_ask": f"{org['name']} is a strong lead, and your customer {d['who']} can introduce you. Warm beats cold here{ok}.",
        "revisit": f"{name} {r}. It's been long enough to check back{ok}.",
        "reopen": f"{org['name']} went quiet, but there's news: {r.split('now: ', 1)[-1]}. Worth a fresh note{ok}.",
        "follow_up": f"{name} has been {r}. I wrote a follow-up that adds something new{ok}.",
        "first": f"{org['name']} has a reason to hear from you now: {r[0].lower() + r[1:]}{ok}.",
    }.get(k)


def _asked(reason):
    what = {"price": "asked what it costs", "how_it_works": "asked how it works", "not_decider": "needs to loop in someone else",
            "existing_tool": "asked about switching from their current setup", "timing": "asked about timing"}
    return what.get(playbook.detect_objection(reason), "asked a question")


def _short(conn, d):
    """A few words for a ready draft, for the 'also ready' line."""
    org = conn.execute("SELECT * FROM orgs WHERE id = ?", (d["org_id"],)).fetchone()
    what = {"referral": "a referral ask to", "follow_up": "a follow-up to", "first": "a first note to", "reopen": "a fresh note to",
            "revisit": "a check-in with", "intro_ask": "an intro ask for", "nudge": "a setup nudge for"}.get(d["kind"], "a note to")
    target = org["name"] if d["kind"] in ("referral", "intro_ask", "nudge") else playbook.who(org)
    return f"{what} {target} (ok {d['id']})"


def brief(conn, props, did=(), drafted=()):
    """The morning WhatsApp. Claude writes it from the facts when available; the rules version reads fine too."""
    n = {r["stage"]: r["c"] for r in conn.execute("SELECT stage, COUNT(*) AS c FROM pipeline GROUP BY stage")}
    order = {k: i for i, k in enumerate(["book", "answer", "intro_on", "nudge", "intro_ask", "first", "revisit",
                                         "referral", "reopen", "follow_up"])}
    drafted = sorted(drafted, key=lambda d: order.get(d["kind"], 99))
    items = [x for x in (_line(conn, d) for d in drafted) if x]
    fresh = {d["org_id"] for d in drafted}
    waiting = sum(1 for p in props if p["kind"] in ("approve", "send") and p["org_id"] not in fresh)
    if llm.available() and items:
        try:
            facts = {"pipeline_counts": n, "what_you_did_overnight": list(did), "priorities_in_order": items,
                     "older_drafts_still_waiting": waiting}
            text, _ = llm.run_agent(MORNING_SYSTEM, [{"role": "user", "content": json.dumps(facts, indent=1)}], [], {},
                                    effort="medium", max_steps=1)
            if text and all(f"ok {d['id']}" in text for d in drafted):
                return text
        except Exception:
            pass

    live, setting_up = n.get("live", 0), n.get("onboarding", 0) + n.get("signed_up", 0)
    opener = f"Morning Aaron. {live} {'org is' if live == 1 else 'orgs are'} live on Pebble"
    opener += f" and {setting_up} {'is' if setting_up == 1 else 'are'} getting set up." if setting_up else "."
    out = [opener]
    if items:
        out.append("Here's what I'd do first:\n" + "\n".join(f"• {i}" for i in items[:3]))
        rest = [_short(conn, d) for d in drafted[3:]]
        if rest:
            out.append("Also ready: " + (", ".join(rest[:-1]) + ", and " + rest[-1] if len(rest) > 1 else rest[0]) + ".")
    else:
        out.append('Quiet day. Nobody\'s waiting on you. Say "find more venues near Chattanooga" and I\'ll go look.')
    if did:
        out.append("Since yesterday I " + "; ".join(did) + ".")
    if waiting:
        out.append(f"{waiting} older {'draft is' if waiting == 1 else 'drafts are'} still waiting on you.")
    return "\n\n".join(out)


MORNING_SYSTEM = """You write Aaron's morning WhatsApp from his GTM agent. You get facts about his pipeline, already in
priority order. Write it the way a sharp GTM lead texts the founder they work for at 8am.

""" + playbook.AARON_STYLE + """

Shape: open with the single most important thing and what you'd do about it. Then the next two or three priorities,
each with its reason. Mention what you did overnight in one line if it matters. Keep every "(ok N)" exactly as given,
next to the right person, so he can approve from his phone. Under 140 words. Never invent facts."""


def metric(conn):
    """Reply rate by note: of messages sent as note N, how many got a reply."""
    rows = conn.execute("""SELECT touch, COUNT(*) AS sent, SUM(replied_at IS NOT NULL) AS replied
                           FROM drafts WHERE status = 'sent' AND purpose IN ('outreach','reply') AND touch > 0
                           GROUP BY touch ORDER BY touch""").fetchall()
    return [(r["touch"], r["replied"], r["sent"]) for r in rows]
