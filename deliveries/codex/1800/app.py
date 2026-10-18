import argparse
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BODY_LIMIT = 16384
SESSION_TTL = 12 * 60 * 60
ITERATIONS = 240000
STATUSES = ('todo', 'doing', 'done')
PRIORITIES = ('low', 'normal', 'high')
TASK_FIELDS = {'title', 'description', 'status', 'priority'}
TASK_COLUMNS = 'id, title, description, status, priority'


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)


def initialize(database):
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database))
    try:
        with connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL
            )''')
            connection.execute('''CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo',
                priority TEXT NOT NULL DEFAULT 'normal'
            )''')
            connection.execute('CREATE INDEX IF NOT EXISTS tasks_owner ON tasks(owner_id, id)')
            for username, password in (
                ('zeka', 'deneme-zeka-2026'),
                ('yapar', 'deneme-yapar-2026'),
            ):
                if connection.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone() is None:
                    salt = secrets.token_bytes(32)
                    connection.execute(
                        'INSERT INTO users(username, salt, password_hash) VALUES (?, ?, ?)',
                        (username, salt, password_hash(password, salt)),
                    )
    finally:
        connection.close()


def validate_task(data):
    if set(data) - TASK_FIELDS:
        raise APIError(400, 'Bilinmeyen görev alanı.')
    title = data.get('title')
    if not isinstance(title, str):
        raise APIError(400, 'Başlık metin olmalıdır.')
    title = title.strip()
    if not 1 <= len(title) <= 120:
        raise APIError(400, 'Başlık 1–120 karakter olmalıdır.')
    description = data.get('description', '')
    status = data.get('status', 'todo')
    priority = data.get('priority', 'normal')
    if not isinstance(description, str) or len(description) > 1000:
        raise APIError(400, 'Açıklama en fazla 1000 karakterlik metin olmalıdır.')
    if not isinstance(status, str) or status not in STATUSES:
        raise APIError(400, 'Geçersiz durum.')
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise APIError(400, 'Geçersiz öncelik.')
    for value in (title, description):
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            raise APIError(400, 'Geçersiz metin kodlaması.') from None
    return title, description, status, priority


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, database, page):
        super().__init__(address, Handler)
        self.database = database
        self.page = page
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.dummy_salt = secrets.token_bytes(32)


class Handler(BaseHTTPRequestHandler):
    server_version = 'GorevMasasi'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {'error': 'İstek işlenemedi.'})

    def respond(self, status, data, cookie=None, html=False):
        payload = data if html else json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if cookie is not None:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Desteklenmeyen istek gövdesi.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1:
            raise APIError(400, 'Geçerli bir JSON gövdesi gerekli.')
        try:
            length = int(lengths[0])
        except ValueError:
            raise APIError(400, 'Geçersiz gövde uzunluğu.') from None
        if length < 0:
            raise APIError(400, 'Geçersiz gövde uzunluğu.')
        if length > BODY_LIMIT:
            raise APIError(413, 'İstek gövdesi çok büyük.')
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, 'Eksik istek gövdesi.')
        try:
            data = json.loads(raw.decode('utf-8'), parse_constant=self.invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, 'Geçersiz JSON.') from None
        if not isinstance(data, dict):
            raise APIError(400, 'JSON gövdesi bir nesne olmalıdır.')
        return data

    @staticmethod
    def invalid_constant(value):
        raise ValueError('Invalid JSON constant')

    def token(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            item = cookies.get('session')
            return item.value if item is not None else None
        except Exception:
            return None

    def identity(self):
        token = self.token()
        now = time.time()
        with self.server.session_lock:
            session = self.server.sessions.get(token)
            if session and session['expires'] > now:
                return session
            if session:
                self.server.sessions.pop(token, None)
        raise APIError(401, 'Lütfen giriş yapın.')

    def dispatch(self):
        try:
            self.route()
        except APIError as error:
            self.respond(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            self.respond(500, {'error': 'İşlem tamamlanamadı. Lütfen tekrar deneyin.'})

    def route(self):
        url = urlsplit(self.path)
        path = url.path
        method = self.command
        if method not in ('GET', 'HEAD', 'OPTIONS'):
            origins = self.headers.get_all('Origin', [])
            if origins and (len(origins) != 1 or origins[0] != self.server.origin):
                raise APIError(403, 'Bu kaynaktan gelen isteğe izin verilmiyor.')
        if path == '/health' and method == 'GET':
            self.respond(200, {'ok': True})
            return
        if path == '/' and method == 'GET':
            self.respond(200, self.server.page, html=True)
            return
        if not (path == '/api' or path.startswith('/api/')):
            raise APIError(404, 'Bulunamadı.')

        allowed = {
            '/api/login': ('POST',),
            '/api/logout': ('POST',),
            '/api/me': ('GET',),
            '/api/stats': ('GET',),
            '/api/tasks': ('GET', 'POST'),
        }
        task_match = re.fullmatch(r'/api/tasks/([0-9]+)', path)
        methods = ('GET', 'PUT', 'DELETE') if task_match else allowed.get(path)
        if methods is None:
            if method not in ('GET', 'POST', 'PUT', 'DELETE'):
                raise APIError(405, 'HTTP yöntemi desteklenmiyor.')
            raise APIError(404, 'Bulunamadı.')
        if method not in methods:
            raise APIError(405, 'HTTP yöntemi desteklenmiyor.')

        if path == '/api/logout':
            with self.server.session_lock:
                self.server.sessions.pop(self.token(), None)
            self.respond(200, {'ok': True}, 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            return

        connection = sqlite3.connect(str(self.server.database), timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            if path == '/api/login':
                data = self.body()
                if set(data) != {'username', 'password'} or not all(isinstance(data.get(key), str) for key in ('username', 'password')):
                    raise APIError(400, 'Kullanıcı adı ve parola metin olarak gerekli.')
                username, password = data['username'], data['password']
                try:
                    username.encode('utf-8')
                    password.encode('utf-8')
                except UnicodeEncodeError:
                    raise APIError(400, 'Geçersiz metin kodlaması.') from None
                user = connection.execute('SELECT id, username, salt, password_hash FROM users WHERE username = ?', (username,)).fetchone()
                calculated = password_hash(password, user['salt'] if user else self.server.dummy_salt)
                expected = user['password_hash'] if user else bytes(32)
                if not hmac.compare_digest(calculated, expected) or user is None:
                    raise APIError(401, 'Kullanıcı adı veya parola yanlış.')
                token = secrets.token_urlsafe(32)
                now = time.time()
                with self.server.session_lock:
                    expired = [key for key, value in self.server.sessions.items() if value['expires'] <= now]
                    for key in expired:
                        del self.server.sessions[key]
                    self.server.sessions[token] = {'id': user['id'], 'username': user['username'], 'expires': now + SESSION_TTL}
                self.respond(200, {'username': user['username']}, 'session=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=' + str(SESSION_TTL))
                return

            user = self.identity()
            owner = user['id']
            if path == '/api/me':
                self.respond(200, {'username': user['username']})
                return
            if path == '/api/stats':
                counts = {'total': 0, 'todo': 0, 'doing': 0, 'done': 0}
                rows = connection.execute('SELECT status, COUNT(*) AS n FROM tasks WHERE owner_id = ? GROUP BY status', (owner,))
                for row in rows:
                    counts[row['status']] = row['n']
                    counts['total'] += row['n']
                self.respond(200, counts)
                return
            if path == '/api/tasks':
                if method == 'POST':
                    values = validate_task(self.body())
                    with connection:
                        cursor = connection.execute('INSERT INTO tasks(owner_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?)', (owner,) + values)
                        row = connection.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE id = ? AND owner_id = ?', (cursor.lastrowid, owner)).fetchone()
                    self.respond(201, dict(row))
                    return
                filters = parse_qs(url.query, keep_blank_values=True, encoding='utf-8', errors='replace')
                for key, choices in (('status', STATUSES), ('priority', PRIORITIES)):
                    if any(value and value not in choices for value in filters.get(key, [])):
                        raise APIError(400, 'Geçersiz filtre.')
                query = filters.get('q', [''])[0].casefold()
                status = filters.get('status', [''])[0]
                priority = filters.get('priority', [''])[0]
                rows = connection.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE owner_id = ? ORDER BY id DESC', (owner,))
                items = []
                for row in rows:
                    task = dict(row)
                    if status and task['status'] != status:
                        continue
                    if priority and task['priority'] != priority:
                        continue
                    if query and query not in task['title'].casefold() and query not in task['description'].casefold():
                        continue
                    items.append(task)
                self.respond(200, {'items': items, 'total': len(items)})
                return

            # A string parameter avoids integer overflow for arbitrarily large URL IDs.
            task_id = task_match.group(1)
            values = validate_task(self.body()) if method == 'PUT' else None
            with connection:
                row = connection.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner)).fetchone()
                if row is None:
                    raise APIError(404, 'Görev bulunamadı.')
                if method == 'PUT':
                    connection.execute('UPDATE tasks SET title = ?, description = ?, status = ?, priority = ? WHERE id = ? AND owner_id = ?', values + (task_id, owner))
                    row = connection.execute('SELECT ' + TASK_COLUMNS + ' FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner)).fetchone()
                elif method == 'DELETE':
                    connection.execute('DELETE FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner))
            self.respond(200, {'deleted': True} if method == 'DELETE' else dict(row))
        finally:
            connection.close()

    do_GET = dispatch
    do_POST = dispatch
    do_PUT = dispatch
    do_DELETE = dispatch
    do_PATCH = dispatch
    do_HEAD = dispatch
    do_OPTIONS = dispatch
    do_TRACE = dispatch
    do_CONNECT = dispatch

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.dispatch
        raise AttributeError(name)


def main():
    parser = argparse.ArgumentParser(description='Görev Masası yerel sunucusu')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port 1–65535 aralığında olmalıdır.')
    if not args.db.is_absolute():
        parser.error('--db mutlak bir dosya yolu olmalıdır.')
    database = args.db.resolve()
    page = Path(__file__).resolve().with_name('index.html').read_bytes()
    initialize(database)
    server = AppServer(('127.0.0.1', args.port), database, page)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
