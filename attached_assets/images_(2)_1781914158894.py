import requests
import random
import string
import time
import sys
from cfonts import render
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread, Lock
from colorama import Fore, Style, Back, init
import os
from hashlib import md5
import secrets
from random import choice
import subprocess
import uuid
import json
import re
from datetime import datetime
init()

try:
    import httpx
except:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx", "httpx[http2]"])
    import httpx

try:
    from rich.console import Console
except ImportError:
    os.system("pip install requests telethon pyfiglet rich cfonts")
    from rich.console import Console

try:
    import aiohttp
except:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp"])
    import aiohttp

try:
    import asyncio
except:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "asyncio"])

# ─── Color definitions ───
b = random.randint(5, 208)
bo = f'\x1b[38;5;{b}m'
ED = '\x1b[38;5;208m'
BLUE = '\033[94m'
Z = '\033[1;31m'
YELLOW = '\033[1;33m'
O = '\033[2;31m'
F = '\033[2;32m'
A = '\033[2;34m'
C = '\033[2;35m'
M = '\033[2;36m'
Y = '\033[1;34m'
Z1 = '\033[2;31m'
B = "\033[1;30m"
R = "\033[1;31m"
G = "\033[1;32m"
Y = "\033[1;33m"
Bl = "\033[1;34m"
P = "\033[1;35m"
C = "\033[1;36m"
W = "\033[1;37m"
X = '\033[1;33m'
S = '\033[1;33m'
J = '\033[2;36m'
N = '\033[1;37m'
U = '\x1b[1;37m'
reset = "\033[0m"
bold = "\033[1m"
dim = "\033[2m"
italic = "\033[3m"

fg_lavender = "\033[38;5;183m"
fg_mint = "\033[38;5;121m"
fg_blue = "\033[38;5;75m"
fg_red = "\033[38;5;196m"
fg_pink = "\033[38;5;213m"
fg_cyan = "\033[38;5;51m"
fg_yellow = "\033[38;5;226m"
fg_orange = "\033[38;5;208m"
fg_white = "\033[38;5;255m"
fg_green = "\033[38;5;82m"
fg_gold = "\033[38;5;220m"
fg_purple = "\033[38;5;141m"


token = input("⌈━─━─━─≪ 𝑻𝑶𝑲𝑬𝑵 ≫─━─━─━⌉\n➤ ")
print("\n")
ID = input("⌈━─━─━─≪ 𝑰𝑫 ≫─━─━─━⌉\n➤ ")

os.system("clear" if os.name == 'posix' else 'cls')

used_usernames = set()
lock = Lock()
hit = 0
badmail = 0
badinsta = 0
goodinsta = 0
bad_user = 0
fucked = 0
good = 0
taken = 0
bad = 0
limit = 0
info_db = {}

session = requests.Session()

def generate_android_ua():
    devices = [
        {"brand": "samsung", "model": "SM-G973F", "device": "beyond1", "board": "exynos9820", "cpu": "exynos9820"},
        {"brand": "samsung", "model": "SM-A536B", "device": "a53x", "board": "s5e8825", "cpu": "exynos1280"},
        {"brand": "samsung", "model": "SM-S918B", "device": "dm1q", "board": "kalama", "cpu": "qcom"},
        {"brand": "Google", "model": "Pixel 6", "device": "raven", "board": "raven", "cpu": "gs101"},
        {"brand": "Google", "model": "Pixel 7", "device": "panther", "board": "panther", "cpu": "gs201"},
        {"brand": "Xiaomi", "model": "M2102J20SG", "device": "ares", "board": "mt6893", "cpu": "mtk"},
        {"brand": "Xiaomi", "model": "Redmi Note 10", "device": "sweet", "board": "sm6150", "cpu": "qcom"},
        {"brand": "OnePlus", "model": "ONEPLUS A6003", "device": "OnePlus6", "board": "sdm845", "cpu": "qcom"},
        {"brand": "OPPO", "model": "CPH2371", "device": "OP4F1F", "board": "mt6893", "cpu": "mtk"},
        {"brand": "HUAWEI", "model": "ELE-L29", "device": "HWELE", "board": "kirin980", "cpu": "hisilicon"},
    ]
    device = random.choice(devices)
    android_version = random.choice(["10", "11", "12", "13", "14"])
    api_level = {"10": "29", "11": "30", "12": "31", "13": "33", "14": "34"}[android_version]
    dpi = random.choice(["320", "360", "394", "411", "420", "440", "450", "480"])
    width = random.choice(["720", "1080", "1440"])
    height = random.choice(["1520", "1600", "2280", "2340", "2400", "2560", "3200"])
    instagram_ver = f"{random.randint(280, 340)}.0.0.{random.randint(10, 40)}.{random.randint(80, 150)}"
    locale = random.choice(["en_US", "en_GB", "ar_SA"])
    random_num = random.randint(300000000, 400000000)
    ua = (f"Instagram {instagram_ver} Android ({api_level}/{android_version}; "
          f"{dpi}dpi; {width}x{height}; {device['brand']}; {device['model']}; "
          f"{device['device']}; {device['board']}; {locale}; {random_num})")
    return ua


