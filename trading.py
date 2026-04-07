"""
Binance Futures Demo — Order Execution Module
All requests routed through proxy from proxy.txt (auto-rotated)
"""
import warnings
try:
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
except ImportError:
    InsecureRequestWarning = None
if InsecureRequestWarning:
    warnings.filterwarnings("ignore", category=InsecureRequestWarning)

import time
import hmac
import hashlib
import requests
from config import API_KEY, API_SECRET, FUTURES_URL as BASE_URL, LEVERAGE
from proxies import get_proxy

# ─── TIME SYNC ───────────────────────────────────────────────────────────────
_time_offset = None

def _sync_time():
    global _time_offset
    if _time_offset is not None:
        return _time_offset
    try:
        r = requests.get(f"{BASE_URL}/fapi/v1/time", proxies={"http": get_proxy(), "https": get_proxy()}, timeout=10)
        server_time = r.json()["serverTime"]
        local_time = int(time.time() * 1000)
        _time_offset = server_time - local_time
        return _time_offset
    except:
        _time_offset = 0
        return 0

# ─── REQUEST HELPERS ─────────────────────────────────────────────────────────

def _proxies():
    return {"http": get_proxy(), "https": get_proxy()}

def _sign(params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return sig

def _request(method, path, params=None, retries=3):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000) + _sync_time()
    params["recvWindow"] = 60000
    params["signature"] = _sign(params)
    url = f"{BASE_URL}{path}"
    headers = {"X-MBX-APIKEY": API_KEY}
    for attempt in range(retries):
        try:
            r = requests.request(method, url, params=params, headers=headers, proxies=_proxies(), timeout=20, verify=False)
            if r.status_code != 200:
                print(f"[TRADING ERROR] {method} {path} → {r.status_code} {r.text[:200]}")
                return None
            return r.json()
        except (requests.exceptions.ProxyError, requests.exceptions.Timeout, OSError) as e:
            if attempt < retries - 1:
                print(f"[RETRY] {method} {path} attempt {attempt+1} failed: {e}. Retrying...")
                time.sleep(2)
                continue
            print(f"[TRADING ERROR] {method} {path} all retries failed: {e}")
            return None
    return None

def _request_algo(method, path, params=None):
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000) + _sync_time()
    params["recvWindow"] = 60000
    params["signature"] = _sign(params)
    url = f"{BASE_URL}{path}"
    headers = {"X-MBX-APIKEY": API_KEY}
    r = requests.request(method, url, params=params, headers=headers, proxies=_proxies(), timeout=15, verify=False)
    if r.status_code != 200:
        print(f"[ALGO ERROR] {method} {path} → {r.status_code} {r.text[:200]}")
        return None
    return r.json()

# ─── ACCOUNT ─────────────────────────────────────────────────────────────────

def get_account():
    return _request("GET", "/fapi/v2/balance")

def get_positions():
    return _request("GET", "/fapi/v2/positionRisk")

def get_open_orders(symbol=None):
    params = {}
    if symbol:
        params["symbol"] = symbol
    return _request("GET", "/fapi/v1/openOrders", params)

def set_leverage(symbol, lev=LEVERAGE):
    params = {"symbol": symbol, "leverage": lev}
    return _request("POST", "/fapi/v1/leverage", params)

# ─── ORDERS ──────────────────────────────────────────────────────────────────

def place_order(symbol, side, order_type, quantity, price=None, stop_price=None):
    params = {
        "symbol":     symbol,
        "side":       side,
        "type":       order_type,
        "quantity":   quantity,
        "reduceOnly": "false",
    }
    if price:
        params["price"] = price
    if stop_price:
        params["stopPrice"] = stop_price
    if order_type == "LIMIT":
        params["timeInForce"] = "GTC"
    return _request("POST", "/fapi/v1/order", params)

