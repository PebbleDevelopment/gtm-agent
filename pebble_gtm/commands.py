"""Front door for every message, from WhatsApp or the terminal.

Exact commands (`today`, `ok 14`, `sent 14`, ...) run here directly, so they're instant and free.
Anything else is plain conversation and goes to the assistant agent (assistant.py).

Approving and marking sent only ever happen here, from Aaron's own words. No agent has a tool for them.
"""

import json
import re

from . import db, draft, followup, llm, prospect, research, tracking

HELP = """*Pebble GTM*. Just text me normally, e.g. "find venues near Chattanooga" or "Dana replied, wants a call".
Shortcuts:
*today*: what to do next, with reasons · *morning*: run the daily pass now
*pipeline* · *orgs* · *metrics*
*prospect* [where]: find new orgs that fit · *enrich* <org>: Clay finds the decider's email
*inbox*: check Gmail for replies and sends now
*research* <org> [web] · *brief* <org>
*draft* <org> [force] · *intro* <org>: ask a live customer to introduce you
*show* <#> · *ok* <#> (approve) · *edit* <#> <text> · *discard* <#>
*sent* <#>: you sent it yourself (automatic if Gmail is connected)
*replied* <org> positive|neutral|negative [note]
*milestone* <org> signed_up|event_created|apps_open|first_payment
*meeting* / *lost* <org> [note] · *signal* <org> <kind> <detail>
*add* name | segment | city, ST | events/yr | flagship | decider | title

Nothing is ever sent to anyone. Approved drafts come back here for you to send."""

KINDS = ", ".join(research.SIGNAL_WEIGHT)
APPROVE = re.compile(r"^(ok|okay|approve|approved|yes|y|👍|✅)\s*#?(\d+)$", re.IGNORECASE)


def _split_flag(rest, flags):
    words = rest.split()
    if words and words[-1].lower() in flags:
        return " ".join(words[:-1]), words[-1].lower()
    return rest, None


def _org(conn, ref):
    org = db.find_org(conn, ref) if ref else None
    if org is None:
        raise ValueError(f"No org matches '{ref}'. Try *orgs*.")
    return org


def _org_then_words(conn, rest):
    """Longest leading run of words that names an org, then the rest."""
    words = rest.split()
    for i in range(len(words), 0, -1):
        org = db.find_org(conn, " ".join(words[:i]))
        if org:
            return org, words[i:]
    raise ValueError(f"No org matches '{rest}'.")


def _draft_row(conn, ref):
    ref = ref.strip().lstrip("#")
    d = conn.execute("SELECT d.*, o.name, o.email FROM drafts d JOIN orgs o ON o.id = COALESCE(d.via_org_id, d.org_id) "
                     "WHERE d.id = ?", (int(ref),)).fetchone() if ref.isdigit() else None
    if d is None:
        raise ValueError(f"No draft #{ref}.")
    return d


def dispatch(conn, text):
    text = (text or "").strip()
    m = APPROVE.match(text)
    cmd, _, rest = text.partition(" ")
    cmd, rest = cmd.lower(), rest.strip()
    try:
        if m:
            reply = cmd_approve(conn, m.group(2))
        elif cmd in HANDLERS:
            reply = HANDLERS[cmd](conn, rest)
        elif llm.available():
            from . import assistant
            try:
                return assistant.chat(conn, text)
            except Exception as e:      # no key, network down: shortcuts still work
                return f"Couldn't reach Claude ({type(e).__name__}: {e}). Shortcuts still work:\n\n{HELP}"
        else:
            return HELP + "\n\n(Plain-language chat needs Claude: pip install anthropic and set ANTHROPIC_API_KEY.)"
    except ValueError as e:
        reply = str(e)
    from . import assistant          # keep shortcuts in the conversation so the assistant knows what happened
    assistant.log_turn(conn, "user", text)
    assistant.log_turn(conn, "assistant", reply)
    return reply


