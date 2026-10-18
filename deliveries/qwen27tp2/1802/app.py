import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(APP_DIR, "index.html")

STATUS_VALUES = ("todo", "doing", "done")
PRIORITY_VALUES = ("low", "normal", "high")
TASK_FIELDS = ("title", "description", "status", "priority")

PBKDF2_ITERATIONS = 120000
SESSION_TTL = 24 * 60 * 60
BODY_LIMIT = 16384


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def verify_password(password, salt, expected):
    candidate = hash_password(password, salt)
    return secrets.compare_digest(candidate, expected)


def init_db(db_path):
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            salt BLOB NOT NULL,
            password_hash BLOB NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            created_at REAL NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id)")
    conn.commit()
    seed_demo_accounts(conn)
    conn.close()


def seed_demo_accounts(conn):
    for username, password in (("zeka", "deneme-zeka-2026"), ("yapar", "deneme-yapar-2026")):
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            salt = secrets.token_bytes(16)
            digest = hash_password(password, salt)
            conn.execute("INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)", (username, salt, digest))
    conn.commit()


class SessionStore:
    def __init__(self):
        self._sessions = {}

    def create(self, username):
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {"username": username, "expires": time.time() + SESSION_TTL}
        return token

    def get(self, token):
        if not token:
            return None
        record = self._sessions.get(token)
        if record is None:
            return None
        if record["expires"] < time.time():
            self._sessions.pop(token, None)
            return None
        return record

    def delete(self, token):
        self._sessions.pop(token, None)


class App:
    def __init__(self, db_path, port):
        self.db_path = db_path
        self.port = port
        self.sessions = SessionStore()
        self.index_html = None
        self._load_index()

    def _load_index(self):
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as handle:
                self.index_html = handle.read()
        except OSError:
            self.index_html = "<!doctype html><html><head><meta charset='utf-8'><title>Görev Masası</title></head><body><h1>Görev Masası</h1><p>index.html bulunamadı.</p></body></html>"

    def db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def authenticate(self, username, password):
        conn = self.db()
        try:
            row = conn.execute("SELECT id, salt, password_hash FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                return None
            _, salt, expected = row
            if not verify_password(password, salt, expected):
                return None
            return username
        finally:
            conn.close()

    def task_to_dict(self, row):
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
        }

    def validate_task_payload(self, payload, require_title=True):
        if not isinstance(payload, dict):
            return None, "Görev verisi bir nesne olmalı."
        for key in payload:
            if key not in TASK_FIELDS:
                return None, "Bilinmeyen alan: %s" % key
        if require_title and "title" not in payload:
            return None, "Başlık gerekli."
        values = {}
        if "title" in payload:
            title = payload["title"]
            if isinstance(title, bool) or not isinstance(title, str):
                return None, "Başlık metin olmalı."
            title = title.strip()
            if len(title) < 1 or len(title) > 120:
                return None, "Başlık 1-120 karakter olmalı."
            values["title"] = title
        if "description" in payload:
            description = payload["description"]
            if isinstance(description, bool) or not isinstance(description, str):
                return None, "Açıklama metin olmalı."
            if len(description) > 1000:
                return None, "Açıklama 1000 karakteri aşamaz."
            values["description"] = description
        else:
            values["description"] = ""
        if "status" in payload:
            status = payload["status"]
            if isinstance(status, bool) or not isinstance(status, str) or status not in STATUS_VALUES:
                return None, "Geçersiz durum."
            values["status"] = status
        else:
            values["status"] = "todo"
        if "priority" in payload:
            priority = payload["priority"]
            if isinstance(priority, bool) or not isinstance(priority, str) or priority not in PRIORITY_VALUES:
                return None, "Geçersiz öncelik."
            values["priority"] = priority
        else:
            values["priority"] = "normal"
        return values, None


