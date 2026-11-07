"""The weekly report: a PDF emailed to Aaron, every Monday morning or whenever he asks (`report`).

It covers what happened this week, what's waiting on him, and every organizer in the pipeline with the contact
details we hold. Each detail is a link (mailto:, tel:, their site, their Instagram) and says where it came from:
you, web research (with the page), the prospect agent, Clay, Instagram, a Gmail bounce, or sample data.

It only ever goes to Aaron's own address (GMAIL_ADDRESS), through Gmail with the same app password as the inbox check.
That's not outreach: nothing here ever emails a prospect.

Needs reportlab (pip install reportlab). Env: REPORT_DAY (mon..sun, or off), REPORT_HOUR (default 7).
"""

import os
import smtplib
from datetime import timedelta
from email.message import EmailMessage
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from . import db, playbook, research

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")

GROUPS = [
    ("Customers", ("live", "onboarding", "signed_up")),
    ("In conversation", ("replied", "meeting")),
    ("Reached out, waiting", ("contacted",)),
    ("Ready to contact", ("drafted", "researched", "new")),
    ("Parked", ("cold", "lost")),
]
STAGE_WORDS = {"live": "live, collecting vendor payments", "onboarding": "setting up", "signed_up": "signed up",
               "replied": "wrote back", "meeting": "meeting booked", "contacted": "waiting on a reply",
               "drafted": "first note drafted", "researched": "researched", "new": "new", "cold": "went quiet",
               "lost": "said not now"}
LABELS = {"decision_maker": "Decides", "email": "Email", "phone": "Phone", "website": "Website", "instagram": "Instagram"}


# ---- What goes in it ----

def link(field, value):
    if not value:
        return None
    if field == "email":
        return f"mailto:{value}"
    if field == "phone":
        return "tel:" + "".join(ch for ch in value if ch.isdigit() or ch == "+")
    if field == "website":
        return value if value.startswith("http") else f"https://{value}"
    if field == "instagram":
        return f"https://www.instagram.com/{value}/"
    return None


def source_of(conn, org, field):
    """(source, url, date) for the current value of a field, newest record first."""
    r = conn.execute("""SELECT source, url, at FROM facts WHERE org_id = ? AND field = ? AND value = ?
                        ORDER BY id DESC LIMIT 1""", (org["id"], field, str(org[field]))).fetchone()
    if r:
        return r["source"], r["url"], r["at"][:10]
    return ("sample data" if org["synthetic"] else "added before sources were tracked"), org["source_url"], None


def contacts(conn, org):
    out = []
    for field, label in LABELS.items():
        value = org[field]
        if not value:
            continue
        shown = value
        if field == "decision_maker" and org["decision_maker_title"]:
            shown = f"{value}, {org['decision_maker_title']}"
        if field == "email" and org["email_status"] not in ("valid", None):
            shown += {"invalid": " (bounced)", "risky": " (risky)", "unknown": " (not verified)"}[org["email_status"]]
        if field == "instagram":
            shown = f"@{value}"
        src, url, on = source_of(conn, org, field)
        out.append({"label": label, "value": shown, "link": link(field, value), "source": src, "source_url": url, "on": on})
    return out


