import argparse
import hashlib
import hmac
import http.cookies
import json
import os
import secrets
import sqlite3
import sys
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

SESSION_TIMEOUT = 24 * 60 * 60
SESSIONS = {}

def hash_password(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash BLOB NOT NULL,
            salt BLOB NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'todo' CHECK(status IN ('todo','doing','done')),
            priority TEXT DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    ''')
    c.execute('SELECT COUNT(*) FROM users')
    if c.fetchone()[0] == 0:
        salt1 = secrets.token_bytes(16)
        salt2 = secrets.token_bytes(16)
        pw1 = hash_password('deneme-zeka-2026', salt1)
        pw2 = hash_password('deneme-yapar-2026', salt2)
        c.execute('INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)', ('zeka', pw1, salt1))
        c.execute('INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)', ('yapar', pw2, salt2))
    conn.commit()
    conn.close()

def get_db(db_path):
    return sqlite3.connect(db_path)

def generate_session_id():
    return secrets.token_urlsafe(32)

def validate_session(session_id):
    if not session_id or session_id not in SESSIONS:
        return None
    session = SESSIONS[session_id]
    if time.time() - session['created_at'] > SESSION_TIMEOUT:
        del SESSIONS[session_id]
        return None
    return session['user_id']

class TaskHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message, status=400):
        self.send_json({'error': message}, status)

    def get_cookie(self, name):
        cookie_header = self.headers.get('Cookie', '')
        cookies = http.cookies.SimpleCookie(cookie_header)
        return cookies.get(name, None)

    def get_session_user(self):
        session_cookie = self.get_cookie('session')
        if session_cookie:
            return validate_session(session_cookie.value)
        return None

    def check_origin(self):
        origin = self.headers.get('Origin', '')
        if origin:
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if origin != expected:
                return False
        return True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == '/health':
            self.send_json({'ok': True})
            return

        if path == '/':
            try:
                with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', len(content))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self.send_error_json('index.html not found', 500)
            return

        if path == '/api/me':
            user_id = self.get_session_user()
            if not user_id:
                self.send_error_json('Unauthorized', 401)
                return
            conn = get_db(self.server.db_path)
            c = conn.cursor()
            c.execute('SELECT username FROM users WHERE id = ?', (user_id,))
            row = c.fetchone()
            conn.close()
            if row:
                self.send_json({'username': row[0]})
            else:
                self.send_error_json('User not found', 404)
            return

        if path == '/api/stats':
            user_id = self.get_session_user()
            if not user_id:
                self.send_error_json('Unauthorized', 401)
                return
            conn = get_db(self.server.db_path)
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ?', (user_id,))
            total = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'todo'", (user_id,))
            todo = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'doing'", (user_id,))
            doing = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'done'", (user_id,))
            done = c.fetchone()[0]
            conn.close()
            self.send_json({'total': total, 'todo': todo, 'doing': doing, 'done': done})
            return

        if path == '/api/tasks':
            user_id = self.get_session_user()
            if not user_id:
                self.send_error_json('Unauthorized', 401)
                return
            q = query.get('q', [''])[0]
            status_filter = query.get('status', [None])[0]
            priority_filter = query.get('priority', [None])[0]
            if status_filter and status_filter not in ('todo', 'doing', 'done'):
                self.send_error_json('Invalid status', 400)
                return
            if priority_filter and priority_filter not in ('low', 'normal', 'high'):
                self.send_error_json('Invalid priority', 400)
                return
            conn = get_db(self.server.db_path)
            c = conn.cursor()
            sql = 'SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?'
            params = [user_id]
            if q:
                sql += ' AND (title LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE)'
                q_param = '%' + q + '%'
                params.extend([q_param, q_param])
            if status_filter:
                sql += ' AND status = ?'
                params.append(status_filter)
            if priority_filter:
                sql += ' AND priority = ?'
                params.append(priority_filter)
            sql += ' ORDER BY id DESC'
            c.execute(sql, params)
            rows = c.fetchall()
            items = []
            for row in rows:
                items.append({
                    'id': row[0],
                    'title': row[1],
                    'description': row[2],
                    'status': row[3],
                    'priority': row[4]
                })
            c.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ?', (user_id,))
            total = c.fetchone()[0]
            conn.close()
            self.send_json({'items': items, 'total': total})
            return

        if path.startswith('/api/tasks/'):
            parts = path.split('/')
            if len(parts) == 3 and parts[2].isdigit():
                task_id = int(parts[2])
                user_id = self.get_session_user()
                if not user_id:
                    self.send_error_json('Unauthorized', 401)
                    return
                conn = get_db(self.server.db_path)
                c = conn.cursor()
                c.execute('SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user_id))
                row = c.fetchone()
                conn.close()
                if not row:
                    self.send_error_json('Not found', 404)
                    return
                self.send_json({
                    'id': row[0],
                    'title': row[1],
                    'description': row[2],
                    'status': row[3],
                    'priority': row[4]
                })
                return
            self.send_error_json('Not found', 404)
            return

        self.send_error_json('Not found', 404)

    def do_POST(self):
        if not self.check_origin():
            self.send_error_json('Forbidden', 403)
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == '/api/login':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 16384:
                    self.send_error_json('Payload too large', 413)
                    return
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self.send_error_json('Invalid JSON', 400)
                return
            if not isinstance(data, dict):
                self.send_error_json('Invalid JSON', 400)
                return
            if 'username' not in data or 'password' not in data:
                self.send_error_json('Missing username or password', 400)
                return
            username = data['username']
            password = data['password']
            if not isinstance(username, str) or not isinstance(password, str):
                self.send_error_json('Invalid input', 400)
                return
            conn = get_db(self.server.db_path)
            c = conn.cursor()
            c.execute('SELECT id, password_hash, salt FROM users WHERE username = ?', (username,))
            row = c.fetchone()
            conn.close()
            if not row:
                self.send_error_json('Invalid credentials', 401)
                return
            user_id, stored_hash, salt = row
            if not hmac.compare_digest(hash_password(password, salt), stored_hash):
                self.send_error_json('Invalid credentials', 401)
                return
            session_id = generate_session_id()
            SESSIONS[session_id] = {'user_id': user_id, 'created_at': time.time()}
            cookie = http.cookies.SimpleCookie()
            cookie['session'] = session_id
            cookie['session']['httponly'] = True
            cookie['session']['samesite'] = 'Strict'
            cookie['session']['path'] = '/'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', cookie.output(header='', sep=''))
            body = json.dumps({'username': username}).encode('utf-8')
            self.send_header('Content-Length', len(body))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == '/api/logout':
            session_cookie = self.get_cookie('session')
            if session_cookie and session_cookie.value in SESSIONS:
                del SESSIONS[session_cookie.value]
            self.send_json({'ok': True})
            return

        if path == '/api/tasks':
            user_id = self.get_session_user()
            if not user_id:
                self.send_error_json('Unauthorized', 401)
                return
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 16384:
                    self.send_error_json('Payload too large', 413)
                    return
                body = self.rfile.read(content_length).decode('utf-8')
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self.send_error_json('Invalid JSON', 400)
                return
            if not isinstance(data, dict):
                self.send_error_json('Invalid JSON', 400)
                return
            if 'title' not in data:
                self.send_error_json('Missing title', 400)
                return
            title = data['title']
            if not isinstance(title, str):
                self.send_error_json('Invalid title', 400)
                return
            title = title.strip()
            if not title or len(title) > 120:
                self.send_error_json('Invalid title', 400)
                return
            description = data.get('description', '')
            if not isinstance(description, str):
                self.send_error_json('Invalid description', 400)
                return
            description = description.strip()
            if len(description) > 1000:
                self.send_error_json('Description too long', 400)
                return
            status = data.get('status', 'todo')
            if status not in ('todo', 'doing', 'done'):
                self.send_error_json('Invalid status', 400)
                return
            priority = data.get('priority', 'normal')
            if priority not in ('low', 'normal', 'high'):
                self.send_error_json('Invalid priority', 400)
                return
            allowed_keys = {'title', 'description', 'status', 'priority'}
            if set(data.keys()) - allowed_keys:
                self.send_error_json('Unknown fields', 400)
                return
            conn = get_db(self.server.db_path)
            c = conn.cursor()
            c.execute('INSERT INTO tasks (owner_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?)',
                      (user_id, title, description, status, priority))
            task_id = c.lastrowid
            conn.commit()
            conn.close()
            self.send_json({
                'id': task_id,
                'title': title,
                'description': description,
                'status': status,
                'priority': priority
            }, 201)
            return

        self.send_error_json('Not found', 404)

    def do_PUT(self):
        if not self.check_origin():
            self.send_error_json('Forbidden', 403)
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith('/api/tasks/'):
            parts = path.split('/')
            if len(parts) == 3 and parts[2].isdigit():
                task_id = int(parts[2])
                user_id = self.get_session_user()
                if not user_id:
                    self.send_error_json('Unauthorized', 401)
                    return
                try:
                    content_length = int(self.headers.get('Content-Length', 0))
                    if content_length > 16384:
                        self.send_error_json('Payload too large', 413)
                        return
                    body = self.rfile.read(content_length).decode('utf-8')
                    data = json.loads(body)
                except (json.JSONDecodeError, ValueError):
                    self.send_error_json('Invalid JSON', 400)
                    return
                if not isinstance(data, dict):
                    self.send_error_json('Invalid JSON', 400)
                    return
                if 'title' not in data:
                    self.send_error_json('Missing title', 400)
                    return
                title = data['title']
                if not isinstance(title, str):
                    self.send_error_json('Invalid title', 400)
                    return
                title = title.strip()
                if not title or len(title) > 120:
                    self.send_error_json('Invalid title', 400)
                    return
                description = data.get('description', '')
                if not isinstance(description, str):
                    self.send_error_json('Invalid description', 400)
                    return
                description = description.strip()
                if len(description) > 1000:
                    self.send_error_json('Description too long', 400)
                    return
                status = data.get('status', 'todo')
                if status not in ('todo', 'doing', 'done'):
                    self.send_error_json('Invalid status', 400)
                    return
                priority = data.get('priority', 'normal')
                if priority not in ('low', 'normal', 'high'):
                    self.send_error_json('Invalid priority', 400)
                    return
                allowed_keys = {'title', 'description', 'status', 'priority'}
                if set(data.keys()) - allowed_keys:
                    self.send_error_json('Unknown fields', 400)
                    return
                conn = get_db(self.server.db_path)
                c = conn.cursor()
                c.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user_id))
                if not c.fetchone():
                    conn.close()
                    self.send_error_json('Not found', 404)
                    return
                c.execute('UPDATE tasks SET title = ?, description = ?, status = ?, priority = ? WHERE id = ? AND owner_id = ?',
                          (title, description, status, priority, task_id, user_id))
                conn.commit()
                conn.close()
                self.send_json({
                    'id': task_id,
                    'title': title,
                    'description': description,
                    'status': status,
                    'priority': priority
                })
                return
            self.send_error_json('Not found', 404)
            return

        self.send_error_json('Not found', 404)

    def do_DELETE(self):
        if not self.check_origin():
            self.send_error_json('Forbidden', 403)
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith('/api/tasks/'):
            parts = path.split('/')
            if len(parts) == 3 and parts[2].isdigit():
                task_id = int(parts[2])
                user_id = self.get_session_user()
                if not user_id:
                    self.send_error_json('Unauthorized', 401)
                    return
                conn = get_db(self.server.db_path)
                c = conn.cursor()
                c.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user_id))
                if not c.fetchone():
                    conn.close()
                    self.send_error_json('Not found', 404)
                    return
                c.execute('DELETE FROM tasks WHERE id = ? AND owner_id = ?', (task_id, user_id))
                conn.commit()
                conn.close()
                self.send_json({'deleted': True})
                return
            self.send_error_json('Not found', 404)
            return

        self.send_error_json('Not found', 404)

    def do_PATCH(self):
        self.send_error_json('Method not allowed', 405)

    def do_HEAD(self):
        self.send_error_json('Method not allowed', 405)

    def do_OPTIONS(self):
        self.send_error_json('Method not allowed', 405)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--db', required=True)
    args = parser.parse_args()

    init_db(args.db)

    server = HTTPServer(('127.0.0.1', args.port), TaskHandler)
    server.db_path = args.db
    print(f'Server running on http://127.0.0.1:{args.port}')
    server.serve_forever()

if __name__ == '__main__':
    main()