def gen_jazoest():
    return str(random.randint(10000, 99999))

def gen_session_id():
    part1 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    part2 = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{part1}:{part2}:{random.randint(100,999)}"

def gen_device_id():
    return "android-" + secrets.token_hex(8)

def gen_uuid():
    return str(uuid.uuid4())

def gen_mid():
    return base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('=')


def display():
    stats = (
        f"\r"
        f"{fg_gold}{bold}╭━━━〔 ✦ 𝐋𝐈𝐕𝐄 𝐒𝐓𝐀𝐓𝐒 ✦ 〕━━━╮{reset}\n"
        f"{fg_lavender}{bold}┃ ✦ 𝐇𝐈𝐓𝐒        ➤ {reset}{fg_white}{hit}{reset}\n"
        f"{fg_green}{bold}┃ ✦ 𝐆𝐎𝐎𝐃        ➤ {reset}{fg_white}{good}{reset}\n"
        f"{fg_red}{bold}┃ ✦ 𝐁𝐀𝐃         ➤ {reset}{fg_white}{bad}{reset}\n"
        f"{fg_blue}{bold}┃ ✦ 𝐓𝐀𝐊𝐄𝐍       ➤ {reset}{fg_white}{taken}{reset}\n"
        f"{fg_mint}{bold}┃ ✦ 𝐁𝐀𝐃𝐌𝐀𝐈𝐋    ➤ {reset}{fg_white}{badmail}{reset}\n"
        f"{fg_gold}{bold}╰━━━〔 ✦ 𝐀𝐋𝐄𝐗 𝐏𝐀𝐍𝐄𝐋 ✦ 〕━━━╯{reset}"
        f"{' ' * 10}"
    )
    sys.stdout.write(stats)
    sys.stdout.flush()


def send_telegram(msg):
    try:
        requests.get(
            f"https://api.telegram.org/bot{token}/sendMessage?chat_id={ID}&text={msg}",
            timeout=5
        )
    except:
        pass


def write_hit(msg):
    with open('pookie.txt', 'a') as ff:
        ff.write(f'{msg}\n')



# ═══════════════════════════════════════════════
# 🚀 ULTRA-FAST API - REPLACES hi2.in & masked email
# ═══════════════════════════════════════════════

def check_email_ultra_fast_v1(email, client):
    """V1: Direct Instagram check_email endpoint - fastest, <500ms"""
    url = "https://i.instagram.com/api/v1/users/check_email/"
    ua = generate_android_ua()
    headers = {
        'User-Agent': ua,
        'content-type': "application/x-www-form-urlencoded; charset=UTF-8",
        'x-ig-app-id': "567067343352427",
        'accept-language': "en-IN, en-US",
    }
    try:
        resp = client.post(url, data=f"email={email}", headers=headers, timeout=10)
        if 'email_is_taken' in resp.text:
            return "registered"
        elif 'available' in resp.text.lower() or 'Email' in resp.text:
            return "not_registered"
        return "check_v2"
    except:
        return "check_v2"


