"""
Kasdienė krypto suvestinė -> Telegram
----------------------------------------
Kartą per dieną (9:00 Lietuvos laiku) siunčia:
- BTC, ETH, SOL, XLM kainas tuo momentu (USD) + 24h pokytį
- Rinkos nuotaikų indeksą (Fear & Greed Index)

Naudoja nemokamus, be API rakto veikiančius šaltinius:
- Binance viešas API (kainos)
- alternative.me (Fear & Greed Index)
"""

import os
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

COINS = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
    "XLMUSDT": "XLM",
}

SENTIMENT_LT = {
    "Extreme Fear": "Ekstremali baimė",
    "Fear": "Baimė",
    "Neutral": "Neutralu",
    "Greed": "Godumas",
    "Extreme Greed": "Ekstremalus godumas",
}


def get_prices() -> dict:
    """Gauna dabartines kainas ir 24h pokytį iš Binance viešo API."""
    prices = {}
    for symbol in COINS:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            prices[symbol] = {
                "usd": float(data["lastPrice"]),
                "usd_24h_change": float(data["priceChangePercent"]),
            }
        except Exception as e:
            print(f"[KLAIDA] Nepavyko gauti {symbol} kainos: {e}")
    return prices


def get_sentiment() -> tuple:
    """Gauna Fear & Greed indeksą (0-100) ir jo klasifikaciją."""
    url = "https://api.alternative.me/fng/?limit=1"
    resp = requests.get(url, timeout=15)
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

    for coin_id, symbol in COINS.items():
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
