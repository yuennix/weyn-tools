import os
import json
import time
import threading
from datetime import timedelta
from flask import Flask, render_template, request, jsonify, Response, session, redirect, url_for
import weyn
import auth

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', os.environ.get('SECRET_KEY', 'weyn-tools-secret-8x2k9p'))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=365)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True

auth.init_db()

_job_thread = None
_stop_event = None
_job_lock   = threading.Lock()

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
    return jsonify({'ok': True})


@app.route('/admin/api/extend', methods=['POST'])
def admin_api_extend():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json()
    key  = (data.get('key') or '').strip()
    mins = int(data.get('extra_minutes', 0) or 0)
    if not key or mins < 1:
        return jsonify({'ok': False, 'error': 'Invalid input'})
    auth.extend_key(key, mins)
    return jsonify({'ok': True})


@app.route('/admin/api/set_revoke_device', methods=['POST'])
def admin_api_set_revoke_device():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data    = request.get_json()
    key     = (data.get('key') or '').strip()
    enabled = data.get('enabled', True)
    if not key:
        return jsonify({'ok': False, 'error': 'Key required'})
    auth.set_key_revoke_device(key, enabled)
    return jsonify({'ok': True})


@app.route('/admin/api/settings', methods=['GET'])
def admin_api_settings_get():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify({'revoke_device_enabled': auth.get_setting('revoke_device_enabled', '1') == '1'})


@app.route('/admin/api/settings', methods=['POST'])
def admin_api_settings_set():
    if not is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json()
    enabled = data.get('revoke_device_enabled', True)
    auth.set_setting('revoke_device_enabled', '1' if enabled else '0')
    return jsonify({'ok': True})


@app.route('/api/settings')
def api_settings_public():
    return jsonify({'revoke_device_enabled': auth.get_setting('revoke_device_enabled', '1') == '1'})


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

        _stop_event = threading.Event()
        _job_thread = threading.Thread(
            target=weyn.run_method1_web,
            args=(token, chat_id, None, min_followers, _stop_event),
            daemon=True
        )
        _job_thread.start()
    return jsonify({'status': 'started', 'method': '1'})


@app.route('/api/stop', methods=['POST'])
def stop():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403
    global _stop_event
    with _job_lock:
        if _stop_event:
            _stop_event.set()
        weyn._web_state['running'] = False
    weyn.close_all_sessions()
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


@app.route('/api/key_info')
def key_info():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403
    key = session.get('auth_key')
    conn = auth.get_db()
    row = conn.execute('SELECT name, expires_at, can_revoke_device FROM access_keys WHERE key=?', (key,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'name': 'Unknown', 'expires_at': None, 'key': key, 'can_revoke_device': True})
    return jsonify({
        'name': row['name'],
        'expires_at': row['expires_at'],
        'key': key,
        'can_revoke_device': bool(row['can_revoke_device'])
    })


@app.route('/api/revoke_device', methods=['POST'])
def revoke_device():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403
    key = session.get('auth_key')
    conn = auth.get_db()
    conn.execute('UPDATE access_keys SET device_id=NULL WHERE key=?', (key,))
    conn.commit()
    conn.close()
    session.pop('auth_key', None)
    return jsonify({'ok': True})


@app.route('/api/stats')
def stats():
    if not is_authenticated():
        return jsonify({'error': 'Unauthorized'}), 403

    def generate():
        while True:
            running   = weyn._web_state.get('running', False)
            tg_status = weyn._web_state.get('tg_status', '')
            tg_error  = weyn._web_state.get('tg_error', '')
            payload = {
                'running'    : running,
                'hits'       : weyn._m1_hits,
                'good'       : weyn._m1_good_insta,
                'bad_insta'  : weyn._m1_bad_insta,
                'bad_email'  : weyn._m1_bad_email,
                'taken'      : weyn._m1_taken,
                'limit'      : weyn._m1_limit,
                'total'      : weyn._m1_total,
                'recent_hits': list(weyn._m1_found_emails[-20:]),
                'tg_status'  : tg_status,
                'tg_error'   : tg_error,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(0.5)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
