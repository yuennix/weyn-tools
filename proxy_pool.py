"""
Proxy rotation used ONLY for the hi2.in availability gate
(_hi2_check_available / _hi2_get_recaptcha_token in weyn.py). That endpoint
rate-limits/blocks by source IP once a scan session sends heavy volume,
which is why real hits stop showing up after tens of thousands of scanned
ids. Routing just that call through a rotating proxy avoids tying it to
the container's own IP.

Two sources, in priority order:
  1. PROXY_LIST env var — authenticated proxies the user supplies, one per
     line, format "ip:port:user:pass" (or plain "ip:port"). Static list,
     no background refresh needed.
  2. Fallback: a free public proxy list (github.com/iplocate/free-proxy-list),
     refreshed every ~30 min to match its own update cadence. Lower quality
     — expect a high dead/slow rate, hence the health tracking below.

Selection is least-recently-used among currently-healthy proxies, which
naturally spreads load across the pool instead of hammering one exit IP
(a big part of why the caller's own IP got banned in the first place).
Callers should always be prepared for get_proxy() to return None (empty
pool / everything benched) and fall back to a direct request.
"""

import os
import random
import threading
import time
import requests

_FREE_SOURCES = [
    "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
    "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/https.txt",
]

_REFRESH_INTERVAL = 30 * 60   # matches the free list's own update cadence
_FAIL_LIMIT       = 4         # consecutive failures before a proxy is benched
_BENCH_SECONDS    = 3 * 60    # how long a benched proxy sits out before retry

_lock                = threading.Lock()
_pool: list          = []     # list of "ip:port" keys
_auth: dict          = {}     # "ip:port" -> (user, pass) for authenticated proxies
_fail_count: dict    = {}
_benched_until: dict = {}
_last_used: dict     = {}
_started             = False
_using_authenticated = False


def _parse_authenticated(raw: str):
    """Accepts one proxy per line, in either format:
      ip:port:user:pass
      user:pass@ip:port
    or plain "ip:port" for unauthenticated proxies.
    """
    proxies, auth = [], {}
    for line in raw.replace(',', '\n').split('\n'):
        line = line.strip()
        if not line:
            continue

        if '@' in line:
            cred, hostport = line.split('@', 1)
            if ':' in cred and ':' in hostport:
                user, pw = cred.split(':', 1)
                key = hostport
                proxies.append(key)
                auth[key] = (user, pw)
            continue

        parts = line.split(':')
        if len(parts) == 4:
            ip, port, user, pw = parts
            key = f'{ip}:{port}'
            proxies.append(key)
            auth[key] = (user, pw)
        elif len(parts) == 2:
            proxies.append(line)
    return proxies, auth


def _fetch_free_list() -> list:
    found = []
    for url in _FREE_SOURCES:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                for line in r.text.splitlines():
                    line = line.strip()
                    if line and ':' in line:
                        found.append(line)
        except Exception:
            continue
    return list(dict.fromkeys(found))  # de-dupe, preserve order


def _refresh_loop():
    global _pool
    while True:
        time.sleep(_REFRESH_INTERVAL)
        if _using_authenticated:
            continue  # static list from the user, nothing to refresh
        fresh = _fetch_free_list()
        if fresh:
            with _lock:
                _pool = fresh
                for d in (_fail_count, _benched_until, _last_used):
                    for key in list(d.keys()):
                        if key not in fresh:
                            d.pop(key, None)


def ensure_started():
    global _started, _pool, _auth, _using_authenticated
    with _lock:
        if _started:
            return
        _started = True

    raw = os.environ.get('PROXY_LIST', '').strip()
    if raw:
        proxies, auth = _parse_authenticated(raw)
        with _lock:
            _pool = proxies
            _auth = auth
            _using_authenticated = True
        return  # authenticated list is static — no refresh thread needed

    fresh = _fetch_free_list()
    if fresh:
        with _lock:
            _pool = fresh
    threading.Thread(target=_refresh_loop, daemon=True).start()


def get_proxy() -> str | None:
    """Return the least-recently-used healthy proxy key, or None if none available."""
    now = time.time()
    with _lock:
        candidates = [p for p in _pool if _benched_until.get(p, 0) <= now]
        if not candidates:
            return None
        candidates.sort(key=lambda p: _last_used.get(p, 0))
        chosen = candidates[0]
        _last_used[chosen] = now
        return chosen


def report_success(proxy: str):
    if not proxy:
        return
    with _lock:
        _fail_count[proxy] = 0
        _benched_until.pop(proxy, None)


def report_failure(proxy: str):
    if not proxy:
        return
    with _lock:
        n = _fail_count.get(proxy, 0) + 1
        _fail_count[proxy] = n
        if n >= _FAIL_LIMIT:
            _benched_until[proxy] = time.time() + _BENCH_SECONDS


def as_requests_dict(proxy: str) -> dict | None:
    """Build a requests-style proxies= dict for a proxy key, with auth if configured."""
    if not proxy:
        return None
    auth = _auth.get(proxy)
    url  = f'http://{auth[0]}:{auth[1]}@{proxy}' if auth else f'http://{proxy}'
    return {'http': url, 'https': url}
