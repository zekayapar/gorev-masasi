import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse, unquote
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
        return row if row else None
    
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
    
    def list_tasks(self, user_id, q='', status='', priority=''):
        query = "SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?"
        params = [user_id]
        
        if q:
            query += " AND (title LIKE ? OR description LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        
        if status and status in ['todo', 'doing', 'done']:
            query += " AND status = ?"
            params.append(status)
        
        if priority and priority in ['low', 'normal', 'high']:
            query += " AND priority = ?"
            params.append(priority)
        
        query += " ORDER BY id DESC"
        rows = self.conn.execute(query, params).fetchall()
        
        total_query = "SELECT COUNT(*) FROM tasks WHERE owner_id = ?"
        total_params = [user_id]
        if q:
            total_query += " AND (title LIKE ? OR description LIKE ?)"
            total_params.extend([f"%{q}%", f"%{q}%"])
        if status and status in ['todo', 'doing', 'done']:
            total_query += " AND status = ?"
            total_params.append(status)
        if priority and priority in ['low', 'normal', 'high']:
            total_query += " AND priority = ?"
            total_params.append(priority)
        
        total = self.conn.execute(total_query, total_params).fetchone()[0]
        
        return {"items": rows, "total": total}
    
    def get_stats(self, user_id):
        total = self.conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ?", (user_id,)).fetchone()[0]
        todo = self.conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'todo'", (user_id,)).fetchone()[0]
        doing = self.conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'doing'", (user_id,)).fetchone()[0]
        done = self.conn.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'done'", (user_id,)).fetchone()[0]
        return {"total": total, "todo": todo, "doing": doing, "done": done}
    
    def close(self):
        self.conn.close()

