"""
Krypto naujienų -> Telegram botas (GitHub Actions versija)
------------------------------------------------------------
Skirtumas nuo originalios versijos: šis scriptas patikrina naujienas
VIENĄ KARTĄ ir baigia darbą (neveikia begaliniame cikle), nes GitHub
Actions paleidžia trumpas užduotis pagal grafiką, o ne nuolatinius procesus.

"Matytų" straipsnių sąrašas saugomas seen_articles.json faile, kuris
turi būti commit'inamas atgal į repozitoriją po kiekvieno paleidimo
(tai automatiškai atlieka prisegtas .github/workflows/check_news.yml).
"""

import os
import json
import feedparser
import requests
from deep_translator import GoogleTranslator

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

RSS_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}

KEYWORDS_FILTER = ["solana", "sol", "bitcoin", "btc", "ethereum", "eth", "stellar", "xlm"]

SEEN_FILE = "seen_articles.json"


def translate_to_lithuanian(text: str) -> str:
    """Išverčia tekstą į lietuvių kalbą. Jei nepavyksta - grąžina originalą."""
    if not text:
        return text
    try:
        return GoogleTranslator(source="auto", target="lt").translate(text)
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko išversti: {e}")
        return text


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    trimmed = list(seen)[-1000:]
    with open(SEEN_FILE, "w") as f:
        json.dump(trimmed, f)


def matches_filter(title: str, summary: str) -> bool:
    if not KEYWORDS_FILTER:
        return True
    text = (title + " " + summary).lower()
    return any(kw.lower() in text for kw in KEYWORDS_FILTER)


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] Žinutė išsiųsta.")


def main():
    seen = load_seen()
    first_run = not seen

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[KLAIDA] Nepavyko nuskaityti {source_name}: {e}")
            continue

        for entry in feed.entries[:15]:
            article_id = entry.get("link", entry.get("id", entry.get("title")))
            if article_id in seen:
                continue

            title = entry.get("title", "Be pavadinimo")
            summary = entry.get("summary", "")
            link = entry.get("link", "")

            if first_run:
                seen.add(article_id)
                continue

            if matches_filter(title, summary):
                title_lt = translate_to_lithuanian(title)
                message = f"<b>[{source_name}]</b> {title_lt}\n\n{link}"
                send_to_telegram(message)

            seen.add(article_id)

    save_seen(seen)
    print("Patikrinimas baigtas.")


if __name__ == "__main__":
    main()
