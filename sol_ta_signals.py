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
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "SOLUSD"
INTERVAL_MINUTES = 60  # 1 valanda
STATE_FILE = "ta_state.json"
CHART_FILE = "sol_chart.png"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TA-Bot/1.0)"}


def fetch_tradingview_rating() -> dict:
    """
    Gauna TradingView PAČIŲ apskaičiuotą techninį reitingą (Strong Buy/Buy/
    Neutral/Sell/Strong Sell) kaip nepriklausomą patikrinimą mūsų skaičiavimams.
    Naudoja viešą endpoint'ą, kuris maitina jų svetainės TA valdiklį.
    Jei nepavyksta (endpoint nedokumentuotas oficialiai, gali keistis) -
    grąžina None, o likusi scripto dalis veikia toliau normaliai.
    """
    try:
        from tradingview_ta import TA_Handler, Interval
        handler = TA_Handler(
            symbol="SOLUSD",
            screener="crypto",
            exchange="KRAKEN",
            interval=Interval.INTERVAL_1_HOUR,
        )
        analysis = handler.get_analysis()
        summary = analysis.summary  # {'RECOMMENDATION': 'BUY', 'BUY': 12, 'SELL': 5, 'NEUTRAL': 9}
        return summary
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko gauti TradingView reitingo: {e}")
        return None


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


