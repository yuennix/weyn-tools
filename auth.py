import sqlite3
import string
import random
from datetime import datetime, timedelta

DB = 'keys.db'


def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS access_keys (
            key         TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'pending',
            created_at  TEXT NOT NULL,
            approved_at TEXT,
            expires_at  TEXT,
            device_id   TEXT
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    conn.execute(
        'INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)',
        ('revoke_device_enabled', '1')
    )
    conn.commit()
    conn.close()


def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()


def _rand_seg(n=4):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))


def generate_key(name):
    key = f"WEYN-{_rand_seg()}-{_rand_seg()}-{_rand_seg()}"
    conn = get_db()
    conn.execute(
        'INSERT INTO access_keys (key, name, status, created_at) VALUES (?,?,?,?)',
        (key, name, 'pending', datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
    return key


def validate_key(key, device_id):
    conn = get_db()
    row = conn.execute('SELECT * FROM access_keys WHERE key=?', (key,)).fetchone()
    if not row:
        conn.close()
        return False, 'Key not found'

    status = row['status']

    if status == 'pending':
        conn.close()
        return False, 'Key is pending admin approval'
    if status == 'revoked':
        conn.close()
        return False, 'Key has been revoked'
    if status == 'expired':
        conn.close()
        return False, 'Key has expired'

    if status == 'approved':
        if row['expires_at']:
            expires = datetime.fromisoformat(row['expires_at'])
            if datetime.utcnow() > expires:
                conn.execute('UPDATE access_keys SET status=? WHERE key=?', ('expired', key))
                conn.commit()
                conn.close()
                return False, 'Key has expired'

        if row['device_id'] and row['device_id'] != device_id:
            conn.close()
            return False, 'Key is already bound to another device'

        conn.execute(
            'UPDATE access_keys SET device_id=? WHERE key=?',
            (device_id, key)
        )
        conn.commit()
        conn.close()
        return True, None

    conn.close()
    return False, 'Unknown key state'


def check_key_valid(key):
    """Used on every request — just checks the key is still approved & not expired."""
    if not key:
        return False
    conn = get_db()
    row = conn.execute('SELECT * FROM access_keys WHERE key=?', (key,)).fetchone()
    if not row:
        conn.close()
        return False
    if row['status'] in ('revoked', 'pending', 'expired'):
        conn.close()
        return False
    if row['expires_at']:
        expires = datetime.fromisoformat(row['expires_at'])
        if datetime.utcnow() > expires:
            conn.execute('UPDATE access_keys SET status=? WHERE key=?', ('expired', key))
            conn.commit()
            conn.close()
            return False
    conn.close()
    return True


def get_all_keys():
    conn = get_db()
    rows = conn.execute('SELECT * FROM access_keys ORDER BY created_at DESC').fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d['expires_at']:
            expires = datetime.fromisoformat(d['expires_at'])
            if d['status'] == 'approved' and datetime.utcnow() > expires:
                d['status'] = 'expired'
        result.append(d)
    return result


def approve_key(key, duration_minutes):
    expires_at = (datetime.utcnow() + timedelta(minutes=duration_minutes)).isoformat()
    conn = get_db()
    conn.execute(
        'UPDATE access_keys SET status=?, approved_at=?, expires_at=?, device_id=NULL WHERE key=?',
        ('approved', datetime.utcnow().isoformat(), expires_at, key)
    )
    conn.commit()
    conn.close()


def revoke_key(key):
    conn = get_db()
    conn.execute('UPDATE access_keys SET status=? WHERE key=?', ('revoked', key))
    conn.commit()
    conn.close()


def delete_key(key):
    conn = get_db()
    conn.execute('DELETE FROM access_keys WHERE key=?', (key,))
    conn.commit()
    conn.close()
