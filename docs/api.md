# API v1

A small, **read-only** JSON API for scripts, bots and widgets (reminders, dashboards, a Discord/Telegram bot, a phone shortcut…). You authenticate with a personal API key from the Account page.

Base URL: `https://tracker.example.com/v1`

## Authentication

Use a personal **API key**. Make one on the website: **Account → API key → Generate**.

- The key is shown **once**, right after you generate it. Copy it then; the site only stores a hash, so it can't show it again.
- **Regenerate** replaces it (the old one stops working immediately). **Revoke** deletes it. The Account page also shows when it was created and last used.
- Changing your password does *not* change your key. Regenerate it if you think it leaked.
- A key can only *read* (everything under `/v1` is `GET`), and it never works on the website itself.

Send it in either of these ways:

```bash
# Bearer token (the key already identifies you, no NRP needed)
curl -H "Authorization: Bearer trk_xxxxxxxx" https://tracker.example.com/v1/due

# or HTTP Basic: NRP as the username, the key as the password
curl -u 5054251001:trk_xxxxxxxx https://tracker.example.com/v1/due
```

With Basic, the NRP has to match the key's owner or you get `401`.

An NRP on its own is never enough: NRPs aren't secret, so a request with no key is refused. Your website password isn't accepted by the API at all, so scripts never need it.

- Accounts still on the default password get `403` (they can't open the Account page anyway until they've changed it).
- After **10 wrong keys within 5 minutes** for the same NRP from the same address (30 if no NRP was sent), further attempts get `429` with a `Retry-After` header (seconds).
- Treat the key like a password: keep it in an environment variable or secret store, not in code you commit.

The API never uses website cookies, so being logged in in your browser does not affect it. It does not send CORS headers, so it can't be called from another website's JavaScript.

## What you can see

The same as on the website: assignments from groups you're in, ones you created, and ones someone added you to by NRP. `done` is **your own** tick, not anyone else's.

## Endpoints

| Method & path | Description |
|---|---|
| `GET /v1/` | Short description of the API. No auth. |
| `GET /v1/me` | Who you are and the groups you're in. |
| `GET /v1/due` | Assignments you haven't marked done that are still upcoming, soonest first. |
| `GET /v1/assignments` | Everything you can see. |
| `GET /v1/groups` | Your groups with member and assignment counts. |

Only `GET` is supported; anything else returns `405`.

### `GET /v1/due`

| Query param | Meaning |
|---|---|
| `days=N` | Only deadlines within the next `N` days (`0`–`365`). |
| `overdue=1` | Also include overdue, not-done assignments (from the last 14 days). Off by default. |
| `group=ID` | Only this group (an id from `/v1/groups`). |

### `GET /v1/assignments`

| Query param | Meaning |
|---|---|
| `status` |  One of `todo` (not done, and not more than 14 days overdue), `done` (ticked by you) or `all`. Default `all`. |
| `group=ID` | Only this group. |

### Assignment object

```json
{
  "id": 12,
  "title": "Homework 2",
  "description": "Chapters 3-4",
  "link": "https://example.com/hw2",
  "submit_link": "https://example.com/hw2/submit",
  "deadline": "2026-10-07T23:59:00+07:00",
  "due_in_seconds": 90945,
  "overdue": false,
  "done": false,
  "group": { "id": 1, "name": "RKA" },
  "created_by": { "nrp": "5054251001", "name": "Example Student" },
  "created_at": "2026-10-06T21:30:00"
}
```

| Field | Notes |
|---|---|
| `deadline` | ISO 8601 **with the UTC offset** of the server (Asia/Jakarta, `+07:00`). Parse it as a timezone-aware datetime. |
| `due_in_seconds` | Seconds from now to the deadline; **negative when overdue**. |
| `link` | The assignment's page; `null` when there is none. |
| `submit_link` | Where to hand it in; `null` when there is none. |
| `group` | `null` for assignments shared only by NRP. |
| `created_by.name` | `null` if the name list doesn't have that NRP. |

### List response

`/v1/due` and `/v1/assignments`:

```json
{
  "generated_at": "2026-10-06T23:53:14+07:00",
  "user": { "nrp": "5054251002", "name": "Example Student" },
  "count": 2,
  "assignments": [ { "...": "assignment object" } ]
}
```

### `GET /v1/me`

```json
{
  "nrp": "5054251002",
  "name": "Example Student",
  "admin_of_web": false,
  "groups": [ { "id": 1, "name": "RKA", "admin": false } ]
}
```

### `GET /v1/groups`

```json
{
  "user": { "nrp": "5054251002", "name": "Example Student" },
  "count": 1,
  "groups": [
    { "id": 1, "name": "RKA", "public": false, "admin": false, "members": 50, "assignments": 5 }
  ]
}
```

## Errors

Every error under `/v1` is JSON, never an HTML page:

```json
{ "error": "Invalid API key.", "status": 401 }
```

| Status | When |
|---|---|
| `400` | A query parameter is invalid (`days=abc`, `status=bogus`, …). |
| `401` | Missing or invalid API key. Includes `WWW-Authenticate: Bearer realm="tracker"`. |
| `403` | The account is back on the default password. |
| `404` | Unknown path. |
| `405` | Not a `GET`. |
| `429` | Too many failed logins. See `Retry-After`. |

Responses carry `Cache-Control: no-store`.

## Examples

Put the key in `TRACKER_KEY` first (for example `export TRACKER_KEY=trk_...`).

**curl** — what's due in the next 3 days:

```bash
curl -s -H "Authorization: Bearer $TRACKER_KEY" "https://tracker.example.com/v1/due?days=3"
```

**curl + jq** — one line per assignment:

```bash
curl -s -H "Authorization: Bearer $TRACKER_KEY" https://tracker.example.com/v1/due \
  | jq -r '.assignments[] | "\(.deadline)  [\(.group.name // "direct")]  \(.title)"'
```

**Python**:

```python
import os, requests

r = requests.get(
    "https://tracker.example.com/v1/due",
    params={"days": 7},
    headers={"Authorization": f"Bearer {os.environ['TRACKER_KEY']}"},
    timeout=10,
)
r.raise_for_status()
for a in r.json()["assignments"]:
    hours = a["due_in_seconds"] / 3600
    print(f'{a["title"]} — due in {hours:.0f}h')
```

**JavaScript (Node 18+)**:

```js
const res = await fetch("https://tracker.example.com/v1/due", {
  headers: { Authorization: `Bearer ${process.env.TRACKER_KEY}` },
});
if (!res.ok) throw new Error((await res.json()).error);
console.log((await res.json()).assignments.map(a => a.title));
```

## Stability

`/v1` is stable: fields won't be renamed or removed, new fields may be added (ignore ones you don't know). A breaking change would go under `/v2`.
