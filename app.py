"""
WhatsApp-style chat website built with Python.

Stack: Flask + Socket.IO + SQLite (single process, no extra services).

Features
--------
* Register / log in  (passwords hashed with werkzeug)
* One-to-one real-time chats, like WhatsApp
* Group chats: create a group, add friends, chat together
* File & photo uploads (drag & drop, up to 25 MB)
* Built-in AI bot (PyBot) in every chat and group
* Online presence ("online" / "last seen") and typing indicator
* Delivery + read receipts: single tick -> double tick -> blue double tick
* Unread counters, chat list with last-message preview, user search
* WhatsApp-Web style dark interface, mobile friendly

Run
---
    python app.py

Then open http://127.0.0.1:5000 - register two accounts in two browser
windows and start chatting.
"""

from __future__ import annotations

import os
import random
import re
import secrets
import socket
import sqlite3
import time
from contextlib import contextmanager
from functools import wraps

from flask import (Flask, jsonify, redirect, render_template, request,
                   session, url_for)
from flask_socketio import SocketIO, join_room
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import ai_bot

HOST = "127.0.0.1"
PORT = 5000
MAX_MESSAGE_LENGTH = 4000
MAX_UPLOAD_BYTES = 25 * 1024 * 1024          # 25 MB per file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chat.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "static", "uploads")
BOT_USERNAME = "pybot"

ALLOWED_EXT = {
    # images
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico",
    # documents
    "pdf", "txt", "md", "csv", "json", "log", "doc", "docx", "xls",
    "xlsx", "ppt", "pptx", "odt", "ods", "rtf", "html", "css", "py",
    "zip", "rar", "7z", "tar", "gz",
    # media
    "mp3", "wav", "ogg", "flac", "m4a", "mp4", "webm", "mov", "mkv",
}
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico"}
VIDEO_EXT = {"mp4", "webm", "mov", "mkv"}
AUDIO_EXT = {"mp3", "wav", "ogg", "flac", "m4a"}

AVATAR_COLORS = [
    "#e91e63", "#9c27b0", "#673ab7", "#3f51b5", "#2196f3",
    "#009688", "#4caf50", "#ff9800", "#ff5722", "#795548",
]

app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(16)

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")


class _QuietWebSocketErrors:
    """WSGI middleware.

    python-engineio raises a harmless ConnectionError when a browser drops its
    WebSocket mid-handshake (tab closed, network lost). Without this, Werkzeug
    prints a scary traceback in the server console every time it happens.
    Swallow it and answer with a plain 499 instead.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        try:
            return self.wsgi_app(environ, start_response)
        except ConnectionError:
            start_response("499 Client Closed Request", [("Content-Length", "0")])
            return [b""]


app.wsgi_app = _QuietWebSocketErrors(app.wsgi_app)

# --------------------------------------------------------------- in-memory
# presence state (rebuilt on every server start; last_seen also persisted)
sids_by_user: dict[int, set[str]] = {}
user_by_sid: dict[str, int] = {}
display_names: dict[int, str] = {}


# ---------------------------------------------------------------- database
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name  TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                avatar_color  TEXT NOT NULL,
                last_seen     INTEGER,
                created_at    INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                text         TEXT NOT NULL,
                sent_at      INTEGER NOT NULL,
                delivered_at INTEGER,
                read_at      INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_messages_pair
                ON messages (sender_id, recipient_id, id);
            CREATE TABLE IF NOT EXISTS friendships (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                addressee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   INTEGER NOT NULL,
                updated_at   INTEGER,
                UNIQUE (requester_id, addressee_id)
            );
            CREATE INDEX IF NOT EXISTS idx_friendships_addressee
                ON friendships (addressee_id, status);

            -- groups ---------------------------------------------------------
            CREATE TABLE IF NOT EXISTS chat_groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                avatar_color TEXT NOT NULL,
                creator_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at  INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_members (
                group_id  INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role      TEXT NOT NULL DEFAULT 'member',
                joined_at INTEGER NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS group_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id   INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                sender_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                text       TEXT NOT NULL DEFAULT '',
                file_url   TEXT,
                file_name  TEXT,
                file_size  INTEGER,
                file_kind  TEXT,
                sent_at    INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_group_messages
                ON group_messages (group_id, id);
            CREATE TABLE IF NOT EXISTS group_reads (
                group_id     INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                last_read_id INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (group_id, user_id)
            );
            """
        )
        # Migration: attachments on existing 1:1 messages (old databases).
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}
        for column, decl in (("file_url", "TEXT"), ("file_name", "TEXT"),
                             ("file_size", "INTEGER"), ("file_kind", "TEXT")):
            if column not in cols:
                conn.execute(f"ALTER TABLE messages ADD COLUMN {column} {decl}")
        # Migration: any pair that already exchanged messages becomes friends.
        conn.execute(
            """
            INSERT OR IGNORE INTO friendships
                (requester_id, addressee_id, status, created_at, updated_at)
            SELECT MIN(sender_id, recipient_id),
                   MAX(sender_id, recipient_id),
                   'accepted',
                   CAST(strftime('%s','now') AS INTEGER),
                   CAST(strftime('%s','now') AS INTEGER)
            FROM messages
            GROUP BY MIN(sender_id, recipient_id), MAX(sender_id, recipient_id)
            """
        )
    ensure_bot()


# ----------------------------------------------------------------- helpers
def now_ts() -> int:
    return int(time.time())


def user_room(user_id: int) -> str:
    return f"user:{user_id}"


def is_online(user_id: int) -> bool:
    return bool(sids_by_user.get(user_id))


