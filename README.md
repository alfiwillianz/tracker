# tracker;

A shared assignment tracker for a class. Everyone logs in with their NRP, posts assignments to the
groups (courses) they're in, and ticks off what they've finished. Dark, minimal, styled after
[alfiwillianz.my.id](https://alfiwillianz.my.id).

Flask + SQLite, server-rendered, with a small amount of plain JavaScript (no framework, no build step).

## Features

- **Accounts** — one per student, seeded on first run. Everyone starts on a default password and **must change it on first login**.
- **Assignments** — title, details, link, a date picker plus a typed 24h time. Grouped by urgency (overdue / today / tomorrow / this week / later), with stats, To do / Done / All tabs and a per-group filter. "Done" is per person.
- **Groups** — one per course. Private (invite only) or public (listed, one-click join). Group creators are admins and can promote others, remove members, delete the group and delete its assignments. Assignments can also be shared with individual people by NRP.
- **RKA** — the default group everyone starts in. Only web admins (the `ADMINS` list) manage its members.
- **No-reload UI** — navigation, forms, live member filtering, a progress bar, animations and toasts. It all degrades to plain page loads without JavaScript.
- **Account page** — change your password and manage your personal API key (shown once, stored hashed, revocable).
- **JSON API** — read-only, for bots and scripts, authenticated with that key. See [docs/api.md](docs/api.md); the running site serves the same page at `/docs` (public, and deliberately not linked from the header).

## Quick start

```bash
cp .env.example .env                # put your Cloudflare tunnel token in .env (see "Public access")
docker compose up -d --build        # app on http://127.0.0.1:5000
```

`docker-compose.yml` also defines the Cloudflare tunnel, and Compose refuses to start **any** service while `CF_TUNNEL_TOKEN` is unset.
To try the app without a tunnel, put any placeholder value in `.env`, or use the no-Docker route below.

First-run login: any seeded NRP (`5054251001` … `5054251051`) with password `123456`, then pick a new one.

### Without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
DATA_DIR=./data python app.py        # http://127.0.0.1:5000
# or, with auto-reload while developing:
DATA_DIR=./data flask --app app run --debug --port 5050
```

## Configuration

Environment variables (set in `docker-compose.yml`):

| Variable | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `./data` (`/data` in Docker) | Where the SQLite database and the session key live. |
| `SECRET_KEY` | generated into `DATA_DIR/secret_key` | Session signing key. Set it to share one key across instances. |
| `ADMINS` | `5054251028` | Comma-separated NRPs who administer the default group (RKA). |
| `TRUST_PROXY` | off | `1` when behind a reverse proxy / tunnel, so client IPs and HTTPS are read from its headers. |
| `COOKIE_SECURE` | off | `1` to mark the session cookie HTTPS-only. Use it with a tunnel/HTTPS; it breaks plain-HTTP logins. |
| `TZ` | — | Timezone for deadlines and "due in…" (`Asia/Jakarta` in compose). |
| `CF_TUNNEL_TOKEN` | — | In `.env`, for the Cloudflare tunnel container. |

### Student names

Names are optional. Put a tab-separated `students.tsv` next to `app.py`:

```
5054251001<TAB>Full Name
5054251002<TAB>Another Name
```

It's read on every start and is **gitignored** because it holds real names. Without it, people show up as their NRP.
The seeded NRP range is `5054251` + `001`…`051`; accounts listed in `REMOVED_USERS` in `app.py` are never created and are purged if present.

## Public access

`docker-compose.yml` runs `cloudflared` next to the app. Create a tunnel in the Cloudflare Zero Trust dashboard, then:

```bash
cp .env.example .env                # put the tunnel token in .env
# In the dashboard, add a Public Hostname pointing at  http://tracker:5000
docker compose up -d --build
```

The app port is bound to `127.0.0.1` only; public traffic goes through the tunnel. Static files are served with a `?v=<mtime>` suffix, so Cloudflare and browsers pick up new CSS/JS after a deploy.

## Data and backups

Everything is in `./data` (SQLite `tracker.db` + `secret_key`). Back it up by copying that folder, or use SQLite's online backup while the app is running:

```bash
docker exec tracker python -c "import sqlite3; s=sqlite3.connect('/data/tracker.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d)"
```

Schema upgrades run automatically at startup (`init_db()` in `app.py`) and keep existing data.
Note that some migrations are one-way — e.g. the old per-assignment `course` column was dropped when groups replaced courses — so take a copy before upgrading.

## Security notes

- Passwords are hashed (Werkzeug); every form has a CSRF token; user text is escaped; security headers are set.
- API keys are random 256-bit values; only a SHA-256 hash is stored. The API never accepts the website password and failed attempts are rate limited. The web login has no rate limit yet.
- Everyone knows everyone's NRP and starts on the same default password, so **tell people to change it right after the site goes up**.

## Layout

```
app.py              routes, database, migrations, API
templates/          Jinja templates (_member_list.html is also served alone to the live filter)
static/styles.css   all styling
static/app.js       no-reload navigation, progress bar, toasts
static/*.js         widgets: picker, select, confirm pop-up, ⋯ menu, live member list, copy button
docs/api.md         API reference (also rendered at /docs)
students.tsv        optional names (gitignored)
```
