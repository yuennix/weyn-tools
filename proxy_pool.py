"""
Lightweight free-proxy rotation, used ONLY for the hi2.in availability gate
(_hi2_check_available in weyn.py). That endpoint rate-limits/blocks by
source IP once a scan session gets into the tens of thousands of requests —
rotating the outgoing IP for just that call lets the container's own IP
avoid getting flagged.

Source: https://github.com/iplocate/free-proxy-list (updates every ~30 min).
These are free public proxies — expect a high dead/slow rate. This module
health-tracks each proxy and always allows falling back to a direct
(no-proxy) request if the pool is empty or every proxy is currently bad.
"""

import random
import threading
import time
import requests

_SOURCES = [
    "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/http.txt",
    "https://raw.githubusercontent.com/iplocate/free-proxy-list/main/protocols/https.txt",
]

_REFRESH_INTERVAL = 30 * 60   # matches upstream's own update cadence
_FAIL_LIMIT       = 3         # consecutive failures before a proxy is benched
_BENCH_SECONDS    = 5 * 60    # how long a benched proxy sits out before retry

_lock       = threading.Lock()
_pool: list = []                     # list of "ip:port" strings
_fail_count: dict = {}               # "ip:port" -> consecutive failure count
_benched_until: dict = {}            # "ip:port" -> unix ts when eligible again
_last_refresh = 0.0
_started      = False


def _fetch_list() -> list:
    found = []
    for url in _SOURCES:
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
    global _pool, _last_refresh
    while True:
        fresh = _fetch_list()
        if fresh:
            with _lock:
                _pool = fresh
                # drop stale bookkeeping for proxies no longer in the list
                for d in (_fail_count, _benched_until):
                    for key in list(d.keys()):
                        if key not in fresh:
                            d.pop(key, None)
                _last_refresh = time.time()
        time.sleep(_REFRESH_INTERVAL)


def ensure_started():
    global _started
    with _lock:
        if _started:
            return
        _started = True
    # Populate synchronously once so the very first hi2 check can use a proxy
    fresh = _fetch_list()
    if fresh:
        with _lock:
            _pool = fresh
    threading.Thread(target=_refresh_loop, daemon=True).start()


def get_proxy() -> str | None:
    """Return a random healthy 'ip:port' string, or None if none available."""
    now = time.time()
    with _lock:
        candidates = [
            p for p in _pool
            if _benched_until.get(p, 0) <= now
        ]
        if not candidates:
            return None
        return random.choice(candidates)


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


def as_requests_dict(proxy: str) -> dict:
    """Build a requests-style proxies= dict for a plain 'ip:port' entry."""
    return {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
