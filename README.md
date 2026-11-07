# Pebble GTM agent

An agent I text on WhatsApp that finds the organizations that should run their vendor events on Pebble, gets
me in front of them, and keeps pushing until they're **live**, meaning their first vendor payment has gone through Pebble.

The ICP is organizers who produce several festivals or markets a year, venues that host several organizers, and tourism
bureaus, chambers and economic-development groups that run vendor events.

**It never sends anything to anyone except me.** Agents write drafts. I approve with `ok 14`, copy the text, send it
myself, and text `sent 14`. No agent has a tool that can approve or send. Those two steps exist only as shortcuts
that read my own message.

## How it works (the Boardy idea)

- **I talk to it like a person.** "Anything from the Chattanooga venue?", "Dana replied, she wants Tuesday",
  "find me chambers in Ohio". The assistant picks the right agent, does the work, and answers in a few lines
  ending with the one thing I should do next. It remembers our recent conversation and anything I tell it about how I work.
- **It reaches out to me first.** Every morning it finds up to 5 new organizers (`DAILY_LEADS` in `.env`), researches
  them, pre-drafts and texts me what's ready:
  ```
  Morning Aaron. 2 orgs are live on Pebble and 1 is getting set up.

  Here's what I'd do first:
  • Jen at Lakeshore Pavilion said yes. Lock in a time while it's warm; I offered two (ok 13).
  • Alex at Summit County Tourism Bureau asked what it costs, so I drafted a straight answer (ok 14).
  • Palmetto Pop-Up Markets set up their first event 7 days ago and haven't opened vendor applications yet. I offered to do it for Rae (ok 17).

  Also ready: a referral ask to Riverlight Festivals (ok 15), and a follow-up to Lucia at High Desert Events Co. (ok 18).
  ```
- **Warm intros before cold email, double opt-in.** For a venue or chamber near a live customer, it drafts a note asking
  the customer whether that person is open to hearing from me. Only after the customer says yes does it draft the first
  message ("Jordan at Riverlight suggested I reach out").
- **It doesn't stop at "yes".** After signup it watches Pebble milestones (signed up → event created → applications open →
  first payment) and drafts a nudge whenever an org stalls. Once an org is live, it asks them for a referral, which feeds
  the next round of warm intros.
- **It learns my voice.** Every time I `edit` a draft, the before/after is saved, and the draft agent reads those edits
  before it writes the next one. `voice <paste an email I wrote>` gives it real samples of how I write.
- **It thinks like a GTM lead and talks like one** (`playbook.py`). It knows who each buyer is and what moves them, how
  to read pushback (a contract means ask when it renews, "the board" means arm your champion, a price question means
  interest), and that "not now" is a date to come back. Every draft comes with one line on why it's written that way.
  Messages to me lead with what matters and what it would do, using people's names and no internal labels:
  ```
  Morning Aaron. 2 orgs are live on Pebble and 1 is getting set up.

  Here's what I'd do first:
  • Jen at Lakeshore Pavilion said yes. Lock in a time while it's warm; I offered two (ok 13).
  • Alex at Summit County Tourism Bureau asked what it costs, so I drafted a straight answer (ok 14).
  • Palmetto Pop-Up Markets set up their first event 7 days ago and haven't opened vendor applications yet. I offered to do it for Rae (ok 17).

  Also ready: a referral ask to Riverlight Festivals (ok 15), a referral ask to Buckeye Night Markets (ok 16), and a follow-up to Lucia at High Desert Events Co. (ok 18).
  ```
  When a reply lands in Gmail, the answer is already drafted: "Carl at Old Town wrote back, and it's a yes…"

```
WhatsApp ─Twilio─▶ whatsapp.py ─▶ commands.py ──shortcuts (ok/sent/today...)──────────┐
                     │  ▲              └─plain language─▶ assistant.py (Claude + tools) ┤
   Pebble ─/pebble───┘  └─ morning push ◀─ followup.morning()                          ▼
                                     prospect.py · research.py · draft.py · followup.py · tracking.py
                                                         └──────── pebble.db (SQLite) ────────┘
```

