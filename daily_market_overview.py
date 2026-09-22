"""
Dienos rinkos apžvalgos botas -> Telegram
------------------------------------------------
Kasdien siunčia išsamią rinkos apžvalgą:
- Pagrindiniai JAV indeksai: S&P 500, Nasdaq, Dow Jones (per Yahoo Finance)
- Žaliavos: auksas, nafta (WTI) (per Yahoo Finance)
- Kiekvienam rodikliui - palyginimas su ANKSTESNE diena (kilimas/kritimas)
- Trumpa JAV/pasaulio/Europos naujienų apžvalga (iš Investing.com
  Economy/Economic Indicators RSS, tik šiandienos įrašai)
"""

import os
import re
import html
import requests
import feedparser
import yfinance as yf
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyMarketBot/1.0)"}

TICKERS = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "Dow Jones": "^DJI",
    "Auksas": "GC=F",
    "Nafta (Brent)": "BZ=F",
}

NEWS_FEEDS = {
    "Ekonomika": "https://www.investing.com/rss/news_14.rss",
    "Ekonominiai rodikliai": "https://www.investing.com/rss/news_95.rss",
    "Akcijų rinka": "https://www.investing.com/rss/news_25.rss",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}


def fetch_index_change(name: str, ticker: str) -> dict:
    """Gauna paskutinę kainą ir pokytį % nuo ANKSTESNĖS prekybos dienos."""
    try:
        data = yf.Ticker(ticker).history(period="5d")
        if len(data) < 2:
            return None
        latest = data.iloc[-1]
        previous = data.iloc[-2]
        change_pct = ((latest["Close"] - previous["Close"]) / previous["Close"]) * 100
        return {
            "name": name,
            "price": latest["Close"],
            "change_pct": change_pct,
            "date": data.index[-1].strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti {name} ({ticker}) duomenų: {e}")
        return None


def clean_text(raw: str, max_chars: int = 150) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def fetch_todays_headlines() -> list:
    """
    Renka NESENIAI paskelbtas antraštes iš keturių pagrindinių šaltinių
    (Ekonomika, Ekonominiai rodikliai, Akcijų rinka, Yahoo Finance).
    Laiko langas 36 val. (ne griežtai 24), nes ne visos RSS naujienos
    turi tikslią laiko žymą, o tikslas - PAKANKAMAI turinio apžvalgai,
    ne tik saujelė atsitiktinių įrašų.
    """
    headlines = []
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=36)

    for source_name, feed_url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[KLAIDA] Nepavyko nuskaityti {source_name}: {e}")
            continue

        for entry in feed.entries[:8]:
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            # jei laiko žymos nėra - vis tiek įtraukiam (feedai rikiuoti
            # naujausi pirma, tad greičiausiai tai vis tiek nesenas įrašas)
            title = clean_text(entry.get("title", ""), 150)
            if title:
                headlines.append(title)

    return headlines[:12]  # padidinta nuo 6 iki 12


def format_index_line(data: dict) -> str:
    if not data:
        return ""
    arrow = "🟢▲" if data["change_pct"] >= 0 else "🔴▼"
    return f"{arrow} <b>{data['name']}</b>: {data['price']:,.2f} ({data['change_pct']:+.2f}%)"


def build_message() -> str:
    now_lt = datetime.now(ZoneInfo("Europe/Vilnius"))
    message = f"<b>🌍 DIENOS RINKOS APŽVALGA</b>\n{now_lt.strftime('%Y-%m-%d')}\n\n"

    message += "<b>📈 Pagrindiniai indeksai</b>\n"
    for name, ticker in TICKERS.items():
        data = fetch_index_change(name, ticker)
        line = format_index_line(data)
        if line:
            message += line + "\n"
    message += "\n"

    headlines = fetch_todays_headlines()
    if headlines:
        message += "<b>📰 Šiandienos JAV/Europos/pasaulio naujienos</b>\n"
        for h in headlines:
            message += f"• {h}\n"
        message += "\n"

    message += "<i>Šaltiniai: Yahoo Finance, SoSoValue, Investing.com</i>"
    return message


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] Dienos rinkos apžvalga išsiųsta.")


def main():
    message = build_message()
    send_to_telegram(message)


if __name__ == "__main__":
    main()
