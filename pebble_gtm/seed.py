"""Synthetic sample data shaped like real festival organizers, venues and civic groups.

Every org here is fictional (synthetic=1). Dates are relative to today so the
follow-up rules always have something due when you run it.
"""

from datetime import timedelta

from . import db

# name, segment, city, state, events/yr, flagship, vendors/event, decision maker, title, email, website, tools
ORGS = [
    ("Blue Ridge Makers Collective", "multi_festival", "Asheville", "NC", 7, "Mountain Makers Fall Market", 140,
     "Dana Whitfield", "Founder & Producer", "dana@blueridgemakers.example", "blueridgemakers.example",
     "Google Forms, Venmo, a shared spreadsheet"),
    ("Gulf Coast Festival Productions", "multi_festival", "Mobile", "AL", 9, "Bayfront Seafood & Music Fest", 220,
     "Marcus Lee", "Owner", "marcus@gulfcoastfest.example", "gulfcoastfest.example", "Eventeny"),
    ("The Foundry Yards", "venue", "Chattanooga", "TN", 14, "Foundry Yards Night Market", 80,
     "Priya Raman", "Director of Events & Partnerships", "priya@foundryyards.example", "foundryyards.example",
     "each renting organizer brings their own"),
    ("Visit Cedar Lake", "tourism", "Cedar Lake", "MN", 5, "Cedar Lake Ice Festival", 90,
     "Tom Becker", "Director of Events", "tbecker@visitcedarlake.example", "visitcedarlake.example",
     "JotForm and paper checks"),
    ("Greater Millbrook Chamber of Commerce", "chamber", "Millbrook", "OH", 4, "Millbrook Harvest Days", 160,
     "Angela Ruiz", "Events Manager", "aruiz@millbrookchamber.example", "millbrookchamber.example",
     "Google Forms and Square"),
    ("Sandhill Economic Development Partnership", "econ_dev", "Sandhill", "SC", 3, "Sandhill Small Business Saturday", 70,
     "Rob Ellison", "Small Business Programs Lead", "rellison@sandhilledp.example", "sandhilledp.example",
     "email and spreadsheets"),
    ("High Desert Events Co.", "multi_festival", "Albuquerque", "NM", 6, "High Desert Chile & Craft Fest", 180,
     "Lucia Ortega", "Co-founder", "lucia@highdesertevents.example", "highdesertevents.example",
     "Google Forms, Stripe links"),
    ("Lakeshore Pavilion", "venue", "Port Avery", "WI", 10, "Lakeshore Summer Makers Night", 60,
     "Jen Kowalski", "General Manager", "jen@lakeshorepavilion.example", "lakeshorepavilion.example",
     "organizers handle vendors themselves"),
    ("Old Town Merchants Association", "chamber", "Fairhaven", "TX", 5, "Old Town Holiday Stroll", 110,
     "Carl Jennings", "Executive Director", "carl@oldtownfairhaven.example", "oldtownfairhaven.example",
     "Wufoo and checks"),
    ("Prairie Fest Partners", "multi_festival", "Wichita", "KS", 4, "Prairie Food Truck Rally", 95,
     "Beth Morales", "Managing Partner", "beth@prairiefest.example", "prairiefest.example", "Eventbrite + spreadsheets"),
    ("Riverside Strawberry Festival", "single_event", "Riverside", "IA", 1, "Riverside Strawberry Festival", 40,
     "Gary Olsen", "Volunteer Chair", "gary@riversidestrawberry.example", "riversidestrawberry.example", "paper forms"),
    ("Coastal Craft Fairs LLC", "multi_festival", "Wilmington", "NC", 8, "Coastal Holiday Craft Fair", 150,
     "Nina Shah", "Owner", "nina@coastalcraftfairs.example", "coastalcraftfairs.example", "Eventeny"),
    ("Summit County Tourism Bureau", "tourism", "Summit Springs", "CO", 6, "Summit Springs Brewfest", 75,
     "Alex Park", "Events & Marketing Manager", "apark@summittourism.example", "summittourism.example",
     "Google Forms and invoices"),
    # Pebble's two live customers (fictional stand-ins), and one org that signed up but stalled.
    ("Riverlight Festivals", "multi_festival", "Chattanooga", "TN", 6, "Riverlight Riverfront Market", 120,
     "Jordan Hayes", "Founder", "jordan@riverlightfest.example", "riverlightfest.example", "Pebble"),
    ("Buckeye Night Markets", "multi_festival", "Columbus", "OH", 8, "Buckeye Summer Night Market", 100,
     "Sam Patel", "Owner", "sam@buckeyenight.example", "buckeyenight.example", "Pebble"),
    ("Palmetto Pop-Up Markets", "multi_festival", "Charleston", "SC", 5, "Palmetto Holiday Pop-Up", 85,
     "Rae Simmons", "Founder", "rae@palmettopopup.example", "palmettopopup.example", "Pebble (onboarding)"),
]

# Pebble milestones: org index -> {milestone: days ago}
MILESTONES = {
    13: {"signed_up": 150, "event_created": 148, "apps_open": 140, "first_payment": 132},
    14: {"signed_up": 90, "event_created": 88, "apps_open": 85, "first_payment": 70},
    15: {"signed_up": 9, "event_created": 7},
}

