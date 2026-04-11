"""Technical indicators for signal generation."""


def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    rs = ag / al if al > 0 else 999
    return 100 - 100 / (1 + rs)


def calc_mom(prices, bars=5):
    if len(prices) < bars + 1:
        return 0.0
    return (prices[-1] - prices[-bars]) / prices[-bars] * 100


def calc_vol_ratio(volumes):
    if len(volumes) < 10:
        return 1.0
    avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
    return volumes[-1] / avg if avg > 0 else 1.0


def calc_ema(prices, length=20):
    """Calculate EMA. Returns None if not enough data."""
    if len(prices) < length:
        return None
    k = 2 / (length + 1)
    ema = sum(prices[:length]) / length
    for price in prices[length:]:
        ema = price * k + ema * (1 - k)
    return ema


def calc_atr(prices, period=14):
    """Calculate Average True Range for dynamic SL."""
    if len(prices) < period + 1:
        return None
    tr_list = []
    for i in range(1, min(len(prices), 50)):
        high = prices[i]
        low = prices[i]
        prev_close = prices[i - 1]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if len(tr_list) < period:
        return None
    return sum(tr_list[-period:]) / period


def calc_adx(prices, period=14):
    """
    Calculate ADX (Average Directional Index) using Wilder smoothing.
    Measures trend strength (0-100). ADX < 25 = ranging, ADX > 25 = trending.
    Requires at least period*2 data points for stable values.
    """
    if len(prices) < period * 2:
        return None

    highs = prices
    lows = prices

    # Calculate True Range and Directional Movement
    tr_list = []
    pos_dm_list = []
    neg_dm_list = []

    for i in range(1, len(prices)):
        high = prices[i]
        low = prices[i]
        prev_high = prices[i - 1]
        prev_low = prices[i - 1]
        prev_close = prices[i - 1]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        pos_dm = (
            max(high - prev_high, 0) if (high - prev_high) > (prev_low - low) else 0
        )
        neg_dm = max(prev_low - low, 0) if (prev_low - low) > (high - prev_high) else 0

        tr_list.append(tr)
        pos_dm_list.append(pos_dm)
        neg_dm_list.append(neg_dm)

    if len(tr_list) < period:
        return None

    # Wilder smoothed ATR
    atr = sum(tr_list[-period:]) / period
    pos_dm_smooth = sum(pos_dm_list[-period:]) / period
    neg_dm_smooth = sum(neg_dm_list[-period:]) / period

    # Calculate DMI
    if atr == 0:
        return None
    pos_dmi = (pos_dm_smooth / atr) * 100
    neg_dmi = (neg_dm_smooth / atr) * 100

    # Calculate DX
    dx = (
        abs(pos_dmi - neg_dmi) / (pos_dmi + neg_dmi) * 100
        if (pos_dmi + neg_dmi) > 0
        else 0
    )

    # First ADX = Wilder average of DX
    adx = dx
    k = 2 / (period + 1)

    # Subsequent ADX smoothed with Wilder
    for i in range(len(tr_list) - period, len(tr_list) - 1):
        if i >= 0:
            dxi = (
                abs(pos_dm_list[i] - neg_dm_list[i])
                / (pos_dm_list[i] + neg_dm_list[i])
                * 100
                if (pos_dm_list[i] + neg_dm_list[i]) > 0
                else 0
            )
            adx = adx * (1 - k) + dxi * k

    return adx


def calc_macd(prices, fast=12, slow=26, signal=9):
    """
    Calculate MACD: EMA12 - EMA26, signal line EMA9, histogram.
    Returns (macd_line, signal_line, histogram).
    """
    if len(prices) < slow + signal:
        return None, None, None

    def ema(data, length):
        if len(data) < length:
            return None
        k = 2 / (length + 1)
        ema_val = sum(data[:length]) / length
        for price in data[length:]:
            ema_val = price * k + ema_val * (1 - k)
        return ema_val

    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)

    if ema_fast is None or ema_slow is None:
        return None, None, None

    macd_line = ema_fast - ema_slow

    macd_values = []
    for i in range(slow - fast, len(prices)):
        e1 = ema(prices[: i + 1], fast)
        e2 = ema(prices[: i + 1], slow)
        if e1 is not None and e2 is not None:
            macd_values.append(e1 - e2)

    signal_line = ema(macd_values, signal) if len(macd_values) >= signal else None

    histogram = macd_line - signal_line if signal_line is not None else None

    return macd_line, signal_line, histogram


def calc_bollinger_bands(prices, length=20, num_std=2):
    """
    Calculate Bollinger Bands: SMA20 ± 2σ.
    Returns (upper_band, middle_band, lower_band, position).
    position = (price - lower) / (upper - lower) — 0 = near lower, 1 = near upper.
    """
    if len(prices) < length:
        return None, None, None, None

    middle = sum(prices[-length:]) / length
    variance = sum((p - middle) ** 2 for p in prices[-length:]) / length
    std = variance**0.5

    upper = middle + num_std * std
    lower = middle - num_std * std

    band_range = upper - lower
    position = (prices[-1] - lower) / band_range if band_range > 0 else 0.5

    return upper, middle, lower, position


def calc_ema_multi(prices, lengths=(9, 21, 50)):
    """
    Calculate multiple EMAs at once.
    Returns dict of {length: ema_value}.
    """
    result = {}
    for length in lengths:
        if len(prices) >= length:
            k = 2 / (length + 1)
            ema_val = sum(prices[:length]) / length
            for price in prices[length:]:
                ema_val = price * k + ema_val * (1 - k)
            result[length] = ema_val
        else:
            result[length] = None
    return result


def is_bullish_candle(opens, closes):
    """Returns True if last candle is bullish (close > open)."""
    if len(opens) < 2 or len(closes) < 2:
        return True
    return closes[-1] > opens[-1]


def volume_spike(volumes, threshold=2.0):
    """
    Check if current volume is > threshold times average.
    Returns (bool, is_green).
    """
    if len(volumes) < 20:
        return False, True
    avg = sum(volumes[-20:]) / 20
    return volumes[-1] > avg * threshold, True