def build(conn, days=7):
    end = db.today()
    start = end - timedelta(days=days)
    since = start.isoformat()
    week = {
        "new": conn.execute("""SELECT COUNT(*) FROM orgs o WHERE o.source != 'seed'
                               AND (SELECT MIN(at) FROM facts f WHERE f.org_id = o.id) >= ?""", (since,)).fetchone()[0],
        "sent": conn.execute("SELECT COUNT(*) FROM drafts WHERE sent_at >= ?", (since,)).fetchone()[0],
        "replies": {r["reply_kind"]: r["c"] for r in conn.execute(
            "SELECT reply_kind, COUNT(*) AS c FROM drafts WHERE replied_at >= ? GROUP BY reply_kind", (since,))},
        "milestones": [(r["name"], r["milestone"]) for r in conn.execute(
            """SELECT o.name, m.milestone FROM milestones m JOIN orgs o ON o.id = m.org_id
               WHERE m.at >= ? ORDER BY m.at""", (since,))],
    }
    waiting = [{"id": r["id"], "who": playbook.who(conn.execute("SELECT * FROM orgs WHERE id = ?",
                                                                  (r["rcpt"],)).fetchone()),
                "what": playbook.PURPOSE_WORDS.get(r["purpose"], "note"), "status": r["status"]}
               for r in conn.execute("""SELECT id, purpose, status, COALESCE(via_org_id, org_id) AS rcpt FROM drafts
                                        WHERE status IN ('draft', 'approved') ORDER BY id""")]
    groups = []
    for title, stages in GROUPS:
        orgs = []
        for org in conn.execute(f"""SELECT o.*, p.stage, p.next_action FROM orgs o JOIN pipeline p ON p.org_id = o.id
                                    WHERE p.stage IN ({','.join('?' * len(stages))}) ORDER BY o.name""", stages):
            sig = conn.execute("SELECT * FROM signals WHERE org_id = ? ORDER BY seen_on DESC LIMIT 1", (org["id"],)).fetchone()
            orgs.append({
                "id": org["id"], "name": org["name"], "synthetic": bool(org["synthetic"]),
                "what": f"{research.SEGMENT_LABEL.get(org['segment'], org['segment'])}, {org['city']}, {org['state']}",
                "stage": STAGE_WORDS.get(org["stage"], org["stage"]), "next": org["next_action"],
                "news": sig and {"detail": sig["detail"], "on": sig["seen_on"],
                                 "url": sig["source"] if (sig["source"] or "").startswith(("http", "file://")) else None,
                                 "where": "your screenshot" if (sig["source"] or "").startswith("file://")
                                 else sig["source"]},
                "contacts": contacts(conn, org),
            })
        if orgs:
            groups.append({"title": title, "orgs": orgs})
    n = {r["stage"]: r["c"] for r in conn.execute("SELECT stage, COUNT(*) AS c FROM pipeline GROUP BY stage")}
    return {"start": start, "end": end, "week": week, "waiting": waiting, "groups": groups, "stages": n,
            "synthetic": conn.execute("SELECT COUNT(*) FROM orgs WHERE synthetic = 1").fetchone()[0]}


def summary(data):
    """A few sentences in Aaron's terms. Used as the email body and the WhatsApp reply."""
    w, n = data["week"], data["stages"]
    yes, other = w["replies"].get("positive", 0), sum(v for k, v in w["replies"].items() if k != "positive")
    live, setting_up = n.get("live", 0), n.get("onboarding", 0) + n.get("signed_up", 0)
    parts = [f"{live} organizer{' is' if live == 1 else 's are'} live on Pebble and {setting_up} "
             f"{'is' if setting_up == 1 else 'are'} getting set up."]
    week = []
    if w["new"]:
        week.append(f"I found {w['new']} new organizer{'s' if w['new'] != 1 else ''}")
    if w["sent"]:
        week.append(f"you sent {w['sent']} note{'s' if w['sent'] != 1 else ''}")
    if yes or other:
        week.append(f"{yes + other} {'person' if yes + other == 1 else 'people'} wrote back ({yes} interested)")
    if week:
        parts.append("This week " + (", ".join(week[:-1]) + " and " + week[-1] if len(week) > 1 else week[0]) + ".")
    live = [name for name, m in w["milestones"] if m == "first_payment"]
    if live:
        parts.append(f"{', '.join(live)} took their first vendor payment. 🎉")
    drafts = sum(1 for d in data["waiting"] if d["status"] == "draft")
    if drafts:
        parts.append(f"{drafts} draft{'s are' if drafts != 1 else ' is'} waiting on your OK.")
    return " ".join(parts)


# ---- The PDF ----

def _a(text, href):
    t = escape(str(text))
    return f'<link href="{escape(href)}" color="#1f5c55"><u>{t}</u></link>' if href else t


def _where(c):
    src = escape(c["source"])
    if (c["source_url"] or "").startswith("file://"):
        src += " · " + _a("open image", c["source_url"])
    elif c["source_url"]:
        u = urlparse(c["source_url"] if "//" in c["source_url"] else f"https://{c['source_url']}")
        src += " · " + _a((u.netloc + u.path).rstrip("/"), c["source_url"])
    if c["on"]:
        src += f" · {c['on']}"
    return src


