"""Draft agent.

Writes the next message in Aaron's voice for wherever an org is in the funnel:
  outreach      first touch, touch 2, touch 3 (close-out)
  reply         answering a positive reply (offer times) or a question (pricing)
  intro_ask     to a live customer: "would you ask X if they're open to hearing from me?" (double opt-in)
  activation    to a signed-up org, aimed at their next milestone: event created → applications open → first payment
  referral_ask  to a live customer: "who's one person you'd introduce me to?"

It only writes drafts. Nothing in this project sends anything to a prospect.

With Claude available it works as an agent: it starts from the rules-based draft, reads Aaron's past
edits to learn his voice, writes, runs the same checks the evals use, revises until they pass, then
saves. If it can't produce a draft that passes, Aaron gets the rules-based draft instead.

Voice rules (checks.py enforces them): under 90 words, "Hi <first>," and signed Aaron, the specific
trigger up front, exactly one question, no numbers the brief doesn't support, no hype words.
"""

import json

from . import checks, db, llm, research

ASK = {
    "vendor_apps_open": "Open to a 15-minute walkthrough before the applications pile up?",
    "tool_pain": "Want me to set up your next event on it so you can compare?",
    "new_event": "Want me to set up the new event on it so you can see it with your own vendors?",
    "venue_booking": "Open to a 15-minute look at how it would work for your organizers?",
    "hiring": "Worth 15 minutes before you finalize the role?",
    "grant": "Open to a 15-minute look at the reporting side?",
}


def first_name(org):
    return org["decision_maker"].split()[0]


def value_line(org):
    if org["segment"] == "multi_festival":
        n = f"all {org['events_per_year']} of your events" if org["events_per_year"] else "every one of your events"
        return (f"I built Pebble to run the vendor side of {n} from one dashboard: applications, approvals, booths, "
                "payments and vendor messages. It's free to start; we take about 10% of vendor registration fees, "
                "so there's nothing up front.")
    if org["segment"] == "venue":
        n = f"all {org['events_per_year']} events" if org["events_per_year"] else "every event"
        return (f"I built Pebble so every organizer who books {org['name']} gets a ready vendor system "
                f"(applications, booths, payments, messages), and you see live vendor sales across {n}. "
                "Free to start; we take about 10% of vendor fees.")
    return (f"I built Pebble to run vendor applications, booths and payments for events like {org['flagship_event']}, "
            "with live vendor sales numbers you can hand to your board and funders. Free to start; we take about "
            "10% of vendor fees.")


# ---- Rules-based drafts: the fallback, the agent's starting point, and what the evals check offline ----

def compose(org, trigger, touch, reply_kind=None, extra_signal=None, intro_from=None):
    hi = f"Hi {first_name(org)},"
    if reply_kind == "positive":
        return (f"{hi}\n\nThanks, glad the timing works. Does Tuesday or Wednesday afternoon work for 20 minutes? "
                f"I'll have {org['flagship_event']} set up so it's your vendors on screen, not a demo account.\n\nAaron")
    if reply_kind == "neutral":
        return (f"{hi}\n\nShort version: the basic plan is free, and Pebble takes about 10% of vendor registrations "
                f"and ticket sales. Pro is $249 a month when you want it, and there's no setup fee. Want me to set up "
                f"{org['flagship_event']} so you can see it with your own vendors?\n\nAaron")
    if touch == 1 and intro_from:
        return (f"{hi}\n\n{intro_from['decision_maker'].split()[0]} at {intro_from['name']} suggested I reach out. "
                f"They run their vendors on Pebble.\n\n{value_line(org)}\n\nOpen to a 15-minute look?\n\nAaron\nPebble")
    if touch == 1:
        return f"{hi}\n\nNoticed {trigger['detail']}.\n\n{value_line(org)}\n\n{ASK[trigger['kind']]}\n\nAaron\nPebble"
    if touch == 2:
        also = f" Also saw {extra_signal['detail']}." if extra_signal else ""
        tools = org["current_tools"] or "what you use today"
        return (f"{hi}\n\nFollowing up on {org['flagship_event']}.{also} I can set up your next event on Pebble "
                f"at no cost so you can compare it side by side with {tools}.\n\nWorth a try?\n\nAaron")
    return (f"{hi}\n\nI'll stop here. If vendor applications and payments for {org['flagship_event']} ever get "
            "painful, reply \"pebble\" and I'll set it up for you.\n\nShould I close this out for now?\n\nAaron")


def compose_intro_ask(customer, target):
    who = target["decision_maker"] or "whoever runs events"
    return (f"Hi {first_name(customer)},\n\nQuick favor. I'd like to meet {who} at {target['name']} in {target['city']}, "
            f"because Pebble could do for the organizers there what it does for {customer['flagship_event']}. "
            "If you know them and you're comfortable, would you ask whether they're open to hearing from me? "
            "No pressure either way.\n\nAaron")


