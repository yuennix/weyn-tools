# WEYN

A Flask web app with a key-based authentication gate, admin panel, and Telegram job runner.

## Stack
- **Backend:** Python / Flask
- **Server:** Gunicorn (gthread, 4 threads, timeout 0)
- **Auth:** SQLite-backed key system (`keys.db`)
- **Telegram:** `pytelegrambotapi` via `weyn.py`

## How to run
The `Start application` workflow runs:
```
python -m gunicorn --bind 0.0.0.0:5000 --reuse-port --reload --worker-class gthread --threads 4 --timeout 0 main:app
```

## Key details
- `SESSION_SECRET` env secret is required (already set).
- Admin panel is at `/admin` — default password is hardcoded in `app.py`.
- Users request a key at `/gate`, admin approves it at `/admin`.
- `auth.py` manages the SQLite key database (`keys.db`).
- `weyn.py` contains the Telegram scraping/job logic.

## User preferences