# org index, days ago, kind, detail (written to read after "Noticed ...")
SIGNALS = [
    (0, 5, "vendor_apps_open", "vendor applications opened for the Holiday Makers Market (Dec 13-14)", "blueridgemakers.example/apply"),
    (0, 12, "tool_pain", "your post about chasing 40+ vendor payments across Venmo and checks", "Facebook group: WNC Makers"),
    (1, 8, "new_event", "you announced a new Spring Night Market at the Mobile waterfront", "local news"),
    (2, 3, "venue_booking", "The Foundry Yards published its 2027 calendar with 14 organizer-run events", "foundryyards.example/events"),
    (3, 20, "grant", "Visit Cedar Lake received a $40,000 state tourism grant for event promotion", "state tourism press release"),
    (4, 6, "hiring", "the Chamber is hiring a part-time vendor coordinator for Harvest Days", "job board"),
    (5, 120, "new_event", "the Partnership launched Small Business Saturday last year", "annual report"),
    (6, 30, "vendor_apps_open", "vendor applications opened for the Chile & Craft Fest", "highdesertevents.example"),
    (6, 2, "tool_pain", "your note that vendor booth assignments took three volunteers two weekends", "Instagram story"),
    (7, 14, "venue_booking", "Lakeshore Pavilion added a second makers night per month", "venue newsletter"),
    (8, 40, "new_event", "the Association added a spring sidewalk sale", "chamber newsletter"),
    (9, 4, "vendor_apps_open", "vendor applications opened for the Prairie Food Truck Rally", "prairiefest.example"),
    (10, 15, "vendor_apps_open", "vendor applications opened for the Strawberry Festival", "Facebook page"),
    (11, 35, "tool_pain", "your review comparing vendor platforms", "blog"),
    (12, 28, "grant", "Summit County approved event funding tied to vendor sales reporting", "county minutes"),
]

# Past outreach so the follow-up rules and metrics have history to work with.
# org index, brief why_now (None = sent without a trigger), touches: (touch, sent days ago, replied days ago, reply kind)
HISTORY = [
    (6, "Vendor applications for the Chile & Craft Fest opened 30 days ago.", [(1, 10, None, None)]),
    (7, "Lakeshore Pavilion added a second makers night per month.", [(1, 6, 2, "positive")]),
    (8, "The Association added a spring sidewalk sale.", [(1, 25, None, None), (2, 9, None, None)]),
    (9, None, [(1, 70, None, None), (2, 63, None, None), (3, 50, None, None)]),
    (11, "Nina reviewed vendor platforms publicly.", [(1, 30, 27, "negative")]),
    (12, "Summit County approved event funding tied to vendor sales reporting.", [(1, 25, None, None), (2, 18, 1, "neutral")]),
    (15, "Palmetto announced a holiday pop-up series.", [(1, 30, 27, "positive"), (2, 20, None, None)]),
]

REPLY_NOTES = {
    "positive": "Yes, would love to see it. We're redoing our organizer packet for spring.",
    "neutral": "What does this cost for six events? We're a public bureau.",
    "negative": "We're on an Eventeny contract through 2027, not looking right now.",
}


def seed(conn, reset=True):
    if reset:
        db.reset(conn)

    today = db.today()
    ago = lambda n: (today - timedelta(days=n)).isoformat()

    ids = []
    for o in ORGS:
        cur = conn.execute(
            """INSERT INTO orgs (name, segment, city, state, events_per_year, flagship_event, vendors_per_event,
               decision_maker, decision_maker_title, email, website, current_tools, synthetic, email_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,'valid')""", o)
        ids.append(cur.lastrowid)
        conn.execute("INSERT INTO pipeline (org_id, stage) VALUES (?, 'new')", (cur.lastrowid,))

    for idx, days, kind, detail, source in SIGNALS:
        conn.execute("INSERT INTO signals (org_id, seen_on, kind, detail, source) VALUES (?,?,?,?,?)",
                     (ids[idx], ago(days), kind, detail, source))

    for idx, why_now, touches in HISTORY:
        org_id = ids[idx]
        first_sent = max(t[1] for t in touches)
        brief_id = conn.execute(
            "INSERT INTO briefs (org_id, created_at, fit_score, why_now, body) VALUES (?,?,?,?,?)",
            (org_id, ago(first_sent + 1), None, why_now, "(historical brief)")).lastrowid
        for touch, sent, replied, kind in touches:
            conn.execute(
                """INSERT INTO drafts (org_id, brief_id, touch, body, status, created_at, approved_at, sent_at,
                   replied_at, reply_kind) VALUES (?,?,?,?, 'sent', ?,?,?,?,?)""",
                (org_id, brief_id, touch, f"(historical touch {touch})", ago(sent + 1), ago(sent), ago(sent),
                 ago(replied) if replied is not None else None, kind))
            if kind:
                db.log(conn, "aaron", "reply", org_id, f"{kind}: {REPLY_NOTES[kind]}")
        last = min(t[1] for t in touches)
        conn.execute("UPDATE pipeline SET last_contact = ? WHERE org_id = ?", (ago(last), org_id))

    for idx, ms in MILESTONES.items():
        for m, days in ms.items():
            conn.execute("INSERT INTO milestones (org_id, milestone, at) VALUES (?,?,?)", (ids[idx], m, ago(days)))

    # Prairie Fest went cold after three touches; a new signal has appeared since.
    conn.execute("UPDATE pipeline SET stage = 'cold' WHERE org_id = ?", (ids[9],))

    from . import tracking
    for org_id in ids:
        tracking.refresh_stage(conn, org_id)
    conn.commit()
    return ids
