import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta
from functools import wraps

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (Flask, abort, flash, g, jsonify, make_response, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DATA_DIR, "tracker.db")
os.makedirs(DATA_DIR, exist_ok=True)

NRP_PREFIX = "5054251"
NRP_COUNT = 51  # 5054251001 .. 5054251051
DEFAULT_PASSWORD = "123456"
DEFAULT_GROUP = "RKA"
REMOVED_USERS = {"5054251034"}  # purged from the DB entirely (see init_db), never re-seeded
STUDENTS_FILE = os.path.join(os.path.dirname(__file__), "students.tsv")


def load_students():
    """nrp -> name from students.tsv (tab separated). Missing file just means no names."""
    out = {}
    try:
        with open(STUDENTS_FILE, encoding="utf-8") as f:
            for line in f:
                nrp, _, name = line.rstrip("\n").partition("\t")
                if nrp.strip() and name.strip():
                    out[nrp.strip()] = name.strip()
    except FileNotFoundError:
        pass
    return out
ADMINS = set(os.environ.get("ADMINS", f"{NRP_PREFIX}028").split(","))

app = Flask(__name__)
app.json.sort_keys = False  # keep API fields in the order we write them
if os.environ.get("TRUST_PROXY") == "1":  # behind cloudflared / a reverse proxy
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)


def _secret_key():
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    path = os.path.join(DATA_DIR, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    with open(path) as f:
        return f.read().strip()


app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)


