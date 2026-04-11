"""Discord integration: notifications, embeds, board updates."""
import requests
from datetime import datetime
import pytz

from config import (
    DISCORD_BOT_TOKEN, DISCORD_SIGNAL_WEBHOOK_URL, DISCORD_BOARD_CHANNEL_ID,
    DISCORD_SIGNAL_CHANNEL_ID, DISCORD_API_URL,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, LEVERAGE, MAX_POSITIONS,
    TP_1_RATIO, TP_2_RATIO, COINS_WHITELIST,
)

DISCORD_API = DISCORD_API_URL
BOARD_MSG_FILE = "board_msg_id.txt"


def discord_req(method, path, data=None):
    url = f"{DISCORD_API}{path}"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=10)
        elif method == "POST":
            r = requests.post(url, headers=headers, json=data, timeout=10)
        elif method == "PATCH":
            r = requests.patch(url, headers=headers, json=data, timeout=10)
        if r.status_code in (200, 201):
            return r.json()
        print(f"[DISCORD ERROR] {method} {path} → HTTP {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        print(f"[DISCORD ERROR] {method} {path} → {e}")
        return None


def get_board_msg_id():
    try:
        with open(BOARD_MSG_FILE) as f:
            return f.read().strip() or None
    except:
        return None


def save_board_msg_id(msg_id):
    with open(BOARD_MSG_FILE, "w") as f:
        f.write(str(msg_id))


def discord_notify(title, description, color=0xFFAA00, fields=None):
    payload = {
        "username": "Professor Psyduck 🐤",
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": f"Professor Mode 🐤 | {datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%H:%M:%S WIB')}"}
        }]
    }
    if fields:
        payload["embeds"][0]["fields"] = fields
    try:
        r = requests.post(DISCORD_SIGNAL_WEBHOOK_URL, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            url = f"{DISCORD_API}/channels/{DISCORD_SIGNAL_CHANNEL_ID}/messages"
            headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
            requests.post(url, json=payload, headers=headers, timeout=10)
    except:
        pass


def get_mark_prices(symbols, BASE_URL):
    """Get current mark prices for symbols using batch endpoint."""
    result = {}
    if not symbols or not BASE_URL:
        return result
    try:
        url = f"{BASE_URL}/fapi/v1/ticker/price"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            for item in r.json():
                if item.get("symbol") in symbols:
                    result[item["symbol"]] = float(item["price"])
    except:
        pass
    return result


def build_board_embed(data, positions=None, BASE_URL=None):
    rows = data.get("rows", [])
    ts = data.get("ts", "")
    tracked = data.get("tracked", 0)
    cycle = data.get("cycle", 0)
    longs = [r for r in rows if r.get("signal") == "📈BOUNCE"]
    spikes = [r for r in rows if r.get("signal") == "⚡SPIKE"]
    fades = [r for r in rows if r.get("signal") == "📉FADE"]

    def fmt(r):
        ema = r.get("ema")
        ema_str = f" | EMA `${ema:.4f}`" if ema else ""
        adx = r.get("adx")
        adx_str = f" | ADX `{adx:.0f}`" if adx else ""
        arrow = r.get("arrow", "▲")
        chg = r.get("change_pct", 0)
        arrow_str = f"{arrow}{abs(chg):.2f}%"
        return f'`{r["symbol"]}` {arrow_str} RSI `{r["rsi"]:.0f}` Mom `{r["mom5"]:+.2f}%`{ema_str}{adx_str}'

    sections = []
    if longs:
        longs.sort(key=lambda x: -x["mom5"])
        lines = [f'🟢 {fmt(r)}' for r in longs[:8]]
        sections.append(("🟢 LONG", lines, 0x00FF00))
    if fades:
        fades.sort(key=lambda x: -abs(x["mom5"]))
        lines = [f'🔴 {fmt(r)}' for r in fades[:8]]
        sections.append(("🔴 SHORT", lines, 0xFF4444))
    if spikes:
        spikes.sort(key=lambda x: -x["vol_ratio"])
        lines = [f'⚡ {fmt(r)}' for r in spikes[:8]]
        sections.append(("⚡ SPIKE", lines, 0xFFD700))

    if not sections:
        desc = "_No active signals - scanning..._"
        color = 0x555555
    else:
        parts = []
        for title, lines, _ in sections:
            parts.append(f"**{title}**")
            parts.extend(lines)
        desc = "\n".join(parts)
        color = 0x00FF00 if longs and not fades else (0xFF4444 if fades else 0x555555)

    open_pos = [p for p in positions if float(p.get("positionAmt", 0)) != 0] if positions else []

    fields = [
        {"name": "🎯 Config", "value": f"SL: `{STOP_LOSS_PCT}%`/ATR | TP: `{TP_1_RATIO}x`/`{TP_2_RATIO}x` | Lev: `{LEVERAGE}x` | Pos: `{len(open_pos)}/{MAX_POSITIONS}`", "inline": False},
        {"name": "📡 Scanner", "value": f"Whitelist: `{len(COINS_WHITELIST)}` coins | `{ts}`", "inline": False}
    ]

    if open_pos:
        syms = [p["symbol"] for p in open_pos]
        mark_prices = get_mark_prices(syms, BASE_URL) if BASE_URL else {}

        pos_lines = []
        total_pnl = 0.0
        for p in open_pos:
            amt = float(p["positionAmt"])
            entry = float(p["entryPrice"])
            sym = p["symbol"]
            upnl = float(p.get("unRealizedProfit", 0))
            mark = mark_prices.get(sym) or float(p.get("markPrice", entry))
            side = "🟢LONG" if amt > 0 else "🔴SHORT"
            emoji = "🟢" if upnl >= 0 else "🔴"
            notional = entry * abs(amt)
            margin = notional / LEVERAGE
            pnl_pct = (upnl / margin) * 100 if margin > 0 else 0
            pos_lines.append(f"{emoji} **{sym}** {side} `${abs(amt)}` | Mark `${mark:.4f}` | Entry `${entry:.4f}` | PnL `${upnl:+.4f}` ({pnl_pct:+.1f}%) | Lev `{LEVERAGE}x`")
            total_pnl += upnl

        pos_text = "\n".join(pos_lines)
        # Discord field value max = 1024 chars
        if len(pos_text) > 1024:
            pos_text = pos_text[:1021] + "..."
        if len(open_pos) > 10:
            pos_text += f"\n_...and {len(open_pos) - 10} more positions_"

        fields.append({
            "name": f"📊 Open Positions ({len(open_pos)}) | Total PnL: `{total_pnl:+.2f}`",
            "value": pos_text,
            "inline": False
        })

    return {
        "title": "⚡ Professor Psyduck - Live Scanner 🐤",
        "description": desc,
        "color": color,
        "fields": fields,
        "footer": {"text": f"🟢 LIVE | Scan #{cycle} | {tracked} coins | {ts}"}
    }
