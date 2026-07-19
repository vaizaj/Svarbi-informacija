"""
Kasdienė krypto suvestinė -> Telegram
----------------------------------------
Kartą per dieną (9:00 Lietuvos laiku) siunčia:
- BTC, ETH, SOL, XLM kainas tuo momentu (USD) + 24h pokytį
- Rinkos nuotaikų indeksą (Fear & Greed Index)

Kainoms naudojami TRYS nemokami šaltiniai su atsargine (fallback) logika:
CoinGecko -> Binance -> CoinCap. Jei vienas neveikia (pvz. dėl serverio
lokacijos apribojimų), automatiškai bandomas kitas.
"""

import os
import time
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# CoinGecko id -> papildoma informacija kitiems šaltiniams
COINS = {
    "bitcoin": {"symbol": "BTC", "binance": "BTCUSDT", "coincap": "bitcoin"},
    "ethereum": {"symbol": "ETH", "binance": "ETHUSDT", "coincap": "ethereum"},
    "solana": {"symbol": "SOL", "binance": "SOLUSDT", "coincap": "solana"},
    "stellar": {"symbol": "XLM", "binance": "XLMUSDT", "coincap": "stellar"},
}

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CryptoNewsBot/1.0)"}

SENTIMENT_LT = {
    "Extreme Fear": "Ekstremali baimė",
    "Fear": "Baimė",
    "Neutral": "Neutralu",
    "Greed": "Godumas",
    "Extreme Greed": "Ekstremalus godumas",
}


def try_coingecko() -> dict:
    ids = ",".join(COINS.keys())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    result = {}
    for coin_id in COINS:
        if coin_id in data:
            result[coin_id] = {
                "usd": data[coin_id]["usd"],
                "usd_24h_change": data[coin_id].get("usd_24h_change", 0),
            }
    return result


def try_binance() -> dict:
    result = {}
    for coin_id, info in COINS.items():
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={info['binance']}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        result[coin_id] = {
            "usd": float(data["lastPrice"]),
            "usd_24h_change": float(data["priceChangePercent"]),
        }
    return result


def try_coincap() -> dict:
    result = {}
    for coin_id, info in COINS.items():
        url = f"https://api.coincap.io/v2/assets/{info['coincap']}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        result[coin_id] = {
            "usd": float(data["priceUsd"]),
            "usd_24h_change": float(data["changePercent24Hr"]),
        }
    return result


def get_prices() -> dict:
    """Bando kelis šaltinius iš eilės, kol vienas pilnai suveikia."""
    sources = [
        ("CoinGecko", try_coingecko),
        ("Binance", try_binance),
        ("CoinCap", try_coincap),
    ]
    for name, func in sources:
        try:
            prices = func()
            if prices and len(prices) == len(COINS):
                print(f"[OK] Kainos gautos iš: {name}")
                return prices
            print(f"[ĮSPĖJIMAS] {name} grąžino nepilnus duomenis, bandau kitą šaltinį.")
        except Exception as e:
            print(f"[ĮSPĖJIMAS] {name} nepavyko: {e}")
        time.sleep(2)
    print("[KLAIDA] Nepavyko gauti kainų iš jokio šaltinio.")
    return {}


def get_sentiment() -> tuple:
    """Gauna Fear & Greed indeksą (0-100) ir jo klasifikaciją."""
    url = "https://api.alternative.me/fng/?limit=1"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()["data"][0]
    value = data["value"]
    classification = data["value_classification"]
    classification_lt = SENTIMENT_LT.get(classification, classification)
    return value, classification_lt


def format_change(change: float) -> str:
    arrow = "🟢" if change >= 0 else "🔴"
    sign = "+" if change >= 0 else ""
    return f"{arrow} {sign}{change:.2f}%"


def build_message() -> str:
    prices = get_prices()

    lines = ["<b>📊 Dienos krypto suvestinė</b>\n"]

    for coin_id, info in COINS.items():
        symbol = info["symbol"]
        if coin_id in prices:
            price = prices[coin_id]["usd"]
            change = prices[coin_id].get("usd_24h_change", 0)
            lines.append(f"<b>{symbol}</b>: ${price:,.2f}  {format_change(change)}")
        else:
            lines.append(f"<b>{symbol}</b>: nepavyko gauti kainos")

    try:
        value, classification_lt = get_sentiment()
        lines.append(f"\n<b>Rinkos nuotaikų indeksas:</b> {value}/100 ({classification_lt})")
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko gauti nuotaikų indekso: {e}")

    return "\n".join(lines)


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
        print("[OK] Dienos suvestinė išsiųsta.")


def main():
    message = build_message()
    send_to_telegram(message)


if __name__ == "__main__":
    main()