# ---------- db ----------

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            nrp TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            must_change INTEGER NOT NULL DEFAULT 1,
            name TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            deadline TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(nrp),
            created_at TEXT NOT NULL,
            group_id INTEGER REFERENCES class_groups(id)
        );
        CREATE TABLE IF NOT EXISTS class_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            created_by TEXT REFERENCES users(nrp),
            is_default INTEGER NOT NULL DEFAULT 0,
            is_public INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER NOT NULL REFERENCES class_groups(id) ON DELETE CASCADE,
            nrp TEXT NOT NULL REFERENCES users(nrp),
            is_admin INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, nrp)
        );
        CREATE TABLE IF NOT EXISTS assignment_recipients (
            assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
            nrp TEXT NOT NULL REFERENCES users(nrp),
            PRIMARY KEY (assignment_id, nrp)
        );
        CREATE TABLE IF NOT EXISTS completions (
            nrp TEXT NOT NULL REFERENCES users(nrp),
            assignment_id INTEGER NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
            PRIMARY KEY (nrp, assignment_id)
        );
        """
    )
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        default_hash = generate_password_hash(DEFAULT_PASSWORD)
        conn.executemany(
            "INSERT INTO users (nrp, password_hash, must_change) VALUES (?, ?, 1)",
            [(f"{NRP_PREFIX}{i:03d}", default_hash) for i in range(1, NRP_COUNT + 1)
             if f"{NRP_PREFIX}{i:03d}" not in REMOVED_USERS],
        )
    # names (students.tsv is the source of truth)
    if "name" not in [r[1] for r in conn.execute("PRAGMA table_info(users)")]:
        conn.execute("ALTER TABLE users ADD COLUMN name TEXT")
    conn.executemany("UPDATE users SET name = ? WHERE nrp = ?",
                     [(name, nrp) for nrp, name in load_students().items()])
    # migrate DBs created before groups existed
    migrated = "group_id" not in [r[1] for r in conn.execute("PRAGMA table_info(assignments)")]
    if migrated:
        conn.execute("ALTER TABLE assignments ADD COLUMN group_id INTEGER REFERENCES class_groups(id)")
    rka = conn.execute("SELECT id FROM class_groups WHERE is_default = 1").fetchone()
    if rka is None:  # everyone starts in RKA
        cur = conn.execute(
            "INSERT INTO class_groups (name, created_by, is_default, created_at) VALUES (?, NULL, 1, ?)",
            (DEFAULT_GROUP, datetime.now().isoformat(timespec="seconds")),
        )
        rka = (cur.lastrowid,)
        conn.execute("INSERT INTO group_members (group_id, nrp) SELECT ?, nrp FROM users", (rka[0],))
    # purge removed accounts: their memberships, picks, completions and own assignments
    for nrp in REMOVED_USERS:
        conn.execute("DELETE FROM completions WHERE nrp = ? OR assignment_id IN "
                     "(SELECT id FROM assignments WHERE created_by = ?)", (nrp, nrp))
        conn.execute("DELETE FROM assignment_recipients WHERE nrp = ? OR assignment_id IN "
                     "(SELECT id FROM assignments WHERE created_by = ?)", (nrp, nrp))
        conn.execute("DELETE FROM assignments WHERE created_by = ?", (nrp,))
        conn.execute("DELETE FROM group_members WHERE nrp = ?", (nrp,))
        conn.execute("UPDATE class_groups SET created_by = NULL WHERE created_by = ?", (nrp,))
        conn.execute("DELETE FROM users WHERE nrp = ?", (nrp,))
    if migrated:
        conn.execute("UPDATE assignments SET group_id = ? WHERE group_id IS NULL", (rka[0],))
    if "is_public" not in [r[1] for r in conn.execute("PRAGMA table_info(class_groups)")]:
        conn.execute("ALTER TABLE class_groups ADD COLUMN is_public INTEGER NOT NULL DEFAULT 0")
    # courses are now just groups: drop the old free-text column
    if "course" in [r[1] for r in conn.execute("PRAGMA table_info(assignments)")]:
        conn.execute("ALTER TABLE assignments DROP COLUMN course")
    # group admins: creators are admins by default; web ADMINS run RKA
    if "is_admin" not in [r[1] for r in conn.execute("PRAGMA table_info(group_members)")]:
        conn.execute("ALTER TABLE group_members ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0")
        conn.execute("""UPDATE group_members SET is_admin = 1 WHERE nrp =
                        (SELECT created_by FROM class_groups WHERE id = group_members.group_id)""")
    conn.executemany("UPDATE group_members SET is_admin = 1 WHERE group_id = ? AND nrp = ?",
                     [(rka[0], n) for n in ADMINS])
    conn.commit()
    conn.close()


init_db()


# ---------- auth / csrf ----------

def current_user():
    return session.get("nrp")


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapped


@app.before_request
def guards():
    if request.endpoint == "static" or request.path.startswith("/v1"):
        return  # static files, and the cookie-less JSON API (it has its own Basic auth)
    # CSRF
    if request.method == "POST":
        token = session.get("csrf")
        if not token or not secrets.compare_digest(token, request.form.get("csrf", "")):
            abort(400, "Bad CSRF token")
    # force password change on first login
    nrp = current_user()
    if nrp and request.endpoint not in ("change_password", "logout"):
        row = db().execute("SELECT must_change FROM users WHERE nrp = ?", (nrp,)).fetchone()
        if row is None:
            session.clear()
            return redirect(url_for("login"))
        if row["must_change"]:
            return redirect(url_for("change_password"))


def names():
    if "names" not in g:
        g.names = {r["nrp"]: r["name"] for r in db().execute("SELECT nrp, name FROM users")}
    return g.names


@app.template_filter("who")
def who(nrp):
    """Display name for an NRP, falling back to the NRP itself."""
    return names().get(nrp) or nrp


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "me": current_user()}


@app.url_defaults
def bust_static_cache(endpoint, values):
    """/static/x.css?v=<mtime>: a changed file gets a new URL, so Cloudflare/browser caches can't serve stale CSS/JS."""
    if endpoint == "static" and "filename" in values:
        try:
            values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
        except OSError:
            pass


@app.after_request
def headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    return resp


# ---------- helpers ----------

def parse_deadline(value):
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except (ValueError, TypeError):
        return None


def clean_link(value):
    value = (value or "").strip()
    if value and not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    return value[:500]


def humanize(delta_seconds):
    s = abs(int(delta_seconds))
    if s < 3600:
        return f"{max(s // 60, 1)}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def parse_time(value):
    """Accepts 23:59, 9:30, 09.30, 2359; empty means end of day."""
    value = (value or "").strip()
    if not value:
        return 23, 59
    m = re.fullmatch(r"(\d{1,2})(?:[:.]?(\d{2}))?", value)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return (h, mi) if h < 24 and mi < 60 else None


VISIBLE_SQL = """(a.created_by = :me
    OR a.group_id IN (SELECT group_id FROM group_members WHERE nrp = :me)
    OR a.id IN (SELECT assignment_id FROM assignment_recipients WHERE nrp = :me))"""


def my_groups():
    return db().execute(
        """SELECT g.* FROM class_groups g
           JOIN group_members m ON m.group_id = g.id AND m.nrp = ?
           ORDER BY g.is_default DESC, g.name COLLATE NOCASE""",
        (current_user(),),
    ).fetchall()


def default_group_id():
    return db().execute("SELECT id FROM class_groups WHERE is_default = 1").fetchone()["id"]