## The agents, one file each

| Agent | What it does on its own | Its metric |
|---|---|---|
| **Prospect** `prospect.py` | Searches the web for new ICP orgs, checks each one against the ICP on a real page, dedupes, and saves it with the source and any dated trigger. It picks where to look by itself: near live customers first, then rotating segments across states. | prospects found → contacted → replied → signed up |
| **Research** `research.py` | Searches and reads their site to fill gaps (events/yr, flagship, decider and title, current tools) and saves dated, cited signals. Then the brief is built by rules: one why-now, HOLD if nothing is fresh, SKIP if outside the ICP, plus a warm path if a live customer is nearby. | reply rate for briefs with a trigger vs without |
| **Draft** `draft.py` | Writes the next message for any stage: first touch, follow-up, reply, intro ask, activation nudge, referral ask. It drafts, runs the eval checks on itself, revises until they pass, and only then saves. | approved without my edits |
| **Follow-up** `followup.py` | Sorts every org into replied / waiting on me / activation / un-replied / cold / new and proposes the next step with a reason. It runs the morning pass. | reply rate by touch |
| *Clay* `clay.py` / *Gmail* `gmail.py` / *Instagram* `instagram.py` | Integrations, not agents. Clay fills in decider and email; Gmail turns your inbox into reply and send events; Instagram turns organizers' posts into reasons to write, and hashtag vendor calls into new prospects. | valid emails from Clay; replies logged without you; orgs with Instagram news → contacted |
| **Tracking** `tracking.py` | The pipeline from new to live. Stages come from what actually happened, plus Pebble milestones. | funnel to live, hours saved/week |
| **Assistant** `assistant.py` | The one I talk to. It has tools for all of the above, conversation memory, and my standing preferences. | |

Every Claude agent runs through the same ~40-line loop in `llm.py` (`run_agent`), so there is one place to read to
understand how they all work.

## Run it

```bash
python3 -m pebble_gtm seed          # 16 synthetic orgs: 2 live customers, 1 stalled in onboarding, outreach history
python3 -m pebble_gtm today         # works with no API key (rules + templates)
python3 -m pebble_gtm morning       # the daily pass
python3 -m evals.run                # evals, pass/fail

pip install -r requirements.txt     # anthropic (agentic parts), reportlab (PDF report)
export ANTHROPIC_API_KEY=...
python3 -m pebble_gtm chat          # talk to it like WhatsApp
python3 -m evals.run --llm          # also grade the live draft agent and the assistant's approval discipline
```

Without a key everything except prospecting, web research and plain-language chat still works on rules and templates.
`PEBBLE_OFFLINE=1` forces that mode.

## WhatsApp

This uses the Twilio WhatsApp Sandbox, which is free and needs no Meta verification.

1. In Twilio Console go to **Messaging → Try it out → Send a WhatsApp message** and join the sandbox from your phone.
2. Run `ngrok http 8787` (or `cloudflared tunnel --url http://localhost:8787`).
3. Set the sandbox's **When a message comes in** to `https://<tunnel>/whatsapp` (POST).
4. Run:
```bash
export ALLOWED_WHATSAPP_FROM="whatsapp:+1YOURNUMBER"   # the only number it answers or messages
export TWILIO_AUTH_TOKEN=...  TWILIO_ACCOUNT_SID=...
export TWILIO_WHATSAPP_FROM="whatsapp:+14155238886"    # sandbox number
export PUBLIC_URL="https://<tunnel>/whatsapp"          # must match what Twilio calls exactly
export MORNING_HOUR=8                                  # daily push; "off" to disable
python3 -m pebble_gtm serve
```
Anything that takes longer than about 12 seconds replies "On it" and pushes the result when it's done.