def compose_activation(org, next_milestone):
    hi, ev = f"Hi {first_name(org)},", org["flagship_event"]
    if next_milestone == "event_created":
        return (f"{hi}\n\nGlad you're on Pebble. The fastest way to see it work is with {ev}. Send me last year's "
                "vendor list (any spreadsheet is fine) and I'll set the event up for you this week. Want me to do that?\n\nAaron")
    if next_milestone == "apps_open":
        return (f"{hi}\n\nSaw {ev} is set up in Pebble. Next step is opening vendor applications, and I can import "
                "your past vendors so they get the link on day one. Should I do that today?\n\nAaron")
    return (f"{hi}\n\nApplications for {ev} are open. Once you approve vendors, their booth fees are collected in Pebble "
            "automatically. Want to look over your approval settings together for 10 minutes?\n\nAaron")


def compose_referral_ask(org):
    return (f"Hi {first_name(org)},\n\nNow that {org['flagship_event']} is collecting vendor payments in Pebble, I'm "
            "looking for the next few organizers to bring on: venues you book, your chamber, or another producer you "
            "trust. Is there one person you'd introduce me to?\n\nAaron")


# ---- The agent ----

WRITER = """You write short messages for Aaron, founder of Pebble (an AI platform that runs a festival's or market's whole
vendor ecosystem: applications, approvals, booths, payments, vendor messages, live sales data, plus ticketing).
Aaron writes like a founder texting a peer: plain, warm, specific, no marketing language.

You get a starting draft, the brief, the thread so far, Aaron's past edits and his stated preferences. Write the
message he'd actually send. Learn from his edits: they show what he cuts and how he phrases things.
Process: write it, call check_draft, fix every problem it reports, and when it passes call save_draft. Then stop.
Hard rules: under 90 words; open "Hi <first name>,"; end with "Aaron" on its own line; exactly one question mark;
keep the specific trigger; no number that isn't in the brief or starting draft; never invent customers, stats or results."""


def write_with_claude(conn, recipient, baseline, brief_body, purpose, thread, instructions=None):
    edits = [json.loads(e) for e in db.recall(conn, "voice_edit", 5)]
    prefs = db.recall(conn, "preference", 10)
    saved = {}

    def check_draft(text):
        problems = checks.draft_problems(text, recipient, brief_body + "\n" + baseline)
        return "passes" if not problems else "Fix: " + "; ".join(problems)

    def save_draft(text):
        problems = checks.draft_problems(text, recipient, brief_body + "\n" + baseline)
        if problems:
            raise ValueError("Not saved. Fix: " + "; ".join(problems))
        saved["text"] = text.strip()
        return "saved"

    context = {
        "purpose": purpose, "to": f"{recipient['decision_maker']}, {recipient['decision_maker_title']} at {recipient['name']}",
        "starting_draft": baseline, "brief": brief_body, "thread_so_far": thread,
        "aarons_edits": [{"he_got": e["before"], "he_sent": e["after"]} for e in edits],
        "aarons_preferences": prefs, "instructions_from_aaron": instructions,
    }
    tools = [llm.tool("check_draft", "Run the voice and accuracy checks.", {"text": {"type": "string"}}, ["text"]),
             llm.tool("save_draft", "Save the final draft. Rejected if checks fail.", {"text": {"type": "string"}}, ["text"])]
    llm.run_agent(WRITER, [{"role": "user", "content": json.dumps(context, indent=1)}], tools,
                  {"check_draft": check_draft, "save_draft": save_draft}, effort="high", max_steps=8)
    return saved.get("text")


def _thread(conn, org_id):
    return [{"touch": d["touch"], "purpose": d["purpose"], "sent": d["sent_at"], "body": d["body"],
             "reply": d["reply_kind"]}
            for d in conn.execute("SELECT * FROM drafts WHERE org_id = ? AND status = 'sent' ORDER BY id", (org_id,))]