def cmd_today(conn, rest):
    props = followup.run(conn)
    if not props:
        return "Nothing due today."
    out, bucket = ["*Today*"], None
    for p in props:
        if p["bucket"] != bucket:
            bucket = p["bucket"]
            out.append(f"\n_{bucket}_")
        out.append(f"• *{p['org']}* (#{p['org_id']}): {p['action']}\n  why: {p['reason']}")
    return "\n".join(out)


def cmd_morning(conn, rest):
    return followup.morning(conn)


def cmd_pipeline(conn, rest):
    followup.run(conn)
    out = ["*Pipeline*  (stage · last contact · next)"]
    for r in tracking.table(conn):
        nxt = r["next_action"] or r["outcome"] or "-"
        out.append(f"#{r['id']} {r['name']}: *{r['stage']}* · {r['last_contact'] or '-'} · {nxt}")
    return "\n".join(out)


def cmd_orgs(conn, rest):
    rows = conn.execute("SELECT id, name, segment FROM orgs ORDER BY id").fetchall()
    return "\n".join(f"#{r['id']} {r['name']} ({r['segment']})" for r in rows)


def cmd_prospect(conn, rest):
    from . import clay
    added, summary = prospect.run(conn, rest or None)
    if not added:
        return summary or "Found nothing solid that time."
    sent = [i for i, _, _ in added if clay.configured() and clay.needs_enrichment(conn, db.find_org(conn, i))]
    for i in sent:
        clay.request(conn, i)
    return (f"Added {len(added)}:\n" + "\n".join(f"#{i} {n}: {why}" for i, n, why in added)
            + (f"\n\nSent {len(sent)} to Clay for emails." if sent else "")
            + "\n\nNext: *research <#> web* or wait for tomorrow's morning pass.")


def cmd_enrich(conn, rest):
    from . import clay
    return clay.request(conn, _org(conn, rest)["id"])


def cmd_inbox(conn, rest):
    from . import gmail
    notes = gmail.check(conn)
    return "\n".join(notes) if notes else "Nothing new in Gmail from anyone in the pipeline."


def cmd_research(conn, rest):
    ref, flag = _split_flag(rest, {"web"})
    org = _org(conn, ref)
    brief = research.run(conn, org["id"], web=flag == "web")
    note = f"\n\n_{brief['agent_note']}_" if brief.get("agent_note") else ""
    return brief["body"] + note + (f"\n\nNext: *draft {org['id']}*" if brief["why_now"] else "")


def cmd_brief(conn, rest):
    org = _org(conn, rest)
    b = research.latest_brief(conn, org["id"])
    if b is None or b["body"] == "(historical brief)":
        return f"No brief yet. Run *research {org['id']}*."
    return b["body"]


def _show_new(d, note):
    return (f"*Draft #{d['id']}* to {d['to']} ({d['purpose']}, {note})\n\n{d['body']}\n\n"
            f"*ok {d['id']}* · *edit {d['id']} <new text>* · *discard {d['id']}*")


def cmd_draft(conn, rest):
    ref, flag = _split_flag(rest, {"force"})
    org = _org(conn, ref)
    d, note = draft.run(conn, org["id"], force=flag == "force")
    return note if d is None else _show_new(d, note)


def cmd_intro(conn, rest):
    org = _org(conn, rest)
    d, note = draft.run(conn, org["id"], intro=True)
    return note if d is None else _show_new(d, note)


def cmd_show(conn, rest):
    d = _draft_row(conn, rest)
    return f"*Draft #{d['id']}* to {d['name']} [{d['status']}]\n\n{d['body']}"


