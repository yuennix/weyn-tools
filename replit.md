# WEYN TOOLS

A Flask-based web tool for automated Instagram account scanning. Features a cyber-themed dark UI, an access key authentication system, real-time live stats via Server-Sent Events, and Telegram hit notifications.

## Project Structure

- `app.py` — Flask routes, auth helpers, API endpoints, SSE stats stream
- `weyn.py` — Core scanning engine (Method 1), threading, Instagram API calls, Telegram notifications
- `auth.py` — SQLite-backed access key management (generate, approve, revoke, expire)
- `main.py` — Gunicorn entry point
- `templates/` — Jinja2 HTML templates (gate, index, admin, admin_login)
- `static/` — CSS and JS frontend assets
- `keys.db` — SQLite database for access keys (auto-created on first run)

## Running the App

```
python -m gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app
```

## Environment Variables

- `SESSION_SECRET` — Flask session secret key (required)

## Deployment

Deployed as a **VM** (always-running) on Replit. Autoscale is not suitable because the scanner uses persistent in-memory state and long-running background threads.

## User Preferences

- Keep the cyber/dark aesthetic for all UI changes
- Do not change the access key authentication system