def create_chart(df, rsi, macd_line, signal_line, bb_upper, bb_mid, bb_lower,
                  stoch_k, stoch_d, sma50, sma200) -> str:
    """Sukuria grafiką su kaina + Bollinger/SMA, RSI, MACD, Stochastic - paskutinės ~100 žvakės."""
    n = 100
    df_plot = df.tail(n).reset_index(drop=True)
    x = pd.to_datetime(df_plot["time"], unit="s")

    fig, axes = plt.subplots(4, 1, figsize=(11, 12), sharex=True,
                              gridspec_kw={"height_ratios": [3, 1, 1, 1]})
    fig.patch.set_facecolor("#0F172A")
    for ax in axes:
        ax.set_facecolor("#0F172A")
        ax.tick_params(colors="#CBD5E1", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#334155")

    # 1) Kaina + SMA + Bollinger
    ax1 = axes[0]
    ax1.plot(x, df_plot["close"], color="#38BDF8", linewidth=1.3, label="SOL kaina")
    ax1.plot(x, bb_upper.tail(n).values, color="#94A3B8", linewidth=0.8, linestyle="--", label="BB viršus")
    ax1.plot(x, bb_lower.tail(n).values, color="#94A3B8", linewidth=0.8, linestyle="--", label="BB apačia")
    ax1.fill_between(x, bb_upper.tail(n).values, bb_lower.tail(n).values, color="#334155", alpha=0.2)
    if not sma50.tail(n).isna().all():
        ax1.plot(x, sma50.tail(n).values, color="#FBBF24", linewidth=1, label="SMA50")
    if not sma200.tail(n).isna().all():
        ax1.plot(x, sma200.tail(n).values, color="#F472B6", linewidth=1, label="SMA200")
    ax1.set_title("SOL/USD - 1h grafikas", color="#E2E8F0", fontsize=12, loc="left")
    ax1.legend(loc="upper left", fontsize=7, facecolor="#1E293B", labelcolor="#E2E8F0", framealpha=0.7)

    # 2) RSI
    ax2 = axes[1]
    ax2.plot(x, rsi.tail(n).values, color="#A78BFA", linewidth=1.2)
    ax2.axhline(70, color="#F87171", linewidth=0.7, linestyle="--")
    ax2.axhline(30, color="#4ADE80", linewidth=0.7, linestyle="--")
    ax2.set_ylabel("RSI", color="#CBD5E1", fontsize=8)
    ax2.set_ylim(0, 100)

    # 3) MACD
    ax3 = axes[2]
    ax3.plot(x, macd_line.tail(n).values, color="#38BDF8", linewidth=1, label="MACD")
    ax3.plot(x, signal_line.tail(n).values, color="#FBBF24", linewidth=1, label="Signal")
    hist = (macd_line - signal_line).tail(n).values
    colors = ["#4ADE80" if v >= 0 else "#F87171" for v in hist]
    ax3.bar(x, hist, color=colors, width=0.03, alpha=0.6)
    ax3.set_ylabel("MACD", color="#CBD5E1", fontsize=8)
    ax3.legend(loc="upper left", fontsize=7, facecolor="#1E293B", labelcolor="#E2E8F0", framealpha=0.7)

    # 4) Stochastic
    ax4 = axes[3]
    ax4.plot(x, stoch_k.tail(n).values, color="#38BDF8", linewidth=1, label="%K")
    ax4.plot(x, stoch_d.tail(n).values, color="#FBBF24", linewidth=1, label="%D")
    ax4.axhline(80, color="#F87171", linewidth=0.7, linestyle="--")
    ax4.axhline(20, color="#4ADE80", linewidth=0.7, linestyle="--")
    ax4.set_ylabel("Stoch", color="#CBD5E1", fontsize=8)
    ax4.set_ylim(0, 100)
    ax4.legend(loc="upper left", fontsize=7, facecolor="#1E293B", labelcolor="#E2E8F0", framealpha=0.7)

    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    plt.setp(ax4.get_xticklabels(), rotation=30, ha="right")

    plt.tight_layout()
    plt.savefig(CHART_FILE, dpi=130, facecolor=fig.get_facecolor())
    plt.close(fig)
    return CHART_FILE


def send_photo_to_telegram(photo_path: str, caption: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(photo_path, "rb") as photo:
        files = {"photo": photo}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"}
        resp = requests.post(url, data=data, files=files, timeout=30)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti grafiko: {resp.text}")
    else:
        print("[OK] Grafikas išsiųstas.")


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

    # --- Paaiškinimų žodynas kiekvienam galimam signalui ---
    explanations = {
        "macd_bull": "MACD linija kirto signalinę liniją iš apačios į viršų - tai dažnai rodo, kad trumpalaikis momentumas pradeda stiprėti pirkėjų naudai. Vienas iš labiausiai paplitusių momentumo indikatorių tarp treiderių.",
        "macd_bear": "MACD linija kirto signalinę liniją iš viršaus į apačią - tai dažnai rodo silpstantį momentumą arba pardavėjų persvaros pradžią.",
        "golden_cross": "50 periodų slankusis vidurkis pakilo virš 200 periodų vidurkio. Istoriškai tai laikoma vienu patikimiausių ILGALAIKĖS tendencijos pasikeitimo į augimo pusę signalų, nors jis vėluoja (rodo, kas jau vyksta, ne kas vyks).",
        "death_cross": "50 periodų slankusis vidurkis nukrito žemiau 200 periodų vidurkio. Tai laikoma ilgalaikės mažėjančios tendencijos signalu.",
        "rsi_oversold": "RSI nukrito žemiau 30 ribos - istoriškai tokiuose lygiuose kaina dažnai (bet ne visada) randa laikiną atramą ir atšoka, nes moneta laikoma 'per daug išparduota' trumpuoju laikotarpiu.",
        "rsi_overbought": "RSI pakilo virš 70 ribos - istoriškai tokiuose lygiuose kaina dažnai patiria korekciją, nes moneta laikoma 'per daug išpirkta' trumpuoju laikotarpiu.",
        "bb_lower": "Kaina išėjo už apatinės Bollinger juostos ribos - tai statistiškai retas įvykis (kaina paprastai 95% laiko yra tarp juostų), rodantis padidėjusį pardavimo spaudimą arba galimą trumpalaikį persistūmimą.",
        "bb_upper": "Kaina išėjo už viršutinės Bollinger juostos ribos - statistiškai retas įvykis, rodantis stiprų pirkimo spaudimą arba galimą trumpalaikį perkaitimą.",
        "stoch_oversold": "Stochastic oscillatorius (greitesnis nei RSI) rodo perparduotą zoną - dažnai naudojamas kartu su RSI patvirtinimui.",
        "stoch_overbought": "Stochastic oscillatorius rodo pervirkintą zoną.",
        "strong_trend": "ADX viršijo 25 ribą - tai reiškia, kad rinka šiuo metu turi AIŠKIĄ, stiprią tendenciją (kryptis priklauso nuo +DI/-DI). Svarbu: tendencijos sekimo strategijos (pvz. MACD, SMA) paprastai patikimesnės stiprios tendencijos metu.",
        "weak_trend": "ADX nukrito žemiau 25 - rinka šiuo metu neturi aiškios krypties ('sukasi vietoje'). Tendencijos indikatoriai (MACD, SMA kirtimai) šiuo metu MAŽIAU patikimi - dažnesni klaidingi signalai.",
        "vwap_up": "Kaina pakilo virš svertinės vidutinės kainos (VWAP) - institucijos dažnai naudoja VWAP kaip 'sąžiningos vertės' atskaitos tašką; kaina virš jo rodo pirkėjų persvarą nuo skaičiavimo pradžios.",
        "vwap_down": "Kaina nukrito žemiau VWAP - rodo pardavėjų persvarą nuo skaičiavimo pradžios.",
    }

    signal_keys = []  # sekam, kurie paaiškinimai aktualūs šiam pranešimui

    def flag_change(key, current_value, on_true_msg, on_false_msg, exp_true, exp_false):
        prev = state.get(key)
        if prev is not None and current_value is not None and prev != current_value:
            signals.append(on_true_msg if current_value else on_false_msg)
            signal_keys.append(exp_true if current_value else exp_false)

    signals.clear()
    flag_change(
        "macd_above_signal", macd_above_signal_now,
        "📈 <b>MACD bullish kirtimasis</b>", "📉 <b>MACD bearish kirtimasis</b>",
        "macd_bull", "macd_bear",
    )
    flag_change(
        "sma50_above_sma200", sma50_above_sma200_now,
        "✨ <b>Golden Cross</b> (SMA50 > SMA200)", "⚠️ <b>Death Cross</b> (SMA50 < SMA200)",
        "golden_cross", "death_cross",
    )
    if rsi_oversold_now and not state.get("rsi_oversold", False):
        signals.append(f"🔵 <b>RSI perparduota</b> ({current_rsi:.1f})")
        signal_keys.append("rsi_oversold")
    if rsi_overbought_now and not state.get("rsi_overbought", False):
        signals.append(f"🔴 <b>RSI pervirkinta</b> ({current_rsi:.1f})")
        signal_keys.append("rsi_overbought")
    if price_below_bb_lower and not state.get("price_below_bb_lower", False):
        signals.append("🔵 <b>Kaina po apatine Bollinger juosta</b>")
        signal_keys.append("bb_lower")
    if price_above_bb_upper and not state.get("price_above_bb_upper", False):
        signals.append("🔴 <b>Kaina virš viršutinės Bollinger juostos</b>")
        signal_keys.append("bb_upper")
    if stoch_oversold_now and not state.get("stoch_oversold", False):
        signals.append(f"🔵 <b>Stochastic perparduota</b> ({current_stoch_k:.1f})")
        signal_keys.append("stoch_oversold")
    if stoch_overbought_now and not state.get("stoch_overbought", False):
        signals.append(f"🔴 <b>Stochastic pervirkinta</b> ({current_stoch_k:.1f})")
        signal_keys.append("stoch_overbought")
    flag_change(
        "strong_trend", strong_trend_now,
        f"💪 <b>Stiprėjanti tendencija</b> (ADX {current_adx:.1f})",
        f"😴 <b>Silpstanti tendencija</b> (ADX {current_adx:.1f})",
        "strong_trend", "weak_trend",
    )
    flag_change(
        "price_above_vwap", price_above_vwap_now,
        "📊 <b>Kaina kirto VWAP į viršų</b>", "📊 <b>Kaina kirto VWAP į apačią</b>",
        "vwap_up", "vwap_down",
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

    # --- Bendros apžvalgos sintezė (aprašomoji, ne prognozė) ---
    if total_directional > 0:
        bullish_ratio = bullish_count / total_directional
    else:
        bullish_ratio = 0.5

    if bullish_ratio >= 0.75:
        overview = "Dauguma indikatorių šiuo metu sutampa į bullish (kylančią) pusę."
    elif bullish_ratio <= 0.25:
        overview = "Dauguma indikatorių šiuo metu sutampa į bearish (krentančią) pusę."
    else:
        overview = "Indikatoriai šiuo metu prieštarauja vieni kitiems - nėra aiškaus sutapimo į vieną pusę."

    if current_adx > 25:
        overview += f" Tendencija šiuo metu vertinama kaip STIPRI (ADX {current_adx:.1f}), tad krypties indikatoriai (MACD, SMA) šiuo metu paprastai patikimesni."
    else:
        overview += f" Tendencija šiuo metu SILPNA/neaiški (ADX {current_adx:.1f}), tad krypties signalai šiuo metu rizikingesni - dažnesni klaidingi kirtimai."

    # --- Nepriklausomas patikrinimas: TradingView pačių reitingas ---
    tv_summary = fetch_tradingview_rating()
    tv_section = ""
    if tv_summary:
        tv_rec = tv_summary.get("RECOMMENDATION", "N/A")
        tv_buy = tv_summary.get("BUY", 0)
        tv_sell = tv_summary.get("SELL", 0)
        tv_neutral = tv_summary.get("NEUTRAL", 0)

        our_direction = "bullish" if bullish_ratio > 0.5 else "bearish" if bullish_ratio < 0.5 else "neutralu"
        tv_direction = "bullish" if "BUY" in tv_rec else "bearish" if "SELL" in tv_rec else "neutralu"

        if our_direction == tv_direction:
            agreement = "✅ SUTAMPA su mūsų analize - tai stiprina pasitikėjimą signalu."
        else:
            agreement = "⚠️ NESUTAMPA su mūsų analize - verta būti atsargesniam, šaltiniai prieštarauja."

        tv_section = (
            f"\n<b>📡 TradingView nepriklausomas reitingas:</b> {tv_rec} "
            f"(Buy: {tv_buy}, Sell: {tv_sell}, Neutral: {tv_neutral})\n{agreement}\n"
        )

    # --- Grafiko generavimas ir siuntimas ---
    try:
        chart_path = create_chart(
            df, rsi, macd_line, signal_line, bb_upper, bb_mid, bb_lower,
            stoch_k, stoch_d, sma50, sma200,
        )
        short_caption = f"🔍 SOL signalas (1h) - ${current_price:,.2f}\n" + " | ".join(
            s.split("<b>")[1].split("</b>")[0] if "<b>" in s else s for s in signals
        )
        if len(short_caption) > 1024:
            short_caption = short_caption[:1000] + "..."
        send_photo_to_telegram(chart_path, short_caption)
    except Exception as e:
        print(f"[KLAIDA] Nepavyko sukurti/išsiųsti grafiko: {e}")

    # --- Detalus tekstinis paaiškinimas ---
    message = f"<b>🔍 SOL techninės analizės signalas (1h)</b>\n\nKaina: ${current_price:,.2f}\n\n"
    for sig_text, exp_key in zip(signals, signal_keys):
        message += f"{sig_text}\n<i>{explanations.get(exp_key, '')}</i>\n\n"

    message += (
        f"<b>📊 Sutapimo santrauka:</b> {bullish_count}/{total_directional} indikatorių bullish, "
        f"{bearish_count}/{total_directional} bearish\n\n"
    )
    message += f"<b>🧭 Bendra apžvalga:</b> {overview}\n"
    message += tv_section
    message += (
        "\n<i>Tai NĖRA finansinis patarimas ir NĖRA prognozė - joks indikatorių derinys "
        "negali patikimai nuspėti trumpalaikės kainos krypties. Tai tik esamos indikatorių "
        "būsenos suvestinė sprendimui priimti. Sprendimą priimk pats.</i>"
    )

    send_to_telegram(message)


if __name__ == "__main__":
    main()