def cmd_approve(conn, rest):
    d = _draft_row(conn, rest)
    if d["status"] != "draft":
        return f"Draft #{d['id']} is already {d['status']}."
    conn.execute("UPDATE drafts SET status = 'approved', approved_at = ? WHERE id = ?", (db.now(), d["id"]))
    tracking.set_next(conn, d["org_id"], f"Send approved draft #{d['id']} yourself", db.today().isoformat())
    db.log(conn, "aaron", "approve", d["org_id"], f"#{d['id']}")
    conn.commit()
    rcpt = conn.execute("SELECT email, email_status FROM orgs WHERE id = ?", (d["via_org_id"] or d["org_id"],)).fetchone()
    warn = {"invalid": " ⚠ this address bounced before", "risky": " (Clay says risky)", "unknown": " (not verified)"}
    to = f"{rcpt['email']}{warn.get(rcpt['email_status'], '')}" if rcpt["email"] else f"{d['name']} (no email yet: *enrich {d['org_id']}*)"
    return (f"Approved #{d['id']}. Nothing has been sent. Copy this to {to} yourself, "
            f"then reply *sent {d['id']}* (or just send it; Gmail check logs it):\n\n{d['body']}")


def cmd_edit(conn, rest):
    ref, _, body = rest.partition(" ")
    d = _draft_row(conn, ref)
    if d["status"] != "draft" or not body.strip():
        return "Usage: edit <#> <full new text>, on a draft that isn't approved yet."
    conn.execute("UPDATE drafts SET body = ?, edited = 1 WHERE id = ?", (body.strip(), d["id"]))
    db.remember(conn, "voice_edit", json.dumps({"before": d["body"], "after": body.strip()}))   # the draft agent learns from this
    db.log(conn, "aaron", "edit", d["org_id"], f"#{d['id']}")
    conn.commit()
    return f"Updated #{d['id']}. I'll write closer to this next time. *ok {d['id']}* when ready."


def cmd_discard(conn, rest):
    d = _draft_row(conn, rest)
    conn.execute("UPDATE drafts SET status = 'discarded' WHERE id = ?", (d["id"],))
    tracking.refresh_stage(conn, d["org_id"])
    conn.commit()
    return f"Discarded #{d['id']}."


def cmd_sent(conn, rest):
    d = _draft_row(conn, rest)
    if d["status"] != "approved":
        return f"Draft #{d['id']} is {d['status']}. Only approved drafts can be marked sent."
    conn.execute("UPDATE drafts SET status = 'sent', sent_at = ? WHERE id = ?", (db.today().isoformat(), d["id"]))
    db.log(conn, "aaron", "sent", d["org_id"], f"#{d['id']}")
    stage = tracking.refresh_stage(conn, d["org_id"])
    followup.run(conn)
    conn.commit()
    return f"Logged #{d['id']} as sent. Stage: *{stage}*."


def cmd_replied(conn, rest):
    parts = rest.split()
    kind_at = next((i for i, w in enumerate(parts) if w.lower() in ("positive", "neutral", "negative")), None)
    if kind_at is None:
        return "Usage: replied <org> positive|neutral|negative [note]"
    org = _org(conn, " ".join(parts[:kind_at]))
    logged = tracking.mark_reply(conn, org["id"], parts[kind_at].lower(), " ".join(parts[kind_at + 1:]))
    if logged is None:
        return f"Nothing has been sent to {org['name']} yet."
    stage, thread_org = logged
    followup.run(conn)
    about = "" if thread_org == org["id"] else f" (about {db.find_org(conn, thread_org)['name']})"
    return f"Logged reply from {org['name']}{about}. Stage: *{stage}*. *draft {thread_org}* writes what's next."


def cmd_milestone(conn, rest):
    org, words = _org_then_words(conn, rest)
    if not words or words[0] not in db.MILESTONES:
        return f"Usage: milestone <org> {'|'.join(db.MILESTONES)}"
    stage = tracking.mark_milestone(conn, org["id"], words[0])
    followup.run(conn)
    return f"{org['name']}: {words[0]}. Stage: *{stage}*." + (" 🎉 Live on Pebble." if stage == "live" else "")


def _manual_stage(stage):
    def handler(conn, rest):
        org, words = _org_then_words(conn, rest)
        tracking.set_stage(conn, org["id"], stage, " ".join(words) or None)
        tracking.set_next(conn, org["id"], None if stage == "lost" else "Run the meeting, then log signed_up", None)
        conn.commit()
        return f"{org['name']} → *{stage}*."
    return handler


