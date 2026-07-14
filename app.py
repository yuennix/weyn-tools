import os
import json
import time
import threading
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
import weyn
import auth

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

auth.init_db()

_job_thread        = None
_stop_event        = None
_job_lock          = threading.Lock()
_force_logout_keys: set = set()

ADMIN_PASSWORD = 'yuennix'


# ── Auth helpers ─────────────────────────────────────────────────────────────

def is_authenticated():
    return auth.check_key_valid(session.get('auth_key'))


def is_admin():
    return session.get('admin_logged_in') is True


# ── Gate / Auth routes ────────────────────────────────────────────────────────

@app.route('/gate')
def gate():
    if is_authenticated():
        return redirect('/')
    return render_template('gate.html')


@app.route('/api/generate_key', methods=['POST'])
def api_generate_key():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'ok': False, 'error': 'Name is required'})
    if len(name) > 40:
        return jsonify({'ok': False, 'error': 'Name too long'})
    key = auth.generate_key(name)
    return jsonify({'ok': True, 'key': key})


@app.route('/api/validate_key', methods=['POST'])
def api_validate_key():
    data      = request.get_json()
    key       = (data.get('key') or '').strip().upper()
    device_id = (data.get('device_id') or '').strip()
    if not key or not device_id:
        return jsonify({'ok': False, 'error': 'Missing key or device ID'})
    ok, result = auth.validate_key(key, device_id)
    if ok:
        session.permanent = True
        session['auth_key'] = key
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': result})


@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.pop('auth_key', None)
    return jsonify({'ok': True})


@app.route('/api/key_info')
def api_key_info():
    key = session.get('auth_key')
    if not key:
        return jsonify({'error': 'Not authenticated'}), 403
    import sqlite3
    conn = sqlite3.connect(auth.DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute('SELECT name, expires_at, approved_at, status FROM access_keys WHERE key=?', (key,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'error': 'Key not found'}), 404
    return jsonify({'name': row['name'], 'expires_at': row['expires_at'], 'approved_at': row['approved_at'], 'status': row['status']})


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if request.method == 'POST':
        pw = (request.form.get('password') or '').strip()
        if pw == ADMIN_PASSWORD:
            session['admin_logged_in'] = True
            return redirect('/admin')
        return render_template('admin_login.html', error='Wrong password')
    if not is_admin():
        return render_template('admin_login.html', error=None)
    return render_template('admin.html')


@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_logged_in', None)
    return jsonify({'ok': True})


@app.route('/admin/api/keys')
def admin_api_keys():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify({'keys': auth.get_all_keys()})


@app.route('/admin/api/approve', methods=['POST'])
def admin_api_approve():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json()
    key  = (data.get('key') or '').strip()
    mins = int(data.get('duration_minutes', 60))
    if not key or mins < 1:
        return jsonify({'ok': False, 'error': 'Invalid input'})
    auth.approve_key(key, mins)
    return jsonify({'ok': True})


@app.route('/admin/api/revoke', methods=['POST'])
def admin_api_revoke():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json()
    key  = (data.get('key') or '').strip()
    if not key:
        return jsonify({'ok': False, 'error': 'Key required'})
    auth.revoke_key(key)
    _force_logout_keys.add(key)
    return jsonify({'ok': True})


@app.route('/admin/api/delete', methods=['POST'])
def admin_api_delete():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json()
    key  = (data.get('key') or '').strip()
    if not key:
        return jsonify({'ok': False, 'error': 'Key required'})
    auth.delete_key(key)
    _force_logout_keys.add(key)
    return jsonify({'ok': True})


# ── Main app routes (protected) ───────────────────────────────────────────────

@app.route('/')
def index():
    if not is_authenticated():
        return redirect('/gate')
    return render_template('index.html')


@app.route('/api/start', methods=['POST'])
def start():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403
    global _job_thread, _stop_event
    with _job_lock:
        if weyn._web_state.get('running'):
            return jsonify({'error': 'Already running'}), 400
        data          = request.get_json()
        token         = (data.get('token') or '').strip()
        chat_id       = (data.get('chat_id') or '').strip()
        min_followers = int(data.get('min_followers', 0) or 0)

        if not token or not chat_id:
            return jsonify({'error': 'Bot Token and Chat ID are required'}), 400

        method = (data.get('method') or '1').strip()
        year_raw    = data.get('year_choice')
        year_choice = None
        if year_raw is not None:
            try:
                year_choice = int(year_raw)
            except (ValueError, TypeError):
                year_choice = None

        _stop_event = threading.Event()
        if method == '2':
            weyn._web_state['method'] = '2'
            _job_thread = threading.Thread(
                target=weyn.run_method2_web,
                args=(token, chat_id, min_followers, _stop_event),
                daemon=True
            )
        elif method == '3':
            weyn._web_state['method'] = '3'
            _job_thread = threading.Thread(
                target=weyn.run_method3_web,
                args=(token, chat_id, _stop_event),
                daemon=True
            )
        else:
            method = '1'
            weyn._web_state['method'] = '1'
            _job_thread = threading.Thread(
                target=weyn.run_method1_web,
                args=(token, chat_id, year_choice, min_followers, _stop_event),
                daemon=True
            )
        _job_thread.start()
    return jsonify({'status': 'started', 'method': method})


@app.route('/api/stop', methods=['POST'])
def stop():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403
    global _stop_event
    with _job_lock:
        if _stop_event:
            _stop_event.set()
    weyn.force_stop()
    return jsonify({'status': 'stopped'})


@app.route('/api/download_hits')
def download_hits():
    if not is_authenticated():
        return redirect('/gate')
    path = weyn.HITS_FILE
    if not os.path.exists(path):
        return ('No hits have been saved yet.', 404)
    return __import__('flask').send_file(
        os.path.abspath(path),
        as_attachment=True,
        download_name='weyn_hits.txt',
        mimetype='text/plain'
    )


_M2_HI2_FILE = 'm2_hi2.txt'
_M3_HI2_FILE = 'm3_hi2.txt'


@app.route('/api/download_m2')
def download_m2():
    if not is_authenticated():
        return redirect('/gate')
    emails = list(weyn._m2_email_list)
    if not emails:
        return ('No M2 hits this session.', 404)
    # Rewrite the same file on disk every download so it always reflects
    # the current session's hits (emails only, one per line).
    with open(_M2_HI2_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(emails) + '\n')
    return __import__('flask').send_file(
        os.path.abspath(_M2_HI2_FILE),
        as_attachment=True,
        download_name='hi2.txt',
        mimetype='text/plain'
    )


@app.route('/api/download_m3')
def download_m3():
    if not is_authenticated():
        return redirect('/gate')
    emails = list(weyn._m3_email_list)
    if not emails:
        return ('No M3 hits this session.', 404)
    with open(_M3_HI2_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(emails) + '\n')
    return __import__('flask').send_file(
        os.path.abspath(_M3_HI2_FILE),
        as_attachment=True,
        download_name='hi2.txt',
        mimetype='text/plain'
    )


@app.route('/api/find_chat_id', methods=['POST'])
def find_chat_id():
    if not is_authenticated():
        return jsonify({'ok': False, 'error': 'Unauthorized'}), 403
    data  = request.get_json()
    token = (data.get('token') or '').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'Bot Token is required'}), 400
    try:
        import requests as req
        resp = req.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={'limit': 100, 'allowed_updates': ['message']},
            timeout=10
        )
        body = resp.json()
        if not body.get('ok'):
            return jsonify({'ok': False, 'error': body.get('description', 'Failed to get updates')})
        chats = {}
        for update in body.get('result', []):
            msg = update.get('message') or update.get('channel_post')
            if msg:
                chat = msg['chat']
                cid  = chat['id']
                if chat['type'] in ('group', 'supergroup', 'channel'):
                    chats[cid] = {
                        'id':   cid,
                        'name': chat.get('title', str(cid)),
                        'type': chat['type'],
                    }
        return jsonify({'ok': True, 'chats': list(chats.values())})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/stats')
