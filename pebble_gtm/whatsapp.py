"""WhatsApp front door, via a Twilio WhatsApp webhook. Standard library only.

Twilio POSTs each WhatsApp message Aaron sends to /whatsapp. We check it really came
from Twilio and from Aaron's number, run it, and answer in TwiML. Slow work (prospecting,
web research, Claude drafting) answers "on it" first and pushes the result when done.

Proactive: every morning at MORNING_HOUR the follow-up agent's daily pass runs and its
summary is pushed to Aaron without him asking.

Pebble product events: POST /pebble with {"org": <id|name>, "milestone": "signed_up|event_created|
apps_open|first_payment"} and header X-Pebble-Secret, so activation tracking updates itself.

Clay results: POST /clay from your Clay table's HTTP API column, header X-Clay-Secret (see clay.py).
Gmail: every GMAIL_POLL_MINUTES the inbox is checked for replies (see gmail.py) and new ones are pushed.

The only phone this ever messages is ALLOWED_WHATSAPP_FROM.

Env:
  ALLOWED_WHATSAPP_FROM  your number, e.g. whatsapp:+15551234567 (required)
  TWILIO_AUTH_TOKEN      verifies requests are from Twilio (required unless --insecure)
  TWILIO_ACCOUNT_SID     needed only to push results of slow commands
  TWILIO_WHATSAPP_FROM   the Twilio sender, e.g. whatsapp:+14155238886 (sandbox)
  PUBLIC_URL             the https URL Twilio calls, e.g. https://abc.ngrok-free.app/whatsapp
  MORNING_HOUR           local hour for the daily push (default 8; set to "off" to disable)
  PEBBLE_WEBHOOK_SECRET  shared secret for /pebble milestone events
  CLAY_CALLBACK_SECRET   shared secret for /clay results
  GMAIL_POLL_MINUTES     how often to check Gmail (default 10)
"""

import base64
import hashlib
import hmac
import json
import os
import threading
import time
from datetime import datetime
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

from . import clay, commands, db, followup, gmail, tracking

INLINE_SECONDS = 12      # Twilio gives a webhook 15s before it gives up
WHATSAPP_LIMIT = 1500    # Twilio's per-message cap is 1600 characters


def twilio_signature_ok(auth_token, url, params, signature):
    """Twilio signs: base64(HMAC-SHA1(token, url + each key+value, keys sorted))."""
    payload = url + "".join(k + params[k] for k in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, signature or "")


def chunks(text, size=WHATSAPP_LIMIT):
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:          # a single very long line
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    return out + [cur] if cur else out


def twiml(text):
    msgs = "".join(f"<Message>{escape(c)}</Message>" for c in chunks(text)) if text else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{msgs}</Response>'


def push_to_aaron(text):
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID"), os.environ.get("TWILIO_AUTH_TOKEN")
    sender, to = os.environ.get("TWILIO_WHATSAPP_FROM"), os.environ["ALLOWED_WHATSAPP_FROM"]
    if not (sid and token and sender):
        print("[push skipped: set TWILIO_ACCOUNT_SID and TWILIO_WHATSAPP_FROM]\n" + text)
        return
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    for part in chunks(text):
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data=urllib.parse.urlencode({"From": sender, "To": to, "Body": part}).encode(),
            headers={"Authorization": f"Basic {auth}"})
        urllib.request.urlopen(req, timeout=20).read()


def run_command(text):
    conn = db.connect()
    try:
        return commands.dispatch(conn, text)
    finally:
        conn.close()