def market_close(symbol, side, quantity):
    params = {
        "symbol":     symbol,
        "side":       side,
        "type":       "MARKET",
        "quantity":   quantity,
        "reduceOnly": "true",
    }
    return _request("POST", "/fapi/v1/order", params)

def cancel_order(symbol, order_id):
    params = {"symbol": symbol, "orderId": order_id}
    return _request("DELETE", "/fapi/v1/order", params)

def cancel_all_open(symbol):
    params = {"symbol": symbol}
    return _request("DELETE", "/fapi/v1/allOpenOrders", params)

# ─── QUANTITY ────────────────────────────────────────────────────────────────

def get_symbol_precision(symbol):
    exchange_info = _request("GET", "/fapi/v1/exchangeInfo")
    if not exchange_info:
        return 3
    for s in exchange_info.get("symbols", []):
        if s["symbol"] == symbol:
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    dec = len(str(step).rstrip("0").split(".")[-1])
                    return dec
    return 3

def get_market_max_qty(symbol):
    """Get maxQty from MARKET_LOT_SIZE filter."""
    exchange_info = _request("GET", "/fapi/v1/exchangeInfo")
    if not exchange_info:
        return None
    for s in exchange_info.get("symbols", []):
        if s["symbol"] == symbol:
            for f in s.get("filters", []):
                if f["filterType"] == "MARKET_LOT_SIZE":
                    return float(f["maxQty"])
    return None

def calc_quantity_from_risk(symbol, balance_usdt, entry_price, sl_pct, lev=LEVERAGE):
    prec = get_symbol_precision(symbol)
    risk_amount = balance_usdt * lev * sl_pct / 100
    qty = risk_amount / entry_price
    step = 10 ** (-prec)
    qty = float(int(qty / step) * step)
    qty = round(qty, prec)
    # Cap to market max qty if exists
    max_qty = get_market_max_qty(symbol)
    if max_qty and qty > max_qty:
        qty = round(max_qty, prec)
    return qty

def calc_quantity_simple(symbol, usdt_amount, price):
    prec = get_symbol_precision(symbol)
    qty = usdt_amount / price
    step = 10 ** (-prec)
    qty = int(qty / step) * step
    qty = round(qty, prec)
    max_qty = get_market_max_qty(symbol)
    if max_qty and qty > max_qty:
        qty = round(max_qty, prec)
    return qty

# ─── POSITION HELPERS ────────────────────────────────────────────────────────

def has_position(symbol):
    positions = get_positions()
    if not positions:
        return None
    for p in positions:
        if p.get("symbol") == symbol and float(p.get("positionAmt", 0)) != 0:
            return p
    return None

def count_open_positions(positions_data):
    count = 0
    for p in positions_data or []:
        if float(p.get("positionAmt", 0)) != 0:
            count += 1
    return count

# ─── SL/TP (Algo Orders) ─────────────────────────────────────────────────────

def set_sl_tp(symbol, side, entry_price, sl_pct, tp_pct, quantity):
    """
    SL/TP via market protective orders.
    For testnet, we use market orders to simulate SL/TP since algo orders
    require algotype params that testnet doesn't fully support.
    Stores the SL/TP prices as reference; closing handled by opposite signal.
    """
    if side == "BUY":
        sl_price = round(entry_price * (1 - sl_pct / 100), 8)
        tp_price = round(entry_price * (1 + tp_pct / 100), 8)
    else:
        sl_price = round(entry_price * (1 + sl_pct / 100), 8)
        tp_price = round(entry_price * (1 - tp_pct / 100), 8)

    return {
        "sl_price": sl_price,
        "tp_price": tp_price,
    }

# ─── TEST CONNECTION ─────────────────────────────────────────────────────────

def test_connection():
    acc = get_account()
    if acc is None:
        return False
    print("[✓] Connected to Binance Futures Demo via proxy")
    for asset in acc:
        if asset.get("asset") == "USDT" and float(asset.get("availableBalance", 0)) > 0:
            print(f"    USDT Balance: {asset['availableBalance']}")
    return True
