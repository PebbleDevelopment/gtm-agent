"""Clay enrichment: find and verify the person who decides, and their email.

The prospect and research agents are good at finding organizations and what they run, but
often miss a verified email. Clay is good at exactly that. The round trip is asynchronous:

  1. request(): POST the org to your Clay table's webhook (one row per org, carrying pebble_org_id).
  2. Clay's waterfall finds the events or vendor lead and verifies their email.
  3. An HTTP API column in that table POSTs the result back to this server's /clay endpoint,
     and receive() writes it to the store and tells Aaron on WhatsApp.

Clay only ever fills gaps. It never overwrites a decider Aaron or research already set, and it
replaces an email only when the current one is missing or has bounced.

Env:
  CLAY_WEBHOOK_URL     your Clay table's webhook source URL
  CLAY_WEBHOOK_TOKEN   the table's webhook auth token, if you turned one on (sent as x-clay-webhook-auth)
  CLAY_CALLBACK_SECRET the secret Clay's HTTP API column sends back as X-Clay-Secret
"""

import json
import os
import urllib.request
from datetime import timedelta

from . import db, research

# Who to look for, in order. Clay's "find people at company" step takes a title list.
TARGET_TITLES = {
    "multi_festival": ["Founder", "Owner", "Producer", "Event Director", "Vendor Coordinator"],
    "venue": ["Director of Events", "Events Manager", "General Manager", "Partnerships Director"],
    "tourism": ["Director of Events", "Events Manager", "Marketing Director", "Executive Director"],
    "chamber": ["Events Manager", "Events Director", "Executive Director", "President"],
    "econ_dev": ["Small Business Manager", "Main Street Director", "Programs Director", "Executive Director"],
}
EMAIL_STATUSES = {"valid", "risky", "invalid", "unknown"}
RETRY_DAYS = 7


def configured():
    return bool(os.environ.get("CLAY_WEBHOOK_URL"))


def domain(org):
    src = org["website"] or (org["email"] or "").split("@")[-1]
    return src.split("//")[-1].split("/")[0].removeprefix("www.") or None


def needs_enrichment(conn, org):
    """Worth sending to Clay: fits the ICP, still pre-signup, no usable email, not asked recently."""
    if research.fit_score(org) < 50:
        return False
    if org["email"] and org["email_status"] in ("valid", "risky"):
        return False
    stage = conn.execute("SELECT stage FROM pipeline WHERE org_id = ?", (org["id"],)).fetchone()["stage"]
    if stage not in ("new", "researched", "drafted", "cold"):
        return False
    since = (db.today() - timedelta(days=RETRY_DAYS)).isoformat()
    asked = conn.execute("SELECT 1 FROM activity WHERE agent = 'clay' AND action = 'request' AND org_id = ? AND at >= ?",
                         (org["id"], since)).fetchone()
    return asked is None


def request(conn, org_id):
    org = conn.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
    if not configured():
        return f"Clay isn't set up (CLAY_WEBHOOK_URL). {org['name']} still needs a verified email."
    row = {
        "pebble_org_id": org["id"], "name": org["name"], "domain": domain(org), "website": org["website"],
        "city": org["city"], "state": org["state"], "segment": org["segment"],
        "known_decision_maker": org["decision_maker"], "known_title": org["decision_maker_title"],
        "target_titles": ", ".join(TARGET_TITLES.get(org["segment"], [])),
    }
    headers = {"Content-Type": "application/json"}
    if os.environ.get("CLAY_WEBHOOK_TOKEN"):
        headers["x-clay-webhook-auth"] = os.environ["CLAY_WEBHOOK_TOKEN"]
    req = urllib.request.Request(os.environ["CLAY_WEBHOOK_URL"], data=json.dumps(row).encode(), headers=headers)
    urllib.request.urlopen(req, timeout=20).read()
    db.log(conn, "clay", "request", org_id, domain(org))
    conn.commit()
    return f"Sent {org['name']} to Clay. I'll message you when it comes back."


def receive(conn, payload):
    """Handle Clay's callback. Returns the WhatsApp line for Aaron, or None if nothing changed."""
    org = conn.execute("SELECT * FROM orgs WHERE id = ?", (int(payload["pebble_org_id"]),)).fetchone()
    if org is None:
        raise ValueError("unknown pebble_org_id")
    status = (payload.get("email_status") or "unknown").lower()
    status = status if status in EMAIL_STATUSES else "unknown"
    email = (payload.get("email") or "").strip().lower() or None
    person = (payload.get("decision_maker") or "").strip() or None
    title = (payload.get("decision_maker_title") or "").strip() or None

    changes = {}
    if person and not org["decision_maker"]:
        changes["decision_maker"] = person
        if title:
            changes["decision_maker_title"] = title
    elif title and not org["decision_maker_title"] and person == org["decision_maker"]:
        changes["decision_maker_title"] = title
    if email and status != "invalid" and (not org["email"] or org["email_status"] == "invalid"):
        changes["email"], changes["email_status"] = email, status
    elif email and email == org["email"]:
        changes["email_status"] = status

    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE orgs SET {sets} WHERE id = ?", (*changes.values(), org["id"]))
    db.log(conn, "clay", "result", org["id"], json.dumps({**changes, "linkedin": payload.get("linkedin_url")}))
    conn.commit()

    if "email" in changes or "decision_maker" in changes:
        who = changes.get("decision_maker", org["decision_maker"])
        t = changes.get("decision_maker_title", org["decision_maker_title"])
        mail = changes.get("email", org["email"])
        warn = "" if changes.get("email_status", org["email_status"]) == "valid" else f" ({changes.get('email_status')})"
        return f"Clay found {who}{f', {t}' if t else ''} at *{org['name']}*: {mail}{warn}. *draft {org['id']}* when you're ready."
    if status == "invalid" or not email:
        return f"Clay couldn't find a usable email at *{org['name']}*. Try their contact page or ask a live customer."
    return None


def metric(conn):
    """Of orgs sent to Clay, how many came back with a valid email."""
    asked = conn.execute("SELECT COUNT(DISTINCT org_id) FROM activity WHERE agent = 'clay' AND action = 'request'").fetchone()[0]
    valid = conn.execute("""SELECT COUNT(DISTINCT a.org_id) FROM activity a JOIN orgs o ON o.id = a.org_id
                            WHERE a.agent = 'clay' AND a.action = 'request' AND o.email_status = 'valid'""").fetchone()[0]
    return valid, asked