def check_email_ultra_fast_v2(email, client):
    """V2: Bloks/CAA search endpoint - fast fallback"""
    android = "android-" + secrets.token_hex(8)
    device = str(uuid.uuid4())
    family = str(uuid.uuid4())
    url = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    payload = {
        'params': '{"client_input_params":{"search_query":"' + email + '","was_headers_prefill_available":0,"was_headers_prefill_used":0,"text_input_id":"akyuf0:61","accounts_list":[],"fetched_email_list":[],"fetched_email_token_list":{},"sso_accounts_auth_data":[],"ig_oauth_token":[],"auth_secure_device_id":"","encrypted_msisdn":"","is_oauth_without_permission":0,"is_whatsapp_installed":1,"is_from_logged_in_switcher":0,"flash_call_permissions_status":{"READ_PHONE_STATE":"PERMANENTLY_DENIED","READ_CALL_LOG":"DENIED","ANSWER_PHONE_CALLS":"DENIED"}},"server_params":{"event_request_id":"' + str(uuid.uuid4()) + '","is_from_logged_out":0,"device_id":"' + android + '","login_surface":"login_home","waterfall_id":"' + str(uuid.uuid4()) + '","is_platform_login":0,"login_entry_point":"logged_out","family_device_id":"' + family + '","qe_device_id":"' + device + '"}}',
        'bk_client_context': '{"bloks_version":"5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b","styles_id":"instagram"}',
        'bloks_versioning_id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b"
    }
    headers = {
        'User-Agent': generate_android_ua(),
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
        'x-pigeon-session-id': f"UFS-{uuid.uuid4()}-0",
    }
    try:
        resp = client.post(url, data=payload, headers=headers, timeout=10)
        if email in resp.text:
            return "registered"
        return "not_registered"
    except:
        return "unknown"


# ═══════════════════════════════════════════════
# 🔥 NEW LIGHTWEIGHT HI2 ALTERNATIVE (NO RECAPTCHA)
# ═══════════════════════════════════════════════