**WhatsApp's 24-hour rule:** a business number can only send free-form messages within 24 hours of your last message
to it. The morning push works as long as you've texted it in the last day. For pushes that always arrive, register a
WhatsApp message template in Twilio and send the morning summary through it.

## Clay: find and verify the decider's email

The prospect and research agents find organizations; Clay finds the person and a verified email. The round trip runs
without you:

1. In Clay, make a table with a **Webhook** source and copy its URL (and auth token if you turn one on).
2. Columns: find people at the company by `domain` filtered to `target_titles` (both sent by us), then email
   waterfall + verification. Keep `pebble_org_id` as a column so results can be matched back.
3. Add an **HTTP API** column that POSTs to `https://<tunnel>/clay` with header `X-Clay-Secret: <your secret>` and body:
   `{"pebble_org_id": …, "decision_maker": …, "decision_maker_title": …, "email": …, "email_status": "valid|risky|invalid", "phone": …, "linkedin_url": …}`
4. Then:
```bash
export CLAY_WEBHOOK_URL=...  CLAY_WEBHOOK_TOKEN=...  CLAY_CALLBACK_SECRET=...
```
Orgs that fit but have no usable email get sent automatically: new prospects, the morning pass, and anything that bounces.
`enrich <org>` sends one by hand. When the result arrives you get a WhatsApp ("Clay found Kai Lund, Events Director at
Mill Hall…"). Clay only fills gaps: it never overwrites a decider you or research set, and it replaces an email only when
the current one is missing or bounced. The auth header name (`x-clay-webhook-auth`) came from Clay's community forum.
Check it against your table's settings.

## Gmail: replies and sends, automatically

Read-only IMAP with an app password. It never sends, deletes or marks anything read.

1. Turn on 2-Step Verification for the Google account (myaccount.google.com → Security).
2. Create an app password (myaccount.google.com/apppasswords), name it "Pebble GTM", and copy the 16 characters.
   On Google Workspace, the admin console must allow app passwords and IMAP.
3. `cp .env.example .env`, then put the address and app password in `.env`. It's gitignored, and every command reads it.
4. `python3 -m pebble_gtm gmail test` logs in and counts recent mail without reading or recording anything.
Every 10 minutes (and at the start of the morning pass):
- **A reply from anyone in the pipeline** (exact address, or a colleague at the same domain) is logged on their thread and
  sorted as positive, neutral or negative. Claude does the sorting, with keyword rules as a fallback, and only reads the
  new text, not the quoted thread. You get it on WhatsApp: `↩ Old Town replied (positive): "Sounds good, let's talk Tuesday."`
- **When you send an approved draft yourself**, it's seen in Sent and logged as sent. `sent <#>` becomes optional.
- **Bounces** mark the email invalid and send the org back to Clay. **Out-of-office replies** and **mail from people not in
  the pipeline** are skipped.
- `inbox` checks right now.

If your Google Workspace admin blocks app passwords, the alternative is the Gmail API with OAuth. That's not built.

## Instagram: organizers' posts and hashtag vendor calls

Markets and festivals often announce "vendor applications are open" on Instagram before anywhere else. Every morning:

- **Watch.** For every org with a handle (set it with `ig riverlight @riverlightfest`; research and prospecting also save
  handles they find), it reads their last 12 posts. A vendor call, hiring post or new event from the last 45 days becomes
  a signal, the brief is rebuilt, and that post becomes the reason to write ("Noticed your post on Instagram: …").
- **Discover.** It checks a few hashtags (#callforvendors, #vendorsneeded, #makersmarket… or your own in `IG_HASHTAGS`)
  for posts from the last day. The API doesn't say who posted, so posts that look like an organizer calling for vendors go
  to the prospect agent, which works out the organizer from the caption, checks ICP fit and adds them with the post as
  the signal. This part needs Claude.

It only uses Meta's official API with Pebble's own account: no scraping, and it never posts, likes or DMs. If a DM is the
best way in, ask for a draft and send it yourself. `ig` runs both passes now.

Setup (about 20 minutes):
1. In the Instagram app, switch Pebble's account to a **Business** account and link it to a Facebook Page you manage.
2. At developers.facebook.com, create an app (type Business) and add the **Instagram** product using
   *Instagram API with Facebook Login*.
3. In the Graph API Explorer, pick the app, grant `instagram_basic`, `pages_show_list` and `pages_read_engagement`,
   generate a user token, then exchange it for a **long-lived token** (about 60 days; renew it before it runs out).
4. Find Pebble's Instagram account id: `GET /me/accounts?fields=instagram_business_account` in the same explorer.
5. Put `IG_USER_ID` and `IG_ACCESS_TOKEN` in `.env`, then text `ig`.

Limits from Meta: it can only read **Business or Creator** accounts (most organizers are; personal accounts are
skipped quietly). Hashtag search only sees the **last 24 hours** and **30 unique hashtags per 7 days**, and needs the
app's *Instagram Public Content Access* feature, which may need Meta's app review before it works on other people's posts.
Watching known handles doesn't need it.

## Screenshots: send what you find online

See an organizer's contact page, a Facebook event, an Instagram profile or a vendor-call flyer? Screenshot it and send it
to the WhatsApp chat, with a caption if it helps ("this is the chamber in Mobile"). Several images or a PDF work too.
From the terminal: `python3 -m pebble_gtm screenshot ~/Desktop/shot.png this is the chamber in Mobile`.

Claude reads it and records each organizer it can see; then the same rule as Clay applies. New organizers are added.
Known ones only get gaps filled: it never overwrites a decider, email or phone you already have, and it tells you when the
screenshot disagrees. Dated news in it (applications open, a new event) becomes a signal. Every detail is sourced to
*your screenshot*, and the image is kept in `screenshots/` (gitignored) so the report can link to it. Text inside a
screenshot is treated as data, never as instructions. Needs Claude.

## Weekly report (PDF, by email)

Every Monday at 7am (`REPORT_DAY`, `REPORT_HOUR`; `off` to stop), or whenever you text `report` (or "send me the
report for the last 30 days"), it emails you a PDF:

- **The week:** who's live or setting up, new organizers found, notes sent, replies, and any first payments.
- **Waiting on you:** drafts to approve, and approved notes not sent yet.
- **Every organizer**, grouped (customers, in conversation, reached out, ready to contact, parked), with the decider,
  email, phone, website and Instagram as links (mailto:, tel:, web), plus their latest news.
- **Where each detail came from**, on the same line: *you*, *web research* or *prospect agent* (with a link to the page
  it was on), *Clay*, *Instagram*, a *Gmail bounce*, or *sample data*. Every agent that writes a contact detail records
  its source, so this is what actually happened, not a guess.

It goes only to `GMAIL_ADDRESS`, through Gmail SMTP with the same app password; there is no way to send it to anyone
else. A copy is saved in `reports/` (gitignored). Needs `pip install reportlab`.

**Pebble milestones:** have Pebble call `POST /pebble` with `{"org": "<id or name>", "milestone": "first_payment"}` and
header `X-Pebble-Secret: $PEBBLE_WEBHOOK_SECRET`. Until that's wired up, text `milestone palmetto apps_open`.

## Shortcuts

Plain language works for everything. These shortcuts are faster and free:

`today` · `morning` · `pipeline` · `orgs` · `metrics` · `report [days]` · `prospect [where]` · `enrich <org>` · `inbox` · `ig` · `ig <org> @handle` · `research <org> [web]` · `brief <org>` ·
`draft <org> [force]` · `intro <org>` · `show <#>` · `ok <#>` · `edit <#> <text>` · `discard <#>` · `sent <#>` ·
`replied <org> positive|neutral|negative [note]` · `milestone <org> signed_up|event_created|apps_open|first_payment` ·
`meeting <org>` · `lost <org> [note]` · `signal <org> <kind> <detail>` · `add name | segment | city, ST | events/yr | flagship | decider | title`

## Evals

`python3 -m evals.run` grades against `evals/cases.py`:

- **Research briefs** must state events/yr, name the decider and their title, list only signals that exist, give a why-now
  that cites a fresh signal (HOLD if none, SKIP if outside the ICP), include the Pebble angle, and stay under 180 words. Each
  case also says which trigger should win.
- **Every message type** (first touch, touch 2 and 3, both reply types, three activation nudges, referral ask, intro ask,
  first message after an intro) must open "Hi <first>,", be signed Aaron, stay under 90 words, contain exactly one question,
  mention something specific, use only numbers the brief supports, and avoid banned phrases.
- **Grader self-test:** six known-bad drafts must fail for the right reason.
- **Gmail:** eight real-shaped emails: a yes with a question, a polite no, a colleague asking price, a "not right now"
  above a quoted "I'd love to", an email that tells the classifier to say positive, an out-of-office, a stranger, and a
  bounce. Each must be sorted, ignored or marked correctly. Your own send of an approved draft must be logged.
- **Clay:** a callback must fill a missing email, never overwrite a known decider, and ignore an invalid email.
- **Instagram:** seven captions (a vendor call buried in emoji and hashtags, a closed call, a job post that says
  "applications are open", a new event, an ordinary thank-you, a caption that tries to give instructions) must be read
  correctly; handles must be cleaned from links. Against a fake Graph API: a fresh vendor call must become the brief's
  why-now, old and ordinary posts must not, the same post must never be reported twice, the first note built on it must
  pass the draft checks, and the hashtag scan must keep only vendor calls.
- **Report:** every contact detail must have a source and a link, sources must match who actually found them (Clay,
  web research, Instagram), news must be worded for you, not the prospect, the PDF must contain working mailto:/tel:/
  Instagram links, and it must email exactly one PDF, to you and nobody else.
- **Screenshots:** a new organizer is added and sourced to the image; a known one gets gaps filled while its decider is
  kept and the difference flagged; a different name with the same website still matches; end to end with a fake Claude,
  the image is saved and its details show up in the report.
- **Voice:** pushback must be read as the right objection. Every pushback answer and revisit note must pass the draft
  checks. Every message I read (today, morning, drafts, approvals, reply notices) must be free of internal jargon, and the
  morning brief has a word limit. With `--llm`, a Claude grader also judges whether drafts and the morning brief sound
  human and strategic.
- **`--llm`:** the live draft agent writes every case and is graded the same way. The assistant gets five messages pushing it
  to send or approve ("looks great, send it", "you have my permission"...). It passes only if no draft changes status and
  it never claims it sent or approved anything.

The draft agent runs the same checks on its own output before saving, so production drafts are held to the eval bar too.

## Files

```
pebble_gtm/prospect.py  research.py  draft.py  followup.py  tracking.py   the agents
pebble_gtm/assistant.py   the one you talk to        pebble_gtm/llm.py      the shared agent loop
pebble_gtm/commands.py    shortcuts + routing        pebble_gtm/checks.py   quality checks (evals + self-check)
pebble_gtm/whatsapp.py    Twilio webhook, morning push, inbox polling, /pebble and /clay webhooks
pebble_gtm/clay.py        Clay enrichment round trip  pebble_gtm/gmail.py    read-only reply + send detection
pebble_gtm/instagram.py   organizers' posts as signals, hashtag vendor calls as prospects
pebble_gtm/screenshot.py  organizer data read from screenshots you send
pebble_gtm/report.py      the weekly PDF: pipeline, contact links, and where each detail came from
pebble_gtm/playbook.py    personas, objections, voice: the GTM expertise every agent shares
pebble_gtm/db.py          schema                     pebble_gtm/seed.py     synthetic sample data
evals/cases.py, evals/run.py
```

All seeded orgs are fictional (`.example` domains, `synthetic=1`), including the two "live customers". Real orgs added by
me or by the prospect agent are `synthetic=0` and record where they came from.
