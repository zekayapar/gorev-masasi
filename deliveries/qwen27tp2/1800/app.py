import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(APP_DIR, "index.html")

PBKDF2_ITER = 120000
SESSION_TTL = 24 * 60 * 60
BODY_LIMIT = 16384

VALID_STATUS = ("todo", "doing", "done")
VALID_PRIORITY = ("low", "normal", "high")

ALLOWED_CREATE_KEYS = {"title", "description", "status", "priority"}
ALLOWED_UPDATE_KEYS = {"title", "description", "status", "priority"}

DEMO_ACCOUNTS = [
    ("zeka", "deneme-zeka-2026"),
    ("yapar", "deneme-yapar-2026"),
]


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)


def verify_password(password, salt, expected):
    candidate = hash_password(password, salt)
    return hmac.compare_digest(candidate, expected)


def init_db(db_path):
    parent = os.path.dirname(os.path.abspath(db_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
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
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            created_at TEXT NOT NULL,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id)")
    conn.commit()
    for username, password in DEMO_ACCOUNTS:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            salt = secrets.token_bytes(16)
            pw_hash = hash_password(password, salt)
            conn.execute(
                "INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)",
                (username, salt, pw_hash),
            )
    conn.commit()
    return conn


class App:
    def __init__(self, db_path, port):
        self.db_path = db_path
        self.port = port
        self.sessions = {}
        self._lock = threading.Lock()
        self.conn = init_db(db_path)
        self.origin = "http://127.0.0.1:%d" % port

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def _check_origin(self, headers):
        origin = headers.get("Origin")
        if origin is None:
            return True
        return origin == self.origin

    def _read_body(self, headers):
        length = headers.get("Content-Length")
        if length is None:
            return b""
        try:
            n = int(length)
        except (TypeError, ValueError):
            return b""
        if n < 0:
            return b""
        if n > BODY_LIMIT:
            return None
        return self.rfile.read(n)

    def _parse_json(self, raw):
        if raw is None:
            return None, "body_too_large"
        if not raw:
            return None, "empty_body"
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, "invalid_json"
        return data, None

    def _get_session_user(self, headers):
        cookie_header = headers.get("Cookie")
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
        token = morsel.value
        if not token:
            return None
        with self._lock:
            entry = self.sessions.get(token)
            if entry is None:
                return None
            if entry["expires"] < time.time():
                del self.sessions[token]
                return None
            return entry["user"]

    def _create_session(self, user):
        token = secrets.token_urlsafe(32)
        with self._lock:
            self.sessions[token] = {"user": user, "expires": time.time() + SESSION_TTL}
        return token

    def _invalidate_session(self, token):
        with self._lock:
            self.sessions.pop(token, None)

    def _task_row_to_dict(self, row):
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
        }

    def _validate_task_fields(self, data, require_title):
        if not isinstance(data, dict):
            return None, "invalid_body"
        allowed = ALLOWED_UPDATE_KEYS if require_title else ALLOWED_CREATE_KEYS
        for key in data.keys():
            if key not in allowed:
                return None, "unknown_field"
        title = data.get("title")
        if require_title or "title" in data:
            if not isinstance(title, str):
                return None, "invalid_title"
            title = title.strip()
            if len(title) < 1 or len(title) > 120:
                return None, "invalid_title"
        else:
            title = None
        description = data.get("description", "")
        if description is None:
            description = ""
        if not isinstance(description, str):
            return None, "invalid_description"
        if len(description) > 1000:
            return None, "invalid_description"
        status = data.get("status", "todo")
        if status is None:
            status = "todo"
        if not isinstance(status, str) or status not in VALID_STATUS:
            return None, "invalid_status"
        priority = data.get("priority", "normal")
        if priority is None:
            priority = "normal"
        if not isinstance(priority, str) or priority not in VALID_PRIORITY:
            return None, "invalid_priority"
        return {
            "title": title,
            "description": description,
            "status": status,
            "priority": priority,
        }, None

    def _handle_health(self):
        return 200, {"ok": True}, "application/json"

    def _handle_index(self):
        try:
            with open(INDEX_PATH, "rb") as f:
                content = f.read()
        except Exception:
            return 500, {"error": "internal_error"}, "application/json"
        return 200, content, "text/html; charset=utf-8"

    def _handle_login(self, headers):
        if not self._check_origin(headers):
            return 403, {"error": "forbidden"}, "application/json"
        raw = self._read_body(headers)
        data, err = self._parse_json(raw)
        if err == "body_too_large":
            return 413, {"error": "body_too_large"}, "application/json"
        if err or not isinstance(data, dict):
            return 400, {"error": "invalid_body"}, "application/json"
        username = data.get("username")
        password = data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return 400, {"error": "invalid_body"}, "application/json"
        row = self.conn.execute(
            "SELECT id, username, salt, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if row is None:
            return 401, {"error": "invalid_credentials"}, "application/json"
        if not verify_password(password, row["salt"], row["password_hash"]):
            return 401, {"error": "invalid_credentials"}, "application/json"
        token = self._create_session(row)
        return 200, {"username": row["username"]}, "application/json", token

    def _handle_me(self, headers):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        return 200, {"username": user["username"]}, "application/json"

    def _handle_logout(self, headers):
        cookie_header = headers.get("Cookie")
        token = None
        if cookie_header:
            cookie = SimpleCookie()
            try:
                cookie.load(cookie_header)
            except Exception:
                cookie = None
            if cookie is not None:
                morsel = cookie.get("session")
                if morsel is not None:
                    token = morsel.value
        if token:
            self._invalidate_session(token)
        return 200, {"ok": True}, "application/json", "logout"

    def _handle_create_task(self, headers):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        if not self._check_origin(headers):
            return 403, {"error": "forbidden"}, "application/json"
        raw = self._read_body(headers)
        data, err = self._parse_json(raw)
        if err == "body_too_large":
            return 413, {"error": "body_too_large"}, "application/json"
        if err or not isinstance(data, dict):
            return 400, {"error": "invalid_body"}, "application/json"
        fields, verr = self._validate_task_fields(data, require_title=True)
        if verr:
            return 400, {"error": verr}, "application/json"
        cur = self.conn.execute(
            """INSERT INTO tasks (owner_id, title, description, status, priority, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user["id"],
                fields["title"],
                fields["description"],
                fields["status"],
                fields["priority"],
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ),
        )
        self.conn.commit()
        task_id = cur.lastrowid
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return 201, self._task_row_to_dict(row), "application/json"

    def _handle_get_task(self, headers, task_id):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user["id"])
        ).fetchone()
        if row is None:
            return 404, {"error": "not_found"}, "application/json"
        return 200, self._task_row_to_dict(row), "application/json"

    def _handle_update_task(self, headers, task_id):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        if not self._check_origin(headers):
            return 403, {"error": "forbidden"}, "application/json"
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user["id"])
        ).fetchone()
        if row is None:
            return 404, {"error": "not_found"}, "application/json"
        raw = self._read_body(headers)
        data, err = self._parse_json(raw)
        if err == "body_too_large":
            return 413, {"error": "body_too_large"}, "application/json"
        if err or not isinstance(data, dict):
            return 400, {"error": "invalid_body"}, "application/json"
        fields, verr = self._validate_task_fields(data, require_title=True)
        if verr:
            return 400, {"error": verr}, "application/json"
        self.conn.execute(
            """UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
               WHERE id = ? AND owner_id = ?""",
            (
                fields["title"],
                fields["description"],
                fields["status"],
                fields["priority"],
                task_id,
                user["id"],
            ),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return 200, self._task_row_to_dict(row), "application/json"

    def _handle_delete_task(self, headers, task_id):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        if not self._check_origin(headers):
            return 403, {"error": "forbidden"}, "application/json"
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user["id"])
        ).fetchone()
        if row is None:
            return 404, {"error": "not_found"}, "application/json"
        self.conn.execute("DELETE FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user["id"]))
        self.conn.commit()
        return 200, {"deleted": True}, "application/json"

    def _handle_list_tasks(self, headers, query):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        q = (query.get("q") or [""])[0]
        status = (query.get("status") or [""])[0]
        priority = (query.get("priority") or [""])[0]
        if status and status not in VALID_STATUS:
            return 400, {"error": "invalid_status"}, "application/json"
        if priority and priority not in VALID_PRIORITY:
            return 400, {"error": "invalid_priority"}, "application/json"
        sql = "SELECT * FROM tasks WHERE owner_id = ?"
        params = [user["id"]]
        if q:
            sql += " AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = "%" + escaped + "%"
            params.append(like)
            params.append(like)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if priority:
            sql += " AND priority = ?"
            params.append(priority)
        sql += " ORDER BY id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        items = [self._task_row_to_dict(r) for r in rows]
        return 200, {"items": items, "total": len(items)}, "application/json"

    def _handle_stats(self, headers):
        user = self._get_session_user(headers)
        if user is None:
            return 401, {"error": "unauthorized"}, "application/json"
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM tasks WHERE owner_id = ? GROUP BY status",
            (user["id"],),
        ).fetchall()
        counts = {"todo": 0, "doing": 0, "done": 0}
        total = 0
        for r in rows:
            counts[r["status"]] = r["c"]
            total += r["c"]
        return 200, {
            "total": total,
            "todo": counts["todo"],
            "doing": counts["doing"],
            "done": counts["done"],
        }, "application/json"

    def _route(self, method, path, headers, query):
        if path == "/health":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}, "application/json"
            return self._handle_health()
        if path == "/":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}, "application/json"
            return self._handle_index()
        if path == "/api/login":
            if method != "POST":
                return 405, {"error": "method_not_allowed"}, "application/json"
            return self._handle_login(headers)
        if path == "/api/me":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}, "application/json"
            return self._handle_me(headers)
        if path == "/api/logout":
            if method != "POST":
                return 405, {"error": "method_not_allowed"}, "application/json"
            return self._handle_logout(headers)
        if path == "/api/tasks":
            if method == "GET":
                return self._handle_list_tasks(headers, query)
            if method == "POST":
                return self._handle_create_task(headers)
            return 405, {"error": "method_not_allowed"}, "application/json"
        if path == "/api/stats":
            if method != "GET":
                return 405, {"error": "method_not_allowed"}, "application/json"
            return self._handle_stats(headers)
        if path.startswith("/api/tasks/"):
            rest = path[len("/api/tasks/"):]
            if "/" in rest:
                return 404, {"error": "not_found"}, "application/json"
            try:
                task_id = int(rest)
            except (TypeError, ValueError):
                return 404, {"error": "not_found"}, "application/json"
            if task_id <= 0:
                return 404, {"error": "not_found"}, "application/json"
            if method == "GET":
                return self._handle_get_task(headers, task_id)
            if method == "PUT":
                return self._handle_update_task(headers, task_id)
            if method == "DELETE":
                return self._handle_delete_task(headers, task_id)
            return 405, {"error": "method_not_allowed"}, "application/json"
        return 404, {"error": "not_found"}, "application/json"


class Handler(BaseHTTPRequestHandler):
    server_version = "GorevMasagi/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type, extra_headers=None):
        if isinstance(body, (dict, list)):
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _dispatch(self, method):
        try:
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query, keep_blank_values=True)
            headers = self.headers
            app = self.server.app
            result = app._route(method, path, headers, query)
            status = result[0]
            body = result[1]
            content_type = result[2]
            extra = None
            if len(result) > 3:
                extra = result[3]
            if extra == "logout":
                extra_headers = {
                    "Set-Cookie": "session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
                }
            elif isinstance(extra, str) and extra:
                extra_headers = {
                    "Set-Cookie": "session=%s; Path=/; HttpOnly; SameSite=Strict" % extra
                }
            else:
                extra_headers = None
            self._send(status, body, content_type, extra_headers)
        except Exception:
            try:
                self._send(500, {"error": "internal_error"}, "application/json")
            except Exception:
                pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_OPTIONS(self):
        self._dispatch("OPTIONS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", type=str, required=True)
    args = parser.parse_args()
    app = App(args.db, args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.app = app
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()


if __name__ == "__main__":
    main()