def friendship_status(a: int, b: int) -> str:
    """Relationship of a towards b: friends | pending_out | pending_in | none."""
    with db() as conn:
        row = conn.execute(
            """SELECT requester_id, status FROM friendships
               WHERE (requester_id = ? AND addressee_id = ?)
                  OR (requester_id = ? AND addressee_id = ?)""",
            (a, b, b, a),
        ).fetchone()
    if row is None:
        return "none"
    if row["status"] == "accepted":
        return "friends"
    return "pending_out" if row["requester_id"] == a else "pending_in"


def notify_friends(a: int, b: int) -> None:
    """Tell both users' devices that they are now friends."""
    for uid, other in ((a, b), (b, a)):
        peer = fetch_user(other)
        if peer is not None:
            socketio.emit("friend_accepted",
                          {"user_id": other, "name": peer["display_name"]},
                          to=user_room(uid))


def fetch_user(user_id: int):
    with db() as conn:
        return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def user_public(row, online: bool | None = None) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "avatar_color": row["avatar_color"],
        "online": is_online(row["id"]) if online is None else online,
        "last_seen": row["last_seen"],
    }


def message_public(row) -> dict:
    return {
        "id": row["id"],
        "sender_id": row["sender_id"],
        "recipient_id": row["recipient_id"],
        "text": row["text"],
        "sent_at": row["sent_at"],
        "delivered_at": row["delivered_at"],
        "read_at": row["read_at"],
        "file_url": row["file_url"],
        "file_name": row["file_name"],
        "file_size": row["file_size"],
        "file_kind": row["file_kind"],
    }


def group_message_public(row) -> dict:
    return {
        "id": row["id"],
        "group_id": row["group_id"],
        "sender_id": row["sender_id"],
        "sender_name": row["display_name"],
        "sender_color": row["avatar_color"],
        "text": row["text"],
        "sent_at": row["sent_at"],
        "file_url": row["file_url"],
        "file_name": row["file_name"],
        "file_size": row["file_size"],
        "file_kind": row["file_kind"],
    }


# ------------------------------------------------------------------- PyBot
def bot_user():
    """The built-in AI assistant account (created on first run)."""
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ? AND display_name = 'PyBot'",
            (BOT_USERNAME,)).fetchone()


def friend_user_with_bot(user_id: int, bot_id: int) -> None:
    with db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO friendships
               (requester_id, addressee_id, status, created_at, updated_at)
               VALUES (?, ?, 'accepted', ?, ?)""",
            (bot_id, user_id, now_ts(), now_ts()),
        )


def ensure_bot():
    """Create PyBot if missing and make sure it is friends with everyone."""
    bot = bot_user()
    if bot is None:
        with db() as conn:
            try:
                conn.execute(
                    """INSERT INTO users
                       (username, display_name, password_hash, avatar_color,
                        last_seen, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (BOT_USERNAME, "PyBot",
                     generate_password_hash(secrets.token_urlsafe(24)),
                     "#00a884", now_ts(), now_ts()),
                )
            except sqlite3.IntegrityError:
                pass
        bot = bot_user()
        if bot is None:
            return None

    with db() as conn:
        humans = conn.execute(
            "SELECT id FROM users WHERE id != ?", (bot["id"],)).fetchall()
        for h in humans:
            friend_user_with_bot(h["id"], bot["id"])
    return bot


