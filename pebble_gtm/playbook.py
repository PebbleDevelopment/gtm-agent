"""The GTM playbook every agent shares: who each buyer is, what moves them, how to handle
pushback, and how to talk, both to prospects and to Aaron.

This is the "expert" part. The agents' prompts quote it, the rules-based fallbacks use it
directly, and changing a line here changes how the whole system thinks.
"""

import re

# Who each segment is, what they care about, and what to lead with.
PERSONAS = {
    "multi_festival": {
        "who": "an owner-operator producing several events a year, usually wearing every hat",
        "cares": "fewer hours chasing vendors, getting paid on time, looking professional to vendors",
        "pains": "applications in one tool, payments in another, booth maps in a spreadsheet, and the same vendor questions forty times",
        "lead_with": "time back during vendor season, and doing it once for every event instead of rebuilding each time",
        "offer": "set up their next event for them, using last year's vendor list",
        "avoid": "talking about 'platforms' or 'ecosystems'; they want the Tuesday-night problem gone",
    },
    "venue": {
        "who": "a venue's events or partnerships lead who books outside organizers",
        "cares": "organizers who rebook, fewer day-of headaches, knowing what happens on their property",
        "pains": "every organizer runs vendors differently, and the venue gets the fallout",
        "lead_with": "making the venue easier to book and run events at: a standard vendor setup they can hand every organizer",
        "offer": "set up one organizer on their calendar as a pilot",
        "avoid": "making it sound like more work for the venue",
    },
    "tourism": {
        "who": "a tourism bureau's events or marketing lead accountable to a board and funders",
        "cares": "visitor and local-business impact they can prove, and events that run smoothly with a small team",
        "pains": "impact numbers pieced together from surveys and guesses",
        "lead_with": "live vendor sales numbers they can put in front of their board",
        "offer": "run one event on it and hand them the vendor sales report afterward",
        "avoid": "hard-sell language; public bodies move by consensus",
    },
    "chamber": {
        "who": "a chamber's events manager running signature events with a lean staff and volunteers",
        "cares": "member businesses doing well, sponsor value, not burning out volunteers",
        "pains": "paper and Google Forms, checks to deposit, and a lot of follow-up by hand",
        "lead_with": "taking vendor busywork off a small team, plus numbers showing members did well",
        "offer": "set up their next signature event for them",
        "avoid": "anything that reads like enterprise software",
    },
    "econ_dev": {
        "who": "a small-business or Main Street program lead using markets to grow local businesses",
        "cares": "local business outcomes, grant reporting, program visibility",
        "pains": "proving outcomes to funders with thin data",
        "lead_with": "sales data per vendor that answers 'did this program help local businesses?'",
        "offer": "a pilot on their next market with a funder-ready report",
        "avoid": "growth-hacking language",
    },
    "single_event": {
        "who": "a volunteer chair of a single annual event",
        "cares": "getting through the one weekend",
        "pains": "paper forms",
        "lead_with": "nothing yet: outside the ICP",
        "offer": "none",
        "avoid": "spending time here",
    },
}

# Pushback, how to spot it in a reply, what an expert does next, and when to come back.
OBJECTIONS = {
    "existing_tool": {
        "signals": ["eventeny", "already use", "already have", "contract", "we use", "locked in", "current system", "signed with"],
        "move": "Respect it. Ask when it renews and offer to run one event on Pebble alongside it, so the switch is low-risk.",
        "revisit_days": 60,
    },
    "timing": {
        "signals": ["not right now", "after the season", "next year", "busy season", "too busy", "maybe later", "not at this time",
                    "in the spring", "after the holidays", "circle back later"],
        "move": "Agree, and ask when they start planning next season. Come back right before vendor applications open.",
        "revisit_days": 45,
    },
    "not_decider": {
        "signals": ["board", "committee", "not my call", "not my decision", "forward this", "my boss", "director decides", "council"],
        "move": "Ask who else should see it and offer a short summary they can forward. Make your champion look good.",
        "revisit_days": 7,
    },
    "price": {
        "signals": ["cost", "price", "pricing", "how much", "budget", "fee", "afford", "expensive"],
        "move": "Answer plainly (free to start, about 10% of vendor fees, Pro $249/mo), then offer to set up one event so they can judge it on real vendors.",
        "revisit_days": None,
    },
    "how_it_works": {
        "signals": ["how does", "what does it", "does it integrate", "can it", "do you support", "what about"],
        "move": "Answer the exact question in one or two lines, then offer to show it on their own event.",
        "revisit_days": None,
    },
}