def can_view(aid):
    return db().execute(
        f"SELECT 1 FROM assignments a WHERE a.id = :id AND {VISIBLE_SQL}",
        {"id": aid, "me": current_user()},
    ).fetchone() is not None


def multi(name):
    """All values of a repeated form field (picker checkboxes), plus any typed text."""
    return ",".join(request.form.getlist(name))


def people(exclude=()):
    """Everyone who can be picked, sorted by name."""
    rows = db().execute(
        "SELECT nrp, name FROM users ORDER BY COALESCE(name, nrp) COLLATE NOCASE").fetchall()
    return [r for r in rows if r["nrp"] not in set(exclude)]


def parse_nrps(text):
    """Split on commas/spaces/newlines. '28' or '028' expands to the class prefix.
    Returns (valid nrps, unknown tokens)."""
    tokens = []
    for t in re.split(r"[\s,;]+", (text or "").strip()):
        if re.fullmatch(r"\d{1,3}", t):
            t = f"{NRP_PREFIX}{int(t):03d}"
        if t and t not in tokens:
            tokens.append(t)
    known = {r["nrp"] for r in db().execute(
        f"SELECT nrp FROM users WHERE nrp IN ({','.join('?' * len(tokens))})", tokens)} if tokens else set()
    return [t for t in tokens if t in known], [t for t in tokens if t not in known]


def form_fields(allowed_groups):
    title = request.form.get("title", "").strip()[:150]
    description = request.form.get("description", "").strip()[:2000]
    link = clean_link(request.form.get("link"))
    date = request.form.get("date", "").strip()
    time = request.form.get("time", "").strip()
    errors = []
    raw_group = request.form.get("group", "")
    group_id = int(raw_group) if raw_group.isdigit() else None
    if group_id is not None and group_id not in allowed_groups:
        errors.append("Pick one of your groups.")
        group_id = None
    nrps_text = multi("nrps")
    recipients, unknown = parse_nrps(nrps_text)
    if unknown:
        errors.append("Unknown NRP: " + ", ".join(unknown))
    if group_id is None and not recipients and not errors:
        errors.append("Pick a group or add at least one NRP.")
    if not title:
        errors.append("Title is required.")
    deadline = ""
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        hm = parse_time(time)
        if hm is None:
            errors.append("Time should look like 23:59.")
        else:
            deadline = d.replace(hour=hm[0], minute=hm[1]).strftime("%Y-%m-%dT%H:%M")
    except ValueError:
        errors.append("Pick a deadline date.")
    return dict(title=title, description=description,
                link=link, deadline=deadline, date=date, time=time,
                group_id=group_id, nrps=nrps_text, recipients=recipients), errors


# ---------- routes ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        nrp = request.form.get("nrp", "").strip()
        password = request.form.get("password", "")
        row = db().execute("SELECT * FROM users WHERE nrp = ?", (nrp,)).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            csrf = session.get("csrf")
            session.clear()
            session["csrf"] = csrf or secrets.token_hex(16)
            session["nrp"] = nrp
            session.permanent = True
            return redirect(url_for("index"))
        flash("Wrong NRP or password.")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    user = db().execute("SELECT * FROM users WHERE nrp = ?", (current_user(),)).fetchone()
    forced = bool(user["must_change"])
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        confirm = request.form.get("confirm", "")
        if not check_password_hash(user["password_hash"], current):
            flash("Current password is wrong.")
        elif len(new) < 8:
            flash("New password must be at least 8 characters.")
        elif new == DEFAULT_PASSWORD or new == current:
            flash("Pick a password different from the old one.")
        elif new != confirm:
            flash("Passwords don't match.")
        else:
            db().execute(
                "UPDATE users SET password_hash = ?, must_change = 0 WHERE nrp = ?",
                (generate_password_hash(new), current_user()),
            )
            db().commit()
            flash("Password updated.")
            return redirect(url_for("index"))
    return render_template("change_password.html", forced=forced)


MEMBERS_PER_PAGE = 10
OVERDUE_GRACE_DAYS = 14  # undone items older than this only show under "All"
BUCKETS = [("past", "Overdue"), ("today", "Today"), ("tomorrow", "Tomorrow"),
           ("week", "This week"), ("later", "Later")]


