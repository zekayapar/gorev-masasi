import argparse
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import closing
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ITERATIONS = 260000
BODY_LIMIT = 16384
SESSION_LIFETIME = 12 * 60 * 60
STATUSES = {'todo', 'doing', 'done'}
PRIORITIES = {'low', 'normal', 'high'}
TASK_COLUMNS = 'id, title, description, status, priority'


class APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def connect(path):
    db = sqlite3.connect(str(path), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, salt BLOB NOT NULL, password_hash BLOB NOT NULL)')
        db.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL REFERENCES users(id), title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'todo', priority TEXT NOT NULL DEFAULT 'normal')")
        db.execute('CREATE INDEX IF NOT EXISTS tasks_owner_id ON tasks(owner_id, id)')
        for username, password in [('zeka', 'deneme-zeka-2026'), ('yapar', 'deneme-yapar-2026')]:
            if db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone() is None:
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
                db.execute('INSERT INTO users (username, salt, password_hash) VALUES (?, ?, ?)', (username, salt, digest))


def task_fields(data):
    if set(data) - {'title', 'description', 'status', 'priority'}:
        raise APIError(400, 'Bilinmeyen görev alanı.')
    title = data.get('title')
    description = data.get('description', '')
    status = data.get('status', 'todo')
    priority = data.get('priority', 'normal')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise APIError(400, 'Başlık 1–120 karakter olmalıdır.')
    if not isinstance(description, str) or len(description) > 1000:
        raise APIError(400, 'Açıklama en fazla 1000 karakter olmalıdır.')
    if not isinstance(status, str) or status not in STATUSES:
        raise APIError(400, 'Geçersiz durum.')
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise APIError(400, 'Geçersiz öncelik.')
    return title.strip(), description, status, priority


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, db_path, html):
        super().__init__(('127.0.0.1', port), Handler)
        self.db_path = db_path
        self.html = html
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        self.sessions = {}
        self.session_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, data, cookie=None, html=False):
        payload = data if html else json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        self.respond(code, {'error': 'Desteklenmeyen yöntem.' if code == 501 else 'İstek işlenemedi.'})

    def token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            return cookie['session'].value if 'session' in cookie else None
        except Exception:
            return None

    def identity(self):
        token = self.token()
        now = time.monotonic()
        with self.server.session_lock:
            session = self.server.sessions.get(token)
            if session is not None and session[2] > now:
                return session[0], session[1]
            if session is not None:
                self.server.sessions.pop(token, None)
        raise APIError(401, 'Lütfen giriş yapın.')

    def read_json(self):
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Geçersiz istek gövdesi.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]+', lengths[0]):
            raise APIError(400, 'Geçersiz istek gövdesi.')
        if len(lengths[0]) > 10:
            raise APIError(413, 'İstek gövdesi çok büyük.')
        size = int(lengths[0])
        if size > BODY_LIMIT:
            raise APIError(413, 'İstek gövdesi çok büyük.')
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise APIError(400, 'Eksik istek gövdesi.')
        try:
            data = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, 'Geçerli bir JSON nesnesi gönderin.') from None
        if not isinstance(data, dict):
            raise APIError(400, 'Geçerli bir JSON nesnesi gönderin.')
        return data

    def dispatch(self):
        try:
            self.route()
        except APIError as error:
            self.close_connection = True
            self.respond(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.respond(500, {'error': 'İşlem tamamlanamadı. Lütfen tekrar deneyin.'})

    def route(self):
        self.connection.settimeout(20)
        url = urlsplit(self.path)
        path = unquote(url.path)
        method = self.command
        api = path == '/api' or path.startswith('/api/')
        if method not in {'GET', 'HEAD', 'OPTIONS'}:
            origins = self.headers.get_all('Origin', [])
            if origins and (len(origins) != 1 or origins[0] != self.server.origin):
                raise APIError(403, 'Bu kaynaktan yapılan isteğe izin verilmiyor.')
        if api and method not in {'GET', 'POST', 'PUT', 'DELETE'}:
            raise APIError(405, 'Desteklenmeyen yöntem.')
        if path == '/health':
            if method != 'GET':
                raise APIError(405, 'Desteklenmeyen yöntem.')
            self.respond(200, {'ok': True})
            return
        if path == '/':
            if method != 'GET':
                raise APIError(405, 'Desteklenmeyen yöntem.')
            self.respond(200, self.server.html, html=True)
            return
        if path == '/api/login':
            if method != 'POST':
                raise APIError(405, 'Desteklenmeyen yöntem.')
            data = self.read_json()
            if set(data) != {'username', 'password'} or not isinstance(data['username'], str) or not isinstance(data['password'], str):
                raise APIError(400, 'Kullanıcı adı ve parola gereklidir.')
            with closing(connect(self.server.db_path)) as db:
                user = db.execute('SELECT id, username, salt, password_hash FROM users WHERE username = ?', (data['username'],)).fetchone()
            salt = user['salt'] if user else b'unknown-user-salt'
            digest = hashlib.pbkdf2_hmac('sha256', data['password'].encode('utf-8'), salt, ITERATIONS)
            if user is None or not hmac.compare_digest(digest, user['password_hash']):
                raise APIError(401, 'Kullanıcı adı veya parola hatalı.')
            token = secrets.token_urlsafe(32)
            now = time.monotonic()
            with self.server.session_lock:
                expired = [key for key, value in self.server.sessions.items() if value[2] <= now]
                for key in expired:
                    del self.server.sessions[key]
                self.server.sessions[token] = (user['id'], user['username'], now + SESSION_LIFETIME)
            self.respond(200, {'username': user['username']}, 'session=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=' + str(SESSION_LIFETIME))
            return
        if path == '/api/logout':
            if method != 'POST':
                raise APIError(405, 'Desteklenmeyen yöntem.')
            with self.server.session_lock:
                self.server.sessions.pop(self.token(), None)
            self.respond(200, {'ok': True}, 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT')
            return
        if path == '/api/me':
            if method != 'GET':
                raise APIError(405, 'Desteklenmeyen yöntem.')
            _, username = self.identity()
            self.respond(200, {'username': username})
            return
        if path == '/api/stats':
            if method != 'GET':
                raise APIError(405, 'Desteklenmeyen yöntem.')
            owner_id, _ = self.identity()
            with closing(connect(self.server.db_path)) as db:
                rows = db.execute('SELECT status, COUNT(*) AS n FROM tasks WHERE owner_id = ? GROUP BY status', (owner_id,)).fetchall()
            stats = {'total': 0, 'todo': 0, 'doing': 0, 'done': 0}
            for row in rows:
                stats[row['status']] = row['n']
                stats['total'] += row['n']
            self.respond(200, stats)
            return
        if path == '/api/tasks' or path.startswith('/api/tasks/'):
            owner_id, _ = self.identity()
            if path == '/api/tasks':
                if method == 'GET':
                    query = parse_qs(url.query, keep_blank_values=True)
                    q = query.get('q', [''])[-1].casefold()
                    status = query.get('status', [''])[-1]
                    priority = query.get('priority', [''])[-1]
                    if status and status not in STATUSES:
                        raise APIError(400, 'Geçersiz durum filtresi.')
                    if priority and priority not in PRIORITIES:
                        raise APIError(400, 'Geçersiz öncelik filtresi.')
                    with closing(connect(self.server.db_path)) as db:
                        rows = db.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE owner_id = ? ORDER BY id DESC', (owner_id,)).fetchall()
                    items = [dict(row) for row in rows if (not status or row['status'] == status) and (not priority or row['priority'] == priority) and (not q or q in row['title'].casefold() or q in row['description'].casefold())]
                    self.respond(200, {'items': items, 'total': len(items)})
                    return
                if method == 'POST':
                    fields = task_fields(self.read_json())
                    with closing(connect(self.server.db_path)) as db, db:
                        cursor = db.execute('INSERT INTO tasks (owner_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?)', (owner_id, *fields))
                        task = dict(db.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE id = ? AND owner_id = ?', (cursor.lastrowid, owner_id)).fetchone())
                    self.respond(201, task)
                    return
                raise APIError(405, 'Desteklenmeyen yöntem.')
            match = re.fullmatch(r'/api/tasks/([0-9]+)', path)
            if match is None or len(match[1]) > 19:
                raise APIError(404, 'Görev bulunamadı.')
            task_id = int(match[1])
            if not 1 <= task_id <= 9223372036854775807:
                raise APIError(404, 'Görev bulunamadı.')
            if method not in {'GET', 'PUT', 'DELETE'}:
                raise APIError(405, 'Desteklenmeyen yöntem.')
            with closing(connect(self.server.db_path)) as db, db:
                row = db.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id)).fetchone()
                if row is None:
                    raise APIError(404, 'Görev bulunamadı.')
                if method == 'GET':
                    result = dict(row)
                elif method == 'PUT':
                    fields = task_fields(self.read_json())
                    db.execute('UPDATE tasks SET title = ?, description = ?, status = ?, priority = ? WHERE id = ? AND owner_id = ?', (*fields, task_id, owner_id))
                    result = dict(db.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id)).fetchone())
                else:
                    db.execute('DELETE FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
                    result = {'deleted': True}
            self.respond(200, result)
            return
        raise APIError(404, 'Adres bulunamadı.')

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch
    do_OPTIONS = dispatch
    do_HEAD = dispatch
    do_TRACE = dispatch
    do_CONNECT = dispatch

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)


def main():
    parser = argparse.ArgumentParser(description='Görev Masası')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port 1–65535 arasında olmalıdır.')
    if not args.db.is_absolute():
        parser.error('--db mutlak bir yol olmalıdır.')
    html = Path(__file__).with_name('index.html').read_bytes()
    initialize(args.db)
    server = AppServer(args.port, args.db, html)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
