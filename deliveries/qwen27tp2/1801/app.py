import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(APP_DIR, "index.html")

STATUS_VALUES = ("todo", "doing", "done")
PRIORITY_VALUES = ("low", "normal", "high")

PBKDF2_ITER = 120000
SESSION_TTL = 86400
BODY_LIMIT = 16384


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITER)


def make_password_record(password):
    salt = secrets.token_bytes(16)
    digest = hash_password(password, salt)
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, digest_hex):
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    digest = hash_password(password, salt)
    return secrets.compare_digest(digest, expected)


def init_db(db_path):
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            salt TEXT NOT NULL,
            digest TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id);
        """
    )
    conn.commit()
    cur = conn.execute("SELECT COUNT(*) AS c FROM users")
    count = cur.fetchone()["c"]
    if count == 0:
        for username, password in (("zeka", "deneme-zeka-2026"), ("yapar", "deneme-yapar-2026")):
            salt_hex, digest_hex = make_password_record(password)
            conn.execute(
                "INSERT INTO users (username, salt, digest) VALUES (?, ?, ?)",
                (username, salt_hex, digest_hex),
            )
        conn.commit()
    return conn


class App:
    def __init__(self, db_path, host, port):
        self.db_path = db_path
        self.host = host
        self.port = port
        self.origin = "http://%s:%d" % (host, port)
        self.sessions = {}
        self.sessions_lock = threading.Lock()
        self.conn = init_db(db_path)
        self.conn_lock = threading.Lock()
        self.index_html = None
        try:
            with open(INDEX_PATH, "r", encoding="utf-8") as fh:
                self.index_html = fh.read()
        except OSError:
            self.index_html = "<!doctype html><html><body><h1>index.html bulunamadı</h1></body></html>"

    def db(self):
        with self.conn_lock:
            return self.conn

    def get_user_by_username(self, username):
        cur = self.db().execute("SELECT * FROM users WHERE username = ?", (username,))
        return cur.fetchone()

    def get_user_by_id(self, user_id):
        cur = self.db().execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()

    def create_session(self, user_id, username):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.sessions_lock:
            self.sessions[token] = {"user_id": user_id, "username": username, "expires": now + SESSION_TTL}
        return token

    def get_session(self, token):
        if not token:
            return None
        now = time.time()
        with self.sessions_lock:
            sess = self.sessions.get(token)
            if not sess:
                return None
            if sess["expires"] < now:
                self.sessions.pop(token, None)
                return None
            return sess

    def delete_session(self, token):
        if not token:
            return
        with self.sessions_lock:
            self.sessions.pop(token, None)

    def task_to_dict(self, row):
        return {
            "id": row["id"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "priority": row["priority"],
        }

    def validate_task_fields(self, data, require_title):
        if not isinstance(data, dict):
            return None, "Görev alanları bir nesne olmalı"
        allowed = {"title", "description", "status", "priority"}
        for key in data.keys():
            if key not in allowed:
                return None, "Bilinmeyen alan: %s" % key
        title = None
        if require_title or "title" in data:
            if "title" not in data:
                return None, "Başlık gerekli"
            t = data["title"]
            if isinstance(t, bool) or not isinstance(t, str):
                return None, "Başlık metin olmalı"
            t = t.strip()
            if len(t) < 1 or len(t) > 120:
                return None, "Başlık 1-120 karakter olmalı"
            title = t
        description = ""
        if "description" in data:
            d = data["description"]
            if isinstance(d, bool) or not isinstance(d, str):
                return None, "Açıklama metin olmalı"
            if len(d) > 1000:
                return None, "Açıklama en fazla 1000 karakter olmalı"
            description = d
        status = "todo"
        if "status" in data:
            s = data["status"]
            if isinstance(s, bool) or not isinstance(s, str) or s not in STATUS_VALUES:
                return None, "Geçersiz durum"
            status = s
        priority = "normal"
        if "priority" in data:
            p = data["priority"]
            if isinstance(p, bool) or not isinstance(p, str) or p not in PRIORITY_VALUES:
                return None, "Geçersiz öncelik"
            priority = p
        return {"title": title, "description": description, "status": status, "priority": priority}, None


class Handler(BaseHTTPRequestHandler):
    server_version = "GorevMasasi/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, content_type, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers:
                self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra_headers)

    def _error(self, status, message):
        self._json(status, {"error": message})

    def _read_body(self):
        length = self.headers.get("Content-Length")
        if length is None:
            return b""
        try:
            length = int(length)
        except (TypeError, ValueError):
            return None
        if length < 0 or length > BODY_LIMIT:
            return None
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _session_from_cookie(self):
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get("session")
        if not morsel:
            return None
        token = morsel.value
        return self.app.get_session(token)

    def _check_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        if origin != self.app.origin:
            self._error(403, "İzin verilmeyen kaynak")
            return False
        return True

    def _require_login(self):
        sess = self._session_from_cookie()
        if not sess:
            self._error(401, "Oturum gerekli")
            return None
        return sess

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self._json(200, {"ok": True})
            return
        if path == "/":
            self._send(200, self.app.index_html, "text/html; charset=utf-8")
            return
        if path == "/api/me":
            sess = self._session_from_cookie()
            if not sess:
                self._error(401, "Oturum gerekli")
                return
            self._json(200, {"username": sess["username"]})
            return
        if path == "/api/tasks":
            sess = self._require_login()
            if not sess:
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            q = (query.get("q", [""])[0] or "").strip()
            status = (query.get("status", [""])[0] or "").strip()
            priority = (query.get("priority", [""])[0] or "").strip()
            if status and status not in STATUS_VALUES:
                self._error(400, "Geçersiz durum filtresi")
                return
            if priority and priority not in PRIORITY_VALUES:
                self._error(400, "Geçersiz öncelik filtresi")
                return
            sql = "SELECT * FROM tasks WHERE owner_id = ?"
            params = [sess["user_id"]]
            if q:
                sql += " AND (title LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')"
                like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                params.append(like)
                params.append(like)
            if status:
                sql += " AND status = ?"
                params.append(status)
            if priority:
                sql += " AND priority = ?"
                params.append(priority)
            sql += " ORDER BY id DESC"
            rows = self.app.db().execute(sql, params).fetchall()
            items = [self.app.task_to_dict(r) for r in rows]
            self._json(200, {"items": items, "total": len(items)})
            return
        if path.startswith("/api/tasks/"):
            sess = self._require_login()
            if not sess:
                return
            rest = path[len("/api/tasks/"):]
            if "/" in rest:
                self._error(404, "Bulunamadı")
                return
            try:
                task_id = int(rest)
            except ValueError:
                self._error(404, "Bulunamadı")
                return
            row = self.app.db().execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, sess["user_id"])
            ).fetchone()
            if not row:
                self._error(404, "Bulunamadı")
                return
            self._json(200, self.app.task_to_dict(row))
            return
        if path == "/api/stats":
            sess = self._require_login()
            if not sess:
                return
            rows = self.app.db().execute(
                "SELECT status, COUNT(*) AS c FROM tasks WHERE owner_id = ? GROUP BY status", (sess["user_id"],)
            ).fetchall()
            counts = {"todo": 0, "doing": 0, "done": 0}
            total = 0
            for r in rows:
                counts[r["status"]] = r["c"]
                total += r["c"]
            self._json(200, {"total": total, "todo": counts["todo"], "doing": counts["doing"], "done": counts["done"]})
            return
        self._error(404, "Bulunamadı")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/login":
            if not self._check_origin():
                return
            raw = self._read_body()
            if raw is None:
                self._error(413, "İstek çok büyük")
                return
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                self._error(400, "Geçersiz JSON")
                return
            if not isinstance(data, dict):
                self._error(400, "Geçersiz JSON kökü")
                return
            username = data.get("username")
            password = data.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                self._error(400, "Kullanıcı adı ve parola metin olmalı")
                return
            user = self.app.get_user_by_username(username.strip())
            if not user or not verify_password(password, user["salt"], user["digest"]):
                self._error(401, "Kullanıcı adı veya parola hatalı")
                return
            token = self.app.create_session(user["id"], user["username"])
            cookie = "session=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (token, SESSION_TTL)
            self._json(200, {"username": user["username"]}, [("Set-Cookie", cookie)])
            return
        if path == "/api/logout":
            if not self._check_origin():
                return
            cookie_header = self.headers.get("Cookie")
            token = None
            if cookie_header:
                cookie = SimpleCookie()
                try:
                    cookie.load(cookie_header)
                    m = cookie.get("session")
                    if m:
                        token = m.value
                except Exception:
                    token = None
            if token:
                self.app.delete_session(token)
            self._json(200, {"ok": True}, [("Set-Cookie", "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")])
            return
        if path == "/api/tasks":
            if not self._check_origin():
                return
            sess = self._require_login()
            if not sess:
                return
            raw = self._read_body()
            if raw is None:
                self._error(413, "İstek çok büyük")
                return
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                self._error(400, "Geçersiz JSON")
                return
            if not isinstance(data, dict):
                self._error(400, "Geçersiz JSON kökü")
                return
            fields, err = self.app.validate_task_fields(data, require_title=True)
            if err:
                self._error(400, err)
                return
            cur = self.app.db().execute(
                "INSERT INTO tasks (owner_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?)",
                (sess["user_id"], fields["title"], fields["description"], fields["status"], fields["priority"]),
            )
            self.app.db().commit()
            task_id = cur.lastrowid
            row = self.app.db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            self._json(201, self.app.task_to_dict(row))
            return
        self._error(405, "Yöntem desteklenmiyor")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/tasks/"):
            if not self._check_origin():
                return
            sess = self._require_login()
            if not sess:
                return
            rest = path[len("/api/tasks/"):]
            if "/" in rest:
                self._error(404, "Bulunamadı")
                return
            try:
                task_id = int(rest)
            except ValueError:
                self._error(404, "Bulunamadı")
                return
            row = self.app.db().execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, sess["user_id"])
            ).fetchone()
            if not row:
                self._error(404, "Bulunamadı")
                return
            raw = self._read_body()
            if raw is None:
                self._error(413, "İstek çok büyük")
                return
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except Exception:
                self._error(400, "Geçersiz JSON")
                return
            if not isinstance(data, dict):
                self._error(400, "Geçersiz JSON kökü")
                return
            fields, err = self.app.validate_task_fields(data, require_title=True)
            if err:
                self._error(400, err)
                return
            self.app.db().execute(
                "UPDATE tasks SET title = ?, description = ?, status = ?, priority = ? WHERE id = ? AND owner_id = ?",
                (fields["title"], fields["description"], fields["status"], fields["priority"], task_id, sess["user_id"]),
            )
            self.app.db().commit()
            row = self.app.db().execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            self._json(200, self.app.task_to_dict(row))
            return
        self._error(405, "Yöntem desteklenmiyor")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/tasks/"):
            if not self._check_origin():
                return
            sess = self._require_login()
            if not sess:
                return
            rest = path[len("/api/tasks/"):]
            if "/" in rest:
                self._error(404, "Bulunamadı")
                return
            try:
                task_id = int(rest)
            except ValueError:
                self._error(404, "Bulunamadı")
                return
            row = self.app.db().execute(
                "SELECT * FROM tasks WHERE id = ? AND owner_id = ?", (task_id, sess["user_id"])
            ).fetchone()
            if not row:
                self._error(404, "Bulunamadı")
                return
            self.app.db().execute("DELETE FROM tasks WHERE id = ? AND owner_id = ?", (task_id, sess["user_id"]))
            self.app.db().commit()
            self._json(200, {"deleted": True})
            return
        self._error(405, "Yöntem desteklenmiyor")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--db", type=str, required=True)
    args = parser.parse_args()
    app = App(args.db, "127.0.0.1", args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    Handler.app = app
    print("Görev Masası http://127.0.0.1:%d" % args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.conn.close()


if __name__ == "__main__":
    main()
