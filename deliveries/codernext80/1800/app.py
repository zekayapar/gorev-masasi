import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional, Tuple, Any

SESSION_TIMEOUT = 24 * 60 * 60  # 24 hours in seconds
SESSIONS: Dict[str, Tuple[str, float]] = {}  # session_id -> (username, expires_at)

def hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000).hex()

def generate_salt() -> bytes:
    return secrets.token_bytes(16)

def generate_session_id() -> str:
    return secrets.token_urlsafe(32)

def init_db(db_path: str):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'todo',
            priority TEXT DEFAULT 'normal',
            created_at INTEGER DEFAULT (strftime('%s', 'now')),
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    ''')
    # Seed demo accounts only if missing
    cursor.execute('SELECT COUNT(*) FROM users')
    if cursor.fetchone()[0] == 0:
        salt1 = generate_salt()
        salt2 = generate_salt()
        password_hash1 = hash_password('deneme-zeka-2026', salt1)
        password_hash2 = hash_password('deneme-yapar-2026', salt2)
        cursor.execute('INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)',
                       ('zeka', password_hash1, salt1.hex()))
        cursor.execute('INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)',
                       ('yapar', password_hash2, salt2.hex()))
    conn.commit()
    conn.close()

def get_db_connection(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def verify_password(username: str, password: str, db_path: str) -> Optional[int]:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT id, password_hash, salt FROM users WHERE username = ?', (username,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    salt = bytes.fromhex(row['salt'])
    expected_hash = hash_password(password, salt)
    if hmac.compare_digest(row['password_hash'], expected_hash):
        return row['id']
    return None

def validate_task_input(data: dict) -> Tuple[bool, str]:
    allowed_keys = {'title', 'description', 'status', 'priority'}
    if not isinstance(data, dict):
        return False, 'Invalid JSON object'
    if not set(data.keys()).issubset(allowed_keys):
        return False, 'Unknown fields in request'
    if 'title' not in data:
        return False, 'Title is required'
    title = data.get('title', '')
    if not isinstance(title, str):
        return False, 'Title must be a string'
    title = title.strip()
    if not title or len(title) > 120:
        return False, 'Title must be 1-120 characters'
    if 'description' in data:
        desc = data['description']
        if not isinstance(desc, str):
            return False, 'Description must be a string'
        if len(desc) > 1000:
            return False, 'Description must be at most 1000 characters'
    if 'status' in data:
        status = data['status']
        if status not in ('todo', 'doing', 'done'):
            return False, 'Invalid status value'
    if 'priority' in data:
        priority = data['priority']
        if priority not in ('low', 'normal', 'high'):
            return False, 'Invalid priority value'
    return True, ''

def parse_query_params(query: str) -> dict:
    params = {}
    for part in query.split('&'):
        if '=' in part:
            key, value = part.split('=', 1)
            params[key] = urllib.parse.unquote_plus(value)
        elif part:
            params[part] = ''
    return params

class TaskHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    
    def log_message(self, format, *args):
        pass  # Suppress logging
    
    def send_json_response(self, status_code: int, data: dict):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def send_html_response(self, status_code: int, html: str):
        body = html.encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def get_session_username(self) -> Optional[str]:
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            part = part.strip()
            if part.startswith('session='):
                session_id = part[len('session='):]
                if session_id in SESSIONS:
                    username, expires_at = SESSIONS[session_id]
                    if time.time() < expires_at:
                        return username
                    else:
                        del SESSIONS[session_id]
                break
        return None
    
    def check_origin(self) -> bool:
        origin = self.headers.get('Origin', '')
        if origin:
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            return origin == expected_origin
        return True  # Allow requests without Origin (e.g., CLI)
    
    def do_GET(self):
        if not self.check_origin():
            self.send_json_response(403, {'error': 'Forbidden origin'})
            return
        
        if self.path == '/health':
            self.send_json_response(200, {'ok': True})
        elif self.path == '/':
            self.serve_login_page()
        elif self.path.startswith('/api/tasks/'):
            self.handle_get_task()
        elif self.path == '/api/tasks':
            self.handle_get_tasks()
        elif self.path == '/api/stats':
            self.handle_get_stats()
        elif self.path == '/api/me':
            self.handle_get_me()
        else:
            self.send_json_response(404, {'error': 'Not found'})
    
    def do_POST(self):
        if not self.check_origin():
            self.send_json_response(403, {'error': 'Forbidden origin'})
            return
        
        if self.path == '/api/login':
            self.handle_login()
        elif self.path == '/api/logout':
            self.handle_logout()
        elif self.path == '/api/tasks':
            self.handle_create_task()
        else:
            self.send_json_response(404, {'error': 'Not found'})
    
    def do_PUT(self):
        if not self.check_origin():
            self.send_json_response(403, {'error': 'Forbidden origin'})
            return
        
        if self.path.startswith('/api/tasks/'):
            self.handle_update_task()
        else:
            self.send_json_response(404, {'error': 'Not found'})
    
    def do_DELETE(self):
        if not self.check_origin():
            self.send_json_response(403, {'error': 'Forbidden origin'})
            return
        
        if self.path.startswith('/api/tasks/'):
            self.handle_delete_task()
        else:
            self.send_json_response(404, {'error': 'Not found'})
    
    def do_PATCH(self):
        self.handle_unsupported_method()
    
    def do_HEAD(self):
        self.handle_unsupported_method()
    
    def do_OPTIONS(self):
        self.handle_unsupported_method()
    
    def handle_unsupported_method(self):
        if self.path.startswith('/api/'):
            self.send_json_response(405, {'error': 'Method not allowed'})
        else:
            self.send_json_response(404, {'error': 'Not found'})
    
    def serve_login_page(self):
        html = '''<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Görev Masası</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f5f5f5; }
        .container { max-width: 480px; margin: 0 auto; padding: 20px; }
        .login-box { background: white; padding: 24px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
        h1 { margin-bottom: 20px; color: #333; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 6px; font-weight: 500; color: #555; }
        input[type="text"], input[type="password"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        button { width: 100%; padding: 12px; background: #007bff; color: white; border: none; border-radius: 4px; font-size: 16px; cursor: pointer; }
        button:hover { background: #0056b3; }
        .demo { margin-top: 16px; padding: 12px; background: #f0f7ff; border-radius: 4px; font-size: 13px; }
        .error { color: #dc3545; margin-top: 12px; }
        .success { color: #28a745; margin-top: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="login-box">
            <h1>Görev Masası</h1>
            <form id="loginForm">
                <div class="form-group">
                    <label for="username">Kullanıcı adı</label>
                    <input type="text" id="username" name="username" required>
                </div>
                <div class="form-group">
                    <label for="password">Parola</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <button type="submit">Giriş yap</button>
                <div id="loginError" class="error"></div>
            </form>
            <div class="demo">
                <strong>Örnek hesaplar:</strong><br>
                zeka / deneme-zeka-2026<br>
                yapar / deneme-yapar-2026
            </div>
        </div>
    </div>
    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const username = document.getElementById('username').value.trim();
            const password = document.getElementById('password').value;
            const errorEl = document.getElementById('loginError');
            errorEl.textContent = '';
            try {
                const res = await fetch('/api/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                if (res.ok) {
                    window.location.href = '/';
                } else {
                    const data = await res.json();
                    errorEl.textContent = data.error || 'Giriş başarısız';
                }
            } catch (err) {
                errorEl.textContent = 'Bağlantı hatası';
            }
        });
    </script>
</body>
</html>'''
        self.send_html_response(200, html)
    
    def handle_login(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 16384:
                self.send_json_response(413, {'error': 'Request body too large'})
                return
            body = self.rfile.read(content_length)
            data = json.loads(body)
            if not isinstance(data, dict):
                self.send_json_response(400, {'error': 'Invalid JSON object'})
                return
            username = data.get('username', '')
            password = data.get('password', '')
            if not isinstance(username, str) or not isinstance(password, str):
                self.send_json_response(400, {'error': 'Invalid input type'})
                return
            user_id = verify_password(username, password, self.server.db_path)
            if user_id is None:
                self.send_json_response(401, {'error': 'Invalid credentials'})
                return
            session_id = generate_session_id()
            SESSIONS[session_id] = (username, time.time() + SESSION_TIMEOUT)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Set-Cookie', f'session={session_id}; HttpOnly; SameSite=Strict; Path=/')
            body = json.dumps({'username': username}).encode('utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except json.JSONDecodeError:
            self.send_json_response(400, {'error': 'Invalid JSON'})
        except Exception:
            self.send_json_response(400, {'error': 'Invalid request'})
    
    def handle_logout(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            part = part.strip()
            if part.startswith('session='):
                session_id = part[len('session='):]
                if session_id in SESSIONS:
                    del SESSIONS[session_id]
                break
        self.send_json_response(200, {'ok': True})
    
    def handle_get_me(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        self.send_json_response(200, {'username': username})
    
    def handle_create_task(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 16384:
                self.send_json_response(413, {'error': 'Request body too large'})
                return
            body = self.rfile.read(content_length)
            data = json.loads(body)
            valid, error = validate_task_input(data)
            if not valid:
                self.send_json_response(400, {'error': error})
                return
            conn = get_db_connection(self.server.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_response(401, {'error': 'Not authenticated'})
                return
            owner_id = row['id']
            title = data['title'].strip()
            description = data.get('description', '').strip()
            status = data.get('status', 'todo')
            priority = data.get('priority', 'normal')
            cursor.execute('''
                INSERT INTO tasks (owner_id, title, description, status, priority)
                VALUES (?, ?, ?, ?, ?)
            ''', (owner_id, title, description, status, priority))
            task_id = cursor.lastrowid
            conn.commit()
            conn.close()
            self.send_json_response(201, {
                'id': task_id,
                'title': title,
                'description': description,
                'status': status,
                'priority': priority
            })
        except json.JSONDecodeError:
            self.send_json_response(400, {'error': 'Invalid JSON'})
        except Exception:
            self.send_json_response(400, {'error': 'Invalid request'})
    
    def handle_get_tasks(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        try:
            params = parse_query_params(self.path.split('?')[1] if '?' in self.path else '')
            status_filter = params.get('status', '')
            priority_filter = params.get('priority', '')
            q = params.get('q', '')
            
            if status_filter and status_filter not in ('todo', 'doing', 'done'):
                self.send_json_response(400, {'error': 'Invalid status filter'})
                return
            if priority_filter and priority_filter not in ('low', 'normal', 'high'):
                self.send_json_response(400, {'error': 'Invalid priority filter'})
                return
            
            conn = get_db_connection(self.server.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_response(401, {'error': 'Not authenticated'})
                return
            owner_id = row['id']
            
            query = 'SELECT * FROM tasks WHERE owner_id = ?'
            params_list = [owner_id]
            
            if q:
                query += ' AND (title LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE)'
                params_list.extend(['%' + q + '%', '%' + q + '%'])
            if status_filter:
                query += ' AND status = ?'
                params_list.append(status_filter)
            if priority_filter:
                query += ' AND priority = ?'
                params_list.append(priority_filter)
            
            query += ' ORDER BY id DESC'
            cursor.execute(query, params_list)
            tasks = []
            for row in cursor.fetchall():
                tasks.append({
                    'id': row['id'],
                    'title': row['title'],
                    'description': row['description'],
                    'status': row['status'],
                    'priority': row['priority']
                })
            
            # Get total count
            cursor.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ?', (owner_id,))
            total = cursor.fetchone()[0]
            
            conn.close()
            self.send_json_response(200, {'items': tasks, 'total': total})
        except Exception as e:
            self.send_json_response(400, {'error': 'Invalid request'})
    
    def handle_get_task(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        try:
            task_id = int(self.path.split('/')[-1])
        except ValueError:
            self.send_json_response(404, {'error': 'Task not found'})
            return
        conn = get_db_connection(self.server.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        owner_id = row['id']
        cursor.execute('SELECT * FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
        task = cursor.fetchone()
        conn.close()
        if not task:
            self.send_json_response(404, {'error': 'Task not found'})
            return
        self.send_json_response(200, {
            'id': task['id'],
            'title': task['title'],
            'description': task['description'],
            'status': task['status'],
            'priority': task['priority']
        })
    
    def handle_update_task(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        try:
            task_id = int(self.path.split('/')[-1])
        except ValueError:
            self.send_json_response(404, {'error': 'Task not found'})
            return
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 16384:
                self.send_json_response(413, {'error': 'Request body too large'})
                return
            body = self.rfile.read(content_length)
            data = json.loads(body)
            valid, error = validate_task_input(data)
            if not valid:
                self.send_json_response(400, {'error': error})
                return
            conn = get_db_connection(self.server.db_path)
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
            row = cursor.fetchone()
            if not row:
                conn.close()
                self.send_json_response(401, {'error': 'Not authenticated'})
                return
            owner_id = row['id']
            cursor.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
            if not cursor.fetchone():
                conn.close()
                self.send_json_response(404, {'error': 'Task not found'})
                return
            title = data['title'].strip()
            description = data.get('description', '').strip()
            status = data.get('status', 'todo')
            priority = data.get('priority', 'normal')
            cursor.execute('''
                UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
                WHERE id = ? AND owner_id = ?
            ''', (title, description, status, priority, task_id, owner_id))
            conn.commit()
            conn.close()
            self.send_json_response(200, {
                'id': task_id,
                'title': title,
                'description': description,
                'status': status,
                'priority': priority
            })
        except json.JSONDecodeError:
            self.send_json_response(400, {'error': 'Invalid JSON'})
        except Exception:
            self.send_json_response(400, {'error': 'Invalid request'})
    
    def handle_delete_task(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        try:
            task_id = int(self.path.split('/')[-1])
        except ValueError:
            self.send_json_response(404, {'error': 'Task not found'})
            return
        conn = get_db_connection(self.server.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        owner_id = row['id']
        cursor.execute('SELECT id FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
        if not cursor.fetchone():
            conn.close()
            self.send_json_response(404, {'error': 'Task not found'})
            return
        cursor.execute('DELETE FROM tasks WHERE id = ? AND owner_id = ?', (task_id, owner_id))
        conn.commit()
        conn.close()
        self.send_json_response(200, {'deleted': True})
    
    def handle_get_stats(self):
        username = self.get_session_username()
        if username is None:
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        conn = get_db_connection(self.server.db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE username = ?', (username,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            self.send_json_response(401, {'error': 'Not authenticated'})
            return
        owner_id = row['id']
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ?', (owner_id,))
        total = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (owner_id, 'todo'))
        todo = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (owner_id, 'doing'))
        doing = cursor.fetchone()[0]
        cursor.execute('SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?', (owner_id, 'done'))
        done = cursor.fetchone()[0]
        conn.close()
        self.send_json_response(200, {
            'total': total,
            'todo': todo,
            'doing': doing,
            'done': done
        })

def main():
    parser = argparse.ArgumentParser(description='Görev Masası')
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--db', type=str, required=True)
    args = parser.parse_args()
    
    db_dir = os.path.dirname(args.db)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    
    init_db(args.db)
    
    class CustomServer(HTTPServer):
        db_path = args.db
    
    server = CustomServer(('127.0.0.1', args.port), TaskHandler)
    print(f'Server running at http://127.0.0.1:{args.port}')
    server.serve_forever()

if __name__ == '__main__':
    main()