class RequestHandler(BaseHTTPRequestHandler):
    def __init__(self, *args, app=None, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)
    
    def _set_headers(self, status=200, content_type='application/json', extra_headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
    
    def _read_json_body(self, max_size=16384):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > max_size:
            self._set_headers(413)
            self.wfile.write(json.dumps({"error": "Payload too large"}).encode())
            return None
        
        try:
            raw = self.rfile.read(content_length)
            return json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            self._set_headers(400)
            self.wfile.write(json.dumps({"error": "Invalid JSON"}).encode())
            return None
    
    def _get_session_user(self):
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            if part.strip().startswith('session='):
                token = part.strip().split('=')[1]
                return self.app.validate_session(token)
        return None
    
    def _require_login(self):
        user_id = self._get_session_user()
        if not user_id:
            self._set_headers(401)
            self.wfile.write(json.dumps({"error": "Unauthorized"}).encode())
            return None
        return user_id
    
    def _check_origin(self):
        origin = self.headers.get('Origin')
        if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
            self._set_headers(403)
            self.wfile.write(json.dumps({"error": "Forbidden"}).encode())
            return False
        return True
    
    def do_GET(self):
        if self.path == '/health':
            self._set_headers(200)
            self.wfile.write(json.dumps({"ok": True}).encode())
            return
        
        if self.path == '/':
            with open('index.html', 'rb') as f:
                content = f.read()
            self._set_headers(200, 'text/html')
            self.wfile.write(content)
            return
        
        if self.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if self.path == '/api/me':
                user_id = self._require_login()
                if user_id:
                    username = self.app.conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()[0]
                    self._set_headers(200)
                    self.wfile.write(json.dumps({"username": username}).encode())
                return
            
            if self.path == '/api/stats':
                user_id = self._require_login()
                if user_id:
                    stats = self.app.get_stats(user_id)
                    self._set_headers(200)
                    self.wfile.write(json.dumps(stats).encode())
                return
            
            if self.path.startswith('/api/tasks/'):
                parts = self.path.split('/')
                if len(parts) != 4:
                    self._set_headers(404)
                    self.wfile.write(json.dumps({"error": "Not found"}).encode())
                    return
                
                try:
                    task_id = int(parts[3])
                except ValueError:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Invalid task ID"}).encode())
                    return
                
                user_id = self._require_login()
                if user_id:
                    task = self.app.get_task(user_id, task_id)
                    if task:
                        self._set_headers(200)
                        self.wfile.write(json.dumps({
                            "id": task[0],
                            "title": task[1],
                            "description": task[2],
                            "status": task[3],
                            "priority": task[4]
                        }).encode())
                    else:
                        self._set_headers(404)
                        self.wfile.write(json.dumps({"error": "Not found"}).encode())
                return
            
            if self.path.startswith('/api/tasks?'):
                user_id = self._require_login()
                if user_id:
                    query = urlparse(self.path).query
                    params = parse_qs(query)
                    q = unquote(params.get('q', [''])[0])
                    status = unquote(params.get('status', [''])[0])
                    priority = unquote(params.get('priority', [''])[0])
                    
                    if status and status not in ['todo', 'doing', 'done']:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Invalid status"}).encode())
                        return
                    
                    if priority and priority not in ['low', 'normal', 'high']:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Invalid priority"}).encode())
                        return
                    
                    result = self.app.list_tasks(user_id, q, status, priority)
                    self._set_headers(200)
                    self.wfile.write(json.dumps({
                        "items": [
                            {
                                "id": row[0],
                                "title": row[1],
                                "description": row[2],
                                "status": row[3],
                                "priority": row[4]
                            }
                            for row in result['items']
                        ],
                        "total": result['total']
                    }).encode())
                return
            
            self._set_headers(404)
            self.wfile.write(json.dumps({"error": "Not found"}).encode())
            return
        
        self._set_headers(404)
        self.wfile.write(json.dumps({"error": "Not found"}).encode())
    
    def do_POST(self):
        if self.path.startswith('/api/'):
            if not self._check_origin():
                return
            
            if self.path == '/api/login':
                body = self._read_json_body()
                if not body:
                    return
                
                if not isinstance(body, dict) or 'username' not in body or 'password' not in body:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Invalid request"}).encode())
                    return
                
                username = body['username']
                password = body['password']
                user = self.app._get_user_credentials(username)
                
                if not user or not self.app._verify_password(password, user[1], user[2]):
                    self._set_headers(401)
                    self.wfile.write(json.dumps({"error": "Unauthorized"}).encode())
                    return
                
                token = self.app.create_session(user[0])
                self._set_headers(200, extra_headers={
                    'Set-Cookie': f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={self.app.session_lifetime}'
                })
                self.wfile.write(json.dumps({"username": username}).encode())
                return
            
            if self.path == '/api/logout':
                user_id = self._require_login()
                if user_id:
                    cookie = self.headers.get('Cookie', '')
                    token = None
                    for part in cookie.split(';'):
                        if part.strip().startswith('session='):
                            token = part.strip().split('=')[1]
                            break
                    if token:
                        self.app.invalidate_session(token)
                    self._set_headers(200)
                    self.wfile.write(json.dumps({"ok": True}).encode())
                return
            
            if self.path == '/api/tasks':
                user_id = self._require_login()
                if user_id:
                    body = self._read_json_body()
                    if not body:
                        return
                    
                    if not isinstance(body, dict):
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Invalid request"}).encode())
                        return
                    
                    title = body.get('title', '').strip()
                    if not title or len(title) > 120:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Invalid title"}).encode())
                        return
                    
                    description = body.get('description', '')
                    if len(description) > 1000:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Description too long"}).encode())
                        return
                    
                    status = body.get('status', 'todo')
                    if status not in ['todo', 'doing', 'done']:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Invalid status"}).encode())
                        return
                    
                    priority = body.get('priority', 'normal')
                    if priority not in ['low', 'normal', 'high']:
                        self._set_headers(400)
                        self.wfile.write(json.dumps({"error": "Invalid priority"}).encode())
                        return
                    
                    task_id = self.app.create_task(user_id, title, description, status, priority)
                    task = self.app.get_task(user_id, task_id)
                    self._set_headers(201)
                    self.wfile.write(json.dumps({
                        "id": task[0],
                        "title": task[1],
                        "description": task[2],
                        "status": task[3],
                        "priority": task[4]
                    }).encode())
                return
            
            self._set_headers(405)
            self.wfile.write(json.dumps({"error": "Method not allowed"}).encode())
            return
        
        self._set_headers(404)
        self.wfile.write(json.dumps({"error": "Not found"}).encode())
    
    def do_PUT(self):
        if self.path.startswith('/api/tasks/'):
            if not self._check_origin():
                return
            
            parts = self.path.split('/')
            if len(parts) != 4:
                self._set_headers(404)
                self.wfile.write(json.dumps({"error": "Not found"}).encode())
                return
            
            try:
                task_id = int(parts[3])
            except ValueError:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Invalid task ID"}).encode())
                return
            
            user_id = self._require_login()
            if user_id:
                body = self._read_json_body()
                if not body:
                    return
                
                if not isinstance(body, dict):
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Invalid request"}).encode())
                    return
                
                title = body.get('title', '').strip()
                if not title or len(title) > 120:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Invalid title"}).encode())
                    return
                
                description = body.get('description', '')
                if len(description) > 1000:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Description too long"}).encode())
                    return
                
                status = body.get('status', 'todo')
                if status not in ['todo', 'doing', 'done']:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Invalid status"}).encode())
                    return
                
                priority = body.get('priority', 'normal')
                if priority not in ['low', 'normal', 'high']:
                    self._set_headers(400)
                    self.wfile.write(json.dumps({"error": "Invalid priority"}).encode())
                    return
                
                task = self.app.update_task(user_id, task_id, title, description, status, priority)
                if task:
                    self._set_headers(200)
                    self.wfile.write(json.dumps({
                        "id": task[0],
                        "title": task[1],
                        "description": task[2],
                        "status": task[3],
                        "priority": task[4]
                    }).encode())
                else:
                    self._set_headers(404)
                    self.wfile.write(json.dumps({"error": "Not found"}).encode())
            return
        
        self._set_headers(405)
        self.wfile.write(json.dumps({"error": "Method not allowed"}).encode())
    
    def do_DELETE(self):
        if self.path.startswith('/api/tasks/'):
            if not self._check_origin():
                return
            
            parts = self.path.split('/')
            if len(parts) != 4:
                self._set_headers(404)
                self.wfile.write(json.dumps({"error": "Not found"}).encode())
                return
            
            try:
                task_id = int(parts[3])
            except ValueError:
                self._set_headers(400)
                self.wfile.write(json.dumps({"error": "Invalid task ID"}).encode())
                return
            
            user_id = self._require_login()
            if user_id:
                self.app.delete_task(user_id, task_id)
                self._set_headers(200)
                self.wfile.write(json.dumps({"deleted": True}).encode())
            return
        
        self._set_headers(405)
        self.wfile.write(json.dumps({"error": "Method not allowed"}).encode())
    
    def do_OPTIONS(self):
        if self.path.startswith('/api/'):
            self._set_headers(200, extra_headers={
                'Allow': 'GET, POST, PUT, DELETE, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, Origin',
                'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS'
            })
        else:
            self._set_headers(404)
    
    def log_message(self, format, *args):
        pass

def run(port, db_path):
    app = TaskManagerApp(db_path)
    handler_class = lambda *args, **kwargs: RequestHandler(*args, app=app, **kwargs)
    server = HTTPServer(('127.0.0.1', port), handler_class)
    print(f"Server running on http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--db', type=str, required=True)
    args = parser.parse_args()
    run(args.port, args.db)