def stats():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403

    auth_key = session.get('auth_key')

    def generate():
        _auth_check_counter = 0
        _heartbeat_counter  = 0
        while True:
            # Immediate force-logout when admin revokes or deletes this key
            if auth_key in _force_logout_keys:
                _force_logout_keys.discard(auth_key)
                yield f"event: expired\ndata: {json.dumps({'expired': True})}\n\n"
                return
            _auth_check_counter += 1
            _heartbeat_counter  += 1
            # Thresholds doubled (sleep is 0.5s) so wall-clock intervals stay same:
            # auth check every 60 ticks = 30 s, heartbeat every 30 ticks = 15 s
            if _auth_check_counter >= 60:
                _auth_check_counter = 0
                if not auth.check_key_valid(auth_key):
                    yield f"event: expired\ndata: {json.dumps({'expired': True})}\n\n"
                    return
            if _heartbeat_counter >= 30:
                _heartbeat_counter = 0
                yield ": keepalive\n\n"
            running    = weyn._web_state.get('running', False)
            tg_status  = weyn._web_state.get('tg_status', '')
            tg_error   = weyn._web_state.get('tg_error', '')
            method     = weyn._web_state.get('method', '1')
            start_time = weyn._web_state.get('start_time')
            elapsed    = (time.time() - start_time) if (running and start_time) else 0
            if method == '2':
                payload = {
                    'running'    : running,
                    'method'     : '2',
                    'hits'       : weyn._m2_hits,
                    'good'       : weyn._m2_good_insta,
                    'bad_insta'  : weyn._m2_bad_insta,
                    'bad_email'  : weyn._m2_bad_email,
                    'total'      : weyn._m2_total,
                    'scanned'    : weyn._m2_scanned,
                    'elapsed'    : elapsed,
                    'recent_hits': list(weyn._m2_found_emails[-20:]),
                    'tg_status'  : tg_status,
                    'tg_error'   : tg_error,
                }
            elif method == '3':
                payload = {
                    'running'    : running,
                    'method'     : '3',
                    'hits'       : weyn._m3_hits,
                    'good'       : weyn._m3_good_insta,
                    'bad_insta'  : weyn._m3_bad_insta,
                    'bad_email'  : weyn._m3_bad_email,
                    'total'      : weyn._m3_total,
                    'scanned'    : weyn._m3_scanned,
                    'elapsed'    : elapsed,
                    'recent_hits': list(weyn._m3_found_emails[-20:]),
                    'tg_status'  : tg_status,
                    'tg_error'   : tg_error,
                }
            else:
                payload = {
                    'running'    : running,
                    'method'     : '1',
                    'hits'       : weyn._m1_hits,
                    'good'       : weyn._m1_good_insta,
                    'bad_insta'  : weyn._m1_bad_insta,
                    'bad_email'  : weyn._m1_bad_email,
                    'total'      : weyn._m1_total,
                    'scanned'    : weyn._m1_scanned,
                    'elapsed'    : elapsed,
                    'recent_hits': list(weyn._m1_found_emails[-20:]),
                    'tg_status'  : tg_status,
                    'tg_error'   : tg_error,
                }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(0.5)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