def bot_reply_to(user_id: int, text: str) -> None:
    """Think for a moment, then answer like a real chat partner would."""
    def work():
        bot = bot_user()
        if bot is None:
            return
        time.sleep(0.4 + random.random() * 0.8)
        socketio.emit("typing", {"from": bot["id"], "name": "PyBot",
                                 "typing": True}, to=user_room(user_id))

        # short conversational memory: the last things we said to each other
        history: list[tuple[str, str]] = []
        try:
            with db() as conn:
                rows = conn.execute(
                    """SELECT sender_id, text FROM messages
                       WHERE ((sender_id = ? AND recipient_id = ?)
                          OR (sender_id = ? AND recipient_id = ?))
                         AND text != ''
                       ORDER BY id DESC LIMIT 12""",
                    (bot["id"], user_id, user_id, bot["id"]),
                ).fetchall()
            for r in reversed(rows):
                history.append(
                    ("assistant" if r["sender_id"] == bot["id"] else "user",
                     r["text"]))
        except sqlite3.Error:
            history = []

        answer = ai_bot.reply(text, history)
        time.sleep(min(2.5, 0.35 + len(answer) / 90))
        socketio.emit("typing", {"from": bot["id"], "name": "PyBot",
                                 "typing": False}, to=user_room(user_id))

        ts = now_ts()
        with db() as conn:
            # the bot has "read" everything you sent it -> blue ticks for you
            seen = conn.execute(
                """UPDATE messages
                   SET delivered_at = COALESCE(delivered_at, ?), read_at = ?
                   WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL
                   RETURNING id""",
                (ts, ts, user_id, bot["id"]),
            ).fetchall()
            cur = conn.execute(
                """INSERT INTO messages
                   (sender_id, recipient_id, text, sent_at, delivered_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (bot["id"], user_id, answer, ts, ts),
            )
            msg_id = cur.lastrowid
        seen_ids = [s["id"] for s in seen]
        if seen_ids:
            socketio.emit("receipt", {"ids": seen_ids, "status": "read"},
                          to=user_room(user_id))
        msg = {"id": msg_id, "sender_id": bot["id"], "recipient_id": user_id,
               "text": answer, "sent_at": ts, "delivered_at": ts,
               "read_at": None, "file_url": None, "file_name": None,
               "file_size": None, "file_kind": None}
        socketio.emit("message", msg, to=user_room(user_id))

    socketio.start_background_task(work)


def bot_group_reply(group_id: int, prompt: str) -> None:
    """PyBot chips in inside groups: always when mentioned, sometimes otherwise."""
    def work():
        bot = bot_user()
        if bot is None or not is_group_member(group_id, bot["id"]):
            return
        low = prompt.lower()
        mentioned = ("pybot" in low or "@bot" in low or "hey bot" in low
                     or low.startswith("bot ") or low.strip() in ("bot", "@pybot"))
        if not mentioned:
            # chill out unless spoken to: answer short messages occasionally
            if len(prompt) < 6 or random.random() > 0.25:
                return
        time.sleep(0.5 + random.random() * 1.2)
        emit_to_group("group_typing",
                      {"gid": group_id, "from": bot["id"], "name": "PyBot",
                       "typing": True}, group_id)
        answer = ai_bot.reply(prompt, [])
        time.sleep(min(2.0, 0.3 + len(answer) / 100))
        emit_to_group("group_typing",
                      {"gid": group_id, "from": bot["id"], "name": "PyBot",
                       "typing": False}, group_id)

        ts = now_ts()
        with db() as conn:
            cur = conn.execute(
                """INSERT INTO group_messages
                   (group_id, sender_id, text, sent_at) VALUES (?, ?, ?, ?)""",
                (group_id, bot["id"], answer, ts),
            )
            msg_id = cur.lastrowid
        emit_to_group("group_message",
                      {"id": msg_id, "group_id": group_id,
                       "sender_id": bot["id"], "sender_name": bot["display_name"],
                       "sender_color": bot["avatar_color"], "text": answer,
                       "sent_at": ts, "file_url": None, "file_name": None,
                       "file_size": None, "file_kind": None},
                      group_id)

    socketio.start_background_task(work)


def bot_welcome(user_id: int) -> None:
    """Greet people the moment they sign in (first visit gets the full tour)."""
    bot = bot_user()
    user = fetch_user(user_id)
    if bot is None or user is None or bot["id"] == user_id:
        return

    with db() as conn:
        spoken_before = conn.execute(
            "SELECT 1 FROM messages WHERE sender_id = ? AND recipient_id = ? "
            "LIMIT 1", (bot["id"], user_id)).fetchone() is not None

    first = (user["display_name"] or "friend").split()[0]
    hour = time.localtime().tm_hour
    if 5 <= hour < 12:
        wish = "Good morning"
    elif 12 <= hour < 17:
        wish = "Good afternoon"
    elif 17 <= hour < 22:
        wish = "Good evening"
    else:
        wish = "Hello night owl"

    lines = (
        [f"{wish}, {first}! Welcome back to PyChat 👋 Anything I can help with?"]
        if spoken_before else
        [
            f"👋 Hey {first}! I'm PyBot 🤖 — the assistant that lives inside PyChat.",
            'Ask me for a joke, a fun fact, the time or some maths — try '
            '"what is 12 * 7".',
            "Press 👥 up top to start a group, and 📎 (or just drag & drop) "
            "to send files. 🚀",
        ]
    )

    ts = now_ts()
    queued = []
    with db() as conn:
        for text in lines:
            cur = conn.execute(
                """INSERT INTO messages
                   (sender_id, recipient_id, text, sent_at, delivered_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (bot["id"], user_id, text, ts, ts),
            )
            queued.append({"id": cur.lastrowid, "sender_id": bot["id"],
                           "recipient_id": user_id, "text": text, "sent_at": ts,
                           "delivered_at": ts, "read_at": None,
                           "file_url": None, "file_name": None,
                           "file_size": None, "file_kind": None})
    for msg in queued:                     # reaches any tab already signed in
        socketio.emit("message", msg, to=user_room(user_id))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth_page"))
        return view(*args, **kwargs)
    return wrapped


# ------------------------------------------------------------------ groups
def is_group_member(group_id: int, user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id)).fetchone()
    return row is not None


def group_public(row, me: int) -> dict:
    """Everything the sidebar needs to draw one group row."""
    gid = row["id"]
    with db() as conn:
        members = conn.execute(
            """SELECT u.id, u.display_name, u.avatar_color, u.username
               FROM group_members gm JOIN users u ON u.id = gm.user_id
               WHERE gm.group_id = ? ORDER BY gm.joined_at""",
            (gid,),
        ).fetchall()
        last = conn.execute(
            """SELECT m.*, u.display_name, u.avatar_color
               FROM group_messages m JOIN users u ON u.id = m.sender_id
               WHERE m.group_id = ? ORDER BY m.id DESC LIMIT 1""",
            (gid,),
        ).fetchone()
        last_read = conn.execute(
            "SELECT last_read_id FROM group_reads WHERE group_id = ? AND user_id = ?",
            (gid, me)).fetchone()
        unread = conn.execute(
            """SELECT COUNT(*) AS n FROM group_messages
               WHERE group_id = ? AND sender_id != ? AND id > ?""",
            (gid, me, last_read["last_read_id"] if last_read else 0)).fetchone()["n"]
        role = conn.execute(
            "SELECT role FROM group_members WHERE group_id = ? AND user_id = ?",
            (gid, me)).fetchone()

    members_list = [{"id": m["id"], "display_name": m["display_name"],
                     "avatar_color": m["avatar_color"],
                     "username": m["username"]} for m in members]
    return {
        "id": gid,
        "name": row["name"],
        "avatar_color": row["avatar_color"],
        "creator_id": row["creator_id"],
        "role": role["role"] if role else "member",
        "members": members_list,
        "member_count": len(members_list),
        "unread": unread,
        "last_text": last["text"] if last else "",
        "last_at": last["sent_at"] if last else None,
        "last_sender": last["sender_id"] if last else None,
        "last_sender_name": last["display_name"] if last else None,
        "has_file": bool(last and last["file_url"]) if last else False,
        "file_kind": last["file_kind"] if last else None,
        "last_file": last["file_url"] if last else None,
        "last_file_name": last["file_name"] if last else None,
    }


