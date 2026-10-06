import os
import re
import secrets
import sqlite3
from datetime import datetime
from functools import wraps

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DATA_DIR, "tracker.db")
os.makedirs(DATA_DIR, exist_ok=True)

NRP_PREFIX = "5054251"
NRP_COUNT = 51  # 5054251001 .. 5054251051
DEFAULT_PASSWORD = "123456"
ADMINS = set(os.environ.get("ADMINS", f"{NRP_PREFIX}028").split(","))

app = Flask(__name__)
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
            must_change INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            link TEXT NOT NULL DEFAULT '',
            deadline TEXT NOT NULL,
            created_by TEXT NOT NULL REFERENCES users(nrp),
            created_at TEXT NOT NULL
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
            [(f"{NRP_PREFIX}{i:03d}", default_hash) for i in range(1, NRP_COUNT + 1)],
        )
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
    if request.endpoint == "static":
        return
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


@app.context_processor
def inject():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf": session["csrf"], "me": current_user(), "is_admin": current_user() in ADMINS}


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


def form_fields():
    course = request.form.get("course", "").strip()[:80]
    title = request.form.get("title", "").strip()[:150]
    description = request.form.get("description", "").strip()[:2000]
    link = clean_link(request.form.get("link"))
    date = request.form.get("date", "").strip()
    time = request.form.get("time", "").strip()
    errors = []
    if not course:
        errors.append("Course is required.")
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
    return dict(course=course, title=title, description=description,
                link=link, deadline=deadline, date=date, time=time), errors


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


@app.route("/")
@login_required
def index():
    view = request.args.get("view", "todo")
    if view not in ("todo", "done", "all"):
        view = "todo"
    course = request.args.get("course", "")
    rows = db().execute(
        """
        SELECT a.*, (c.nrp IS NOT NULL) AS done
        FROM assignments a
        LEFT JOIN completions c ON c.assignment_id = a.id AND c.nrp = ?
        ORDER BY a.deadline ASC
        """,
        (current_user(),),
    ).fetchall()
    now = datetime.now()
    items = []
    for r in rows:
        dl = parse_deadline(r["deadline"])
        delta = (dl - now).total_seconds()
        item = dict(r)
        item["dl"] = dl
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
    courses = sorted({i["course"] for i in items}, key=str.lower)

    if view == "done":
        items = [i for i in items if i["done"]]
    elif view == "todo":
        items = [i for i in items if i["todo"]]
    if course:
        items = [i for i in items if i["course"] == course]

    groups = []
    for key, label in BUCKETS:
        rows_ = [i for i in items if i["bucket"] == key]
        if key == "past":
            rows_.reverse()  # most recently missed first
            if view != "todo":
                label = "Past"
        if rows_:
            groups.append({"label": label, "rows": rows_})
    return render_template("index.html", groups=groups, view=view, course=course,
                           courses=courses, stats=stats, counts=counts, total=len(items))


@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        data, errors = form_fields()
        if errors:
            for e in errors:
                flash(e)
            return render_template("form.html", a=data, editing=False)
        db().execute(
            """INSERT INTO assignments (course, title, description, link, deadline, created_by, created_at)
               VALUES (:course, :title, :description, :link, :deadline, :by, :at)""",
            {**data, "by": current_user(), "at": datetime.now().isoformat(timespec="seconds")},
        )
        db().commit()
        return redirect(url_for("index"))
    return render_template("form.html", a={}, editing=False)


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
    if request.method == "POST":
        data, errors = form_fields()
        if errors:
            for e in errors:
                flash(e)
            return render_template("form.html", a={**data, "id": aid}, editing=True)
        db().execute(
            """UPDATE assignments SET course=:course, title=:title, description=:description,
               link=:link, deadline=:deadline WHERE id=:id""",
            {**data, "id": aid},
        )
        db().commit()
        return redirect(url_for("index"))
    return render_template("form.html", a=dict(row), editing=True)


@app.route("/delete/<int:aid>", methods=["POST"])
@login_required
def delete(aid):
    if current_user() in ADMINS:
        if db().execute("SELECT 1 FROM assignments WHERE id = ?", (aid,)).fetchone() is None:
            abort(404)
    else:
        get_owned(aid)
    db().execute("DELETE FROM assignments WHERE id = ?", (aid,))
    db().commit()
    return redirect(request.referrer or url_for("index"))


@app.route("/toggle/<int:aid>", methods=["POST"])
@login_required
def toggle(aid):
    if db().execute("SELECT 1 FROM assignments WHERE id = ?", (aid,)).fetchone() is None:
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
