import argparse, json, os, re, secrets, sqlite3, sys, time, urllib.parse, hashlib, hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer

# Global state
DB_CONN = None
SESSIONS = {}  # session_id -> {'username': str, 'expires': float}
PORT = None

# Constants
MAX_JSON_SIZE = 16384
SESSION_COOKIE_NAME = 'session'
SESSION_TTL = 24 * 3600  # seconds

# ---------- Utility functions ----------

def json_response(handler, status, obj):
    data = json.dumps(obj).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)

def error_json(handler, status, message):
    json_response(handler, status, {'error': message})

def read_json(handler):
    length = handler.headers.get('Content-Length')
    if length is None:
        error_json(handler, HTTPStatus.LENGTH_REQUIRED, 'Content-Length required')
        return None
    try:
        length = int(length)
    except ValueError:
        error_json(handler, HTTPStatus.BAD_REQUEST, 'Invalid Content-Length')
        return None
    if length > MAX_JSON_SIZE:
        error_json(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, 'Payload too large')
        return None
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        error_json(handler, HTTPStatus.BAD_REQUEST, 'Malformed JSON')
        return None

def parse_cookies(handler):
    cookie = handler.headers.get('Cookie')
    if not cookie:
        return {}
    parts = cookie.split(';')
    result = {}
    for part in parts:
        if '=' in part:
            k, v = part.strip().split('=', 1)
            result[k] = v
    return result

def set_session_cookie(handler, session_id):
    cookie = f"{SESSION_COOKIE_NAME}={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}"
    handler.send_header('Set-Cookie', cookie)

def clear_session_cookie(handler):
    cookie = f"{SESSION_COOKIE_NAME}=deleted; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    handler.send_header('Set-Cookie', cookie)

def get_session(handler):
    cookies = parse_cookies(handler)
    sid = cookies.get(SESSION_COOKIE_NAME)
    if not sid:
        return None
    sess = SESSIONS.get(sid)
    if not sess:
        return None
    if sess['expires'] < time.time():
        del SESSIONS[sid]
        return None
    return sid, sess

def require_login(handler):
    sess = get_session(handler)
    if not sess:
        error_json(handler, HTTPStatus.UNAUTHORIZED, 'Authentication required')
        return None
    return sess[1]['username']

def check_origin(handler):
    if handler.command in ('POST', 'PUT', 'DELETE'):
        origin = handler.headers.get('Origin')
        if origin and origin != f'http://127.0.0.1:{PORT}':
            error_json(handler, HTTPStatus.FORBIDDEN, 'Invalid Origin')
            return False
    return True

def hash_password(password, salt=None, iterations=150000):
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, dklen=32)
    return salt, iterations, dk

def verify_password(stored_salt, stored_iter, stored_hash, password):
    _, _, dk = hash_password(password, stored_salt, stored_iter)
    return hmac.compare_digest(dk, stored_hash)

# ---------- Database helpers ----------