def broadcast_group_update(group_id: int) -> None:
    """Push a fresh copy of the group to every member's sidebar."""
    with db() as conn:
        grow = conn.execute("SELECT * FROM chat_groups WHERE id = ?",
                            (group_id,)).fetchone()
    if grow is None:
        return
    for uid in group_member_ids(group_id):
        socketio.emit("group_update", {"group": group_public(grow, uid)},
                      to=user_room(uid))


def group_member_ids(group_id: int) -> list[int]:
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id FROM group_members WHERE group_id = ?",
            (group_id,)).fetchall()
    return [r["user_id"] for r in rows]


def emit_to_group(event: str, payload: dict, group_id: int) -> None:
    """Send an event to every member (works even for members who just joined)."""
    for uid in group_member_ids(group_id):
        socketio.emit(event, payload, to=user_room(uid))


# ------------------------------------------------------------- HTTP routes
@app.route("/")
def index():
    return redirect(url_for("chat_page") if session.get("user_id")
                    else url_for("auth_page"))


@app.route("/auth")
def auth_page():
    if session.get("user_id"):
        return redirect(url_for("chat_page"))
    return render_template("auth.html", tab=request.args.get("tab", "login"),
                           error=None, form=None)


@app.route("/register", methods=["POST"])
def register():
    username = (request.form.get("username") or "").strip().lower()
    display_name = (request.form.get("display_name") or "").strip()
    password = request.form.get("password") or ""

    error = None
    if not re.fullmatch(r"[a-z0-9_]{3,20}", username):
        error = "Username: 3-20 characters, letters/numbers/underscore only."
    elif username == BOT_USERNAME:
        error = "That username belongs to the PyBot assistant."
    elif not display_name:
        error = "Please enter your name."
    elif len(password) < 6:
        error = "Password must be at least 6 characters."

    if error:
        return render_template("auth.html", tab="register", error=error,
                               form=request.form)

    with db() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO users
                   (username, display_name, password_hash, avatar_color, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (username, display_name[:40], generate_password_hash(password),
                 random.choice(AVATAR_COLORS), now_ts()),
            )
        except sqlite3.IntegrityError:
            return render_template("auth.html", tab="register",
                                   error="That username is already taken.",
                                   form=request.form)
        session["user_id"] = cur.lastrowid
        new_id = cur.lastrowid
    bot = ensure_bot()
    if bot is not None:
        friend_user_with_bot(new_id, bot["id"])
    bot_welcome(new_id)
    return redirect(url_for("chat_page"))