def pdf(data, path):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    ink, muted, pine, line = colors.HexColor("#1d2623"), colors.HexColor("#5c6b66"), colors.HexColor("#1f5c55"), \
        colors.HexColor("#cdd5d2")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=13, textColor=ink)
    small = ParagraphStyle("s", parent=body, fontSize=8, leading=10.5, textColor=muted)
    h1 = ParagraphStyle("h1", parent=body, fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=body, fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=14,
                        spaceAfter=6, textColor=pine)
    name = ParagraphStyle("n", parent=body, fontName="Helvetica-Bold", fontSize=11, leading=14)

    s, e = data["start"], data["end"]
    story = [Paragraph("Pebble GTM weekly report", h1),
             Paragraph(f"{s:%b %-d} to {e:%b %-d, %Y}", small), Spacer(1, 8),
             Paragraph(escape(summary(data)).replace("🎉", ""), body)]
    if data["synthetic"]:
        story.append(Paragraph(f"Includes {data['synthetic']} sample organizers (made-up data, marked SAMPLE).", small))

    if data["waiting"]:
        story.append(Paragraph("Waiting on you", h2))
        for d in data["waiting"]:
            how = f"reply <b>ok {d['id']}</b> to approve" if d["status"] == "draft" else "approved, send it from Gmail"
            story.append(Paragraph(f"• {escape(d['what']).capitalize()} to {escape(d['who'])}: {how}", body))

    story.append(Paragraph("Where each detail came from", h2))
    story.append(Paragraph("Each contact detail below is a link and names its source: <b>you</b> (you told me), <b>web "
                           "research</b> or <b>prospect agent</b> (with the page it was on), <b>Clay</b> (enrichment), "
                           "<b>Instagram</b>, <b>Gmail bounce</b>, or <b>sample data</b>.", small))

    for g in data["groups"]:
        story.append(Paragraph(f"{g['title']} ({len(g['orgs'])})", h2))
        for o in g["orgs"]:
            head = escape(o["name"]) + ('  <font color="#8a5a1f" size="7">SAMPLE</font>' if o["synthetic"] else "")
            block = [Paragraph(head, name),
                     Paragraph(f"{escape(o['what'])} · {escape(o['stage'])}"
                               + (f" · next: {escape(o['next'])}" if o["next"] else ""), small)]
            rows = [[Paragraph(c["label"], small), Paragraph(_a(c["value"], c["link"]), body), Paragraph(_where(c), small)]
                    for c in o["contacts"]]
            if o["news"]:
                n = o["news"]
                where = _a(n["where"], n["url"]) if n["url"] else escape(n["where"] or "")
                rows.append([Paragraph("Latest news", small), Paragraph(escape(playbook.third_person(n["detail"])), body),
                             Paragraph(f"{where} · {n['on']}", small)])
            if rows:
                t = Table(rows, colWidths=[0.9 * inch, 3.3 * inch, 2.8 * inch])
                t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -2), 0.25, line),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 3),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
                block += [Spacer(1, 3), t]
            block.append(Spacer(1, 10))
            story.append(KeepTogether(block))

    def footer(canvas, doc):
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(0.75 * inch, 0.5 * inch, f"Pebble GTM · {e:%b %-d, %Y} · page {doc.page}")

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    SimpleDocTemplate(path, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch, topMargin=0.7 * inch,
                      bottomMargin=0.75 * inch, title="Pebble GTM weekly report", author="Pebble GTM").build(
        story, onFirstPage=footer, onLaterPages=footer)
    return path


# ---- Sending it ----

def send(path, data):
    """Email the PDF to Aaron's own address. There is deliberately no way to pass another recipient."""
    me = os.environ["GMAIL_ADDRESS"]
    msg = EmailMessage()
    msg["From"], msg["To"] = me, me
    msg["Subject"] = f"Pebble GTM report, {data['start']:%b %-d} to {data['end']:%b %-d}"
    msg.set_content(summary(data) + "\n\nThe full report is attached. Every contact detail in it links out and says "
                    "where it came from.\n\nPebble GTM")
    with open(path, "rb") as f:
        msg.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(path))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(me, os.environ["GMAIL_APP_PASSWORD"])
        s.send_message(msg)


def run(conn, days=7, email=True):
    """Build, save, and (if Gmail is connected) email the report. Returns the line for Aaron."""
    from . import gmail
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return "The PDF report needs reportlab. Run: pip install reportlab"
    data = build(conn, days)
    path = pdf(data, os.path.join(REPORTS_DIR, f"pebble-report-{data['end']}.pdf"))
    db.log(conn, "report", "built", None, path)
    conn.commit()
    orgs = sum(len(g["orgs"]) for g in data["groups"])
    if email and gmail.configured():
        send(path, data)
        db.log(conn, "report", "emailed", None, os.environ["GMAIL_ADDRESS"])
        conn.commit()
        return f"Emailed you the report ({orgs} organizers, with links and sources). {summary(data)}"
    return (f"Report saved to {os.path.abspath(path)} ({orgs} organizers). Connect Gmail and I'll email it to you. "
            + summary(data))