def bucket_of(dl, now):
    if dl < now:
        return "past"
    days = (dl.date() - now.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "week" if days <= 7 else "later"


def visible_assignments(nrp, gid=None):
    """Every assignment `nrp` can see, as dicts with deadline/done/overdue/bucket/tone filled in.
    Shared by the web UI and the JSON API."""
    admin_gids = {r["group_id"] for r in db().execute(
        "SELECT group_id FROM group_members WHERE nrp = ? AND is_admin = 1", (nrp,))}
    rows = db().execute(
        f"""
        SELECT a.*, gr.name AS group_name, (c.nrp IS NOT NULL) AS done
        FROM assignments a
        LEFT JOIN class_groups gr ON gr.id = a.group_id
        LEFT JOIN completions c ON c.assignment_id = a.id AND c.nrp = :me
        WHERE {VISIBLE_SQL}
        ORDER BY a.deadline ASC
        """,
        {"me": nrp},
    ).fetchall()
    if gid:
        rows = [r for r in rows if r["group_id"] == gid]
    now = datetime.now()
    items = []
    for r in rows:
        dl = parse_deadline(r["deadline"])
        delta = (dl - now).total_seconds()
        item = dict(r)
        item["dl"] = dl
        item["delta"] = delta
        item["can_delete"] = item["created_by"] == nrp or item["group_id"] in admin_gids
        item["done"] = bool(item["done"])
        item["overdue"] = delta < 0
        item["rel"] = humanize(delta) + " overdue" if delta < 0 else "in " + humanize(delta)
        item["bucket"] = bucket_of(dl, now)
        if item["done"]:
            item["tone"] = "done"
        elif delta < 0:
            item["tone"] = "bad"
        elif delta < 86400 * 2:
            item["tone"] = "warn"
        else:
            item["tone"] = ""
        item["todo"] = not item["done"] and (delta > -86400 * OVERDUE_GRACE_DAYS)
        items.append(item)
    return items


@app.route("/")
@login_required
def index():
    view = request.args.get("view", "todo")
    if view not in ("todo", "done", "all"):
        view = "todo"
    mygroups = my_groups()
    gid = request.args.get("group", type=int)
    if gid not in {g["id"] for g in mygroups}:
        gid = None
    items = visible_assignments(current_user(), gid)

    stats = {
        "overdue": sum(1 for i in items if i["todo"] and i["overdue"]),
        "week": sum(1 for i in items if i["todo"] and i["bucket"] in ("today", "tomorrow", "week")),
        "done": sum(1 for i in items if i["done"]),
    }
    counts = {
        "todo": sum(1 for i in items if i["todo"]),
        "done": stats["done"],
        "all": len(items),
    }

    if view == "done":
        items = [i for i in items if i["done"]]
    elif view == "todo":
        items = [i for i in items if i["todo"]]

    groups = []
    for key, label in BUCKETS:
        rows_ = [i for i in items if i["bucket"] == key]
        if key == "past":
            rows_.reverse()  # most recently missed first
            if view != "todo":
                label = "Past"
        if rows_:
            groups.append({"label": label, "rows": rows_})
    return render_template("index.html", groups=groups, view=view,
                           stats=stats, counts=counts, total=len(items),
                           mygroups=mygroups, group=gid or 0)


def save_recipients(aid, recipients):
    db().execute("DELETE FROM assignment_recipients WHERE assignment_id = ?", (aid,))
    db().executemany(
        "INSERT OR IGNORE INTO assignment_recipients (assignment_id, nrp) VALUES (?, ?)",
        [(aid, n) for n in recipients if n != current_user()],
    )


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    groups = my_groups()
    if request.method == "POST":
        data, errors = form_fields({g["id"] for g in groups})
        if errors:
            for e in errors:
                flash(e)
            return render_template("form.html", a=data, editing=False, groups=groups,
                                   people=people({current_user()}))
        cur = db().execute(
            """INSERT INTO assignments (title, description, link, deadline, created_by, created_at, group_id)
               VALUES (:title, :description, :link, :deadline, :by, :at, :group_id)""",
            {**data, "by": current_user(), "at": datetime.now().isoformat(timespec="seconds")},
        )
        save_recipients(cur.lastrowid, data["recipients"])
        db().commit()
        return redirect(url_for("index", group=data["group_id"] or None))
    wanted = request.args.get("group", type=int)
    gid = wanted if wanted in {g["id"] for g in groups} else default_group_id()
    return render_template("form.html", a={"group_id": gid}, editing=False, groups=groups,
                           people=people({current_user()}))


def get_owned(aid):
    row = db().execute("SELECT * FROM assignments WHERE id = ?", (aid,)).fetchone()
    if row is None:
        abort(404)
    if row["created_by"] != current_user():
        abort(403)
    return row


@app.route("/edit/<int:aid>", methods=["GET", "POST"])
@login_required
def edit(aid):
    row = get_owned(aid)
    groups = my_groups()
    allowed = {g["id"] for g in groups} | {row["group_id"]}
    if request.method == "POST":
        data, errors = form_fields(allowed)
        if errors:
            for e in errors:
                flash(e)
            return render_template("form.html", a={**data, "id": aid}, editing=True, groups=groups,
                                   people=people({current_user()}))
        db().execute(
            """UPDATE assignments SET title=:title, description=:description,
               link=:link, deadline=:deadline, group_id=:group_id WHERE id=:id""",
            {**data, "id": aid},
        )
        save_recipients(aid, data["recipients"])
        db().commit()
        return redirect(url_for("index"))
    recipients = [r["nrp"] for r in db().execute(
        "SELECT nrp FROM assignment_recipients WHERE assignment_id = ?", (aid,))]
    return render_template("form.html", a={**dict(row), "recipients": recipients}, editing=True,
                           groups=groups, people=people({current_user()}))


@app.route("/delete/<int:aid>", methods=["POST"])
@login_required
def delete(aid):
    row = db().execute("SELECT * FROM assignments WHERE id = ?", (aid,)).fetchone()
    if row is None:
        abort(404)
    # the creator, or an admin of the assignment's group
    if row["created_by"] != current_user() and not is_group_admin(row["group_id"]):
        abort(403)
    db().execute("DELETE FROM assignments WHERE id = ?", (aid,))
    db().commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/toggle/<int:aid>", methods=["POST"])
@login_required
def toggle(aid):
    if not can_view(aid):
        abort(404)
    cur = db().execute(
        "DELETE FROM completions WHERE nrp = ? AND assignment_id = ?", (current_user(), aid)
    )
    if cur.rowcount == 0:
        db().execute(
            "INSERT INTO completions (nrp, assignment_id) VALUES (?, ?)", (current_user(), aid)
        )
    db().commit()
    return redirect(request.referrer or url_for("index"))


# ---------- groups ----------

def is_group_admin(gid, nrp=None):
    if gid is None:
        return False
    return db().execute(
        "SELECT 1 FROM group_members WHERE group_id = ? AND nrp = ? AND is_admin = 1",
        (gid, nrp or current_user()),
    ).fetchone() is not None


def admin_count(gid):
    return db().execute(
        "SELECT COUNT(*) FROM group_members WHERE group_id = ? AND is_admin = 1", (gid,)
    ).fetchone()[0]


def get_group(gid):
    """Group row if the user is a member."""
    row = db().execute("SELECT * FROM class_groups WHERE id = ?", (gid,)).fetchone()
    if row is None:
        abort(404)
    member = db().execute(
        "SELECT 1 FROM group_members WHERE group_id = ? AND nrp = ?", (gid, current_user())
    ).fetchone()
    if not member:
        abort(404)
    return row


def can_manage(group):
    """Group admins only."""
    return is_group_admin(group["id"])


def add_members(gid, text):
    valid, unknown = parse_nrps(text)
    db().executemany(
        "INSERT OR IGNORE INTO group_members (group_id, nrp) VALUES (?, ?)",
        [(gid, n) for n in valid],
    )
    if unknown:
        flash("Skipped unknown NRP: " + ", ".join(unknown))
    return valid


@app.route("/groups")
@login_required
def groups():
    cut = (datetime.now() - timedelta(days=OVERDUE_GRACE_DAYS)).strftime("%Y-%m-%dT%H:%M")
    rows = db().execute(
        """
        SELECT g.*, m.is_admin AS mine_admin,
          (SELECT COUNT(*) FROM group_members WHERE group_id = g.id) AS members,
          (SELECT COUNT(*) FROM assignments WHERE group_id = g.id) AS total,
          (SELECT COUNT(*) FROM assignments a WHERE a.group_id = g.id AND a.deadline >= :cut
             AND a.id NOT IN (SELECT assignment_id FROM completions WHERE nrp = :me)) AS todo
        FROM class_groups g
        JOIN group_members m ON m.group_id = g.id AND m.nrp = :me
        ORDER BY g.is_default DESC, g.name COLLATE NOCASE
        """,
        {"me": current_user(), "cut": cut},
    ).fetchall()
    public = db().execute(
        """
        SELECT g.*,
          (SELECT COUNT(*) FROM group_members WHERE group_id = g.id) AS members,
          (SELECT COUNT(*) FROM assignments WHERE group_id = g.id) AS total
        FROM class_groups g
        WHERE g.is_public = 1
          AND g.id NOT IN (SELECT group_id FROM group_members WHERE nrp = ?)
        ORDER BY g.name COLLATE NOCASE
        """,
        (current_user(),),
    ).fetchall()
    return render_template("groups.html", rows=rows, public=public)


@app.route("/groups/new")
@login_required
def group_new():
    return render_template("group_new.html", people=people({current_user()}))


@app.route("/groups/create", methods=["POST"])
@login_required
def group_create():
    name = request.form.get("name", "").strip()[:40]
    if not name:
        flash("Give the group a name.")
        return redirect(url_for("group_new"))
    cur = db().execute(
        "INSERT INTO class_groups (name, created_by, is_default, is_public, created_at) VALUES (?, ?, 0, ?, ?)",
        (name, current_user(), int(request.form.get("visibility") == "public"),
         datetime.now().isoformat(timespec="seconds")),
    )
    gid = cur.lastrowid
    db().execute("INSERT INTO group_members (group_id, nrp, is_admin) VALUES (?, ?, 1)",
                 (gid, current_user()))
    add_members(gid, multi("members"))
    db().commit()
    return redirect(url_for("group_view", gid=gid))


@app.route("/groups/<int:gid>")
@login_required
def group_view(gid):
    group = get_group(gid)
    all_nrps = {r["nrp"] for r in db().execute("SELECT nrp FROM group_members WHERE group_id = ?", (gid,))}
    member_count = len(all_nrps)

    # filter: every word must appear in "name nrp" (case-insensitive); LIKE wildcards are escaped
    q = request.args.get("q", "").strip()[:60]
    where, params = "m.group_id = ?", [gid]
    for term in q.lower().split():
        where += " AND LOWER(COALESCE(u.name, '') || ' ' || m.nrp) LIKE ? ESCAPE '\\'"
        params.append("%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    shown_count = db().execute(
        f"SELECT COUNT(*) FROM group_members m JOIN users u ON u.nrp = m.nrp WHERE {where}", params
    ).fetchone()[0]

    pages = max(1, -(-shown_count // MEMBERS_PER_PAGE))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    members = db().execute(
        f"""SELECT m.nrp, m.is_admin FROM group_members m JOIN users u ON u.nrp = m.nrp
            WHERE {where} ORDER BY m.is_admin DESC, COALESCE(u.name, u.nrp) COLLATE NOCASE
            LIMIT ? OFFSET ?""",
        params + [MEMBERS_PER_PAGE, (page - 1) * MEMBERS_PER_PAGE]).fetchall()
    total = db().execute(
        "SELECT COUNT(*) FROM assignments WHERE group_id = ?", (gid,)).fetchone()[0]
    ctx = dict(group=group, members=members, total=total,
               manage=can_manage(group), is_member=current_user() in all_nrps,
               people=people(all_nrps), member_count=member_count,
               shown_count=shown_count, q=q,
               page=page, pages=pages, first=(page - 1) * MEMBERS_PER_PAGE + 1)
    # live filter/pager (members.js) asks for just the list; everyone else gets the page
    live = request.headers.get("X-Requested-With") == "fetch"
    resp = make_response(render_template("_member_list.html" if live else "group.html", **ctx))
    resp.headers["Vary"] = "X-Requested-With"
    return resp


def members_url(gid):
    """Back to the member list, keeping the current page and filter."""
    return url_for("group_view", gid=gid, page=request.form.get("page", type=int),
                   q=request.form.get("q") or None, _anchor="members")


@app.route("/groups/<int:gid>/join", methods=["POST"])
@login_required
def group_join(gid):
    group = db().execute("SELECT * FROM class_groups WHERE id = ?", (gid,)).fetchone()
    if group is None or not group["is_public"]:
        abort(404)  # private groups look like they don't exist
    db().execute("INSERT OR IGNORE INTO group_members (group_id, nrp, is_admin) VALUES (?, ?, 0)",
                 (gid, current_user()))
    db().commit()
    return redirect(url_for("group_view", gid=gid))


@app.route("/groups/<int:gid>/visibility", methods=["POST"])
@login_required
def group_visibility(gid):
    group = get_group(gid)
    if group["is_default"] or not can_manage(group):
        abort(403)
    db().execute("UPDATE class_groups SET is_public = ? WHERE id = ?",
                 (int(request.form.get("visibility") == "public"), gid))
    db().commit()
    return redirect(url_for("group_view", gid=gid))


@app.route("/groups/<int:gid>/members", methods=["POST"])
@login_required
def group_add_members(gid):
    group = get_group(gid)
    if group["is_default"] and not can_manage(group):  # RKA: admins only
        abort(403)
    add_members(gid, multi("members"))
    db().commit()
    return redirect(url_for("group_view", gid=gid))


@app.route("/groups/<int:gid>/remove", methods=["POST"])
@login_required
def group_remove(gid):
    group = get_group(gid)
    nrp = request.form.get("nrp", "")
    leaving = nrp == current_user()
    if leaving:
        if group["is_default"]:
            abort(403)
    elif not can_manage(group):
        abort(403)
    if is_group_admin(gid, nrp) and admin_count(gid) <= 1:
        flash("This is the only admin. Make someone else admin first.")
        return redirect(members_url(gid))
    db().execute("DELETE FROM group_members WHERE group_id = ? AND nrp = ?", (gid, nrp))
    db().commit()
    if leaving:
        return redirect(url_for("groups"))
    return redirect(members_url(gid))


@app.route("/groups/<int:gid>/role", methods=["POST"])
@login_required
def group_role(gid):
    group = get_group(gid)
    if not can_manage(group):
        abort(403)
    nrp = request.form.get("nrp", "")
    make_admin = request.form.get("admin") == "1"
    if not db().execute("SELECT 1 FROM group_members WHERE group_id = ? AND nrp = ?", (gid, nrp)).fetchone():
        abort(404)
    if not make_admin and is_group_admin(gid, nrp) and admin_count(gid) <= 1:
        flash("A group needs at least one admin.")
        return redirect(members_url(gid))
    db().execute("UPDATE group_members SET is_admin = ? WHERE group_id = ? AND nrp = ?",
                 (int(make_admin), gid, nrp))
    db().commit()
    return redirect(members_url(gid))


@app.route("/groups/<int:gid>/delete", methods=["POST"])
@login_required
def group_delete(gid):
    group = get_group(gid)
    if group["is_default"] or not can_manage(group):
        abort(403)
    db().execute("DELETE FROM assignments WHERE group_id = ?", (gid,))
    db().execute("DELETE FROM class_groups WHERE id = ?", (gid,))
    db().commit()
    return redirect(url_for("groups"))


# ---------- JSON API v1 ----------
# Read-only. Auth is HTTP Basic: NRP as the username, your tracker password as the password.
# (An NRP alone isn't a secret, so it can't be the only credential.)

API_FAIL_LIMIT = 10      # wrong passwords allowed per (ip, nrp)...
API_FAIL_WINDOW = 300    # ...within this many seconds
_api_fails = {}
_DUMMY_HASH = generate_password_hash("not-a-real-password")  # equalises timing for unknown NRPs


def api_error(status, message, **headers):
    resp = jsonify(error=message, status=status)
    resp.status_code = status
    for k, v in headers.items():
        resp.headers[k.replace("_", "-")] = str(v)
    return resp


def api_auth(view):
    @wraps(view)
    def wrapped(*a, **kw):
        auth = request.authorization
        if auth is None or auth.type != "basic" or not auth.username:
            return api_error(401, "Use HTTP Basic auth: your NRP as username and your tracker password.",
                             WWW_Authenticate='Basic realm="tracker"')
        nrp, now = auth.username.strip(), time.time()
        key = (request.remote_addr, nrp)
        recent = [t for t in _api_fails.get(key, []) if now - t < API_FAIL_WINDOW]
        if len(recent) >= API_FAIL_LIMIT:
            return api_error(429, "Too many failed attempts. Try again later.",
                             Retry_After=int(API_FAIL_WINDOW - (now - recent[0])) + 1)
        user = db().execute("SELECT * FROM users WHERE nrp = ?", (nrp,)).fetchone()
        ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, auth.password or "")
        if not (user and ok):
            _api_fails[key] = recent + [now]
            if len(_api_fails) > 2000:  # don't grow forever
                for k in [k for k, v in _api_fails.items() if now - v[-1] > API_FAIL_WINDOW]:
                    del _api_fails[k]
            return api_error(401, "Wrong NRP or password.", WWW_Authenticate='Basic realm="tracker"')
        _api_fails.pop(key, None)
        if user["must_change"]:
            return api_error(403, "Log in on the website and change your default password first.")
        g.api_nrp = nrp
        return view(*a, **kw)
    return wrapped


@app.after_request
def api_headers(resp):
    if request.path.startswith("/v1"):
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Vary"] = "Authorization"
    return resp


@app.errorhandler(404)
@app.errorhandler(405)
def api_http_errors(e):
    if request.path.startswith("/v1"):
        return api_error(e.code, "Not found." if e.code == 404 else "Method not allowed. The API is read-only (GET).")
    return e


def api_person(nrp):
    return {"nrp": nrp, "name": names().get(nrp)}


def api_assignment(i):
    return {
        "id": i["id"],
        "title": i["title"],
        "description": i["description"],
        "link": i["link"] or None,
        "deadline": i["dl"].astimezone().isoformat(timespec="seconds"),  # local server time, with UTC offset
        "due_in_seconds": int(i["delta"]),                                # negative = overdue
        "overdue": i["overdue"],
        "done": i["done"],
        "group": {"id": i["group_id"], "name": i["group_name"]} if i["group_id"] else None,
        "created_by": api_person(i["created_by"]),
        "created_at": i["created_at"],
    }


def api_int(name, lo, hi):
    """Optional integer query param; returns (value, error_response)."""
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None, None
    try:
        val = int(raw)
    except ValueError:
        return None, api_error(400, f"'{name}' must be an integer.")
    if not lo <= val <= hi:
        return None, api_error(400, f"'{name}' must be between {lo} and {hi}.")
    return val, None


def api_list(items):
    return jsonify(generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                   user=api_person(g.api_nrp), count=len(items),
                   assignments=[api_assignment(i) for i in items])


@app.route("/v1/")
@app.route("/v1")
def api_index():
    return jsonify(
        version=1,
        auth="HTTP Basic: NRP as username, your tracker password as password",
        endpoints={
            "GET /v1/me": "who you are and the groups you're in",
            "GET /v1/due": "not-done assignments that are still upcoming. "
                           "Params: days=N (only the next N days), overdue=1 (also include overdue), group=ID",
            "GET /v1/assignments": "everything you can see. Params: status=todo|done|all (default all), group=ID",
            "GET /v1/groups": "your groups with member and assignment counts",
        },
    )


@app.route("/v1/me")
@api_auth
def api_me():
    rows = db().execute(
        """SELECT g.id, g.name, g.is_public, g.is_default, m.is_admin FROM class_groups g
           JOIN group_members m ON m.group_id = g.id AND m.nrp = ?
           ORDER BY g.is_default DESC, g.name COLLATE NOCASE""", (g.api_nrp,)).fetchall()
    return jsonify(**api_person(g.api_nrp), admin_of_web=g.api_nrp in ADMINS,
                   groups=[{"id": r["id"], "name": r["name"], "admin": bool(r["is_admin"])} for r in rows])


@app.route("/v1/due")
@api_auth
def api_due():
    days, err = api_int("days", 0, 365)
    if err:
        return err
    gid, err = api_int("group", 1, 2**31)
    if err:
        return err
    include_overdue = request.args.get("overdue") in ("1", "true", "yes")
    items = [i for i in visible_assignments(g.api_nrp, gid)
             if i["todo"] and (include_overdue or not i["overdue"])
             and (days is None or i["overdue"] or i["delta"] <= days * 86400)]
    return api_list(items)


@app.route("/v1/assignments")
@api_auth
def api_assignments():
    status = request.args.get("status", "all")
    if status not in ("todo", "done", "all"):
        return api_error(400, "'status' must be todo, done or all.")
    gid, err = api_int("group", 1, 2**31)
    if err:
        return err
    items = visible_assignments(g.api_nrp, gid)
    if status == "done":
        items = [i for i in items if i["done"]]
    elif status == "todo":
        items = [i for i in items if i["todo"]]
    return api_list(items)


@app.route("/v1/groups")
@api_auth
def api_groups():
    rows = db().execute(
        """SELECT g.id, g.name, g.is_public, g.is_default, m.is_admin,
             (SELECT COUNT(*) FROM group_members WHERE group_id = g.id) AS members,
             (SELECT COUNT(*) FROM assignments WHERE group_id = g.id) AS assignments
           FROM class_groups g JOIN group_members m ON m.group_id = g.id AND m.nrp = ?
           ORDER BY g.is_default DESC, g.name COLLATE NOCASE""", (g.api_nrp,)).fetchall()
    return jsonify(user=api_person(g.api_nrp), count=len(rows), groups=[
        {"id": r["id"], "name": r["name"], "public": bool(r["is_public"]), "admin": bool(r["is_admin"]),
         "members": r["members"], "assignments": r["assignments"]} for r in rows])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