def init_db(db_path):
    global DB_CONN
    need_seed = not os.path.exists(db_path)
    DB_CONN = sqlite3.connect(db_path, check_same_thread=False)
    DB_CONN.row_factory = sqlite3.Row
    cur = DB_CONN.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash BLOB NOT NULL,
            salt BLOB NOT NULL,
            iterations INTEGER NOT NULL
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )
    ''')
    DB_CONN.commit()
    seed_demo_accounts()

def seed_demo_accounts():
    demos = [
        ('zeka', 'deneme-zeka-2026'),
        ('yapar', 'deneme-yapar-2026')
    ]
    cur = DB_CONN.cursor()
    for username, password in demos:
        cur.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cur.fetchone():
            continue
        salt, it, phash = hash_password(password)
        cur.execute('INSERT INTO users (username, password_hash, salt, iterations) VALUES (?,?,?,?)',
                    (username, phash, salt, it))
    DB_CONN.commit()

def get_user(username):
    cur = DB_CONN.cursor()
    cur.execute('SELECT * FROM users WHERE username = ?', (username,))
    return cur.fetchone()

def get_user_by_id(uid):
    cur = DB_CONN.cursor()
    cur.execute('SELECT * FROM users WHERE id = ?', (uid,))
    return cur.fetchone()

def create_task(owner_id, data):
    cur = DB_CONN.cursor()
    cur.execute('''
        INSERT INTO tasks (owner_id, title, description, status, priority)
        VALUES (?,?,?,?,?)
    ''', (owner_id, data['title'], data.get('description',''), data.get('status','todo'), data.get('priority','normal')))
    DB_CONN.commit()
    task_id = cur.lastrowid
    return get_task(owner_id, task_id)

def get_task(owner_id, task_id):
    cur = DB_CONN.cursor()
    cur.execute('SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
    row = cur.fetchone()
    return dict(row) if row else None

def update_task(owner_id, task_id, data):
    cur = DB_CONN.cursor()
    cur.execute('''
        UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
        WHERE id = ? AND owner_id = ?
    ''', (data['title'], data.get('description',''), data.get('status','todo'), data.get('priority','normal'), task_id, owner_id))
    DB_CONN.commit()
    return get_task(owner_id, task_id)

def delete_task(owner_id, task_id):
    cur = DB_CONN.cursor()
    cur.execute('DELETE FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
    DB_CONN.commit()
    return cur.rowcount > 0

def list_tasks(owner_id, q=None, status=None, priority=None):
    cur = DB_CONN.cursor()
    sql = 'SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?'
    params = [owner_id]
    if status:
        sql += ' AND status = ?'
        params.append(status)
    if priority:
        sql += ' AND priority = ?'
        params.append(priority)
    sql += ' ORDER BY id DESC'
    cur.execute(sql, params)
    rows = cur.fetchall()
    tasks = [dict(row) for row in rows]
    if q:
        q_cf = q.casefold()
        filtered = []
        for t in tasks:
            if q_cf in t['title'].casefold() or q_cf in t['description'].casefold():
                filtered.append(t)
        tasks = filtered
    return tasks

def task_stats(owner_id):
    cur = DB_CONN.cursor()
    cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ?', (owner_id,))
    total = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (owner_id, 'todo'))
    todo = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (owner_id, 'doing'))
    doing = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (owner_id, 'done'))
    done = cur.fetchone()[0]
    return {'total': total, 'todo': todo, 'doing': doing, 'done': done}

# ---------- Request Handler ----------
class TaskHandler(BaseHTTPRequestHandler):
    server_version = "TaskMgr/1.0"

    def do_GET(self):
        if not check_origin(self):
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == '/health':
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        if path == '/':
            self.serve_index()
            return
        if path.startswith('/api/'):
            self.handle_api_get(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self):
        if not check_origin(self):
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/'):
            self.handle_api_post(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_PUT(self):
        if not check_origin(self):
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/'):
            self.handle_api_put(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_DELETE(self):
        if not check_origin(self):
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path.startswith('/api/'):
            self.handle_api_delete(parsed)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    # ----- Unsupported methods -----
    def do_PATCH(self):
        self.method_not_allowed()
    def do_OPTIONS(self):
        self.method_not_allowed()
    def do_HEAD(self):
        self.method_not_allowed()
    def do_CONNECT(self):
        self.method_not_allowed()
    def do_TRACE(self):
        self.method_not_allowed()

    def method_not_allowed(self):
        if self.path.startswith('/api/'):
            error_json(self, HTTPStatus.METHOD_NOT_ALLOWED, 'Method not allowed')
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    # ----- Static -----
    def serve_index(self):
        try:
            base_dir = os.path.abspath(os.path.dirname(__file__))
            with open(os.path.join(base_dir, 'index.html'), 'rb') as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, 'Failed to load index.html')

    # ----- API routing -----
    def handle_api_get(self, parsed):
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == '/api/me':
            username = require_login(self)
            if username:
                json_response(self, HTTPStatus.OK, {'username': username})
            return
        if path == '/api/tasks':
            username = require_login(self)
            if not username:
                return
            user = get_user(username)
            q = query.get('q', [None])[0]
            status = query.get('status', [None])[0]
            priority = query.get('priority', [None])[0]
            if status and status not in ('todo','doing','done'):
                error_json(self, HTTPStatus.BAD_REQUEST, 'Invalid status filter')
                return
            if priority and priority not in ('low','normal','high'):
                error_json(self, HTTPStatus.BAD_REQUEST, 'Invalid priority filter')
                return
            items = list_tasks(user['id'], q, status, priority)
            json_response(self, HTTPStatus.OK, {'items': items, 'total': len(items)})
            return
        m = re.fullmatch(r'/api/tasks/(\d+)', path)
        if m:
            username = require_login(self)
            if not username:
                return
            task_id = int(m.group(1))
            user = get_user(username)
            task = get_task(user['id'], task_id)
            if not task:
                error_json(self, HTTPStatus.NOT_FOUND, 'Task not found')
                return
            json_response(self, HTTPStatus.OK, task)
            return
        if path == '/api/stats':
            username = require_login(self)
            if not username:
                return
            user = get_user(username)
            stats = task_stats(user['id'])
            json_response(self, HTTPStatus.OK, stats)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def handle_api_post(self, parsed):
        path = parsed.path
        if path == '/api/login':
            data = read_json(self)
            if data is None:
                return
            username = data.get('username')
            password = data.get('password')
            if not isinstance(username, str) or not isinstance(password, str):
                error_json(self, HTTPStatus.BAD_REQUEST, 'Invalid credentials format')
                return
            user = get_user(username)
            if not user or not verify_password(user['salt'], user['iterations'], user['password_hash'], password):
                error_json(self, HTTPStatus.UNAUTHORIZED, 'Invalid username or password')
                return
            session_id = secrets.token_urlsafe(32)
            SESSIONS[session_id] = {'username': username, 'expires': time.time() + SESSION_TTL}
            self.send_response(HTTPStatus.OK)
            set_session_cookie(self, session_id)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'username': username}).encode('utf-8'))
            return
        if path == '/api/logout':
            sess = get_session(self)
            if sess:
                sid = sess[0]
                SESSIONS.pop(sid, None)
                self.send_response(HTTPStatus.OK)
                clear_session_cookie(self)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                error_json(self, HTTPStatus.UNAUTHORIZED, 'Not logged in')
            return
        if path == '/api/tasks':
            username = require_login(self)
            if not username:
                return
            data = read_json(self)
            if data is None:
                return
            valid, err = validate_task_input(data, creation=True)
            if not valid:
                error_json(self, HTTPStatus.BAD_REQUEST, err)
                return
            user = get_user(username)
            task = create_task(user['id'], data)
            json_response(self, HTTPStatus.CREATED, task)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def handle_api_put(self, parsed):
        path = parsed.path
        m = re.fullmatch(r'/api/tasks/(\d+)', path)
        if not m:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        username = require_login(self)
        if not username:
            return
        task_id = int(m.group(1))
        data = read_json(self)
        if data is None:
            return
        valid, err = validate_task_input(data, creation=False)
        if not valid:
            error_json(self, HTTPStatus.BAD_REQUEST, err)
            return
        user = get_user(username)
        existing = get_task(user['id'], task_id)
        if not existing:
            error_json(self, HTTPStatus.NOT_FOUND, 'Task not found')
            return
        task = update_task(user['id'], task_id, data)
        json_response(self, HTTPStatus.OK, task)

    def handle_api_delete(self, parsed):
        path = parsed.path
        m = re.fullmatch(r'/api/tasks/(\d+)', path)
        if not m:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
            return
        username = require_login(self)
        if not username:
            return
        task_id = int(m.group(1))
        user = get_user(username)
        existing = get_task(user['id'], task_id)
        if not existing:
            error_json(self, HTTPStatus.NOT_FOUND, 'Task not found')
            return
        success = delete_task(user['id'], task_id)
        if success:
            json_response(self, HTTPStatus.OK, {'deleted': True})
        else:
            error_json(self, HTTPStatus.INTERNAL_SERVER_ERROR, 'Deletion failed')

    # ----- Override logging -----
    def log_message(self, format, *args):
        return

# ---------- Validation ----------

def validate_task_input(data, creation=True):
    if not isinstance(data, dict):
        return False, 'JSON object required'
    allowed = {'title', 'description', 'status', 'priority'}
    extra = set(data.keys()) - allowed
    if extra:
        return False, f'Unexpected fields: {", ".join(extra)}'
    title = data.get('title')
    if not isinstance(title, str):
        return False, 'title must be a string'
    title = title.strip()
    if not (1 <= len(title) <= 120):
        return False, 'title length must be 1..120'
    data['title'] = title
    desc = data.get('description', '')
    if not isinstance(desc, str):
        return False, 'description must be a string'
    if len(desc) > 1000:
        return False, 'description too long'
    data['description'] = desc
    status = data.get('status', 'todo')
    if status not in ('todo', 'doing', 'done'):
        return False, 'invalid status'
    data['status'] = status
    priority = data.get('priority', 'normal')
    if priority not in ('low', 'normal', 'high'):
        return False, 'invalid priority'
    data['priority'] = priority
    return True, None

# ---------- Main ----------

def main():
    parser = argparse.ArgumentParser(description='Task manager')
    parser.add_argument('--port', type=int, required=True, help='Port number')
    parser.add_argument('--db', type=str, required=True, help='Path to SQLite DB')
    args = parser.parse_args()
    global PORT
    PORT = args.port
    init_db(os.path.abspath(args.db))
    server_address = ('127.0.0.1', PORT)
    httpd = HTTPServer(server_address, TaskHandler)
    print(f'Serving on http://127.0.0.1:{PORT}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if DB_CONN:
            DB_CONN.close()

if __name__ == '__main__':
    main()