# How the messages to prospects should read. The draft agent's prompt quotes these.
PROSPECT_PRINCIPLES = """- Write about their world first, Pebble second. The first line is about them.
- One idea per message. Earn a reply, not a meeting.
- Make yes easy: offer to do the work ("I'll set it up with last year's vendor list").
- Every follow-up adds something new: a fresh signal, a useful idea, a smaller ask. Never "just checking in".
- Sound like a founder who knows events: plain words, contractions, a specific detail only someone paying attention would know.
- No fake familiarity, no flattery, no invented results, no hype."""

# How the system talks to Aaron. The assistant and the morning brief use this.
AARON_STYLE = """Talk to Aaron the way a sharp GTM lead texts the founder they work for:
- Lead with what matters most today and what you'd do about it. Give your recommendation, not a menu.
- Give the reason in a clause ("Jen said yes, so lock the call before the weekend"). That reasoning is the value you add.
- Use people's and orgs' names ("Jen at Lakeshore"), not ids. Put ids only in the action hint at the end ("ok 13").
- Plain, warm, brief. Contractions. Mix short and longer sentences. No headers, no internal labels (touch 2,
  referral_ask, activation, stage names), and bullets only for three or more parallel items.
- Don't narrate your process ("Let me check..."). Do the work, then say what you found.
- Be straight. If something's weak, say so. If you don't know, say that and how you'd find out.
- A real win (a signup, a first payment) gets one line of genuine enthusiasm, not a parade."""

PURPOSE_WORDS = {
    "outreach": "first note", "reply": "reply", "intro_ask": "intro ask", "activation": "onboarding nudge",
    "referral_ask": "referral ask",
}


def persona(segment):
    return PERSONAS.get(segment, PERSONAS["multi_festival"])


def detect_objection(text):
    """The first pushback pattern in a reply, or None. Order matters: a contract beats a price question."""
    t = (text or "").lower()
    for key, o in OBJECTIONS.items():
        if any(s in t for s in o["signals"]):
            return key
    return None


def first(name):
    return (name or "").split()[0] if name else "them"


def who(org):
    """'Jen at Lakeshore Pavilion', for messages to Aaron."""
    return f"{first(org['decision_maker'])} at {org['name']}" if org["decision_maker"] else org["name"]


def third_person(detail):
    """Signals are written to the prospect ("your post about..."); notes to Aaron say "their post about..."."""
    d = re.sub(r"^your\b", "their", detail.strip(), flags=re.IGNORECASE)
    d = re.sub(r"^you\b", "they", d, flags=re.IGNORECASE)
    return d[:1].upper() + d[1:]


def angle(org, purpose, trigger=None, objection=None, intro_from=None, touch=1):
    """One line on why a draft is written the way it is. Shown to Aaron with every draft."""
    p = persona(org["segment"])
    if purpose == "intro_ask":
        return "Warm path first. Venues and chambers answer an intro from an organizer they host far more often than a cold note, and asking your customer to check with them first keeps it double opt-in."
    if purpose == "referral_ask":
        return "They're live and happy, which is the best moment to ask. The ask names the kinds of people they know, so one name comes to mind easily."
    if purpose == "activation":
        return "They signed up but stalled. The fastest unblock is offering to do the next step for them."
    if purpose == "reply" and objection:
        return f"They pushed back ({objection.replace('_', ' ')}). {OBJECTIONS[objection]['move']}"
    if purpose == "reply":
        return "They're interested, so make the next step concrete: two time options and a promise to show their own event."
    if intro_from:
        return f"Lead with {first(intro_from['decision_maker'])}'s intro. That's the reason they'll read it."
    if touch == 2:
        return "Second touch adds something new and lowers the ask to a free setup they can compare side by side."
    if touch >= 3:
        return "Last touch. A graceful close-out often gets the reply the first two didn't, and leaves the door open."
    news = third_person(trigger["detail"]) if trigger else ""
    lead = f"Opens on their own news ({news[:1].lower() + news[1:]})" if trigger else "No strong trigger"
    return f"{lead}, then speaks to what {p['who'].split(' who')[0].split(',')[0]} cares about: {p['lead_with']}."
