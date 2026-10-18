import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# Constants
DB_SCHEMA = [
    "PRAGMA foreign_keys = ON;",
    "CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL
    );",
    "CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'todo',
        priority TEXT NOT NULL DEFAULT 'normal',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (owner_id) REFERENCES users(id)
    );",
    "CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner_id);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);"
]

DEMO_USERS = [
    ('zeka', 'deneme-zeka-2026'),
    ('yapar', 'deneme-yapar-2026')
]

SESSION_EXPIRY = 24 * 60 * 60  # 24 hours in seconds

class TaskManagerApp:
    def __init__(self, db_path):
        self.db_path = db_path
        self.sessions = {}  # session_id -> (user_id, expiry_time)
        self._init_db()
    
    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute('PRAGMA foreign_keys = ON;')
        for stmt in DB_SCHEMA:
            conn.execute(stmt)
        conn.commit()
        
        # Check if demo users exist
        cursor = conn.execute('SELECT username FROM users')
        existing_users = {row[0] for row in cursor.fetchall()}
        
        for username, password in DEMO_USERS:
            if username not in existing_users:
                salt = secrets.token_bytes(16)
                password_hash = self._hash_password(password, salt)
                conn.execute(
                    'INSERT INTO users (username, password_hash, password_salt) VALUES (?, ?, ?)',
                    (username, password_hash, salt)
                )
        
        conn.commit()
        conn.close()
    
    def _hash_password(self, password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    
    def _verify_password(self, password, stored_hash, salt):
        return hmac.compare_digest(
            self._hash_password(password, salt),
            stored_hash
        )
    
    def _get_user_id(self, username):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('SELECT id FROM users WHERE username = ?', (username,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def _validate_task_data(self, data, is_update=False):
        required = ['title']
        allowed = {'title', 'description', 'status', 'priority'}
        
        if not isinstance(data, dict):
            return None, 'Invalid JSON format'
        
        # Check for unknown keys
        for key in data:
            if key not in allowed:
                return None, f'Unknown field: {key}'
        
        # Check required fields
        for field in required:
            if field not in data:
                return None, f'Missing required field: {field}'
        
        title = data['title'].strip()
        if not title or len(title) > 120:
            return None, 'Title must be 1-120 characters'
        
        description = data.get('description', '')
        if len(description) > 1000:
            return None, 'Description too long (max 1000 chars)'
        
        status = data.get('status', 'todo')
        if status not in ['todo', 'doing', 'done']:
            return None, 'Invalid status'
        
        priority = data.get('priority', 'normal')
        if priority not in ['low', 'normal', 'high']:
            return None, 'Invalid priority'
        
        return {
            'title': title,
            'description': description,
            'status': status,
            'priority': priority
        }, None
    
    def _get_session_user_id(self, session_id):
        if session_id in self.sessions:
            user_id, expiry = self.sessions[session_id]
            if time.time() < expiry:
                return user_id
            else:
                del self.sessions[session_id]
        return None
    
    def _create_session(self, user_id):
        session_id = secrets.token_hex(32)
        expiry = time.time() + SESSION_EXPIRY
        self.sessions[session_id] = (user_id, expiry)
        return session_id
    
    def _delete_session(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]
    
    def _get_username(self, user_id):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute('SELECT username FROM users WHERE id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def _get_tasks(self, user_id, filters=None):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        query = 'SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?'
        params = [user_id]
        
        if filters:
            q = filters.get('q', '')
            status = filters.get('status')
            priority = filters.get('priority')
            
            conditions = []
            if q:
                conditions.append('(title LIKE ? OR description LIKE ?)')
                params.extend([f'%{q}%', f'%{q}%'])
            
            if status:
                conditions.append('status = ?')
                params.append(status)
            
            if priority:
                conditions.append('priority = ?')
                params.append(priority)
            
            if conditions:
                query += ' AND ' + ' AND '.join(conditions)
        
        query += ' ORDER BY id DESC'
        
        cursor = conn.execute(query, params)
        tasks = [dict(row) for row in cursor.fetchall()]
        
        # Get total count
        count_query = 'SELECT COUNT(*) FROM tasks WHERE owner_id = ?'
        count_params = [user_id]
        if filters:
            q = filters.get('q', '')
            status = filters.get('status')
            priority = filters.get('priority')
            
            conditions = []
            if q:
                conditions.append('(title LIKE ? OR description LIKE ?)')
                count_params.extend([f'%{q}%', f'%{q}%'])
            
            if status:
                conditions.append('status = ?')
                count_params.append(status)
            
            if priority:
                conditions.append('priority = ?')
                count_params.append(priority)
            
            if conditions:
                count_query += ' AND ' + ' AND '.join(conditions)
        
        cursor = conn.execute(count_query, count_params)
        total = cursor.fetchone()[0]
        
        conn.close()
        return tasks, total
    
    def _get_stats(self, user_id):
        conn = sqlite3.connect(self.db_path)
        
        stats = {}
        for status in ['todo', 'doing', 'done']:
            cursor = conn.execute(
                'SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = ?',
                (user_id, status)
            )
            stats[status] = cursor.fetchone()[0]
        
        cursor = conn.execute(
            'SELECT COUNT(*) FROM tasks WHERE owner_id = ?',
            (user_id,)
        )
        stats['total'] = cursor.fetchone()[0]
        
        conn.close()
        return stats

class RequestHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, app=None, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)
    
    def _set_headers(self, status=200, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.end_headers()
    
    def _read_json(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 16384:
            self._set_headers(413)
            self.wfile.write(json.dumps({'error': 'Request too large'}).encode())
            return None
        
        try:
            data = json.loads(self.rfile.read(content_length))
            if not isinstance(data, dict):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid JSON format'}).encode())
                return None
            return data
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
            return None
    
    def _get_session_id(self):
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            if part.strip().startswith('session='):
                return part.strip().split('=', 1)[1]
        return None
    
    def _require_login(self):
        session_id = self._get_session_id()
        user_id = self.app._get_session_user_id(session_id)
        if not user_id:
            self._set_headers(401)
            self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
            return None
        return user_id
    
    def _check_origin(self):
        origin = self.headers.get('Origin')
        if origin:
            expected_origin = f'http://127.0.0.1:{self.server.server_port}'
            if origin != expected_origin:
                self._set_headers(403)
                self.wfile.write(json.dumps({'error': 'Forbidden'}).encode())
                return False
        return True
    
    def do_GET(self):
        if self.path == '/health':
            self._set_headers(200)
            self.wfile.write(json.dumps({'ok': True}).encode())
            return
        
        if self.path == '/':
            try:
                with open('index.html', 'r', encoding='utf-8') as f:
                    content = f.read()
                self._set_headers(200, 'text/html; charset=utf-8')
                self.wfile.write(content.encode())
            except FileNotFoundError:
                self._set_headers(404)
                self.wfile.write(b'Not Found')
            return
        
        if self.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if self.path == '/api/me':
                user_id = self._require_login()
                if user_id:
                    username = self.app._get_username(user_id)
                    self._set_headers(200)
                    self.wfile.write(json.dumps({'username': username}).encode())
                return
            
            if self.path == '/api/stats':
                user_id = self._require_login()
                if user_id:
                    stats = self.app._get_stats(user_id)
                    self._set_headers(200)
                    self.wfile.write(json.dumps(stats).encode())
                return
            
            if self.path.startswith('/api/tasks/'):
                parts = self.path.split('/')
                if len(parts) == 4 and parts[3].isdigit():
                    task_id = int(parts[3])
                    user_id = self._require_login()
                    if user_id:
                        conn = sqlite3.connect(self.app.db_path)
                        cursor = conn.execute(
                            'SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = ?',
                            (task_id, user_id)
                        )
                        task = cursor.fetchone()
                        conn.close()
                        
                        if task:
                            self._set_headers(200)
                            self.wfile.write(json.dumps({
                                'id': task[0],
                                'title': task[1],
                                'description': task[2],
                                'status': task[3],
                                'priority': task[4]
                            }).encode())
                        else:
                            self._set_headers(404)
                            self.wfile.write(json.dumps({'error': 'Task not found'}).encode())
                    return
            
            if self.path == '/api/tasks':
                user_id = self._require_login()
                if user_id:
                    query = urlparse(self.path).query
                    params = parse_qs(query)
                    
                    filters = {}
                    if 'q' in params:
                        filters['q'] = params['q'][0]
                    if 'status' in params:
                        status = params['status'][0]
                        if status not in ['todo', 'doing', 'done']:
                            self._set_headers(400)
                            self.wfile.write(json.dumps({'error': 'Invalid status'}).encode())
                            return
                        filters['status'] = status
                    if 'priority' in params:
                        priority = params['priority'][0]
                        if priority not in ['low', 'normal', 'high']:
                            self._set_headers(400)
                            self.wfile.write(json.dumps({'error': 'Invalid priority'}).encode())
                            return
                        filters['priority'] = priority
                    
                    tasks, total = self.app._get_tasks(user_id, filters)
                    self._set_headers(200)
                    self.wfile.write(json.dumps({
                        'items': tasks,
                        'total': total
                    }).encode())
                return
        
        self._set_headers(404)
        self.wfile.write(b'Not Found')
    
    def do_POST(self):
        if self.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if self.path == '/api/login':
                data = self._read_json()
                if data is None:
                    return
                
                username = data.get('username')
                password = data.get('password')
                
                if not username or not password:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({'error': 'Missing username or password'}).encode())
                    return
                
                conn = sqlite3.connect(self.app.db_path)
                cursor = conn.execute(
                    'SELECT id, password_hash, password_salt FROM users WHERE username = ?',
                    (username,)
                )
                user = cursor.fetchone()
                conn.close()
                
                if user and self.app._verify_password(password, user[1], user[2]):
                    session_id = self.app._create_session(user[0])
                    self._set_headers(200)
                    self.send_header('Set-Cookie', f'session={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_EXPIRY}')
                    self.end_headers()
                    self.wfile.write(json.dumps({'username': username}).encode())
                else:
                    self._set_headers(401)
                    self.wfile.write(json.dumps({'error': 'Invalid credentials'}).encode())
                return
            
            if self.path == '/api/tasks':
                user_id = self._require_login()
                if user_id:
                    data = self._read_json()
                    if data is None:
                        return
                    
                    task_data, error = self.app._validate_task_data(data)
                    if error:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({'error': error}).encode())
                        return
                    
                    conn = sqlite3.connect(self.app.db_path)
                    cursor = conn.execute(
                        'INSERT INTO tasks (owner_id, title, description, status, priority) VALUES (?, ?, ?, ?, ?)',
                        (user_id, task_data['title'], task_data['description'], task_data['status'], task_data['priority'])
                    )
                    task_id = cursor.lastrowid
                    conn.commit()
                    conn.close()
                    
                    self._set_headers(201)
                    self.wfile.write(json.dumps({
                        'id': task_id,
                        'title': task_data['title'],
                        'description': task_data['description'],
                        'status': task_data['status'],
                        'priority': task_data['priority']
                    }).encode())
                return
            
            if self.path == '/api/logout':
                session_id = self._get_session_id()
                if session_id:
                    self.app._delete_session(session_id)
                self._set_headers(200)
                self.send_header('Set-Cookie', 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True}).encode())
                return
        
        self._set_headers(405)
        self.wfile.write(b'Method Not Allowed')
    
    def do_PUT(self):
        if self.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if self.path.startswith('/api/tasks/'):
                parts = self.path.split('/')
                if len(parts) == 4 and parts[3].isdigit():
                    task_id = int(parts[3])
                    user_id = self._require_login()
                    if user_id:
                        data = self._read_json()
                        if data is None:
                            return
                        
                        task_data, error = self.app._validate_task_data(data)
                        if error:
                            self._set_headers(400)
                            self.wfile.write(json.dumps({'error': error}).encode())
                            return
                        
                        conn = sqlite3.connect(self.app.db_path)
                        cursor = conn.execute(
                            'SELECT id FROM tasks WHERE id = ? AND owner_id = ?',
                            (task_id, user_id)
                        )
                        if not cursor.fetchone():
                            conn.close()
                            self._set_headers(404)
                            self.wfile.write(json.dumps({'error': 'Task not found'}).encode())
                            return
                        
                        conn.execute(
                            'UPDATE tasks SET title = ?, description = ?, status = ?, priority = ? WHERE id = ?',
                            (task_data['title'], task_data['description'], task_data['status'], task_data['priority'], task_id)
                        )
                        conn.commit()
                        
                        cursor = conn.execute(
                            'SELECT id, title, description, status, priority FROM tasks WHERE id = ?',
                            (task_id,)
                        )
                        task = cursor.fetchone()
                        conn.close()
                        
                        self._set_headers(200)
                        self.wfile.write(json.dumps({
                            'id': task[0],
                            'title': task[1],
                            'description': task[2],
                            'status': task[3],
                            'priority': task[4]
                        }).encode())
                    return
        
        self._set_headers(405)
        self.wfile.write(b'Method Not Allowed')
    
    def do_DELETE(self):
        if self.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if self.path.startswith('/api/tasks/'):
                parts = self.path.split('/')
                if len(parts) == 4 and parts[3].isdigit():
                    task_id = int(parts[3])
                    user_id = self._require_login()
                    if user_id:
                        conn = sqlite3.connect(self.app.db_path)
                        cursor = conn.execute(
                            'SELECT id FROM tasks WHERE id = ? AND owner_id = ?',
                            (task_id, user_id)
                        )
                        if not cursor.fetchone():
                            conn.close()
                            self._set_headers(404)
                            self.wfile.write(json.dumps({'error': 'Task not found'}).encode())
                            return
                        
                        conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
                        conn.commit()
                        conn.close()
                        
                        self._set_headers(200)
                        self.wfile.write(json.dumps({'deleted': True}).encode())
                    return
        
        self._set_headers(405)
        self.wfile.write(b'Method Not Allowed')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Görev Masası - Personal Task Manager')
    parser.add_argument('--port', type=int, required=True, help='Port to listen on')
    parser.add_argument('--db', type=str, required=True, help='Path to SQLite database')
    args = parser.parse_args()
    
    app = TaskManagerApp(args.db)
    handler = lambda *args, **kwargs: RequestHandler(*args, app=app, **kwargs)
    server = HTTPServer(('127.0.0.1', args.port), handler)
    print(f'Server running on http://127.0.0.1:{args.port}')
    server.serve_forever()