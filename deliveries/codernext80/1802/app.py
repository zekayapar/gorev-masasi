import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple

SESSION_TIMEOUT_HOURS = 24
SESSIONS: Dict[str, Tuple[str, datetime]] = {}
DB_PATH = ""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'todo',
            priority TEXT DEFAULT 'normal',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_id) REFERENCES users(id)
        )
    """)
    conn.commit()
    conn.close()

def seed_demo_accounts():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM users WHERE username IN ('zeka', 'yapar')")
    if c.fetchone()[0] > 0:
        conn.close()
        return
    
    demo_accounts = [
        ("zeka", "deneme-zeka-2026"),
        ("yapar", "deneme-yapar-2026")
    ]
    
    for username, password in demo_accounts:
        salt = secrets.token_hex(16)
        password_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100000
        ).hex()
        try:
            c.execute("INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
                     (username, password_hash, salt))
        except sqlite3.IntegrityError:
            pass
    
    conn.commit()
    conn.close()

def verify_password(username: str, password: str) -> Optional[int]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, password_hash, salt FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    user_id, stored_hash, salt = row
    computed_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    
    if hmac.compare_digest(computed_hash, stored_hash):
        return user_id
    return None

def create_session(user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=SESSION_TIMEOUT_HOURS)
    SESSIONS[session_id] = (str(user_id), expires)
    return session_id

def get_session_user(session_id: str) -> Optional[int]:
    if session_id not in SESSIONS:
        return None
    user_id_str, expires = SESSIONS[session_id]
    if datetime.utcnow() > expires:
        del SESSIONS[session_id]
        return None
    return int(user_id_str)

def invalidate_session(session_id: str):
    if session_id in SESSIONS:
        del SESSIONS[session_id]

def validate_task_input(data: Dict[str, Any], is_update: bool = False) -> Tuple[Dict[str, Any], Optional[str]]:
    errors = []
    
    allowed_keys = {'title', 'description', 'status', 'priority'}
    if not is_update:
        allowed_keys.add('id')
    
    for key in data:
        if key not in allowed_keys:
            return {}, f"Unknown field: {key}"
    
    if 'title' in data:
        title = data['title']
        if not isinstance(title, str):
            return {}, "title must be a string"
        title = title.strip()
        if not title or len(title) > 120:
            return {}, "title must be 1-120 characters"
        data['title'] = title
    elif not is_update:
        return {}, "title is required"
    
    if 'description' in data:
        desc = data['description']
        if not isinstance(desc, str):
            return {}, "description must be a string"
        if len(desc) > 1000:
            return {}, "description must be <= 1000 characters"
        data['description'] = desc
    
    if 'status' in data:
        status = data['status']
        if status not in ('todo', 'doing', 'done'):
            return {}, "status must be one of: todo, doing, done"
    
    if 'priority' in data:
        priority = data['priority']
        if priority not in ('low', 'normal', 'high'):
            return {}, "priority must be one of: low, normal, high"
    
    if is_update and 'id' in data:
        if not isinstance(data['id'], int) or data['id'] <= 0:
            return {}, "id must be a positive integer"
    
    return data, None

class TaskHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    
    def log_message(self, format, *args):
        pass
    
    def send_json(self, status_code: int, data: Dict[str, Any]):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def send_html(self, status_code: int, html: str):
        body = html.encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    
    def get_session(self) -> Optional[int]:
        cookie = self.headers.get('Cookie', '')
        for part in cookie.split(';'):
            part = part.strip()
            if part.startswith('session='):
                session_id = part[8:]
                return get_session_user(session_id)
        return None
    
    def check_origin(self) -> bool:
        origin = self.headers.get('Origin')
        if origin is None:
            return True
        expected_origin = f"http://127.0.0.1:{args.port}"
        return origin == expected_origin
    
    def do_GET(self):
        if not self.check_origin():
            self.send_json(403, {"error": "Forbidden origin"})
            return
        
        if self.path == '/health':
            self.send_json(200, {"ok": True})
            return
        
        if self.path == '/':
            try:
                with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'r', encoding='utf-8') as f:
                    html = f.read()
                self.send_html(200, html)
            except FileNotFoundError:
                self.send_json(500, {"error": "index.html not found"})
            return
        
        if self.path.startswith('/api/'):
            if self.path == '/api/me':
                user_id = self.get_session()
                if user_id is None:
                    self.send_json(401, {"error": "Unauthorized"})
                    return
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT username FROM users WHERE id = ?", (user_id,))
                row = c.fetchone()
                conn.close()
                
                if row:
                    self.send_json(200, {"username": row[0]})
                else:
                    self.send_json(401, {"error": "Unauthorized"})
                return
            
            if self.path == '/api/stats':
                user_id = self.get_session()
                if user_id is None:
                    self.send_json(401, {"error": "Unauthorized"})
                    return
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ?", (user_id,))
                total = c.fetchone()[0]
                
                c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'todo'", (user_id,))
                todo = c.fetchone()[0]
                
                c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'doing'", (user_id,))
                doing = c.fetchone()[0]
                
                c.execute("SELECT COUNT(*) FROM tasks WHERE owner_id = ? AND status = 'done'", (user_id,))
                done = c.fetchone()[0]
                
                conn.close()
                
                self.send_json(200, {
                    "total": total,
                    "todo": todo,
                    "doing": doing,
                    "done": done
                })
                return
            
            if self.path == '/api/tasks':
                user_id = self.get_session()
                if user_id is None:
                    self.send_json(401, {"error": "Unauthorized"})
                    return
                
                query_params = {}
                if '?' in self.path:
                    query_string = self.path.split('?', 1)[1]
                    query_params = urllib.parse.parse_qs(query_string)
                
                q = query_params.get('q', [''])[0]
                status = query_params.get('status', [''])[0]
                priority = query_params.get('priority', [''])[0]
                
                if status and status not in ('todo', 'doing', 'done'):
                    self.send_json(400, {"error": "Invalid status"})
                    return
                if priority and priority not in ('low', 'normal', 'high'):
                    self.send_json(400, {"error": "Invalid priority"})
                    return
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                
                sql = "SELECT id, title, description, status, priority FROM tasks WHERE owner_id = ?"
                params = [user_id]
                
                if q:
                    sql += " AND (title LIKE ? OR description LIKE ?)"
                    q_param = f"%{q}%"
                    params.extend([q_param, q_param])
                
                if status:
                    sql += " AND status = ?"
                    params.append(status)
                
                if priority:
                    sql += " AND priority = ?"
                    params.append(priority)
                
                sql += " ORDER BY id DESC"
                
                c.execute(sql, params)
                rows = c.fetchall()
                
                count_sql = "SELECT COUNT(*) FROM tasks WHERE owner_id = ?"
                count_params = [user_id]
                if q:
                    count_sql += " AND (title LIKE ? OR description LIKE ?)"
                    count_params.extend([q_param, q_param])
                if status:
                    count_sql += " AND status = ?"
                    count_params.append(status)
                if priority:
                    count_sql += " AND priority = ?"
                    count_params.append(priority)
                
                c.execute(count_sql, count_params)
                total = c.fetchone()[0]
                
                conn.close()
                
                tasks = []
                for row in rows:
                    tasks.append({
                        "id": row[0],
                        "title": row[1],
                        "description": row[2],
                        "status": row[3],
                        "priority": row[4]
                    })
                
                self.send_json(200, {"items": tasks, "total": total})
                return
            
            parts = self.path.split('/')
            if len(parts) == 3 and parts[2].isdigit():
                task_id = int(parts[2])
                user_id = self.get_session()
                if user_id is None:
                    self.send_json(401, {"error": "Unauthorized"})
                    return
                
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                
                c.execute("SELECT id, title, description, status, priority FROM tasks WHERE id = ? AND owner_id = ?",
                         (task_id, user_id))
                row = c.fetchone()
                
                if not row:
                    conn.close()
                    self.send_json(404, {"error": "Task not found"})
                    return
                
                task = {
                    "id": row[0],
                    "title": row[1],
                    "description": row[2],
                    "status": row[3],
                    "priority": row[4]
                }
                conn.close()
                
                self.send_json(200, task)
                return
            
            self.send_json(404, {"error": "Not found"})
            return
        
        self.send_json(404, {"error": "Not found"})
    
    def do_POST(self):
        if not self.check_origin():
            self.send_json(403, {"error": "Forbidden origin"})
            return
        
        if self.path == '/api/login':
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 16384:
                self.send_json(413, {"error": "Request body too large"})
                return
            
            try:
                body = self.rfile.read(content_length)
                data = json.loads(body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "Invalid JSON"})
                return
            
            if not isinstance(data, dict):
                self.send_json(400, {"error": "Request body must be a JSON object"})
                return
            
            username = data.get('username')
            password = data.get('password')
            
            if not isinstance(username, str) or not isinstance(password, str):
                self.send_json(400, {"error": "username and password must be strings"})
                return
            
            user_id = verify_password(username, password)
            if user_id is None:
                self.send_json(401, {"error": "Invalid credentials"})
                return
            
            session_id = create_session(user_id)
            self.send_json(200, {"username": username})
            self.send_header('Set-Cookie', f'session={session_id}; HttpOnly; SameSite=Strict; Path=/')
            return
        
        if self.path == '/api/logout':
            session_id = None
            cookie = self.headers.get('Cookie', '')
            for part in cookie.split(';'):
                part = part.strip()
                if part.startswith('session='):
                    session_id = part[8:]
                    break
            
            if session_id:
                invalidate_session(session_id)
            
            self.send_json(200, {"ok": True})
            self.send_header('Set-Cookie', 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            return
        
        if self.path == '/api/tasks':
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Unauthorized"})
                return
            
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 16384:
                self.send_json(413, {"error": "Request body too large"})
                return
            
            try:
                body = self.rfile.read(content_length)
                data = json.loads(body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "Invalid JSON"})
                return
            
            if not isinstance(data, dict):
                self.send_json(400, {"error": "Request body must be a JSON object"})
                return
            
            validated_data, error = validate_task_input(data)
            if error:
                self.send_json(400, {"error": error})
                return
            
            title = validated_data.get('title', '')
            description = validated_data.get('description', '')
            status = validated_data.get('status', 'todo')
            priority = validated_data.get('priority', 'normal')
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                INSERT INTO tasks (owner_id, title, description, status, priority)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, title, description, status, priority))
            task_id = c.lastrowid
            conn.commit()
            conn.close()
            
            self.send_json(201, {
                "id": task_id,
                "title": title,
                "description": description,
                "status": status,
                "priority": priority
            })
            return
        
        parts = self.path.split('/')
        if len(parts) == 3 and parts[2].isdigit():
            task_id = int(parts[2])
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Unauthorized"})
                return
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute("SELECT id FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user_id))
            if not c.fetchone():
                conn.close()
                self.send_json(404, {"error": "Task not found"})
                return
            
            if self.path in [f"/api/tasks/{task_id}"]:
                content_length = int(self.headers.get('Content-Length', 0))
                if content_length > 16384:
                    self.send_json(413, {"error": "Request body too large"})
                    return
                
                try:
                    body = self.rfile.read(content_length)
                    data = json.loads(body.decode('utf-8'))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self.send_json(400, {"error": "Invalid JSON"})
                    return
                
                if not isinstance(data, dict):
                    self.send_json(400, {"error": "Request body must be a JSON object"})
                    return
                
                validated_data, error = validate_task_input(data, is_update=True)
                if error:
                    self.send_json(400, {"error": error})
                    return
                
                title = validated_data.get('title', '')
                description = validated_data.get('description', '')
                status = validated_data.get('status', 'todo')
                priority = validated_data.get('priority', 'normal')
                
                c.execute("""
                    UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
                    WHERE id = ? AND owner_id = ?
                """, (title, description, status, priority, task_id, user_id))
                conn.commit()
                conn.close()
                
                self.send_json(200, {
                    "id": task_id,
                    "title": title,
                    "description": description,
                    "status": status,
                    "priority": priority
                })
                return
        
        self.send_json(404, {"error": "Not found"})
    
    def do_PUT(self):
        if not self.check_origin():
            self.send_json(403, {"error": "Forbidden origin"})
            return
        
        parts = self.path.split('/')
        if len(parts) == 3 and parts[2].isdigit():
            task_id = int(parts[2])
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Unauthorized"})
                return
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute("SELECT id FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user_id))
            if not c.fetchone():
                conn.close()
                self.send_json(404, {"error": "Task not found"})
                return
            
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length > 16384:
                self.send_json(413, {"error": "Request body too large"})
                return
            
            try:
                body = self.rfile.read(content_length)
                data = json.loads(body.decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "Invalid JSON"})
                return
            
            if not isinstance(data, dict):
                self.send_json(400, {"error": "Request body must be a JSON object"})
                return
            
            validated_data, error = validate_task_input(data, is_update=True)
            if error:
                self.send_json(400, {"error": error})
                return
            
            title = validated_data.get('title', '')
            description = validated_data.get('description', '')
            status = validated_data.get('status', 'todo')
            priority = validated_data.get('priority', 'normal')
            
            c.execute("""
                UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?
                WHERE id = ? AND owner_id = ?
            """, (title, description, status, priority, task_id, user_id))
            conn.commit()
            conn.close()
            
            self.send_json(200, {
                "id": task_id,
                "title": title,
                "description": description,
                "status": status,
                "priority": priority
            })
            return
        
        self.send_json(404, {"error": "Not found"})
    
    def do_DELETE(self):
        if not self.check_origin():
            self.send_json(403, {"error": "Forbidden origin"})
            return
        
        parts = self.path.split('/')
        if len(parts) == 3 and parts[2].isdigit():
            task_id = int(parts[2])
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Unauthorized"})
                return
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            
            c.execute("SELECT id FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user_id))
            row = c.fetchone()
            
            if not row:
                conn.close()
                self.send_json(404, {"error": "Task not found"})
                return
            
            c.execute("DELETE FROM tasks WHERE id = ? AND owner_id = ?", (task_id, user_id))
            conn.commit()
            conn.close()
            
            self.send_json(200, {"deleted": True})
            return
        
        self.send_json(404, {"error": "Not found"})
    
    def do_OPTIONS(self):
        self.send_json(200, {"ok": True})

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Görev Masası Task Manager')
    parser.add_argument('--port', type=int, required=True, help='Port to listen on')
    parser.add_argument('--db', type=str, required=True, help='Absolute path to SQLite database')
    args = parser.parse_args()
    
    DB_PATH = args.db
    
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
    
    init_db()
    seed_demo_accounts()
    
    server = HTTPServer(('127.0.0.1', args.port), TaskHandler)
    print(f"Server running on http://127.0.0.1:{args.port}")
    server.serve_forever()
