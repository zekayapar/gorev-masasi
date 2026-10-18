import argparse, json, os, sys, sqlite3, hashlib, secrets, datetime, http.server, urllib.parse, http.cookies

# Constants
MAX_JSON_SIZE = 16384
SESSION_TTL = datetime.timedelta(hours=24)
PBKDF2_ITERATIONS = 200000
ALLOWED_ORIGIN_TEMPLATE = 'http://127.0.0.1:{}'

# Global state (populated in main)
DB_PATH = None
PORT = None
SESSIONS = {}  # session_token -> {'username': str, 'expires': datetime}

# ---------- Database helpers ----------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash BLOB NOT NULL,
            salt BLOB NOT NULL
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'normal',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        )
    ''')
    conn.commit()
    # Seed demo accounts if missing
    demos = [
        ('zeka', 'deneme-zeka-2026'),
        ('yapar', 'deneme-yapar-2026')
    ]
    for username, password in demos:
        cur.execute('SELECT id FROM users WHERE username = ?', (username,))
        if cur.fetchone() is None:
            salt = secrets.token_bytes(16)
            pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
            cur.execute('INSERT INTO users (username, password_hash, salt) VALUES (?,?,?)',
                        (username, pwd_hash, salt))
    conn.commit()
    conn.close()

def get_user_by_username(username):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE username = ?', (username,))
    row = cur.fetchone()
    conn.close()
    return row

def get_user_by_id(user_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

# ---------- Session helpers ----------

def create_session(username):
    token = secrets.token_urlsafe(32)
    expires = datetime.datetime.utcnow() + SESSION_TTL
    SESSIONS[token] = {'username': username, 'expires': expires}
    return token

def get_session(token):
    data = SESSIONS.get(token)
    if not data:
        return None
    if data['expires'] < datetime.datetime.utcnow():
        del SESSIONS[token]
        return None
    return data

def invalidate_session(token):
    SESSIONS.pop(token, None)

# ---------- Request handling ----------
class TaskHandler(http.server.BaseHTTPRequestHandler):
    server_version = "TaskManager/1.0"

    def _set_headers(self, code=200, content_type='application/json', extra_headers=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()

    def _parse_json(self):
        length = self.headers.get('Content-Length')
        if length is None:
            self._send_error(400, 'Missing Content-Length')
            return None
        try:
            length = int(length)
        except ValueError:
            self._send_error(400, 'Invalid Content-Length')
            return None
        if length > MAX_JSON_SIZE:
            self._send_error(413, 'Payload too large')
            return None
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._send_error(400, 'Malformed JSON')
            return None
        if not isinstance(data, dict):
            self._send_error(400, 'JSON root must be object')
            return None
        return data

    def _send_json(self, code, obj, extra_headers=None):
        payload = json.dumps(obj).encode('utf-8')
        self._set_headers(code, 'application/json', extra_headers)
        self.wfile.write(payload)

    def _send_error(self, code, message):
        self._send_json(code, {'error': message})

    def _get_cookies(self):
        cookie_header = self.headers.get('Cookie')
        if not cookie_header:
            return {}
        cookie = http.cookies.SimpleCookie()
        cookie.load(cookie_header)
        return {k: v.value for k, v in cookie.items()}

    def _require_auth(self):
        cookies = self._get_cookies()
        token = cookies.get('session')
        if not token:
            self._send_error(401, 'Authentication required')
            return None
        sess = get_session(token)
        if not sess:
            self._send_error(401, 'Invalid or expired session')
            return None
        return sess['username']

    def _origin_allowed(self):
        origin = self.headers.get('Origin')
        if origin is None:
            return True
        allowed = ALLOWED_ORIGIN_TEMPLATE.format(PORT)
        return origin == allowed

    # ---------- HTTP verbs ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/health':
            self._send_json(200, {'ok': True})
        elif parsed.path == '/':
            self._serve_index()
        elif parsed.path.startswith('/api/'):
            self._handle_api_get(parsed)
        else:
            self._send_error(404, 'Not found')

    def do_POST(self):
        if not self._origin_allowed():
            self._send_error(403, 'Invalid Origin')
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self._handle_api_post(parsed)
        else:
            self._send_error(404, 'Not found')

    def do_PUT(self):
        if not self._origin_allowed():
            self._send_error(403, 'Invalid Origin')
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self._handle_api_put(parsed)
        else:
            self._send_error(404, 'Not found')

    def do_DELETE(self):
        if not self._origin_allowed():
            self._send_error(403, 'Invalid Origin')
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith('/api/'):
            self._handle_api_delete(parsed)
        else:
            self._send_error(404, 'Not found')

    # Unsupported methods -> 405
    def do_PATCH(self):
        self._send_error(405, 'Method Not Allowed')
    def do_HEAD(self):
        self._send_error(405, 'Method Not Allowed')
    def do_CONNECT(self):
        self._send_error(405, 'Method Not Allowed')
    def do_TRACE(self):
        self._send_error(405, 'Method Not Allowed')

    # ---------- Static file ----------
    def _serve_index(self):
        try:
            path = os.path.join(os.path.dirname(__file__), 'index.html')
            with open(path, 'rb') as f:
                content = f.read()
            self._set_headers(200, 'text/html; charset=utf-8')
            self.wfile.write(content)
        except Exception:
            self._send_error(500, 'Failed to read index.html')

    # ---------- API routing ----------
    def _handle_api_get(self, parsed):
        path = parsed.path
        if path == '/api/me':
            username = self._require_auth()
            if username:
                self._send_json(200, {'username': username})
        elif path.startswith('/api/tasks'):
            username = self._require_auth()
            if not username:
                return
            parts = path.split('/')
            if len(parts) == 4 and parts[3].isdigit():
                self._api_get_task(int(parts[3]), username)
            else:
                self._api_list_tasks(parsed.query, username)
        elif path == '/api/stats':
            username = self._require_auth()
            if not username:
                return
            self._api_stats(username)
        else:
            self._send_error(404, 'API endpoint not found')

    def _handle_api_post(self, parsed):
        path = parsed.path
        if path == '/api/login':
            self._api_login()
        elif path == '/api/logout':
            username = self._require_auth()
            if not username:
                return
            self._api_logout()
        elif path == '/api/tasks':
            username = self._require_auth()
            if not username:
                return
            self._api_create_task(username)
        else:
            self._send_error(404, 'API endpoint not found')

    def _handle_api_put(self, parsed):
        path = parsed.path
        parts = path.split('/')
        if len(parts) == 4 and parts[1] == 'api' and parts[2] == 'tasks' and parts[3].isdigit():
            username = self._require_auth()
            if not username:
                return
            self._api_update_task(int(parts[3]), username)
        else:
            self._send_error(404, 'API endpoint not found')

    def _handle_api_delete(self, parsed):
        path = parsed.path
        parts = path.split('/')
        if len(parts) == 4 and parts[1] == 'api' and parts[2] == 'tasks' and parts[3].isdigit():
            username = self._require_auth()
            if not username:
                return
            self._api_delete_task(int(parts[3]), username)
        else:
            self._send_error(404, 'API endpoint not found')

    # ---------- API implementations ----------
    def _api_login(self):
        data = self._parse_json()
        if data is None:
            return
        username = data.get('username')
        password = data.get('password')
        if not isinstance(username, str) or not isinstance(password, str):
            self._send_error(400, 'username and password must be strings')
            return
        user = get_user_by_username(username)
        if not user:
            self._send_error(401, 'Invalid credentials')
            return
        salt = user['salt']
        stored_hash = user['password_hash']
        pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
        if not secrets.compare_digest(pwd_hash, stored_hash):
            self._send_error(401, 'Invalid credentials')
            return
        token = create_session(username)
        cookie = f'session={token}; HttpOnly; SameSite=Strict; Path=/'
        self._send_json(200, {'username': username}, {'Set-Cookie': cookie})

    def _api_logout(self):
        cookies = self._get_cookies()
        token = cookies.get('session')
        if token:
            invalidate_session(token)
        cookie = 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
        self._send_json(200, {'ok': True}, {'Set-Cookie': cookie})

    def _api_create_task(self, username):
        data = self._parse_json()
        if data is None:
            return
        valid, result = self._validate_task_input(data, require_title=True, defaults_on_missing=False)
        if not valid:
            self._send_error(400, result)
            return
        user = get_user_by_username(username)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO tasks (owner_id, title, description, status, priority)
            VALUES (?,?,?,?,?)
        ''', (user['id'], result['title'], result['description'], result['status'], result['priority']))
        conn.commit()
        task_id = cur.lastrowid
        conn.close()
        task = {
            'id': task_id,
            'title': result['title'],
            'description': result['description'],
            'status': result['status'],
            'priority': result['priority']
        }
        self._send_json(201, task)

    def _api_get_task(self, task_id, username):
        user = get_user_by_username(username)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT * FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user['id']))
        row = cur.fetchone()
        conn.close()
        if not row:
            self._send_error(404, 'Task not found')
            return
        task = {
            'id': row['id'],
            'title': row['title'],
            'description': row['description'],
            'status': row['status'],
            'priority': row['priority']
        }
        self._send_json(200, task)

    def _api_update_task(self, task_id, username):
        data = self._parse_json()
        if data is None:
            return
        valid, result = self._validate_task_input(data, require_title=True, defaults_on_missing=True)
        if not valid:
            self._send_error(400, result)
            return
        user = get_user_by_username(username)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user['id']))
        if not cur.fetchone():
            conn.close()
            self._send_error(404, 'Task not found')
            return
        cur.execute('''
            UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
            WHERE id = ?
        ''', (result['title'], result['description'], result['status'], result['priority'], task_id))
        conn.commit()
        conn.close()
        task = {
            'id': task_id,
            'title': result['title'],
            'description': result['description'],
            'status': result['status'],
            'priority': result['priority']
        }
        self._send_json(200, task)

    def _api_delete_task(self, task_id, username):
        user = get_user_by_username(username)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user['id']))
        if not cur.fetchone():
            conn.close()
            self._send_error(404, 'Task not found')
            return
        cur.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
        conn.commit()
        conn.close()
        self._send_json(200, {'deleted': True})

    def _api_list_tasks(self, query_string, username):
        params = urllib.parse.parse_qs(query_string)
        q = params.get('q', [None])[0]
        status = params.get('status', [None])[0]
        priority = params.get('priority', [None])[0]
        if status and status not in ('todo', 'doing', 'done'):
            self._send_error(400, 'Invalid status filter')
            return
        if priority and priority not in ('low', 'normal', 'high'):
            self._send_error(400, 'Invalid priority filter')
            return
        user = get_user_by_username(username)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT * FROM tasks WHERE owner_id = ? ORDER BY id DESC', (user['id'],))
        rows = cur.fetchall()
        conn.close()
        items = []
        for r in rows:
            if status and r['status'] != status:
                continue
            if priority and r['priority'] != priority:
                continue
            if q:
                q_cf = q.casefold()
                if q_cf not in r['title'].casefold() and q_cf not in r['description'].casefold():
                    continue
            items.append({
                'id': r['id'],
                'title': r['title'],
                'description': r['description'],
                'status': r['status'],
                'priority': r['priority']
            })
        self._send_json(200, {'items': items, 'total': len(items)})

    def _api_stats(self, username):
        user = get_user_by_username(username)
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ?', (user['id'],))
        total = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (user['id'], 'todo'))
        todo = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (user['id'], 'doing'))
        doing = cur.fetchone()[0]
        cur.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (user['id'], 'done'))
        done = cur.fetchone()[0]
        conn.close()
        self._send_json(200, {'total': total, 'todo': todo, 'doing': doing, 'done': done})

    # ---------- Validation ----------
    def _validate_task_input(self, data, require_title=False, defaults_on_missing=False):
        allowed_keys = {'title', 'description', 'status', 'priority'}
        unknown = set(data.keys()) - allowed_keys
        if unknown:
            return False, f'Unknown fields: {", ".join(unknown)}'
        # Title
        if 'title' not in data:
            if require_title:
                return False, 'title is required'
            title = ''
        else:
            title = data['title']
            if not isinstance(title, str):
                return False, 'title must be a string'
            title = title.strip()
            if not (1 <= len(title) <= 120):
                return False, 'title length must be 1-120 characters'
        # Description
        if 'description' not in data:
            description = ''
        else:
            description = data['description']
            if not isinstance(description, str):
                return False, 'description must be a string'
            if len(description) > 1000:
                return False, 'description too long (max 1000)'
        # Status
        if 'status' not in data:
            status = 'todo' if defaults_on_missing else 'todo'
        else:
            status = data['status']
            if not isinstance(status, str) or status not in ('todo', 'doing', 'done'):
                return False, 'invalid status'
        # Priority
        if 'priority' not in data:
            priority = 'normal' if defaults_on_missing else 'normal'
        else:
            priority = data['priority']
            if not isinstance(priority, str) or priority not in ('low', 'normal', 'high'):
                return False, 'invalid priority'
        return True, {
            'title': title,
            'description': description,
            'status': status,
            'priority': priority
        }

    # ---------- Override unsupported methods ----------
    def handle_one_request(self):
        try:
            super().handle_one_request()
        except Exception:
            self._send_error(500, 'Internal server error')

def main():
    global DB_PATH, PORT
    parser = argparse.ArgumentParser(description='Görev Masası')
    parser.add_argument('--port', type=int, required=True, help='Port number')
    parser.add_argument('--db', type=str, required=True, help='Absolute path to SQLite DB')
    args = parser.parse_args()
    PORT = args.port
    DB_PATH = args.db
    if not os.path.isabs(DB_PATH):
        print('Error: --db must be an absolute path', file=sys.stderr)
        sys.exit(1)
    init_db()
    server_address = ('127.0.0.1', PORT)
    httpd = http.server.HTTPServer(server_address, TaskHandler)
    print(f'Server listening on http://127.0.0.1:{PORT}')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('Shutting down')
        httpd.server_close()

if __name__ == '__main__':
    main()
