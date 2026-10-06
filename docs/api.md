# API v1

A small, **read-only** JSON API for scripts, bots and widgets (reminders, dashboards, a Discord/Telegram bot, a phone shortcut…).

Base URL: `https://<your-host>/v1` (locally `http://127.0.0.1:5000/v1`).

## Authentication

HTTP **Basic** auth: your **NRP** is the username and your **tracker password** is the password.

```bash
curl -u 5054251001:your-password https://tracker.example.com/v1/due
```

An NRP on its own is not a secret (everyone in the class has the list), so it is never enough by itself.

- Accounts still on the default password are refused with `403` — log in on the website and change it first.
- After **10 wrong passwords within 5 minutes** for the same NRP from the same address, further attempts get `429` with a `Retry-After` header (seconds), even if the password is then correct.
- Treat the password like any other secret: keep it in an environment variable or secret store, not in code you commit.

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
| `status=todo\|done\|all` | `todo` = not done and not older than 14 days overdue; `done` = ticked by you; default `all`. |
| `group=ID` | Only this group. |

### Assignment object

```json
{
  "id": 12,
  "title": "Homework 2",
  "description": "Chapters 3-4",
  "link": "https://example.com/hw2",
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
| `link` | `null` when there is none. |
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
{ "error": "Wrong NRP or password.", "status": 401 }
```

| Status | When |
|---|---|
| `400` | A query parameter is invalid (`days=abc`, `status=bogus`, …). |
| `401` | Missing or wrong credentials. Includes `WWW-Authenticate: Basic realm="tracker"`. |
| `403` | The account is still on the default password. |
| `404` | Unknown path. |
| `405` | Not a `GET`. |
| `429` | Too many failed logins. See `Retry-After`. |

Responses carry `Cache-Control: no-store`.

## Examples

**curl** — what's due in the next 3 days:

```bash
curl -s -u "$TRACKER_NRP:$TRACKER_PASSWORD" "https://tracker.example.com/v1/due?days=3"
```

**curl + jq** — one line per assignment:

```bash
curl -s -u "$TRACKER_NRP:$TRACKER_PASSWORD" https://tracker.example.com/v1/due \
  | jq -r '.assignments[] | "\(.deadline)  [\(.group.name // "direct")]  \(.title)"'
```

**Python**:

```python
import os, requests

r = requests.get(
    "https://tracker.example.com/v1/due",
    params={"days": 7},
    auth=(os.environ["TRACKER_NRP"], os.environ["TRACKER_PASSWORD"]),
    timeout=10,
)
r.raise_for_status()
for a in r.json()["assignments"]:
    hours = a["due_in_seconds"] / 3600
    print(f'{a["title"]} — due in {hours:.0f}h')
```

**JavaScript (Node 18+)**:

```js
const auth = Buffer.from(`${process.env.TRACKER_NRP}:${process.env.TRACKER_PASSWORD}`).toString("base64");
const res = await fetch("https://tracker.example.com/v1/due", { headers: { Authorization: `Basic ${auth}` } });
if (!res.ok) throw new Error((await res.json()).error);
console.log((await res.json()).assignments.map(a => a.title));
```

## Stability

`/v1` is stable: fields won't be renamed or removed, new fields may be added (ignore ones you don't know). A breaking change would go under `/v2`.