class Handler(BaseHTTPRequestHandler):
    server_version = "GorevMasasi/1.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra_headers=None):
        if isinstance(body, str):
            payload = body.encode("utf-8")
        elif isinstance(body, bytes):
            payload = body
        else:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for key, value in extra_headers:
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json(self, status, data, extra_headers=None):
        self._send(status, data, "application/json; charset=utf-8", extra_headers)

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length > BODY_LIMIT:
            self._error(413, "İstek gövdesi çok büyük.")
            return None, True
        data = self.rfile.read(length) if length > 0 else b""
        if len(data) > BODY_LIMIT:
            self._error(413, "İstek gövdesi çok büyük.")
            return None, True
        return data, False

    def _parse_json(self):
        data, sent_error = self._read_body()
        if sent_error:
            return None
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:
            self._error(400, "Geçersiz JSON.")
            return None
        return payload

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        expected = "http://127.0.0.1:%d" % self.server.app.port
        return origin == expected

    def _session_token(self):
        cookie_header = self.headers.get("Cookie", "")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get("session")
        if morsel is None:
            return None
        return morsel.value

    def _current_user(self):
        token = self._session_token()
        record = self.server.app.sessions.get(token)
        if record is None:
            return None
        return record["username"]

    def _require_user(self):
        username = self._current_user()
        if username is None:
            self._error(401, "Giriş gerekli.")
            return None
        return username

    def _task_by_id(self, username, task_id):
        conn = self.server.app.db()
        try:
            row = conn.execute(
                """SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = (
                    SELECT id FROM users WHERE username = ?
                )""",
                (task_id, username),
            ).fetchone()
            return row
        finally:
            conn.close()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/":
            self._send(200, self.server.app.index_html, "text/html; charset=utf-8")
            return
        if path == "/api/me":
            username = self._current_user()
            if username is None:
                self._error(401, "Giriş gerekli.")
            else:
                self._json(200, {"username": username})
            return
        if path == "/api/tasks":
            username = self._require_user()
            if username is None:
                return
            query = parse_qs(parsed.query)
            q = (query.get("q", [""])[0] or "").strip()
            status = (query.get("status", [""])[0] or "").strip()
            priority = (query.get("priority", [""])[0] or "").strip()
            if status and status not in STATUS_VALUES:
                self._error(400, "Geçersiz durum filtresi.")
                return
            if priority and priority not in PRIORITY_VALUES:
                self._error(400, "Geçersiz öncelik filtresi.")
                return
            conn = self.server.app.db()
            try:
                where = ["owner_id = (SELECT id FROM users WHERE username = ?)"]
                params = [username]
                if q:
                    where.append("(title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
                    escaped = q.replace("%", "\\%").replace("_", "\\_").replace("\\", "\\\\")
                    like = "%" + escaped.casefold() + "%"
                    params.extend([like, like])
                if status:
                    where.append("status = ?")
                    params.append(status)
                if priority:
                    where.append("priority = ?")
                    params.append(priority)
                sql = "SELECT id, title, description, status, priority FROM tasks WHERE " + " AND ".join(where) + " ORDER BY id DESC"
                rows = conn.execute(sql, params).fetchall()
                items = [self.server.app.task_to_dict(row) for row in rows]
                self._json(200, {"items": items, "total": len(items)})
            finally:
                conn.close()
            return
        if path == "/api/stats":
            username = self._require_user()
            if username is None:
                return
            conn = self.server.app.db()
            try:
                rows = conn.execute(
                    """SELECT status, COUNT(*) FROM tasks WHERE owner_id = (
                        SELECT id FROM users WHERE username = ?
                    ) GROUP BY status""",
                    (username,),
                ).fetchall()
                counts = {"todo": 0, "doing": 0, "done": 0}
                for status, count in rows:
                    if status in counts:
                        counts[status] = count
                total = sum(counts.values())
                self._json(200, {"total": total, "todo": counts["todo"], "doing": counts["doing"], "done": counts["done"]})
            finally:
                conn.close()
            return
        if path.startswith("/api/tasks/"):
            username = self._require_user()
            if username is None:
                return
            task_id = self._parse_task_id(path[len("/api/tasks/"):])
            if task_id is None:
                self._error(404, "Görev bulunamadı.")
                return
            row = self._task_by_id(username, task_id)
            if row is None:
                self._error(404, "Görev bulunamadı.")
                return
            self._json(200, self.server.app.task_to_dict(row))
            return
        if path.startswith("/api/"):
            self._error(404, "Kaynak bulunamadı.")
            return
        self._error(404, "Kaynak bulunamadı.")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/login":
            if not self._origin_ok():
                self._error(403, "İzin verilmeyen kaynak.")
                return
            payload = self._parse_json()
            if payload is None:
                return
            if not isinstance(payload, dict):
                self._error(400, "Giriş verisi bir nesne olmalı.")
                return
            username = payload.get("username")
            password = payload.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                self._error(400, "Kullanıcı adı ve parola metin olmalı.")
                return
            authenticated = self.server.app.authenticate(username, password)
            if authenticated is None:
                self._error(401, "Kullanıcı adı veya parola hatalı.")
                return
            token = self.server.app.sessions.create(authenticated)
            cookie = "session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (token, SESSION_TTL)
            self._json(200, {"username": authenticated}, [("Set-Cookie", cookie)])
            return
        if path == "/api/logout":
            if not self._origin_ok():
                self._error(403, "İzin verilmeyen kaynak.")
                return
            token = self._session_token()
            if token:
                self.server.app.sessions.delete(token)
            cookie = "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            self._json(200, {"ok": True}, [("Set-Cookie", cookie)])
            return
        if path == "/api/tasks":
            if not self._origin_ok():
                self._error(403, "İzin verilmeyen kaynak.")
                return
            username = self._require_user()
            if username is None:
                return
            payload = self._parse_json()
            if payload is None:
                return
            values, error = self.server.app.validate_task_payload(payload, require_title=True)
            if error:
                self._error(400, error)
                return
            conn = self.server.app.db()
            try:
                cur = conn.execute(
                    """INSERT INTO tasks (owner_id, title, description, status, priority, created_at)
                    VALUES ((SELECT id FROM users WHERE username = ?), ?, ?, ?, ?, ?)""",
                    (username, values["title"], values["description"], values["status"], values["priority"], time.time()),
                )
                conn.commit()
                task_id = cur.lastrowid
                row = conn.execute(
                    "SELECT id, title, description, status, priority FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                self._json(201, self.server.app.task_to_dict(row))
            finally:
                conn.close()
            return
        if path.startswith("/api/"):
            self._error(405, "Yöntem desteklenmiyor.")
            return
        self._error(404, "Kaynak bulunamadı.")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/tasks/"):
            if not self._origin_ok():
                self._error(403, "İzin verilmeyen kaynak.")
                return
            username = self._require_user()
            if username is None:
                return
            task_id = self._parse_task_id(path[len("/api/tasks/"):])
            if task_id is None:
                self._error(404, "Görev bulunamadı.")
                return
            row = self._task_by_id(username, task_id)
            if row is None:
                self._error(404, "Görev bulunamadı.")
                return
            payload = self._parse_json()
            if payload is None:
                return
            values, error = self.server.app.validate_task_payload(payload, require_title=True)
            if error:
                self._error(400, error)
                return
            conn = self.server.app.db()
            try:
                conn.execute(
                    """UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
                    WHERE id = ? AND owner_id = (SELECT id FROM users WHERE username = ?)""",
                    (values["title"], values["description"], values["status"], values["priority"], task_id, username),
                )
                conn.commit()
                updated = conn.execute(
                    "SELECT id, title, description, status, priority FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                self._json(200, self.server.app.task_to_dict(updated))
            finally:
                conn.close()
            return
        if path.startswith("/api/"):
            self._error(405, "Yöntem desteklenmiyor.")
            return
        self._error(404, "Kaynak bulunamadı.")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/tasks/"):
            if not self._origin_ok():
                self._error(403, "İzin verilmeyen kaynak.")
                return
            username = self._require_user()
            if username is None:
                return
            task_id = self._parse_task_id(path[len("/api/tasks/"):])
            if task_id is None:
                self._error(404, "Görev bulunamadı.")
                return
            row = self._task_by_id(username, task_id)
            if row is None:
                self._error(404, "Görev bulunamadı.")
                return
            conn = self.server.app.db()
            try:
                conn.execute(
                    "DELETE FROM tasks WHERE id = ? AND owner_id = (SELECT id FROM users WHERE username = ?)",
                    (task_id, username),
                )
                conn.commit()
                self._json(200, {"deleted": True})
            finally:
                conn.close()
            return
        if path.startswith("/api/"):
            self._error(405, "Yöntem desteklenmiyor.")
            return
        self._error(404, "Kaynak bulunamadı.")

    def do_HEAD(self):
        self.do_GET()

    def do_OPTIONS(self):
        self._error(405, "Yöntem desteklenmiyor.")

    def do_PATCH(self):
        self._error(405, "Yöntem desteklenmiyor.")

    def _parse_task_id(self, raw):
        try:
            value = int(raw)
        except Exception:
            return None
        if value <= 0:
            return None
        return value


def main():
    parser = argparse.ArgumentParser(description="Görev Masası")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", required=True)
    args = parser.parse_args()
    init_db(args.db)
    app = App(args.db, args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = app
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
