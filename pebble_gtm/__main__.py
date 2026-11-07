"""Entry point.

  python -m pebble_gtm seed                 reset the store with synthetic sample data
  python -m pebble_gtm chat                 type commands as if on WhatsApp
  python -m pebble_gtm serve [--insecure]   run the WhatsApp webhook on :8787
  python -m pebble_gtm morning              run the daily proactive pass now (cron-friendly)
  python -m pebble_gtm report [days]       build the PDF report and email it to you (saved under reports/ too)
  python -m pebble_gtm gmail test           check the Gmail login and folders without processing any mail
  python -m pebble_gtm <anything ...>       a shortcut (`today`, `draft 1`) or plain language
"""

import os
import sys


def load_env(path=os.path.join(os.path.dirname(__file__), "..", ".env")):
    """Read KEY=value lines from .env (gitignored) so secrets live in one local file. Real env vars win."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


load_env()

from . import commands, db, seed, whatsapp  # noqa: E402  (after .env so modules see the settings)


def main(argv):
    if not argv:
        print(__doc__)
        return
    if argv[0] == "seed":
        conn = db.connect()
        ids = seed.seed(conn)
        print(f"Seeded {len(ids)} synthetic orgs into {db.DB_PATH}. Try: python -m pebble_gtm today")
    elif argv[:2] == ["gmail", "test"]:
        from . import gmail
        print(gmail.test())
    elif argv[0] == "serve":
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8787
        whatsapp.serve(port=port, insecure="--insecure" in argv)
    elif argv[0] == "chat":
        conn = db.connect()
        print("Type commands as you would on WhatsApp. `help` lists them, Ctrl-D quits.")
        while True:
            try:
                line = input("\nyou> ")
            except EOFError:
                break
            print(commands.dispatch(conn, line))
    else:
        print(commands.dispatch(db.connect(), " ".join(argv)))


if __name__ == "__main__":
    main(sys.argv[1:])