def check_registration_fast(domain, prefix):
    """Ultra-fast domain-based check - no recaptcha, no bullshit.
    Uses multiple free APIs in parallel."""
    results = []
    email = f"{prefix}@{domain}"
    
    # API 1: MailBoxLayer-like free check
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
    except:
        pass
    
    # API 2: Hunter.io-style verification (free tier)
    try:
        r = requests.get(
            f"https://emailvalidation.abstractapi.com/v1/?api_key=YOUR_FREE_KEY&email={email}",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            if data.get('quality_score', 0) > 0.5:
                results.append("valid_email")
    except:
        pass
    
    # API 3: Quick email format validation
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        results.append("valid_format")
    
    return results


def get_domain_stats(email):
    """Get domain creation date and stats - replaces masked email lookup"""
    domain = email.split('@')[1]
    stats = {
        'domain': domain,
        'age': 'unknown',
        'mx': False,
        'valid': False
    }
    try:
        # Quick DNS MX lookup via free API
        r = requests.get(
            f"https://dns.google/resolve?name={domain}&type=MX",
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=5
        )
        if r.status_code == 200:
            data = r.json()
            if 'Answer' in data:
                stats['mx'] = True
    except:
        pass
    return stats


# ═══════════════════════════════════════════════
# 🎯 MAIN CHECK FUNCTION - OPTIMIZED
# ═══════════════════════════════════════════════

def check_email_fast(email):
    global hit, good, bad, badmail, goodinsta
    
    try:
        # Use httpx with HTTP/2 for speed
        with httpx.Client(http2=True, timeout=15) as client:
            # Stage 1: Direct check_email API (fastest)
            result_v1 = check_email_ultra_fast_v1(email, client)
            
            if result_v1 == "registered":
                # Quick domain validation (replaces hi2.in)
                domain = email.split('@')[1]
                prefix = email.split('@')[0]
                reg_status = check_registration_fast(domain, prefix)
                domain_stats = get_domain_stats(email)
                
                # Generate masked-like info without GraphQL
                masked_info = f"{prefix[0]}{'*' * (len(prefix)-2)}{prefix[-1]}@{domain}"
                
                hit += 1
                good += 1
                goodinsta += 1
                
                msg = f'''
⌈━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌉

〔 {domain} 〕

✦ 𝐄𝐦𝐚𝐢𝐥 ➤ {email}
✦ 𝐒𝐓𝐀𝐓𝐔𝐒 ➤ REGISTERED
✦ 𝐃𝐨𝐦𝐚𝐢𝐧 𝐕𝐚𝐥𝐢𝐝 ➤ {'✓' if any('valid' in r for r in reg_status) else '?'}
✦ 𝐌𝐚𝐬𝐤𝐞𝐝 ➤ {masked_info}
✦ 𝐀𝐏𝐈 𝐒𝐞𝐬𝐬𝐢𝐨𝐧 ➤ {gen_session_id()}

⌊━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌋
'''
                write_hit(msg)
                send_telegram(msg)
                display()
                return True
                
            elif result_v1 == "check_v2":
                # Stage 2: Bloks fallback
                result_v2 = check_email_ultra_fast_v2(email, client)
                if result_v2 == "registered":
                    hit += 1
                    good += 1
                    goodinsta += 1
                    domain = email.split('@')[1]
                    msg = f'''
⌈━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌉

〔 {domain} 〕

✦ 𝐄𝐦𝐚𝐢𝐥 ➤ {email}
✦ 𝐒𝐓𝐀𝐓𝐔𝐒 ➤ REGISTERED (Bloks)
✦ 𝐀𝐏𝐈 𝐒𝐞𝐬𝐬𝐢𝐨𝐧 ➤ {gen_session_id()}

⌊━─━─━─≪ 𝑨 𝑳 𝑬 𝑿 ≫─━─━─━⌋
'''
                    write_hit(msg)
                    send_telegram(msg)
                    display()
                    return True
                else:
                    bad += 1
                    display()
                    return False
            else:
                bad += 1
                display()
                return False
                
    except Exception as e:
        bad += 1
        display()
        return False


# ═══════════════════════════════════════════════
# 🧵 WORKER FUNCTION - HIGH THROUGHPUT
# ═══════════════════════════════════════════════

def worker():
    while True:
        user1 = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(6))
        user2 = ''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(6))
        chosen_user = random.choice([user1, user2])
        
        with lock:
            if chosen_user in used_usernames:
                continue
            used_usernames.add(chosen_user)
        
        # Use multiple domains for variety
        chos = random.choice(["@hi2.in", "@telegmail.com", "@mail.com", "@yopmail.com"])
        email = chosen_user + chos
        
        try:
            check_email_fast(email)
        except:
            pass


# ═══════════════════════════════════════════════
# 🚀 LAUNCHER - MAXIMUM THROUGHPUT
# ═══════════════════════════════════════════════

print(f"{fg_red}{bold}𖤐 𝗣𝗟𝗘𝗔𝗦𝗘 𝗧𝗢𝗚𝗚𝗟𝗘 𝗔𝗜𝗥𝗣𝗟𝗔𝗡𝗘 𝗠𝗢𝗗𝗘 𝗢𝗥 𝗖𝗛𝗔𝗡𝗚𝗘 𝗩𝗣𝗡 𝗦𝗘𝗥𝗩𝗘𝗥 𝗪𝗛𝗜𝗟𝗘 𝗥𝗨𝗡𝗡𝗜𝗡𝗚 𝗦𝗖𝗥𝗜𝗣𝗧 𖤐{reset}")
time.sleep(2)

print(f"\n{fg_gold}{bold}[*] Starting with 200 threads for ultra speed...{reset}\n")

# Launch 200 threads for maximum throughput
for _ in range(200):
    Thread(target=worker, daemon=True).start()

try:
    while True:
        time.sleep(60)
except KeyboardInterrupt:
    print(f"\n{fg_red}{bold}[!] BYEE BYEE..{reset}")
    sys.exit(0)
