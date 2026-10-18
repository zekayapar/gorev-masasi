import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timedelta

DB_SCHEMA = [
    "PRAGMA foreign_keys = ON;",
    "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, password_salt TEXT NOT NULL);",
    "CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, token TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE);",
    "CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'todo', priority TEXT NOT NULL DEFAULT 'normal', created_at INTEGER NOT NULL, FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE);"
]

DEMO_USERS = [
    ("zeka", "deneme-zeka-2026"),
    ("yapar", "deneme-yapar-2026")
]

class TaskManagerApp:
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_db()
        self.sessions = {}
        self.session_lifetime = 24 * 60 * 60  # 24 hours
    
    def _init_db(self):
        for stmt in DB_SCHEMA:
            self.conn.execute(stmt)
        self.conn.commit()
        self._seed_demo_users()
    
    def _seed_demo_users(self):
        for username, password in DEMO_USERS:
            if not self._get_user_id(username):
                salt = secrets.token_hex(16)
                password_hash = self._hash_password(password, salt)
                self.conn.execute(
                    "INSERT INTO users (username, password_hash, password_salt) VALUES (?, ?, ?)",
                    (username, password_hash, salt)
                )
        self.conn.commit()
    
    def _hash_password(self, password, salt):
        return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 100000).hex()
    
    def _verify_password(self, password, stored_hash, salt):
        return secrets.compare_digest(self._hash_password(password, salt), stored_hash)
    
    def _get_user_id(self, username):
        row = self.conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        return row[0] if row else None
    
    def _get_user_credentials(self, username):
        row = self.conn.execute(
            "SELECT id, password_hash, password_salt FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        return row if row else None
    
    def create_session(self, user_id):
        token = secrets.token_hex(32)
        expires_at = int(time.time()) + self.session_lifetime
        self.conn.execute(
            "INSERT INTO sessions (user_id, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, token, int(time.time()), expires_at)
        )
        self.conn.commit()
        return token
    
    def validate_session(self, token):
        if not token:
            return None
        row = self.conn.execute(
            "SELECT user_id FROM sessions WHERE token = ? AND expires_at > ?",
            (token, int(time.time()))
        ).fetchone()
        return row[0] if row else None
    
    def invalidate_session(self, token):
        self.conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        self.conn.commit()
    
    def cleanup_expired_sessions(self):
        self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        self.conn.commit()
    
    def create_task(self, user_id, title, description='', status='todo', priority='normal'):
        self.conn.execute(
            "INSERT INTO tasks (owner_id, title, description, status, priority, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, title, description, status, priority, int(time.time()))
        )
        self.conn.commit()
        return self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    
    def get_task(self, user_id, task_id):
        row = self.conn.execute(
            "SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = ?",
            (task_id, user_id)
        ).fetchone()
        if not row:
            return None
        return {
            'id': row[0],
            'title': row[1],
            'description': row[2],
            'status': row[3],
            'priority': row[4]
        }
    
    def update_task(self, user_id, task_id, title, description='', status='todo', priority='normal'):
        self.conn.execute(
            "UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?, created_at = ? WHERE id = ? AND owner_id = ?",
            (title, description, status, priority, int(time.time()), task_id, user_id)
        )
        self.conn.commit()
        return self.get_task(user_id, task_id)
    
    def delete_task(self, user_id, task_id):
        self.conn.execute("DELETE FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user_id))
        self.conn.commit()
        return True
    
    def list_tasks(self, user_id, q=None, status=None, priority=None):
        query = "SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?"
        params = [user_id]
        
        if q:
            query += " AND (title LIKE ? OR description LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        
        if status:
            query += " AND status = ?"
            params.append(status)
        
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        
        query += " ORDER BY id DESC"
        rows = self.conn.execute(query, params).fetchall()
        
        tasks = [
            {
                'id': row[0],
                'title': row[1],
                'description': row[2],
                'status': row[3],
                'priority': row[4]
            }
            for row in rows
        ]
        
        total = len(tasks)
        return {'items': tasks, 'total': total}
    
    def get_stats(self, user_id):
        rows = self.conn.execute(
            "SELECT status, COUNT(*) FROM tasks WHERE owner_id = ? GROUP BY status",
            (user_id,)
        ).fetchall()
        
        stats = {'total': 0, 'todo': 0, 'doing': 0, 'done': 0}
        for status, count in rows:
            stats['total'] += count
            stats[status] = count
        
        return stats

class RequestHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, app=None, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)
    
    def _set_headers(self, status=200, content_type='application/json', extra_headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
    
    def _read_json(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 16384:
            self._set_headers(413)
            self.wfile.write(json.dumps({'error': 'Payload too large'}).encode())
            return None
        
        try:
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
            return None
    
    def _get_session(self):
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            if part.strip().startswith('session='):
                return part.strip().split('=')[1]
        return None
    
    def _check_origin(self):
        origin = self.headers.get('Origin')
        if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
            self._set_headers(403)
            self.wfile.write(json.dumps({'error': 'Forbidden'}).encode())
            return False
        return True
    
    def _require_login(self):
        if not self._check_origin():
            return None
        token = self._get_session()
        user_id = self.app.validate_session(token)
        if not user_id:
            self._set_headers(401)
            self.wfile.write(json.dumps({'error': 'Unauthorized'}).encode())
            return None
        return user_id
    
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/health':
            self._set_headers(200)
            self.wfile.write(json.dumps({'ok': True}).encode())
            return
        
        if parsed.path == '/':
            try:
                with open('index.html', 'rb') as f:
                    content = f.read()
                self._set_headers(200, 'text/html')
                self.wfile.write(content)
            except FileNotFoundError:
                self._set_headers(404)
                self.wfile.write(b'Not Found')
            return
        
        if parsed.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if parsed.path == '/api/me':
                user_id = self._require_login()
                if user_id is None:
                    return
                username = self.app.conn.execute(
                    "SELECT username FROM users WHERE id = ?",
                    (user_id,)
                ).fetchone()[0]
                self._set_headers(200)
                self.wfile.write(json.dumps({'username': username}).encode())
                return
            
            if parsed.path == '/api/stats':
                user_id = self._require_login()
                if user_id is None:
                    return
                stats = self.app.get_stats(user_id)
                self._set_headers(200)
                self.wfile.write(json.dumps(stats).encode())
                return
            
            if parsed.path.startswith('/api/tasks/'):
                parts = parsed.path.split('/')
                if len(parts) != 4:
                    self._set_headers(404)
                    self.wfile.write(json.dumps({'error': 'Not found'}).encode())
                    return
                
                try:
                    task_id = int(parts[3])
                except ValueError:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({'error': 'Invalid task ID'}).encode())
                    return
                
                user_id = self._require_login()
                if user_id is None:
                    return
                
                task = self.app.get_task(user_id, task_id)
                if not task:
                    self._set_headers(404)
                    self.wfile.write(json.dumps({'error': 'Not found'}).encode())
                    return
                
                self._set_headers(200)
                self.wfile.write(json.dumps(task).encode())
                return
            
            if parsed.path == '/api/tasks':
                user_id = self._require_login()
                if user_id is None:
                    return
                
                query_params = parse_qs(parsed.query)
                q = query_params.get('q', [None])[0]
                status = query_params.get('status', [None])[0]
                priority = query_params.get('priority', [None])[0]
                
                if status and status not in ['todo', 'doing', 'done']:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({'error': 'Invalid status'}).encode())
                    return
                
                if priority and priority not in ['low', 'normal', 'high']:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({'error': 'Invalid priority'}).encode())
                    return
                
                result = self.app.list_tasks(user_id, q, status, priority)
                self._set_headers(200)
                self.wfile.write(json.dumps(result).encode())
                return
        
        self._set_headers(404)
        self.wfile.write(json.dumps({'error': 'Not found'}).encode())
    
    def do_POST(self):
        parsed = urlparse(self.path)
        
        if parsed.path == '/api/login':
            if not self._check_origin():
                return
            
            data = self._read_json()
            if data is None:
                return
            
            if not isinstance(data, dict):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
                return
            
            username = data.get('username')
            password = data.get('password')
            
            if not username or not password:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Missing credentials'}).encode())
                return
            
            user = self.app._get_user_credentials(username)
            if not user or not self.app._verify_password(password, user[1], user[2]):
                self._set_headers(401)
                self.wfile.write(json.dumps({'error': 'Invalid credentials'}).encode())
                return
            
            token = self.app.create_session(user[0])
            self._set_headers(200, extra_headers={
                'Set-Cookie': f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={self.app.session_lifetime}'
            })
            self.wfile.write(json.dumps({'username': username}).encode())
            return
        
        if parsed.path == '/api/logout':
            if not self._check_origin():
                return
            
            token = self._get_session()
            if token:
                self.app.invalidate_session(token)
            
            self._set_headers(200, extra_headers={
                'Set-Cookie': 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
            })
            self.wfile.write(json.dumps({'ok': True}).encode())
            return
        
        if parsed.path == '/api/tasks':
            if not self._check_origin():
                return
            
            user_id = self._require_login()
            if user_id is None:
                return
            
            data = self._read_json()
            if data is None:
                return
            
            if not isinstance(data, dict):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
                return
            
            title = data.get('title', '').strip()
            description = data.get('description', '')
            status = data.get('status', 'todo')
            priority = data.get('priority', 'normal')
            
            if 'id' in data or 'owner_id' in data:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Unknown keys'}).encode())
                return
            
            if not title or len(title) > 120:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid title'}).encode())
                return
            
            if len(description) > 1000:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Description too long'}).encode())
                return
            
            if status not in ['todo', 'doing', 'done']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid status'}).encode())
                return
            
            if priority not in ['low', 'normal', 'high']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid priority'}).encode())
                return
            
            task_id = self.app.create_task(user_id, title, description, status, priority)
            task = self.app.get_task(user_id, task_id)
            self._set_headers(201)
            self.wfile.write(json.dumps(task).encode())
            return
        
        self._set_headers(405)
        self.wfile.write(json.dumps({'error': 'Method not allowed'}).encode())
    
    def do_PUT(self):
        parsed = urlparse(self.path)
        
        if parsed.path.startswith('/api/tasks/'):
            if not self._check_origin():
                return
            
            parts = parsed.path.split('/')
            if len(parts) != 4:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Not found'}).encode())
                return
            
            try:
                task_id = int(parts[3])
            except ValueError:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid task ID'}).encode())
                return
            
            user_id = self._require_login()
            if user_id is None:
                return
            
            data = self._read_json()
            if data is None:
                return
            
            if not isinstance(data, dict):
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid JSON'}).encode())
                return
            
            title = data.get('title', '').strip()
            description = data.get('description', '')
            status = data.get('status', 'todo')
            priority = data.get('priority', 'normal')
            
            if 'id' in data or 'owner_id' in data:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Unknown keys'}).encode())
                return
            
            if not title or len(title) > 120:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid title'}).encode())
                return
            
            if len(description) > 1000:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Description too long'}).encode())
                return
            
            if status not in ['todo', 'doing', 'done']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid status'}).encode())
                return
            
            if priority not in ['low', 'normal', 'high']:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid priority'}).encode())
                return
            
            task = self.app.update_task(user_id, task_id, title, description, status, priority)
            if not task:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Not found'}).encode())
                return
            
            self._set_headers(200)
            self.wfile.write(json.dumps(task).encode())
            return
        
        self._set_headers(405)
        self.wfile.write(json.dumps({'error': 'Method not allowed'}).encode())
    
    def do_DELETE(self):
        parsed = urlparse(self.path)
        
        if parsed.path.startswith('/api/tasks/'):
            if not self._check_origin():
                return
            
            parts = parsed.path.split('/')
            if len(parts) != 4:
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Not found'}).encode())
                return
            
            try:
                task_id = int(parts[3])
            except ValueError:
                self._set_headers(400)
                self.wfile.write(json.dumps({'error': 'Invalid task ID'}).encode())
                return
            
            user_id = self._require_login()
            if user_id is None:
                return
            
            if not self.app.delete_task(user_id, task_id):
                self._set_headers(404)
                self.wfile.write(json.dumps({'error': 'Not found'}).encode())
                return
            
            self._set_headers(200)
            self.wfile.write(json.dumps({'deleted': True}).encode())
            return
        
        self._set_headers(405)
        self.wfile.write(json.dumps({'error': 'Method not allowed'}).encode())

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--db', type=str, required=True)
    args = parser.parse_args()
    
    app = TaskManagerApp(args.db)
    handler = lambda *args, **kwargs: RequestHandler(*args, app=app, **kwargs)
    server = HTTPServer(('127.0.0.1', args.port), handler)
    print(f"Server running on http://127.0.0.1:{args.port}")
    server.serve_forever()