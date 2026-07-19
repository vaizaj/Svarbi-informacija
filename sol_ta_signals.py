"""
SOL techninės analizės signalų botas -> Telegram (išplėsta versija)
----------------------------------------------------------------------
Kas valandą tikrina SOL/USD kainą (Kraken duomenys) ir skaičiuoja 8
indikatorius, kuriuos naudoja profesionalūs investuotojai:

1. RSI (14) - pervirkta/perparduota
2. MACD (12, 26, 9) - momentumo kryptis
3. SMA50 / SMA200 - ilgalaikė tendencija (Golden/Death Cross)
4. Bollinger Bands (20, 2) - kaina vs volatilumo juostos
5. Stochastic Oscillator (14, 3) - momentumas
6. ADX (14) - tendencijos STIPRUMAS (ne kryptis)
7. OBV (On-Balance Volume) - ar apimtis patvirtina kainos judėjimą
8. Rolling VWAP - kaina vs vidutinė svertinė kaina

Žinutė siunčiama TIK kai įvyksta bent vienas reikšmingas signalo
pokytis. Kartu su konkrečiu signalu visada rodoma PILNA "sutapimo"
santrauka - kiek indikatorių šiuo metu rodo bullish/bearish.

SVARBU: Tai NĖRA finansinis patarimas ir NĖRA prognozė. Joks
indikatorių derinys negali patikimai nuspėti trumpalaikės kainos
krypties. Tai tik esamos indikatorių būsenos suvestinė sprendimui
priimti - galutinį sprendimą visada priima pats vartotojas.
"""

import os
import json
import requests
import numpy as np
import pandas as pd

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "SOLUSD"
INTERVAL_MINUTES = 60  # 1 valanda
STATE_FILE = "ta_state.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TA-Bot/1.0)"}


