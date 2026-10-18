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
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


BODY_LIMIT = 16384
SESSION_SECONDS = 43200
ITERATIONS = 200000
STATUSES = ('todo', 'doing', 'done')
PRIORITIES = ('low', 'normal', 'high')


class APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


def connect(path):
    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as connection:
        with connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                iterations INTEGER NOT NULL
            )''')
            connection.execute('''CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','done')),
                priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high'))
            )''')
            connection.execute('CREATE INDEX IF NOT EXISTS tasks_owner_id ON tasks(owner_id, id)')
            for username, password in (
                ('zeka', 'deneme-zeka-2026'),
                ('yapar', 'deneme-yapar-2026'),
            ):
                if connection.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone() is None:
                    salt = secrets.token_bytes(16)
                    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITERATIONS)
                    connection.execute(
                        'INSERT INTO users(username,salt,password_hash,iterations) VALUES(?,?,?,?)',
                        (username, salt, digest, ITERATIONS),
                    )


def validate_task(data):
    allowed = {'title', 'description', 'status', 'priority'}
    if set(data) - allowed:
        raise APIError(400, 'Bilinmeyen görev alanı.')
    title = data.get('title')
    description = data.get('description', '')
    status = data.get('status', 'todo')
    priority = data.get('priority', 'normal')
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise APIError(400, 'Başlık, boş olmayan en fazla 120 karakterlik bir metin olmalıdır.')
    if not isinstance(description, str) or len(description) > 1000:
        raise APIError(400, 'Açıklama en fazla 1000 karakterlik bir metin olmalıdır.')
    if not isinstance(status, str) or status not in STATUSES:
        raise APIError(400, 'Geçersiz durum.')
    if not isinstance(priority, str) or priority not in PRIORITIES:
        raise APIError(400, 'Geçersiz öncelik.')
    return title.strip(), description, status, priority


def task_dict(row):
    return {key: row[key] for key in ('id', 'title', 'description', 'status', 'priority')}


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, db_path, html):
        super().__init__(address, Handler)
        self.db_path = db_path
        self.html = html
        self.sessions = {}
        self.session_lock = threading.Lock()
        self.origin = 'http://127.0.0.1:' + str(self.server_address[1])

    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'GorevMasasi'
    sys_version = ''

    def log_message(self, format, *args):
        pass

    def reply(self, status, data, cookie=None, html=False):
        body = data if html else json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Connection', 'close')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(body)

    def read_body(self, required=True):
        if self.headers.get('Transfer-Encoding'):
            raise APIError(400, 'Desteklenmeyen istek gövdesi.')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) > 1:
            raise APIError(400, 'Geçersiz gövde uzunluğu.')
        raw_length = lengths[0] if lengths else '0'
        if not raw_length.isascii() or not raw_length.isdigit():
            raise APIError(400, 'Geçersiz gövde uzunluğu.')
        try:
            length = int(raw_length)
        except ValueError:
            raise APIError(413, 'İstek gövdesi çok büyük.')
        if length > BODY_LIMIT:
            raise APIError(413, 'İstek gövdesi çok büyük.')
        if length == 0 and not required:
            return {}
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise APIError(400, 'Eksik istek gövdesi.')
        try:
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError('duplicate')
                    result[key] = value
                return result

            def reject_constant(value):
                raise ValueError('constant')

            data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError(400, 'Geçerli bir JSON nesnesi gönderin.')
        if not isinstance(data, dict):
            raise APIError(400, 'JSON gövdesi bir nesne olmalıdır.')
        return data

    def token(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            item = cookies.get('session')
            return item.value if item else None
        except CookieError:
            return None

    def identity(self):
        token = self.token()
        with self.server.session_lock:
            session = self.server.sessions.get(token)
            if session and session[2] > time.time():
                return session[0], session[1]
            if session:
                self.server.sessions.pop(token, None)
        raise APIError(401, 'Oturum açmanız gerekiyor.')

    def login(self):
        data = self.read_body()
        username = data.get('username')
        password = data.get('password')
        if not isinstance(username, str) or not isinstance(password, str):
            raise APIError(400, 'Kullanıcı adı ve parola metin olmalıdır.')
        with closing(connect(self.server.db_path)) as connection:
            user = connection.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        salt = user['salt'] if user else bytes(16)
        iterations = user['iterations'] if user else ITERATIONS
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
        expected = user['password_hash'] if user else bytes(32)
        if not hmac.compare_digest(candidate, expected) or user is None:
            raise APIError(401, 'Kullanıcı adı veya parola hatalı.')
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.server.session_lock:
            expired = [key for key, value in self.server.sessions.items() if value[2] <= now]
            for key in expired:
                del self.server.sessions[key]
            self.server.sessions[token] = (user['id'], user['username'], now + SESSION_SECONDS)
        self.reply(200, {'username': user['username']},
                   'session=' + token + '; HttpOnly; SameSite=Strict; Path=/; Max-Age=' + str(SESSION_SECONDS))

    def logout(self):
        self.read_body(required=False)
        with self.server.session_lock:
            self.server.sessions.pop(self.token(), None)
        self.reply(200, {'ok': True}, 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT')

    def list_tasks(self, owner_id, query):
        params = parse_qs(query, keep_blank_values=True)
        q = params.get('q', [''])[-1]
        status = params.get('status', [''])[-1]
        priority = params.get('priority', [''])[-1]
        if status and status not in STATUSES:
            raise APIError(400, 'Geçersiz durum filtresi.')
        if priority and priority not in PRIORITIES:
            raise APIError(400, 'Geçersiz öncelik filtresi.')
        sql = 'SELECT id,title,description,status,priority FROM tasks WHERE owner_id = ?'
        values = [owner_id]
        if status:
            sql += ' AND status = ?'
            values.append(status)
        if priority:
            sql += ' AND priority = ?'
            values.append(priority)
        sql += ' ORDER BY id DESC'
        with closing(connect(self.server.db_path)) as connection:
            rows = connection.execute(sql, values).fetchall()
        needle = q.casefold()
        items = [task_dict(row) for row in rows
                 if not needle or needle in row['title'].casefold() or needle in row['description'].casefold()]
        self.reply(200, {'items': items, 'total': len(items)})

    def stats(self, owner_id):
        result = {'total': 0, 'todo': 0, 'doing': 0, 'done': 0}
        with closing(connect(self.server.db_path)) as connection:
            rows = connection.execute('SELECT status,COUNT(*) AS n FROM tasks WHERE owner_id = ? GROUP BY status', (owner_id,)).fetchall()
        for row in rows:
            result[row['status']] = row['n']
            result['total'] += row['n']
        self.reply(200, result)

    def create_task(self, owner_id):
        fields = validate_task(self.read_body())
        with closing(connect(self.server.db_path)) as connection:
            with connection:
                cursor = connection.execute('INSERT INTO tasks(owner_id,title,description,status,priority) VALUES(?,?,?,?,?)', (owner_id, *fields))
                row = connection.execute('SELECT id,title,description,status,priority FROM tasks WHERE id = ? AND owner_id = ?', (cursor.lastrowid, owner_id)).fetchone()
        self.reply(201, task_dict(row))

    def task_operation(self, owner_id, task_id):
        if self.command == 'PUT':
            fields = validate_task(self.read_body())
        elif self.command == 'DELETE':
            self.read_body(required=False)
        with closing(connect(self.server.db_path)) as connection:
            with connection:
                row = connection.execute('SELECT id,title,description,status,priority FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id)).fetchone()
                if row is None:
                    raise APIError(404, 'Görev bulunamadı.')
                if self.command == 'DELETE':
                    connection.execute('DELETE FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
                    result = {'deleted': True}
                elif self.command == 'PUT':
                    connection.execute('UPDATE tasks SET title = ?,description = ?,status = ?,priority = ? WHERE id = ? AND owner_id = ?', (*fields, task_id, owner_id))
                    updated = connection.execute('SELECT id,title,description,status,priority FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id)).fetchone()
                    result = task_dict(updated)
                else:
                    result = task_dict(row)
        self.reply(200, result)

    def dispatch(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if self.command not in ('GET', 'HEAD', 'OPTIONS'):
            origins = self.headers.get_all('Origin', [])
            if origins and (len(origins) != 1 or origins[0] != self.server.origin):
                raise APIError(403, 'İstek kaynağına izin verilmiyor.')
        match = re.fullmatch(r'/api/tasks/([1-9][0-9]*)', path)
        methods = {
            '/': ('GET',),
            '/health': ('GET',),
            '/api/login': ('POST',),
            '/api/logout': ('POST',),
            '/api/me': ('GET',),
            '/api/tasks': ('GET', 'POST'),
            '/api/stats': ('GET',),
        }
        allowed = ('GET', 'PUT', 'DELETE') if match else methods.get(path)
        if allowed is None:
            if path.startswith('/api/') and self.command not in ('GET', 'POST', 'PUT', 'DELETE'):
                raise APIError(405, 'Bu HTTP yöntemi desteklenmiyor.')
            raise APIError(404, 'Adres bulunamadı.')
        if self.command not in allowed:
            raise APIError(405, 'Bu HTTP yöntemi desteklenmiyor.')
        if path == '/':
            self.reply(200, self.server.html, html=True)
        elif path == '/health':
            self.reply(200, {'ok': True})
        elif path == '/api/login':
            self.login()
        elif path == '/api/logout':
            self.logout()
        else:
            owner_id, username = self.identity()
            if path == '/api/me':
                self.reply(200, {'username': username})
            elif path == '/api/stats':
                self.stats(owner_id)
            elif path == '/api/tasks':
                if self.command == 'GET':
                    self.list_tasks(owner_id, parsed.query)
                else:
                    self.create_task(owner_id)
            elif match:
                digits = match.group(1)
                if len(digits) > 19 or int(digits) > 9223372036854775807:
                    raise APIError(404, 'Görev bulunamadı.')
                self.task_operation(owner_id, int(digits))

    def handle_request(self):
        try:
            self.dispatch()
        except APIError as error:
            self.reply(error.status, {'error': error.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self.reply(500, {'error': 'İşlem tamamlanamadı. Lütfen tekrar deneyin.'})

    do_GET = handle_request
    do_POST = handle_request
    do_PUT = handle_request
    do_DELETE = handle_request
    do_PATCH = handle_request
    do_HEAD = handle_request
    do_OPTIONS = handle_request
    do_TRACE = handle_request
    do_CONNECT = handle_request

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.handle_request
        raise AttributeError(name)


def main():
    parser = argparse.ArgumentParser(description='Görev Masası')
    parser.add_argument('--port', required=True, type=int)
    parser.add_argument('--db', required=True)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port 1 ile 65535 arasında olmalıdır.')
    db_path = Path(args.db)
    if not db_path.is_absolute():
        parser.error('--db mutlak bir yol olmalıdır.')
    html = Path(__file__).resolve().with_name('index.html').read_bytes()
    initialize(db_path)
    server = AppServer(('127.0.0.1', args.port), db_path, html)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