def run(conn, org_id, force=False, instructions=None, use_llm=None, intro=False):
    """Create the next draft for an org. Returns (draft dict, note) or (None, reason)."""
    from . import tracking
    org = conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
    pending = conn.execute("SELECT id FROM drafts WHERE org_id = ? AND status IN ('draft','approved')", (org_id,)).fetchone()
    if pending:
        return None, f"Draft #{pending['id']} is still waiting on you. Approve, edit or discard it first."
    stage = conn.execute("SELECT stage FROM pipeline WHERE org_id = ?", (org_id,)).fetchone()["stage"]
    if stage == "lost":
        return None, f"{org['name']} said no. Nothing to draft."

    brief = research.latest_brief(conn, org_id)
    sent = conn.execute("""SELECT * FROM drafts WHERE org_id = ? AND status = 'sent' AND via_org_id IS NULL
                           AND purpose IN ('outreach','reply') ORDER BY id""", (org_id,)).fetchall()
    recipient, via, trigger, touch = org, None, None, 0

    if stage in ("signed_up", "onboarding"):
        m = tracking.milestones(conn, org_id)
        nxt = next(x for x in db.MILESTONES if x not in m)
        purpose, baseline = "activation", compose_activation(org, nxt)
    elif stage == "live":
        purpose, baseline = "referral_ask", compose_referral_ask(org)
    elif intro:
        via = research.warm_path(conn, org)
        if via is None:
            return None, f"No live customer near {org['name']} to ask for an intro."
        purpose, recipient, baseline = "intro_ask", via, compose_intro_ask(via, org)
    else:
        if not org["decision_maker"]:
            return None, f"Don't know who decides at {org['name']} yet. Try *research {org_id} web*."
        intro_ok = conn.execute("""SELECT o.* FROM drafts d JOIN orgs o ON o.id = d.via_org_id WHERE d.org_id = ?
                                   AND d.purpose = 'intro_ask' AND d.reply_kind = 'positive' ORDER BY d.id DESC LIMIT 1""",
                                (org_id,)).fetchone()
        touch = (sent[-1]["touch"] + 1) if sent else 1
        if stage == "cold":
            touch = 1   # re-opening a cold org starts a fresh thread off a new trigger
        reply_kind = sent[-1]["reply_kind"] if sent and sent[-1]["reply_kind"] in ("positive", "neutral") else None

        if brief is None or brief["body"] == "(historical brief)" or stage == "cold":
            research.run(conn, org_id)
            brief = research.latest_brief(conn, org_id)
        if brief["why_now_signal_id"]:
            trigger = conn.execute("SELECT * FROM signals WHERE id = ?", (brief["why_now_signal_id"],)).fetchone()
        if touch == 1 and trigger is None and not reply_kind and not intro_ok:
            if not force:
                return None, (f"HOLD: {org['name']} has no fresh trigger, so a first message would be generic. "
                              f"Add one with `signal {org_id} <kind> <detail>`, or `draft {org_id} force`.")
            trigger = {"detail": f"you run {org['events_per_year']} events a year including {org['flagship_event']}",
                       "kind": "new_event"}
        extra = None
        if touch == 2:
            extra = conn.execute("SELECT * FROM signals WHERE org_id = ? AND seen_on > ? ORDER BY seen_on DESC LIMIT 1",
                                 (org_id, sent[-1]["sent_at"])).fetchone()
        purpose = "reply" if reply_kind else "outreach"
        baseline = compose(org, trigger, touch, reply_kind, extra, intro_from=intro_ok if touch == 1 else None)

    brief_body = brief["body"] if brief else ""
    body, note = baseline, "rules"
    if use_llm if use_llm is not None else llm.available():
        try:
            written = write_with_claude(conn, recipient, baseline, brief_body, purpose, _thread(conn, org_id), instructions)
            body, note = (written, "written by Claude, passed checks") if written else (baseline, "Claude's draft failed checks; rules version")
        except Exception as e:      # no key, network down: the rules draft still works
            note = f"rules (Claude unavailable: {type(e).__name__})"

    draft_id = conn.execute(
        """INSERT INTO drafts (org_id, via_org_id, brief_id, touch, purpose, body, status, created_at)
           VALUES (?,?,?,?,?,?, 'draft', ?)""",
        (org_id, via["id"] if via else None, brief["id"] if brief else None, touch, purpose, body, db.now())).lastrowid
    db.log(conn, "draft", "draft", org_id, f"#{draft_id} {purpose} touch {touch} ({note})")
    tracking.refresh_stage(conn, org_id)
    tracking.set_next(conn, org_id, f"Approve draft #{draft_id}", db.today().isoformat())
    conn.commit()
    d = dict(conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone())
    status = "" if recipient["email_status"] == "valid" or not recipient["email"] else f", {recipient['email_status']}"
    d["to"] = f"{recipient['decision_maker']} <{recipient['email'] or 'no email yet'}{status}> at {recipient['name']}"
    return d, note


def metric(conn):
    """Share of approved drafts Aaron sent without editing. Low = the voice is off."""
    r = conn.execute("""SELECT COUNT(*) AS n, SUM(CASE WHEN edited = 0 THEN 1 ELSE 0 END) AS clean
                        FROM drafts WHERE approved_at IS NOT NULL AND body NOT LIKE '(historical%'""").fetchone()
    return (r["clean"] or 0, r["n"] or 0)
