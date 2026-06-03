import os
import json
import time
import threading
from flask import Flask, render_template, request, jsonify, Response
import weyn

app = Flask(__name__)

_job_thread = None
_stop_event = None
_job_lock   = threading.Lock()


@app.route('/')
def index():
    years = [str(y) for _, _, y in weyn.ID_RANGES]
    return render_template('index.html', years=years)


@app.route('/api/start', methods=['POST'])
def start():
    global _job_thread, _stop_event
    with _job_lock:
        if weyn._web_state.get('running'):
            return jsonify({'error': 'Already running'}), 400
        data          = request.get_json()
        method        = str(data.get('method', '1'))
        token         = (data.get('token') or '').strip()
        chat_id       = (data.get('chat_id') or '').strip()
        year_raw      = data.get('year', '')
        min_followers = int(data.get('min_followers', 0) or 0)
        year_choice   = int(year_raw) if year_raw else None

        if not token or not chat_id:
            return jsonify({'error': 'Bot Token and Chat ID are required'}), 400

        targets = {
            '1': weyn.run_method1_web,
            '2': weyn.run_method2_web,
            '3': weyn.run_method3_web,
            '4': weyn.run_method4_web,
        }
        fn = targets.get(method)
        if not fn:
            return jsonify({'error': 'Invalid method'}), 400

        _stop_event = threading.Event()
        _job_thread = threading.Thread(
            target=fn,
            args=(token, chat_id, year_choice, min_followers, _stop_event),
            daemon=True
        )
        _job_thread.start()
    return jsonify({'status': 'started', 'method': method})


@app.route('/api/stop', methods=['POST'])
def stop():
    global _stop_event
    with _job_lock:
        if _stop_event:
            _stop_event.set()
        weyn._web_state['running'] = False
    return jsonify({'status': 'stopped'})


@app.route('/api/stats')
def stats():
    def generate():
        while True:
            m       = weyn._web_state.get('method') or '1'
            running = weyn._web_state.get('running', False)
            if m == '1':
                payload = {
                    'running'    : running,
                    'method'     : '1',
                    'hits'       : weyn._m1_hits,
                    'good'       : weyn._m1_good_insta,
                    'bad_insta'  : weyn._m1_bad_insta,
                    'bad_email'  : weyn._m1_bad_email,
                    'taken'      : weyn._m1_taken,
                    'limit'      : weyn._m1_limit,
                    'total'      : weyn._m1_total,
                    'verified'   : 0,
                    'recent_hits': list(weyn._m1_found_emails[-20:]),
                }
            else:
                with weyn._web_lock:
                    payload = {
                        'running'    : running,
                        'method'     : m,
                        'hits'       : weyn._web_state.get('hits', 0),
                        'good'       : weyn._web_state.get('good', 0),
                        'bad_insta'  : weyn._web_state.get('bad_insta', 0),
                        'bad_email'  : 0,
                        'taken'      : weyn._web_state.get('taken', 0),
                        'limit'      : weyn._web_state.get('limit', 0),
                        'total'      : 0,
                        'verified'   : weyn._web_state.get('verified', 0),
                        'recent_hits': list(weyn._web_state.get('recent_hits', []))[-20:],
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