class Handler(BaseHTTPRequestHandler):
    insecure = False

    def _send(self, code, body, ctype="text/xml"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        self._send(200, "ok", "text/plain") if self.path == "/health" else self._send(404, "not found", "text/plain")

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if path == "/pebble":
            return self._pebble_event(raw)
        if path == "/clay":
            return self._clay_result(raw)
        if path != "/whatsapp":
            return self._send(404, "not found", "text/plain")
        params = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

        if not self.insecure:
            url = os.environ.get("PUBLIC_URL") or f"https://{self.headers.get('Host')}{self.path}"
            if not twilio_signature_ok(os.environ["TWILIO_AUTH_TOKEN"], url, params,
                                       self.headers.get("X-Twilio-Signature")):
                return self._send(403, "bad signature", "text/plain")
        if params.get("From") != os.environ["ALLOWED_WHATSAPP_FROM"]:
            return self._send(200, twiml(""))      # ignore anyone who isn't Aaron, silently

        text = params.get("Body", "")
        result = {}
        worker = threading.Thread(target=lambda: result.update(reply=run_command(text)), daemon=True)
        worker.start()
        worker.join(INLINE_SECONDS)
        if "reply" in result:
            return self._send(200, twiml(result["reply"]))

        def finish():
            worker.join()
            push_to_aaron(result.get("reply", "That command failed. Check the server log."))
        threading.Thread(target=finish, daemon=True).start()
        self._send(200, twiml("On it. This one takes a minute; I'll message you the result."))

    def _pebble_event(self, raw):
        secret = os.environ.get("PEBBLE_WEBHOOK_SECRET")
        if not secret or not hmac.compare_digest(secret, self.headers.get("X-Pebble-Secret", "")):
            return self._send(403, "bad secret", "text/plain")
        try:
            event = json.loads(raw)
            conn = db.connect()
            org = db.find_org(conn, event["org"])
            if org is None:
                return self._send(404, "unknown org", "text/plain")
            stage = tracking.mark_milestone(conn, org["id"], event["milestone"])
            conn.close()
        except (KeyError, ValueError) as e:
            return self._send(400, str(e), "text/plain")
        if stage == "live":
            threading.Thread(target=push_to_aaron, args=(f"🎉 {org['name']} just took its first vendor payment "
                                                          "on Pebble. They're live.",), daemon=True).start()
        self._send(200, json.dumps({"stage": stage}), "application/json")

    def _clay_result(self, raw):
        secret = os.environ.get("CLAY_CALLBACK_SECRET")
        if not secret or not hmac.compare_digest(secret, self.headers.get("X-Clay-Secret", "")):
            return self._send(403, "bad secret", "text/plain")
        try:
            conn = db.connect()
            note = clay.receive(conn, json.loads(raw))
            conn.close()
        except (KeyError, ValueError, TypeError) as e:
            return self._send(400, str(e), "text/plain")
        if note:
            threading.Thread(target=push_to_aaron, args=(note,), daemon=True).start()
        self._send(200, json.dumps({"ok": True}), "application/json")

    def log_message(self, fmt, *args):
        print(f"[whatsapp] {self.address_string()} {fmt % args}")


def morning_loop():
    """Push the daily pass once a day at MORNING_HOUR. WhatsApp only allows free-form messages within 24h
    of Aaron's last message, so outside that window Twilio will reject the push (see README)."""
    hour = os.environ.get("MORNING_HOUR", "8")
    if hour == "off":
        return
    last_day = None
    while True:
        now = datetime.now()
        if now.hour == int(hour) and now.date() != last_day:
            last_day = now.date()
            try:
                push_to_aaron(followup.morning(db.connect()))
            except Exception as e:
                print(f"[morning] failed: {e}")
        time.sleep(60)


def report_loop():
    """Email the PDF report once a week (REPORT_DAY at REPORT_HOUR, default Monday 7am) and tell Aaron on WhatsApp."""
    day = os.environ.get("REPORT_DAY", "mon").lower()[:3]
    if day == "off":
        return
    hour = int(os.environ.get("REPORT_HOUR", "7"))
    last_day = None
    while True:
        now = datetime.now()
        if now.strftime("%a").lower() == day and now.hour == hour and now.date() != last_day:
            last_day = now.date()
            try:
                from . import report
                push_to_aaron(report.run(db.connect()))
            except Exception as e:
                print(f"[report] failed: {e}")
        time.sleep(60)


def inbox_loop():
    if not gmail.configured():
        return
    minutes = int(os.environ.get("GMAIL_POLL_MINUTES", "10"))
    while True:
        try:
            notes = gmail.check(db.connect())
            if notes:
                push_to_aaron("\n".join(notes))
        except Exception as e:
            print(f"[gmail] check failed: {e}")
        time.sleep(minutes * 60)


def serve(port=8787, insecure=False):
    if not os.environ.get("ALLOWED_WHATSAPP_FROM"):
        raise SystemExit("Set ALLOWED_WHATSAPP_FROM=whatsapp:+1YOURNUMBER first.")
    if not insecure and not os.environ.get("TWILIO_AUTH_TOKEN"):
        raise SystemExit("Set TWILIO_AUTH_TOKEN (or pass --insecure for local testing only).")
    Handler.insecure = insecure
    threading.Thread(target=morning_loop, daemon=True).start()
    threading.Thread(target=inbox_loop, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    print(f"Listening on :{port}/whatsapp. Point your Twilio WhatsApp sandbox 'When a message comes in' here.")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
