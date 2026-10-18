# Görev Masası

Turkish personal task manager web application.

## Launch Command

```bash
python app.py --port 8080 --db /absolute/path/tasks.sqlite
```

Replace `8080` with your desired port and `/absolute/path/tasks.sqlite` with the absolute path to your database file.

## Demo Accounts

- **zeka** / **deneme-zeka-2026**
- **yapar** / **deneme-yapar-2026**

## Data Persistence

Data is stored in the SQLite database file specified by the `--db` argument. The database is created if it does not exist, and demo accounts are seeded only once during initial setup. Existing data is preserved across restarts.

## Security Notes

This application is designed for local personal use only. Passwords are hashed using salted PBKDF2-HMAC-SHA256 with 100,000 iterations. Sessions are stored in memory and expire after 24 hours. The server binds only to 127.0.0.1 for security.

## Features

- Login with demo accounts
- Create, read, update, and delete tasks
- Task status: Yapılacak, Sürüyor, Tamamlandı
- Task priority: Düşük, Normal, Yüksek
- Task filtering by search, status, and priority
- Task statistics (total, todo, doing, done)
- Responsive UI for mobile devices

## Technical Details

- Python 3.12 standard library only
- SQLite3 for persistence
- Vanilla HTML/CSS/JS
- No external dependencies
- Cookie-based session management with HttpOnly, SameSite=Strict
- Origin validation for mutating requests
- Input validation and sanitization