@app.route("/login", methods=["POST"])
def login():
    username = (request.form.get("username") or "").strip().lower()
    password = request.form.get("password") or ""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?",
                           (username,)).fetchone()
    if row is None or not check_password_hash(row["password_hash"], password):
        return render_template("auth.html", tab="login",
                               error="Wrong username or password.",
                               form=request.form)
    session["user_id"] = row["id"]
    bot = ensure_bot()
    if bot is not None:
        friend_user_with_bot(row["id"], bot["id"])
    bot_welcome(row["id"])
    return redirect(url_for("chat_page"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth_page"))


@app.route("/chat")
@login_required
def chat_page():
    user = fetch_user(session["user_id"])
    if user is None:
        session.clear()
        return redirect(url_for("auth_page"))
    bot = ensure_bot()
    return render_template(
        "chat.html", me=user,
        bot={"id": bot["id"], "username": bot["username"],
             "display_name": bot["display_name"]} if bot else None,
    )


@app.route("/api/chats")
@login_required
def api_chats():
    me = session["user_id"]
    with db() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.display_name, u.avatar_color, u.last_seen,
                   lm.text  AS last_text,
                   lm.file_url AS last_file,
                   lm.file_name AS last_file_name,
                   lm.sent_at AS last_at,
                   lm.sender_id AS last_sender,
                   lm.delivered_at AS last_delivered,
                   lm.read_at AS last_read,
                   (SELECT COUNT(*) FROM messages x
                     WHERE x.sender_id = u.id AND x.recipient_id = :me
                       AND x.read_at IS NULL) AS unread
            FROM users u
            JOIN friendships f ON f.status = 'accepted'
               AND ((f.requester_id = :me AND f.addressee_id = u.id)
                    OR (f.addressee_id = :me AND f.requester_id = u.id))
            JOIN messages lm ON lm.id = (
                SELECT m.id FROM messages m
                WHERE (m.sender_id = :me AND m.recipient_id = u.id)
                   OR (m.sender_id = u.id AND m.recipient_id = :me)
                ORDER BY m.id DESC LIMIT 1
            )
            WHERE u.id != :me
            ORDER BY lm.sent_at DESC
            """,
            {"me": me},
        ).fetchall()

    chats = []
    for r in rows:
        chats.append({
            **user_public(r),
            "last_text": r["last_text"],
            "last_file": r["last_file"],
            "last_file_name": r["last_file_name"],
            "last_at": r["last_at"],
            "last_sender": r["last_sender"],
            "last_delivered": r["last_delivered"],
            "last_read": r["last_read"],
            "unread": r["unread"],
        })
    return jsonify({"chats": chats})


@app.route("/api/users")
@login_required
def api_users():
    me = session["user_id"]
    q = (request.args.get("q") or "").strip()
    sql = "SELECT * FROM users WHERE id != ?"
    params: list = [me]
    if q:
        sql += " AND (username LIKE ? OR display_name LIKE ?)"
        like = f"%{q}%"
        params += [like, like]
    sql += (" ORDER BY (username = ?) DESC, display_name COLLATE NOCASE")
    params = [*params, BOT_USERNAME]
    with db() as conn:
        rows = conn.execute(sql, params).fetchall()
    users = []
    for r in rows:
        users.append({**user_public(r), "friend_status": friendship_status(me, r["id"])})
    return jsonify({"users": users})


@app.route("/api/friends")
@login_required
def api_friends():
    me = session["user_id"]
    with db() as conn:
        incoming = conn.execute(
            """SELECT f.id AS request_id, f.created_at, u.*
               FROM friendships f JOIN users u ON u.id = f.requester_id
               WHERE f.addressee_id = ? AND f.status = 'pending'
               ORDER BY f.created_at DESC""",
            (me,),
        ).fetchall()
        outgoing = conn.execute(
            """SELECT f.id AS request_id, f.created_at, u.*
               FROM friendships f JOIN users u ON u.id = f.addressee_id
               WHERE f.requester_id = ? AND f.status = 'pending'
               ORDER BY f.created_at DESC""",
            (me,),
        ).fetchall()
        friends = conn.execute(
            """SELECT u.* FROM users u
               JOIN friendships f ON f.status = 'accepted'
                  AND ((f.requester_id = :me AND f.addressee_id = u.id)
                       OR (f.addressee_id = :me AND f.requester_id = u.id))
               ORDER BY u.display_name COLLATE NOCASE""",
            {"me": me},
        ).fetchall()
    return jsonify({
        "incoming": [dict(user_public(r), request_id=r["request_id"],
                          requested_at=r["created_at"]) for r in incoming],
        "outgoing": [dict(user_public(r), request_id=r["request_id"])
                     for r in outgoing],
        "friends": [user_public(r) for r in friends],
    })


@app.route("/api/friends/request", methods=["POST"])
@login_required
def api_friend_request():
    me = session["user_id"]
    data = request.get_json(silent=True) or {}
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid user."}), 400
    if target == me:
        return jsonify({"error": "You cannot add yourself."}), 400
    other = fetch_user(target)
    if other is None:
        return jsonify({"error": "Unknown user."}), 404

    status = friendship_status(me, target)
    if status == "friends":
        return jsonify({"error": "You are already friends.",
                        "status": "friends"}), 409
    if status == "pending_out":
        return jsonify({"error": "Friend request already sent.",
                        "status": "pending_out"}), 409

    if status == "pending_in":
        # They already asked you -> clicking Add accepts their request.
        with db() as conn:
            conn.execute(
                """UPDATE friendships SET status = 'accepted', updated_at = ?
                   WHERE requester_id = ? AND addressee_id = ?""",
                (now_ts(), target, me),
            )
        notify_friends(me, target)
        return jsonify({"status": "friends"})

    with db() as conn:
        cur = conn.execute(
            """INSERT INTO friendships (requester_id, addressee_id, status, created_at)
               VALUES (?, ?, 'pending', ?)""",
            (me, target, now_ts()),
        )
        request_id = cur.lastrowid
    me_row = fetch_user(me)
    socketio.emit("friend_request",
                  {"from": me, "name": me_row["display_name"],
                   "request_id": request_id},
                  to=user_room(target))
    return jsonify({"status": "pending_out", "request_id": request_id})


@app.route("/api/friends/respond", methods=["POST"])
@login_required
def api_friend_respond():
    me = session["user_id"]
    data = request.get_json(silent=True) or {}
    try:
        req_id = int(data.get("request_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid request."}), 400
    action = data.get("action")
    if action not in ("accept", "decline"):
        return jsonify({"error": "Invalid action."}), 400

    with db() as conn:
        row = conn.execute(
            """SELECT * FROM friendships
               WHERE id = ? AND addressee_id = ? AND status = 'pending'""",
            (req_id, me),
        ).fetchone()
        if row is None:
            return jsonify({"error": "Request not found."}), 404
        if action == "accept":
            conn.execute(
                "UPDATE friendships SET status = 'accepted', updated_at = ? WHERE id = ?",
                (now_ts(), req_id),
            )
        else:
            conn.execute("DELETE FROM friendships WHERE id = ?", (req_id,))

    if action == "accept":
        notify_friends(me, row["requester_id"])
        return jsonify({"status": "accepted", "user_id": row["requester_id"]})
    return jsonify({"status": "declined"})


@app.route("/api/messages/<int:other_id>")
@login_required
def api_messages(other_id: int):
    me = session["user_id"]
    other = fetch_user(other_id)
    if other is None or other["id"] == me:
        return jsonify({"error": "Unknown user."}), 404
    if friendship_status(me, other_id) != "friends":
        return jsonify({"error": "Send a friend request first."}), 403

    with db() as conn:
        rows = conn.execute(
            """SELECT * FROM messages
               WHERE (sender_id = ? AND recipient_id = ?)
                  OR (sender_id = ? AND recipient_id = ?)
               ORDER BY id ASC LIMIT 500""",
            (me, other_id, other_id, me),
        ).fetchall()

        # Opening the chat marks their -> my messages as read (WhatsApp style).
        unread = conn.execute(
            """SELECT id FROM messages
               WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL""",
            (other_id, me),
        ).fetchall()
        read_ids = [u["id"] for u in unread]
        if read_ids:
            marks = ",".join("?" * len(read_ids))
            conn.execute(
                f"UPDATE messages SET read_at = ? WHERE id IN ({marks})",
                [now_ts(), *read_ids],
            )

    if read_ids:
        socketio.emit("receipt", {"ids": read_ids, "status": "read"},
                      to=user_room(other_id))

    now = now_ts()
    messages = []
    for r in rows:
        m = message_public(r)
        if m["id"] in set(read_ids):
            m["read_at"] = now
        messages.append(m)
    return jsonify({"messages": messages, "peer": user_public(other)})


# ------------------------------------------------------------------ uploads
@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "No file chosen."}), 400

    raw_name = file.filename or ""
    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else ""
    ext = ext if re.fullmatch(r"[a-z0-9]{1,5}", ext) else ""
    if ext not in ALLOWED_EXT:
        return jsonify({"error": "That file type is not allowed."}), 400

    original = secure_filename(raw_name) or f"file.{ext}"

    stored = secrets.token_hex(8) + "." + ext
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest = os.path.join(UPLOAD_DIR, stored)

    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = file.stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise ValueError("too large")
                out.write(chunk)
    except ValueError:
        try:
            os.remove(dest)
        except OSError:
            pass
        return jsonify({"error": "File is too large (max 25 MB)."}), 413
    except OSError:
        return jsonify({"error": "Could not save the file."}), 500

    if size == 0:
        try:
            os.remove(dest)
        except OSError:
            pass
        return jsonify({"error": "That file is empty."}), 400

    kind = ("image" if ext in IMAGE_EXT else
            "video" if ext in VIDEO_EXT else
            "audio" if ext in AUDIO_EXT else "file")
    return jsonify({
        "url": f"/static/uploads/{stored}",
        "name": original,
        "size": size,
        "kind": kind,
    })


# ------------------------------------------------------------------- groups
@app.route("/api/groups")
@login_required
def api_groups():
    me = session["user_id"]
    with db() as conn:
        rows = conn.execute(
            """SELECT g.* FROM chat_groups g
               JOIN group_members gm ON gm.group_id = g.id AND gm.user_id = ?
               ORDER BY g.id DESC""",
            (me,),
        ).fetchall()
    return jsonify({"groups": [group_public(r, me) for r in rows]})


@app.route("/api/groups", methods=["POST"])
@login_required
def api_create_group():
    me = session["user_id"]
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:40]
    if not name:
        return jsonify({"error": "Give your group a name."}), 400

    raw_ids = data.get("member_ids") or []
    members: list[int] = []
    for value in raw_ids[:100]:
        try:
            uid = int(value)
        except (TypeError, ValueError):
            continue
        if uid != me and uid not in members:
            members.append(uid)

    # only friends may be pulled into a group
    allowed = set()
    with db() as conn:
        rows = conn.execute(
            """SELECT addressee_id AS id FROM friendships
               WHERE requester_id = ? AND status = 'accepted'
               UNION
               SELECT requester_id AS id FROM friendships
               WHERE addressee_id = ? AND status = 'accepted'""",
            (me, me),
        ).fetchall()
        allowed = {r["id"] for r in rows}
    members = [uid for uid in members if uid in allowed]
    if not members:
        return jsonify({"error": "Pick at least one friend to add."}), 400

    with db() as conn:
        cur = conn.execute(
            """INSERT INTO chat_groups (name, avatar_color, creator_id, created_at)
               VALUES (?, ?, ?, ?)""",
            (name, random.choice(AVATAR_COLORS), me, now_ts()),
        )
        gid = cur.lastrowid
        # every group starts with the AI assistant as a member
        bot = bot_user()
        roster = [me, *members]
        if bot is not None and bot["id"] not in roster:
            roster.append(bot["id"])
        for uid in roster:
            conn.execute(
                """INSERT OR IGNORE INTO group_members
                   (group_id, user_id, role, joined_at) VALUES (?, ?, ?, ?)""",
                (gid, uid, "admin" if uid == me else "member", now_ts()),
            )
        row = conn.execute("SELECT * FROM chat_groups WHERE id = ?", (gid,)).fetchone()

    socketio.emit("group_created", {"group_id": gid, "name": name, "by": me},
                  to=user_room(me))
    broadcast_group_update(gid)
    return jsonify({"group": group_public(row, me)})


@app.route("/api/groups/<int:gid>/messages")
@login_required
def api_group_messages(gid: int):
    me = session["user_id"]
    if not is_group_member(gid, me):
        return jsonify({"error": "You are not a member of this group."}), 403
    with db() as conn:
        grow = conn.execute("SELECT * FROM chat_groups WHERE id = ?", (gid,)).fetchone()
        if grow is None:
            return jsonify({"error": "Group not found."}), 404
        rows = conn.execute(
            """SELECT m.*, u.display_name, u.avatar_color
               FROM group_messages m JOIN users u ON u.id = m.sender_id
               WHERE m.group_id = ? ORDER BY m.id ASC LIMIT 500""",
            (gid,),
        ).fetchall()
        conn.execute(
            """INSERT OR REPLACE INTO group_reads
               (group_id, user_id, last_read_id)
               SELECT ?, ?, COALESCE((SELECT MAX(id) FROM group_messages
                                      WHERE group_id = ?), 0)""",
            (gid, me, gid),
        )
        last_read = conn.execute(
            "SELECT last_read_id FROM group_reads WHERE group_id = ? AND user_id = ?",
            (gid, me)).fetchone()
    if last_read:
        emit_to_group("group_read", {"gid": gid, "user_id": me}, gid)
    return jsonify({
        "messages": [group_message_public(r) for r in rows],
        "group": group_public(grow, me),
    })


@app.route("/api/groups/<int:gid>/members", methods=["POST"])
@login_required
def api_add_group_member(gid: int):
    me = session["user_id"]
    if not is_group_member(gid, me):
        return jsonify({"error": "Only members can add people."}), 403
    data = request.get_json(silent=True) or {}
    try:
        target = int(data.get("user_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid user."}), 400
    if friendship_status(me, target) != "friends":
        return jsonify({"error": "You can only add your friends."}), 403

    with db() as conn:
        if conn.execute("SELECT id FROM users WHERE id = ?", (target,)).fetchone() is None:
            return jsonify({"error": "Unknown user."}), 404
        already = conn.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (gid, target)).fetchone()
        if already:
            return jsonify({"error": "Already in this group."}), 409
        conn.execute(
            """INSERT INTO group_members (group_id, user_id, role, joined_at)
               VALUES (?, ?, 'member', ?)""",
            (gid, target, now_ts()),
        )
        conn.execute(
            """INSERT OR IGNORE INTO group_reads (group_id, user_id, last_read_id)
               VALUES (?, ?, 0)""", (gid, target))
        grow = conn.execute("SELECT * FROM chat_groups WHERE id = ?", (gid,)).fetchone()

    socketio.emit("group_invite", {"gid": gid, "name": grow["name"]},
                  to=user_room(target))
    broadcast_group_update(gid)
    return jsonify({"status": "added"})


@app.route("/api/groups/<int:gid>/leave", methods=["POST"])
@login_required
def api_leave_group(gid: int):
    me = session["user_id"]
    if not is_group_member(gid, me):
        return jsonify({"error": "You are not in this group."}), 403
    with db() as conn:
        conn.execute("DELETE FROM group_members WHERE group_id = ? AND user_id = ?",
                     (gid, me))
        conn.execute("DELETE FROM group_reads WHERE group_id = ? AND user_id = ?",
                     (gid, me))
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM group_members WHERE group_id = ?",
            (gid,)).fetchone()["n"]
        if left == 0:
            conn.execute("DELETE FROM chat_groups WHERE id = ?", (gid,))
            return jsonify({"status": "group_deleted"})
        grow = conn.execute("SELECT * FROM chat_groups WHERE id = ?", (gid,)).fetchone()
    broadcast_group_update(gid)
    return jsonify({"status": "left", "group": group_public(grow, me)})


# --------------------------------------------------------- socket.io events
@socketio.on("connect")
def on_connect(auth=None):
    uid = session.get("user_id")
    if not uid:
        return False                      # reject anonymous sockets
    user = fetch_user(uid)
    if user is None:
        return False
    join_room(user_room(uid))
    user_by_sid[request.sid] = uid
    sids_by_user.setdefault(uid, set()).add(request.sid)
    display_names[uid] = user["display_name"]
    if len(sids_by_user[uid]) == 1:       # first device came online
        # no "to=" -> broadcast to every connected client
        socketio.emit("presence", {"user_id": uid, "online": True,
                                   "last_seen": None})


@socketio.on("disconnect")
def on_disconnect(reason=None):
    uid = user_by_sid.pop(request.sid, None)
    if uid is None:
        return
    sids = sids_by_user.get(uid)
    if not sids:
        return
    sids.discard(request.sid)
    if not sids:
        sids_by_user.pop(uid, None)
        seen = now_ts()
        with db() as conn:
            conn.execute("UPDATE users SET last_seen = ? WHERE id = ?",
                         (seen, uid))
        socketio.emit("presence", {"user_id": uid, "online": False,
                                   "last_seen": seen})


def _clean_file(data) -> dict | None:
    """Validate attachment fields sent together with a chat message."""
    if not isinstance(data, dict):
        return None
    url = data.get("file_url")
    if not isinstance(url, str) or not url.startswith("/static/uploads/"):
        return None
    if not re.fullmatch(r"/static/uploads/[0-9a-f]{16}\.[a-z0-9]{1,5}", url):
        return None
    name = str(data.get("file_name") or "file")[:120]
    try:
        size = max(0, int(data.get("file_size") or 0))
    except (TypeError, ValueError):
        size = 0
    kind = data.get("file_kind")
    if kind not in ("image", "video", "audio", "file"):
        kind = "file"
    return {"url": url, "name": name, "size": size, "kind": kind}


@socketio.on("message")
def on_message(data):
    uid = user_by_sid.get(request.sid)
    if uid is None:
        return
    data = data or {}
    try:
        to_id = int(data.get("to"))
    except (TypeError, ValueError):
        return
    text = str(data.get("text", "")).strip()[:MAX_MESSAGE_LENGTH]
    attachment = _clean_file(data)
    if (not text and not attachment) or to_id == uid:
        return
    if friendship_status(uid, to_id) != "friends":
        socketio.emit("app_error",
                      {"text": "Send them a friend request first."},
                      to=user_room(uid))
        return

    with db() as conn:
        if conn.execute("SELECT id FROM users WHERE id = ?", (to_id,)).fetchone() is None:
            return
        cur = conn.execute(
            """INSERT INTO messages
               (sender_id, recipient_id, text, sent_at,
                file_url, file_name, file_size, file_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (uid, to_id, text, now_ts(),
             attachment["url"] if attachment else None,
             attachment["name"] if attachment else None,
             attachment["size"] if attachment else None,
             attachment["kind"] if attachment else None),
        )
        msg_id = cur.lastrowid

    msg = {"id": msg_id, "sender_id": uid, "recipient_id": to_id, "text": text,
           "sent_at": now_ts(), "delivered_at": None, "read_at": None,
           "file_url": attachment["url"] if attachment else None,
           "file_name": attachment["name"] if attachment else None,
           "file_size": attachment["size"] if attachment else None,
           "file_kind": attachment["kind"] if attachment else None}
    socketio.emit("message", msg, to=user_room(uid))    # my other devices
    socketio.emit("message", msg, to=user_room(to_id))  # recipient

    bot = bot_user()
    if bot is not None and to_id == bot["id"]:
        prompt = text or f"(sent a {attachment['kind']}: {attachment['name']})"
        bot_reply_to(uid, prompt)


@socketio.on("delivered")
def on_delivered(data):
    """The recipient's browser confirms the message reached its device."""
    uid = user_by_sid.get(request.sid)
    ids = _clean_ids((data or {}).get("ids"))
    if uid is None or not ids:
        return
    marks = ",".join("?" * len(ids))
    with db() as conn:
        rows = conn.execute(
            f"""UPDATE messages SET delivered_at = ?
                WHERE recipient_id = ? AND delivered_at IS NULL
                  AND id IN ({marks})
                RETURNING id, sender_id""",
            [now_ts(), uid, *ids],
        ).fetchall()
    by_sender: dict[int, list[int]] = {}
    for r in rows:
        by_sender.setdefault(r["sender_id"], []).append(r["id"])
    for sender, updated in by_sender.items():
        socketio.emit("receipt", {"ids": updated, "status": "delivered"},
                      to=user_room(sender))


@socketio.on("read")
def on_read(data):
    """The recipient opened the chat -> mark everything from them as read."""
    uid = user_by_sid.get(request.sid)
    if uid is None:
        return
    try:
        other = int((data or {}).get("from"))
    except (TypeError, ValueError):
        return
    with db() as conn:
        rows = conn.execute(
            """UPDATE messages SET read_at = ?
               WHERE sender_id = ? AND recipient_id = ? AND read_at IS NULL
               RETURNING id""",
            (now_ts(), other, uid),
        ).fetchall()
    ids = [r["id"] for r in rows]
    if ids:
        socketio.emit("receipt", {"ids": ids, "status": "read"},
                      to=user_room(other))


@socketio.on("typing")
def on_typing(data):
    uid = user_by_sid.get(request.sid)
    if uid is None:
        return
    try:
        to_id = int((data or {}).get("to"))
    except (TypeError, ValueError):
        return
    socketio.emit("typing",
                  {"from": uid, "name": display_names.get(uid, "Someone"),
                   "typing": bool((data or {}).get("typing"))},
                  to=user_room(to_id))


@socketio.on("group_message")
def on_group_message(data):
    uid = user_by_sid.get(request.sid)
    if uid is None:
        return
    data = data or {}
    try:
        gid = int(data.get("gid"))
    except (TypeError, ValueError):
        return
    text = str(data.get("text", "")).strip()[:MAX_MESSAGE_LENGTH]
    attachment = _clean_file(data)
    if not text and not attachment:
        return
    if not is_group_member(gid, uid):
        socketio.emit("app_error",
                      {"text": "You are no longer in that group."},
                      to=user_room(uid))
        return

    with db() as conn:
        if conn.execute("SELECT id FROM chat_groups WHERE id = ?", (gid,)).fetchone() is None:
            return
        cur = conn.execute(
            """INSERT INTO group_messages
               (group_id, sender_id, text, sent_at,
                file_url, file_name, file_size, file_kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (gid, uid, text, now_ts(),
             attachment["url"] if attachment else None,
             attachment["name"] if attachment else None,
             attachment["size"] if attachment else None,
             attachment["kind"] if attachment else None),
        )
        msg_id = cur.lastrowid
        # the sender has of course read their own message
        conn.execute(
            """INSERT OR REPLACE INTO group_reads (group_id, user_id, last_read_id)
               VALUES (?, ?, ?)""", (gid, uid, msg_id))
        me_row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    msg = {"id": msg_id, "group_id": gid, "sender_id": uid,
           "sender_name": me_row["display_name"],
           "sender_color": me_row["avatar_color"],
           "text": text, "sent_at": now_ts(),
           "file_url": attachment["url"] if attachment else None,
           "file_name": attachment["name"] if attachment else None,
           "file_size": attachment["size"] if attachment else None,
           "file_kind": attachment["kind"] if attachment else None}
    emit_to_group("group_message", msg, gid)   # includes my other devices

    bot = bot_user()
    if bot is not None and bot["id"] != uid and is_group_member(gid, bot["id"]):
        prompt = text or f"(sent a {attachment['kind']}: {attachment['name']})"
        bot_group_reply(gid, prompt)


@socketio.on("group_typing")
def on_group_typing(data):
    uid = user_by_sid.get(request.sid)
    if uid is None:
        return
    data = data or {}
    try:
        gid = int(data.get("gid"))
    except (TypeError, ValueError):
        return
    if not is_group_member(gid, uid):
        return
    emit_to_group("group_typing",
                  {"gid": gid, "from": uid,
                   "name": display_names.get(uid, "Someone"),
                   "typing": bool(data.get("typing"))},
                  gid)


@socketio.on("group_read")
def on_group_read(data):
    """I opened the group -> everyone else can drop their unread badge."""
    uid = user_by_sid.get(request.sid)
    if uid is None:
        return
    try:
        gid = int((data or {}).get("gid"))
    except (TypeError, ValueError):
        return
    if not is_group_member(gid, uid):
        return
    with db() as conn:
        latest = conn.execute(
            "SELECT MAX(id) AS m FROM group_messages WHERE group_id = ?",
            (gid,)).fetchone()["m"] or 0
        conn.execute(
            """INSERT OR REPLACE INTO group_reads (group_id, user_id, last_read_id)
               VALUES (?, ?, ?)""", (gid, uid, latest))
    emit_to_group("group_read", {"gid": gid, "user_id": uid}, gid)


def _clean_ids(raw) -> list[int]:
    if not isinstance(raw, list):
        return []
    ids = []
    for value in raw[:50]:
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


# ------------------------------------------------------------------- start
def _port_busy() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    init_db()
    if _port_busy():
        print(f" * Port {PORT} is already in use - PyChat may already be running.")
        print(f" * Just open http://127.0.0.1:{PORT} in your browser.")
    else:
        print(" * PyChat running on http://127.0.0.1:5000")
        print(" * Register two accounts in two browser windows and chat away.")
        try:
            socketio.run(app, host=HOST, port=PORT,
                         debug=False, allow_unsafe_werkzeug=True)
        except OSError as exc:
            print(f" * Could not start on port {PORT}: {exc}")
            print(" * Close the other program using that port and try again.")
