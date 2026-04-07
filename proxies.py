"""
Proxy loader — reads from proxy.txt, random pick (sticky per session)
Format: http://user:pass@host:port (one per line)
"""
import random
import os

PROXY_FILE = os.path.join(os.path.dirname(__file__), "proxy.txt")

def _load_proxies():
    path = PROXY_FILE
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return lines

PROXY_POOL = _load_proxies()

# Sticky proxy — same proxy for entire session
_session_proxy = None

def get_proxy():
    """Return random proxy from proxy.txt (sticks to session after first call)"""
    global _session_proxy
    if _session_proxy is None:
        if not PROXY_POOL:
            raise RuntimeError("No proxies found in proxy.txt")
        _session_proxy = random.choice(PROXY_POOL)
    return _session_proxy

def get_proxy_dict():
    """Return as requests proxies dict (session-sticky)"""
    return {"http": get_proxy(), "https": get_proxy()}

def reset_proxy():
    """Reset sticky proxy (call between runs if needed)"""
    global _session_proxy
    _session_proxy = None

def test_all_proxies(test_url="https://demo-fapi.binance.com/fapi/v1/ping", timeout=10):
    """Test all proxies against test_url, return results"""
    import warnings
    try:
        from requests.packages.urllib3.exceptions import InsecureRequestWarning
        warnings.filterwarnings("ignore", category=InsecureRequestWarning)
    except ImportError:
        pass
    import requests
    results = []
    for p in PROXY_POOL:
        try:
            r = requests.get(test_url, proxies={"http": p, "https": p}, timeout=timeout, verify=False)
            body = r.text.strip()
            if r.status_code == 200 and body not in ("ok", '{"code": 0, "msg": "Service unavailable"}'):
                results.append((p, "✅", body[:80]))
            else:
                results.append((p, "⚠️", body[:80]))
        except Exception as e:
            results.append((p, "❌", str(e)[:60]))
    return results