def cmd_signal(conn, rest):
    parts = rest.split()
    kind_at = next((i for i, w in enumerate(parts) if w in research.SIGNAL_WEIGHT), None)
    if kind_at is None or kind_at == len(parts) - 1:
        return f"Usage: signal <org> <kind> <detail>. Kinds: {KINDS}"
    org = _org(conn, " ".join(parts[:kind_at]))
    conn.execute("INSERT INTO signals (org_id, seen_on, kind, detail, source) VALUES (?,?,?,?, 'added by Aaron')",
                 (org["id"], db.today().isoformat(), parts[kind_at], " ".join(parts[kind_at + 1:])))
    conn.commit()
    return f"Added signal for {org['name']}. *research {org['id']}* to refresh the brief."


def cmd_add(conn, rest):
    f = [x.strip() for x in rest.split("|")]
    if len(f) < 7 or not f[3].isdigit() or f[1] not in research.SEGMENT_LABEL:
        return ("Usage: add name | segment | city, ST | events/yr | flagship | decider | title [| email | website | tools]\n"
                f"Segments: {', '.join(research.SEGMENT_LABEL)}")
    city, _, state = f[2].partition(",")
    f += [None] * (10 - len(f))
    org_id = db.add_org(conn, {"name": f[0], "segment": f[1], "city": city.strip(), "state": state.strip(),
                               "events_per_year": int(f[3]), "flagship_event": f[4], "decision_maker": f[5],
                               "decision_maker_title": f[6], "email": f[7], "website": f[8], "current_tools": f[9]},
                        source="aaron")
    conn.commit()
    return f"Added #{org_id} {f[0]}. Next: *research {org_id} web*."


def cmd_metrics(conn, rest):
    f = tracking.funnel(conn)
    from . import clay
    p = prospect.metric(conn)
    cv, ca = clay.metric(conn)
    with_t, without_t = research.metric(conn).values()
    clean, approved = draft.metric(conn)
    by_touch = followup.metric(conn)
    hours = tracking.metric(conn)
    pct = lambda a, b: f"{round(100 * a / b)}%" if b else "n/a"
    return "\n".join([
        f"*Funnel*: {f['contacted']} contacted → {f['replied']} replied → {f['signed_up']} signed up → "
        f"{f['onboarding']} onboarding+ → *{f['live']} live*",
        f"*Prospect*: {p['found']} found by the agent → {p['contacted']} contacted → {p['replied']} replied → "
        f"{p['signed_up']} signed up · Clay: {cv}/{ca} sent came back with a valid email",
        f"*Research*: briefs that led to a reply: with a why-now trigger {with_t[0]}/{with_t[1]} ({pct(*with_t)}) "
        f"vs without {without_t[0]}/{without_t[1]} ({pct(*without_t)})",
        f"*Draft*: approved without edits {clean}/{approved} ({pct(clean, approved)})",
        "*Follow-up*: reply rate by touch " + ", ".join(f"T{t} {r}/{s} ({pct(r, s)})" for t, r, s in by_touch),
        f"*Tracking*: ~{hours['hours']} h saved this week ({hours['prospects']} prospects, {hours['briefs']} briefs, "
        f"{hours['drafts']} drafts, {hours['threads']} live threads; assumptions in tracking.py)",
    ])


HANDLERS = {
    "help": lambda c, r: HELP, "today": cmd_today, "next": cmd_today, "morning": cmd_morning,
    "pipeline": cmd_pipeline, "orgs": cmd_orgs, "prospect": cmd_prospect, "enrich": cmd_enrich, "inbox": cmd_inbox, "research": cmd_research,
    "brief": cmd_brief, "draft": cmd_draft, "intro": cmd_intro, "show": cmd_show, "approve": cmd_approve,
    "edit": cmd_edit, "discard": cmd_discard, "sent": cmd_sent, "replied": cmd_replied, "milestone": cmd_milestone,
    "meeting": _manual_stage("meeting"), "lost": _manual_stage("lost"),
    "signal": cmd_signal, "add": cmd_add, "metrics": cmd_metrics,
}
