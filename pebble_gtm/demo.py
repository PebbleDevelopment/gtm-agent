"""A day with the agent, in two minutes, with no accounts or keys: python3 -m pebble_gtm demo

Everything below is the real code running on the synthetic sample data, in a throwaway database. The only stand-ins
are the outside services: the emails and the Instagram post are built in here instead of fetched, and in the screenshot
scene Claude's reading of the image is stood in, because that part needs an API key. Nothing leaves this machine.
"""

import contextlib
import email
import io
import os
import tempfile
from email.policy import default as policy

OUTSIDE = ["ANTHROPIC_API_KEY", "GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
           "CLAY_WEBHOOK_URL", "IG_USER_ID", "IG_ACCESS_TOKEN"]
WIDTH = 78


def scene(time, title, note=None):
    print(f"\n\033[1m{time}  {title}\033[0m" if os.isatty(1) else f"\n{time}  {title}")
    print("─" * WIDTH)
    if note:
        print(f"({note})\n")


def you(text):
    print(f"  you › {text}")


def agent(text):
    lines = text.strip().split("\n")
    print("pebble › " + lines[0])
    for line in lines[1:]:
        print("         " + line)
    print()


def _mail(sender, to, subject, body):
    return email.message_from_string(f"From: {sender}\nTo: {to}\nSubject: {subject}\n"
                                     f"Date: {email.utils.formatdate(localtime=True)}\n"
                                     f"Message-ID: <{abs(hash(body))}@demo>\nContent-Type: text/plain\n\n{body}", policy=policy)


def run():
    for k in OUTSIDE:                      # the demo never talks to a real service, whatever .env says
        os.environ.pop(k, None)
    os.environ["PEBBLE_OFFLINE"] = "1"
    from . import commands, db, gmail, instagram, report, screenshot, seed

    path = os.path.join(tempfile.mkdtemp(), "demo.db")
    db.DB_PATH = os.environ["PEBBLE_DB"] = path
    conn = db.connect(path)
    seed.seed(conn)
    say = lambda text: commands.dispatch(conn, text)

    print("Pebble GTM: a day with the agent (offline demo, sample data, nothing leaves this machine)")

    scene("8:00", "It texts you first", "the morning pass: catch up, research, pre-draft, then tell you what matters")
    agent(say("morning"))

    ref = conn.execute("SELECT id FROM drafts WHERE purpose = 'referral_ask' AND status = 'draft' ORDER BY id").fetchone()["id"]
    scene("8:12", "You read a draft and approve it", "only your own 'ok N' can approve; no agent has that tool")
    you(f"show {ref}")
    agent(say(f"show {ref}"))
    you(f"ok {ref}")
    agent(say(f"ok {ref}"))

    scene("8:20", "You send it from Gmail, and it notices", "with Gmail connected it watches your Sent folder")
    for note in gmail.match_sent(conn, _mail("aaron@pebbletheapp.com", "jordan@riverlightfest.example", "Quick favor", "Hi Jordan")):
        agent(note)
    conn.commit()

    scene("9:05", "An organizer posts a vendor call on Instagram",
          "it reads known organizers' posts each morning through Meta's official API; the post is built in here")
    you("ig sandhill @sandhilledp")
    agent(say("ig sandhill @sandhilledp"))
    instagram.profile = lambda handle: {"media": {"data": [{
        "caption": "📣 Calling all vendors! Applications for Sandhill Small Business Saturday are open now. #shopsmall",
        "timestamp": f"{db.today()}T13:00:00+0000", "permalink": "https://www.instagram.com/p/DEMO1/"}]}} \
        if handle == "sandhilledp" else {"media": {"data": []}}
    for n in instagram.watch(conn):
        agent(n["note"])
    sandhill = db.find_org(conn, "sandhill")["id"]
    you(f"draft {sandhill}")
    agent(say(f"draft {sandhill}"))

    scene("11:02", "A prospect writes back", "the reply is read, sorted, and answered in a draft before you see it")
    agent(gmail.handle_inbox_message(conn, _mail("Carl Jennings <carl@oldtownfairhaven.example>", "aaron@pebbletheapp.com",
                                                 "Re: Holiday Stroll", "Sounds good, let's talk Tuesday.")))

    scene("11:40", "Another pushes back", "it reads the objection and answers the way the playbook says to")
    agent(gmail.handle_inbox_message(conn, _mail("Lucia Ortega <lucia@highdesertevents.example>", "aaron@pebbletheapp.com",
                                                 "Re: Chile & Craft Fest",
                                                 "Thanks Aaron. We're on an Eventeny contract through next year.")))

    scene("13:15", "You screenshot an organizer you found online",
          "send it on WhatsApp; Claude reads the image (stood in here) and the merge rules below are the real code")
    you("[screenshot of a Facebook event] this is the guild in Beaufort")
    shot = screenshot.apply(conn, {"name": "Lowcountry Makers Guild", "segment": "multi_festival", "city": "Beaufort",
                                   "state": "SC", "events_per_year": 5, "flagship_event": "Lowcountry Holiday Market",
                                   "decision_maker": "Tasha Greene", "decision_maker_title": "Market Director",
                                   "email": "tasha@lowcountrymakers.example", "phone": "(843) 555-0177",
                                   "instagram": "@lowcountrymakers", "signal_date": str(db.today()),
                                   "signal_kind": "vendor_apps_open",
                                   "signal_detail": "vendor applications opened for the Lowcountry Holiday Market"},
                            "file:///demo/screenshot.png")
    conn.commit()
    agent(screenshot.note([shot], "Their Facebook event page"))

    scene("17:00", "The weekly report", "emailed to you every Monday, or on demand; built here, not emailed")
    try:
        import reportlab  # noqa: F401
        report.REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")
        data = report.build(conn)
        pdf = report.pdf(data, os.path.join(report.REPORTS_DIR, "pebble-report-demo.pdf"))
        orgs = sum(len(g["orgs"]) for g in data["groups"])
        agent(f"{report.summary(data)}\nReport for {orgs} organizers, every contact detail linked and sourced:\n"
              f"{os.path.abspath(pdf)}")
    except ImportError:
        agent("(Skipped: the PDF report needs reportlab. pip install reportlab)")

    scene("17:05", "How it's doing", "one metric per agent")
    you("metrics")
    agent(say("metrics"))

    scene("17:10", "Evals", "the same checks the draft agent runs on itself before saving; python3 -m evals.run for detail")
    from evals import run as evals
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        failed = evals.main(False)
    print(buf.getvalue().strip().split("\n")[-1])
    print("\nThat's the day. With keys in .env the same thing runs on WhatsApp, on real organizers, with Claude doing the "
          "prospecting, research and writing.")
    return failed
