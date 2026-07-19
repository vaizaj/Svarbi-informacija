"""
SOL techninės analizės signalų botas -> Telegram
----------------------------------------------------
Kas 4 valandas tikrina SOL/USD kainą (Kraken duomenys) ir skaičiuoja:
- RSI (14) - ar moneta "pervirkinta" ar "perparduota"
- MACD (12, 26, 9) - momentumo/tendencijos kryptis
- SMA50 / SMA200 - ilgalaikė tendencija (Golden Cross / Death Cross)

Žinutė siunčiama TIK kai įvyksta reikšmingas pokytis (signalas), o ne
kiekvieną kartą, kai sąlyga tiesiog tebesitęsia - tai išvengia
pasikartojančių/nereikšmingų pranešimų.

SVARBU: Tai NĖRA finansinis patarimas. Tai automatinis indikatorių
skaičiavimas remiantis viešais istoriniais duomenimis. Rinkos gali
judėti priešingai bet kuriam indikatoriui bet kada. Galutinį sprendimą
visada priima pats vartotojas.
"""

import os
import json
import requests
import pandas as pd

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "SOLUSD"
INTERVAL_MINUTES = 240  # 4 valandos
STATE_FILE = "ta_state.json"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TA-Bot/1.0)"}


def fetch_klines() -> pd.DataFrame:
    """Gauna 4h žvakes iš Kraken viešo API (neblokuoja JAV serverių, skirtingai nei Binance)."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={SYMBOL}&interval={INTERVAL_MINUTES}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API klaida: {data['error']}")

    result = data["result"]
    # rezultate yra vienas raktas su žvakių duomenimis (pvz. "SOLUSD"), be "last"
    pair_key = [k for k in result.keys() if k != "last"][0]
    candles = result[pair_key]

    df = pd.DataFrame(candles, columns=[
        "time", "open", "high", "low", "close", "vwap", "volume", "count"
    ])
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    return df


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    return macd_line, signal_line


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
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] Signalo žinutė išsiųsta.")


def main():
    df = fetch_klines()
    close = df["close"]

    rsi = compute_rsi(close)
    macd_line, signal_line = compute_macd(close)
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()

    current_price = float(close.iloc[-1])
    current_rsi = float(rsi.iloc[-1])
    macd_above_signal_now = bool(macd_line.iloc[-1] > signal_line.iloc[-1])
    sma50_above_sma200_now = (
        bool(sma50.iloc[-1] > sma200.iloc[-1]) if not pd.isna(sma200.iloc[-1]) else None
    )
    rsi_oversold_now = bool(current_rsi < 30)
    rsi_overbought_now = bool(current_rsi > 70)

    state = load_state()
    signals = []

    # MACD kirtimasis
    prev_macd_above = state.get("macd_above_signal")
    if prev_macd_above is not None and prev_macd_above != macd_above_signal_now:
        if macd_above_signal_now:
            signals.append("📈 <b>MACD bullish kirtimasis</b> - momentumas keičiasi teigiama linkme")
        else:
            signals.append("📉 <b>MACD bearish kirtimasis</b> - momentumas keičiasi neigiama linkme")

    # SMA50/SMA200 kirtimasis (Golden/Death Cross)
    prev_sma_above = state.get("sma50_above_sma200")
    if (
        prev_sma_above is not None
        and sma50_above_sma200_now is not None
        and prev_sma_above != sma50_above_sma200_now
    ):
        if sma50_above_sma200_now:
            signals.append("✨ <b>Golden Cross</b> - SMA50 kirto SMA200 iš apačios (ilgalaikis bullish signalas)")
        else:
            signals.append("⚠️ <b>Death Cross</b> - SMA50 kirto SMA200 iš viršaus (ilgalaikis bearish signalas)")

    # RSI perėjimas į kraštutines zonas
    prev_rsi_oversold = state.get("rsi_oversold", False)
    prev_rsi_overbought = state.get("rsi_overbought", False)
    if rsi_oversold_now and not prev_rsi_oversold:
        signals.append(f"🔵 <b>RSI perparduota</b> ({current_rsi:.1f}) - galimai artėja atšokimas")
    if rsi_overbought_now and not prev_rsi_overbought:
        signals.append(f"🔴 <b>RSI pervirkinta</b> ({current_rsi:.1f}) - galimai artėja korekcija")

    # Išsaugom naują būseną kitam kartui
    save_state({
        "macd_above_signal": macd_above_signal_now,
        "sma50_above_sma200": sma50_above_sma200_now,
        "rsi_oversold": rsi_oversold_now,
        "rsi_overbought": rsi_overbought_now,
    })

    if not signals:
        print("Nauji signalai nerasti šį kartą.")
        return

    message = f"<b>🔍 SOL techninės analizės signalas (4h)</b>\n\nKaina: ${current_price:,.2f}\n\n"
    message += "\n".join(signals)
    message += "\n\n<i>Tai nėra finansinis patarimas - tik automatinis indikatorių skaičiavimas. Sprendimą priimk pats.</i>"

    send_to_telegram(message)


if __name__ == "__main__":
    main()