def fetch_klines() -> pd.DataFrame:
    """Gauna 1h žvakes iš Kraken viešo API."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={SYMBOL}&interval={INTERVAL_MINUTES}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API klaida: {data['error']}")

    result = data["result"]
    pair_key = [k for k in result.keys() if k != "last"][0]
    candles = result[pair_key]

    df = pd.DataFrame(candles, columns=[
        "time", "open", "high", "low", "close", "vwap_raw", "volume", "count"
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ---------------------------------------------------------------------------
# INDIKATORIŲ SKAIČIAVIMAS
# ---------------------------------------------------------------------------

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal_line


def compute_bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper, sma, lower


def compute_stochastic(df: pd.DataFrame, period: int = 14, smooth: int = 3):
    low_min = df["low"].rolling(period).min()
    high_max = df["high"].rolling(period).max()
    percent_k = 100 * (df["close"] - low_min) / (high_max - low_min)
    percent_d = percent_k.rolling(smooth).mean()
    return percent_k, percent_d


def compute_adx(df: pd.DataFrame, period: int = 14):
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    plus_dm[(plus_dm < minus_dm)] = 0
    minus_dm[(minus_dm < plus_dm)] = 0

    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / period, min_periods=period).mean()
    return adx, plus_di, minus_di


def compute_obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff().fillna(0))
    return (direction * df["volume"]).cumsum()


def compute_rolling_vwap(df: pd.DataFrame, period: int = 20) -> pd.Series:
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical_price * df["volume"]
    return pv.rolling(period).sum() / df["volume"].rolling(period).sum()


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] Signalo žinutė išsiųsta.")


def main():
    df = fetch_klines()
    close = df["close"]
    current_price = float(close.iloc[-1])

    # --- Indikatoriai ---
    rsi = compute_rsi(close)
    macd_line, signal_line = compute_macd(close)
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    bb_upper, bb_mid, bb_lower = compute_bollinger(close)
    stoch_k, stoch_d = compute_stochastic(df)
    adx, plus_di, minus_di = compute_adx(df)
    obv = compute_obv(df)
    vwap = compute_rolling_vwap(df)

    current_rsi = float(rsi.iloc[-1])
    macd_above_signal_now = bool(macd_line.iloc[-1] > signal_line.iloc[-1])
    sma50_above_sma200_now = (
        bool(sma50.iloc[-1] > sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else None
    )
    rsi_oversold_now = bool(current_rsi < 30)
    rsi_overbought_now = bool(current_rsi > 70)

    price_above_bb_upper = bool(current_price > bb_upper.iloc[-1])
    price_below_bb_lower = bool(current_price < bb_lower.iloc[-1])

    current_stoch_k = float(stoch_k.iloc[-1])
    stoch_oversold_now = bool(current_stoch_k < 20)
    stoch_overbought_now = bool(current_stoch_k > 80)

    current_adx = float(adx.iloc[-1]) if not pd.isna(adx.iloc[-1]) else 0.0
    strong_trend_now = bool(current_adx > 25)
    plus_di_above_now = bool(plus_di.iloc[-1] > minus_di.iloc[-1])

    obv_rising_now = bool(obv.iloc[-1] > obv.iloc[-5]) if len(obv) > 5 else None
    price_above_vwap_now = bool(current_price > vwap.iloc[-1]) if not pd.isna(vwap.iloc[-1]) else None

    state = load_state()
    signals = []

    def flag_change(key, current_value, on_true_msg, on_false_msg):
        prev = state.get(key)
        if prev is not None and current_value is not None and prev != current_value:
            signals.append(on_true_msg if current_value else on_false_msg)

    flag_change(
        "macd_above_signal", macd_above_signal_now,
        "📈 <b>MACD bullish kirtimasis</b>",
        "📉 <b>MACD bearish kirtimasis</b>",
    )
    flag_change(
        "sma50_above_sma200", sma50_above_sma200_now,
        "✨ <b>Golden Cross</b> (SMA50 > SMA200)",
        "⚠️ <b>Death Cross</b> (SMA50 < SMA200)",
    )
    if rsi_oversold_now and not state.get("rsi_oversold", False):
        signals.append(f"🔵 <b>RSI perparduota</b> ({current_rsi:.1f})")
    if rsi_overbought_now and not state.get("rsi_overbought", False):
        signals.append(f"🔴 <b>RSI pervirkinta</b> ({current_rsi:.1f})")
    if price_below_bb_lower and not state.get("price_below_bb_lower", False):
        signals.append("🔵 <b>Kaina po apatine Bollinger juosta</b> (galimai perparduota)")
    if price_above_bb_upper and not state.get("price_above_bb_upper", False):
        signals.append("🔴 <b>Kaina virš viršutinės Bollinger juostos</b> (galimai pervirkinta)")
    if stoch_oversold_now and not state.get("stoch_oversold", False):
        signals.append(f"🔵 <b>Stochastic perparduota</b> ({current_stoch_k:.1f})")
    if stoch_overbought_now and not state.get("stoch_overbought", False):
        signals.append(f"🔴 <b>Stochastic pervirkinta</b> ({current_stoch_k:.1f})")
    flag_change(
        "strong_trend", strong_trend_now,
        f"💪 <b>Stiprėjanti tendencija</b> (ADX {current_adx:.1f} > 25)",
        f"😴 <b>Silpstanti tendencija</b> (ADX {current_adx:.1f} < 25)",
    )
    flag_change(
        "price_above_vwap", price_above_vwap_now,
        "📊 <b>Kaina kirto VWAP į viršų</b>",
        "📊 <b>Kaina kirto VWAP į apačią</b>",
    )

    # --- Sutapimo (confluence) skaičiavimas šiam momentui ---
    bullish_count = 0
    bearish_count = 0
    total_directional = 0

    def tally(is_bullish):
        nonlocal bullish_count, bearish_count, total_directional
        if is_bullish is None:
            return
        total_directional += 1
        if is_bullish:
            bullish_count += 1
        else:
            bearish_count += 1

    tally(current_rsi < 50)
    tally(macd_above_signal_now)
    tally(sma50_above_sma200_now)
    tally(not price_above_bb_upper and current_price > bb_mid.iloc[-1])
    tally(current_stoch_k > 50)
    tally(plus_di_above_now)
    tally(obv_rising_now)
    tally(price_above_vwap_now)

    new_state = {
        "macd_above_signal": macd_above_signal_now,
        "sma50_above_sma200": sma50_above_sma200_now,
        "rsi_oversold": rsi_oversold_now,
        "rsi_overbought": rsi_overbought_now,
        "price_below_bb_lower": price_below_bb_lower,
        "price_above_bb_upper": price_above_bb_upper,
        "stoch_oversold": stoch_oversold_now,
        "stoch_overbought": stoch_overbought_now,
        "strong_trend": strong_trend_now,
        "price_above_vwap": price_above_vwap_now,
    }
    is_first_run = not state
    save_state(new_state)

    if is_first_run:
        print("Pirmas paleidimas - būsena užsirašyta, signalai nesiunčiami.")
        return

    if not signals:
        print("Nauji signalai nerasti šį kartą.")
        return

    message = f"<b>🔍 SOL techninės analizės signalas (1h)</b>\n\nKaina: ${current_price:,.2f}\n\n"
    message += "\n".join(signals)
    message += (
        f"\n\n<b>📊 Sutapimo santrauka:</b> {bullish_count}/{total_directional} indikatorių bullish, "
        f"{bearish_count}/{total_directional} bearish"
    )
    message += (
        "\n\n<i>Tai NĖRA finansinis patarimas ir NĖRA prognozė - "
        "tik esamos indikatorių būsenos suvestinė. Sprendimą priimk pats.</i>"
    )

    send_to_telegram(message)


if __name__ == "__main__":
    main()
