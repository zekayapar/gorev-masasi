import argparse, json, os, re, secrets, sqlite3, sys, threading, time, urllib.parse, hashlib
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Constants
MAX_JSON_SIZE = 16384
SESSION_COOKIE_NAME = 'session'
SESSION_TTL = 24 * 3600  # seconds
PASSWORD_ITERATIONS = 150_000
PASSWORD_ALGO = 'sha256'

# In‑memory session store
sessions = {}
session_lock = threading.Lock()

def init_db(db_path):
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            iterations INTEGER NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
        )
    ''')
    conn.commit()
    return conn

def hash_password(password: str, salt: bytes = None, iterations: int = PASSWORD_ITERATIONS):
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac(PASSWORD_ALGO, password.encode('utf-8'), salt, iterations)
    return dk.hex(), salt.hex(), iterations

def verify_password(stored_hash: str, stored_salt: str, stored_iter: int, password: str) -> bool:
    dk = hashlib.pbkdf2_hmac(PASSWORD_ALGO, password.encode('utf-8'), bytes.fromhex(stored_salt), stored_iter)
    return dk.hex() == stored_hash

def seed_demo_accounts(conn):
    demos = [
        ('zeka', 'deneme-zeka-2026'),
        ('yapar', 'deneme-yapar-2026')
    ]
    cur = conn.cursor()
    for username, pwd in demos:
        cur.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cur.fetchone() is None:
            hash_hex, salt_hex, it = hash_password(pwd)
            cur.execute('INSERT INTO users (username, password_hash, salt, iterations) VALUES (?,?,?,?)',
                        (username, hash_hex, salt_hex, it))
    conn.commit()

def parse_cookies(header):
    if not header:
        return {}
    cookie = SimpleCookie()
    cookie.load(header)
    return {k: v.value for k, v in cookie.items()}

def set_cookie(handler, name, value, max_age=None):
    cookie = SimpleCookie()
    cookie[name] = value
    cookie[name]['httponly'] = True
    cookie[name]['samesite'] = 'Strict'
    cookie[name]['path'] = '/'
    if max_age is not None:
        cookie[name]['max-age'] = str(max_age)
    handler.send_header('Set-Cookie', cookie.output(header=''))

def clean_expired_sessions():
    now = time.time()
    with session_lock:
        expired = [k for k, v in sessions.items() if v['expires'] <= now]
        for k in expired:
            del sessions[k]

def create_session(username):
    token = secrets.token_urlsafe(32)
    expires = time.time() + SESSION_TTL
    with session_lock:
        sessions[token] = {'username': username, 'expires': expires}
    return token

def get_session_username(handler):
    clean_expired_sessions()
    cookies = parse_cookies(handler.headers.get('Cookie'))
    token = cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    with session_lock:
        sess = sessions.get(token)
        if sess and sess['expires'] > time.time():
            return sess['username']
    return None

def invalidate_session(handler):
    cookies = parse_cookies(handler.headers.get('Cookie'))
    token = cookies.get(SESSION_COOKIE_NAME)
    if token:
        with session_lock:
            sessions.pop(token, None)
        set_cookie(handler, SESSION_COOKIE_NAME, '', max_age=0)

def json_response(handler, data, status=HTTPStatus.OK):
    payload = json.dumps(data).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)

def error_response(handler, message, status=HTTPStatus.BAD_REQUEST):
    json_response(handler, {'error': message}, status)

class TaskManagerHandler(BaseHTTPRequestHandler):
    server_version = 'TaskMgr/1.0'
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/health':
            json_response(self, {'ok': True})
        elif parsed.path == '/':
            self.serve_index()
        elif parsed.path.startswith('/api/'):
            self.handle_api_get(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self.handle_api_mutation(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self.handle_api_mutation(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self.handle_api_mutation(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    # ---------- helpers ----------
    def serve_index(self):
        try:
            with open('index.html', 'rb') as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Unable to read index.html")

    def read_json(self):
        length = self.headers.get('Content-Length')
        if length is None:
            error_response(self, 'Missing Content-Length', HTTPStatus.LENGTH_REQUIRED)
            return None
        try:
            length = int(length)
        except ValueError:
            error_response(self, 'Invalid Content-Length', HTTPStatus.BAD_REQUEST)
            return None
        if length > MAX_JSON_SIZE:
            error_response(self, 'Payload too large', HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            error_response(self, 'Malformed JSON', HTTPStatus.BAD_REQUEST)
            return None
        except Exception:
            error_response(self, 'Error reading request body', HTTPStatus.BAD_REQUEST)
            return None

    def origin_allowed(self):
        origin = self.headers.get('Origin')
        if origin is None:
            return True
        expected = f'http://127.0.0.1:{self.server.server_port}'
        return origin == expected

    # ---------- API routing ----------
    def handle_api_get(self, parsed):
        path = parsed.path
        if path == '/api/me':
            username = get_session_username(self)
            if username:
                json_response(self, {'username': username})
            else:
                error_response(self, 'Unauthorized', HTTPStatus.UNAUTHORIZED)
        elif path.startswith('/api/tasks'):
            self.handle_tasks_get(parsed)
        elif path == '/api/stats':
            self.handle_stats()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def handle_api_mutation(self, parsed):
        if not self.origin_allowed():
            error_response(self, 'Forbidden origin', HTTPStatus.FORBIDDEN)
            return
        path = parsed.path
        if path == '/api/login' and self.command == 'POST':
            self.handle_login()
        elif path == '/api/logout' and self.command == 'POST':
            self.handle_logout()
        elif path.startswith('/api/tasks'):
            self.handle_tasks_mutation(parsed)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    # ---------- Auth ----------
    def handle_login(self):
        data = self.read_json()
        if data is None:
            return
        username = data.get('username')
        password = data.get('password')
        if not isinstance(username, str) or not isinstance(password, str):
            error_response(self, 'Invalid credentials', HTTPStatus.BAD_REQUEST)
            return
        cur = self.server.db.cursor()
        cur.execute('SELECT password_hash, salt, iterations FROM users WHERE username = ?', (username,))
        row = cur.fetchone()
        if row and verify_password(row[0], row[1], row[2], password):
            token = create_session(username)
            self.send_response(HTTPStatus.OK)
            set_cookie(self, SESSION_COOKIE_NAME, token)
            self.send_header('Content-Type', 'application/json')
            payload = json.dumps({'username': username}).encode('utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            error_response(self, 'Invalid username or password', HTTPStatus.UNAUTHORIZED)

    def handle_logout(self):
        username = get_session_username(self)
        if not username:
            error_response(self, 'Unauthorized', HTTPStatus.UNAUTHORIZED)
            return
        invalidate_session(self)
        json_response(self, {'ok': True})

    # ---------- Tasks ----------
    VALID_STATUS = {'todo', 'doing', 'done'}
    VALID_PRIORITY = {'low', 'normal', 'high'}

    def validate_task_input(self, data, require_title=True):
        if not isinstance(data, dict):
            return 'JSON object expected'
        allowed = {'title', 'description', 'status', 'priority'}
        if any(k not in allowed for k in data.keys()):
            return 'Unexpected field in payload'
        title = data.get('title')
        if require_title:
            if not isinstance(title, str):
                return 'Title must be a string'
            title = title.strip()
            if not (1 <= len(title) <= 120):
                return 'Title length must be 1..120'
        else:
            if title is not None:
                if not isinstance(title, str):
                    return 'Title must be a string'
                title = title.strip()
                if not (1 <= len(title) <= 120):
                    return 'Title length must be 1..120'
        description = data.get('description', '')
        if description is not None and not isinstance(description, str):
            return 'Description must be a string'
        if description is not None and len(description) > 1000:
            return 'Description too long'
        status = data.get('status', 'todo')
        if status not in self.VALID_STATUS:
            return 'Invalid status'
        priority = data.get('priority', 'normal')
        if priority not in self.VALID_PRIORITY:
            return 'Invalid priority'
        cleaned = {
            'title': title.strip() if isinstance(title, str) else None,
            'description': description or '',
            'status': status,
            'priority': priority,
        }
        return cleaned

    def handle_tasks_get(self, parsed):
        username = get_session_username(self)
        if not username:
            error_response(self, 'Unauthorized', HTTPStatus.UNAUTHORIZED)
            return
        cur = self.server.db.cursor()
        cur.execute('SELECT id FROM users WHERE username = ?', (username,))
        owner_id = cur.fetchone()[0]
        path = parsed.path
        m = re.fullmatch(r'/api/tasks/(\d+)', path)
        if m:
            task_id = int(m.group(1))
            cur.execute('SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
            row = cur.fetchone()
            if row:
                task = {'id': row[0], 'title': row[1], 'description': row[2], 'status': row[3], 'priority': row[4]}
                json_response(self, task)
            else:
                error_response(self, 'Task not found', HTTPStatus.NOT_FOUND)
            return
        qs = urllib.parse.parse_qs(parsed.query)
        q = qs.get('q', [''])[0].strip().casefold()
        status = qs.get('status', [None])[0]
        priority = qs.get('priority', [None])[0]
        if status and status not in self.VALID_STATUS:
            error_response(self, 'Invalid status filter', HTTPStatus.BAD_REQUEST)
            return
        if priority and priority not in self.VALID_PRIORITY:
            error_response(self, 'Invalid priority filter', HTTPStatus.BAD_REQUEST)
            return
        sql = 'SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?'
        params = [owner_id]
        if q:
            sql += ' AND (LOWER(title) LIKE ? OR LOWER(description) LIKE ?)'
            like = f'%{q}%'
            params.extend([like, like])
        if status:
            sql += ' AND status = ?'
            params.append(status)
        if priority:
            sql += ' AND priority = ?'
            params.append(priority)
        sql += ' ORDER BY id DESC'
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        items = [{'id': r[0], 'title': r[1], 'description': r[2], 'status': r[3], 'priority': r[4]} for r in rows]
        json_response(self, {'items': items, 'total': len(items)})

    def handle_tasks_mutation(self, parsed):
        username = get_session_username(self)
        if not username:
            error_response(self, 'Unauthorized', HTTPStatus.UNAUTHORIZED)
            return
        cur = self.server.db.cursor()
        cur.execute('SELECT id FROM users WHERE username = ?', (username,))
        owner_id = cur.fetchone()[0]
        path = parsed.path
        if path == '/api/tasks' and self.command == 'POST':
            data = self.read_json()
            if data is None:
                return
            validated = self.validate_task_input(data, require_title=True)
            if isinstance(validated, str):
                error_response(self, validated, HTTPStatus.BAD_REQUEST)
                return
            cur.execute('''INSERT INTO tasks (owner_id, title, description, status, priority)
                           VALUES (?,?,?,?,?)''',
                        (owner_id, validated['title'], validated['description'], validated['status'], validated['priority']))
            self.server.db.commit()
            task_id = cur.lastrowid
            task = {'id': task_id, **validated}
            json_response(self, task, HTTPStatus.CREATED)
            return
        m = re.fullmatch(r'/api/tasks/(\d+)', path)
        if m and self.command == 'PUT':
            task_id = int(m.group(1))
            cur.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
            if not cur.fetchone():
                error_response(self, 'Task not found', HTTPStatus.NOT_FOUND)
                return
            data = self.read_json()
            if data is None:
                return
            validated = self.validate_task_input(data, require_title=True)
            if isinstance(validated, str):
                error_response(self, validated, HTTPStatus.BAD_REQUEST)
                return
            cur.execute('''UPDATE tasks SET title = ?, description = ?, status = ?, priority = ? WHERE id = ?''',
                        (validated['title'], validated['description'], validated['status'], validated['priority'], task_id))
            self.server.db.commit()
            task = {'id': task_id, **validated}
            json_response(self, task)
            return
        if m and self.command == 'DELETE':
            task_id = int(m.group(1))
            cur.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
            if not cur.fetchone():
                error_response(self, 'Task not found', HTTPStatus.NOT_FOUND)
                return
            cur.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
            self.server.db.commit()
            json_response(self, {'deleted': True})
            return
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "Method not allowed for this endpoint")

    def handle_stats(self):
        username = get_session_username(self)
        if not username:
            error_response(self, 'Unauthorized', HTTPStatus.UNAUTHORIZED)
            return
        cur = self.server.db.cursor()
        cur.execute('SELECT id FROM users WHERE username = ?', (username,))
        owner_id = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*), SUM(CASE WHEN status = "todo" THEN 1 ELSE 0 END),\n                            SUM(CASE WHEN status = "doing" THEN 1 ELSE 0 END),\n                            SUM(CASE WHEN status = "done" THEN 1 ELSE 0 END)\n                     FROM tasks WHERE owner_id = ?', (owner_id,))
        total, todo, doing, done = cur.fetchone()
        stats = {
            'total': total or 0,
            'todo': todo or 0,
            'doing': doing or 0,
            'done': done or 0,
        }
        json_response(self, stats)

    def log_message(self, format, *args):
        return

def run_server(port, db_path):
    db = init_db(db_path)
    seed_demo_accounts(db)
    server_address = ('127.0.0.1', port)
    httpd = ThreadingHTTPServer(server_address, TaskManagerHandler)
    httpd.db = db
    print(f'Server listening on http://127.0.0.1:{port}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('Shutting down')
    finally:
        db.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Turkish Personal Task Manager')
    parser.add_argument('--port', type=int, required=True, help='Port number to bind')
    parser.add_argument('--db', type=str, required=True, help='Absolute path to SQLite database file')
    args = parser.parse_args()
    if not os.path.isabs(args.db):
        print('Error: --db must be an absolute path', file=sys.stderr)
        sys.exit(1)
    run_server(args.port, args.db)
