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
import time
import json
import re
import html
import feedparser
import requests
from deep_translator import MyMemoryTranslator

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

RSS_FEEDS = {
    # Sumažinta iki 4 pačių autoritetingiausių/didžiausių šaltinių, kad
    # sumažintume bendrą srautą - likę (CryptoSlate, NewsBTC, U.Today,
    # CryptoPotato, BeInCrypto, Bitcoin Magazine) dažnai perpublikuoja
    # tas pačias naujienas, tik su vėlavimu.
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "Decrypt": "https://decrypt.co/feed",
    "The Block": "https://www.theblock.co/rss.xml",
}

KEYWORDS_FILTER = ["solana", "sol", "bitcoin", "btc", "ethereum", "eth", "stellar", "xlm", "trump", "white house"]

SEEN_FILE = "seen_articles.json"

# Kiek simbolių iš santraukos naudoti (trumpa ištrauka, ne visas straipsnis)
SUMMARY_MAX_CHARS = 650  # padidinta iki ~4 pilnų sakinių
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}


def clean_summary(raw_summary: str) -> str:
    """Pašalina HTML žymes ir apkarpo santrauką iki protingo ilgio,
    stengiantis baigti PILNU sakiniu (ties tašku), ne pusiau žodžiu."""
    if not raw_summary:
        return ""
    # pašalinam HTML žymes
    text = re.sub(r"<[^>]+>", " ", raw_summary)
    # dekoduojam HTML simbolius (&amp; -> & ir pan.)
    text = html.unescape(text)
    # sutvarkom tarpus
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= SUMMARY_MAX_CHARS:
        return text

    truncated = text[:SUMMARY_MAX_CHARS]
    # bandom rasti paskutinį sakinio pabaigos tašką (. ! ?) limito ribose
    last_sentence_end = max(
        truncated.rfind(". "), truncated.rfind("! "), truncated.rfind("? ")
    )
    # naudojam sakinio ribą tik jei ji NĖRA per arti pradžios (kad neliktų
    # per trumpa, jei pirmas sakinys ilgas ir tašką randam per anksti)
    if last_sentence_end > SUMMARY_MAX_CHARS * 0.4:
        return truncated[: last_sentence_end + 1]

    # jei tinkamo sakinio taško nerasta - apkerpam ties žodžio riba
    return truncated.rsplit(" ", 1)[0] + "..."


MIN_SUMMARY_CHARS = 150  # jei RSS santrauka trumpesnė - bandom papildyti iš puslapio


def fetch_og_description(article_url: str) -> str:
    """
    Jei RSS santrauka per trumpa (tik 1 sakinys), bandom gauti straipsnio
    'og:description' meta žymą - tai TA PATI 'trumpinuko' rūšis, kurią
    leidėjas specialiai paruošia socialiniam dalinimuisi, dažnai išsamesnė
    nei RSS laukas. Grąžina "" jei nepavyksta.
    """
    try:
        resp = requests.get(article_url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        match = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            resp.text,
            re.IGNORECASE,
        )
        if match:
            return html.unescape(match.group(1)).strip()
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko gauti og:description: {e}")
    return ""


def translate_to_lithuanian(text: str, retries: int = 2) -> str:
    """
    Išverčia tekstą į lietuvių kalbą per MyMemory (nemokama, be kortelės,
    be Google IP blokavimo rizikos). MyMemory reikalauja pilno kalbos
    kodo formato (en-US, lt-LT), ne trumpo (en, lt).
    """
    if not text:
        return text
    for attempt in range(retries + 1):
        try:
            result = MyMemoryTranslator(source="en-US", target="lt-LT").translate(text)
            time.sleep(0.3)  # mandagumo pauzė TARP visų vertimo kvietimų
            return result
        except Exception as e:
            error_text = str(e)
            if "too many requests" in error_text.lower() and attempt < retries:
                print(f"[ĮSPĖJIMAS] Vertimo limitas pasiektas - laukiu 3s ir bandau vėl ({attempt + 1}/{retries})...")
                time.sleep(3)
                continue
            print(f"[ĮSPĖJIMAS] Nepavyko išversti: {e}")
            return text
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


ERROR_PAGE_MARKERS = [
    "error 500", "error 404", "server error", "that's an error",
    "that's all we know", "page not found", "403 forbidden",
]


def is_error_page_content(title: str, summary: str) -> bool:
    """Atpažįsta, ar antraštė/santrauka atrodo kaip svetainės klaidos puslapio
    turinys (pvz. 'Error 500'), o ne tikras straipsnis - tai apsauga nuo
    šaltinio RSS srauto laikinai užfiksuotų klaidų."""
    text = (title + " " + summary).lower()
    return any(marker in text for marker in ERROR_PAGE_MARKERS)


def matches_filter(title: str, summary: str) -> bool:
    if is_error_page_content(title, summary):
        return False
    if not KEYWORDS_FILTER:
        return True
    text = (title + " " + summary).lower()
    # VISO ŽODŽIO atitikimas (\b - word boundary), kad "sol" neatitiktų
    # "solution", "eth" neatitiktų "method" ir pan. - be to sukeldavo
    # daug klaidingų, nesusijusių straipsnių praėjimą per filtrą.
    for kw in KEYWORDS_FILTER:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, text):
            return True
    return False


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
                # per pirmą paleidimą tik pažymim, nesiunčiam senų naujienų
                seen.add(article_id)
                continue

            if matches_filter(title, summary):
                summary_clean = clean_summary(summary)

                # Jei RSS santrauka per trumpa (tik 1 sakinys) - bandom
                # papildyti iš og:description meta žymos straipsnio puslapyje
                if len(summary_clean) < MIN_SUMMARY_CHARS and link:
                    og_desc = fetch_og_description(link)
                    if og_desc and len(og_desc) > len(summary_clean):
                        summary_clean = clean_summary(og_desc)

                title_lt = translate_to_lithuanian(title)
                summary_lt = translate_to_lithuanian(summary_clean)

                message = f"<b>[{source_name}]</b> {title_lt}"
                if summary_lt:
                    message += f"\n\n{summary_lt}"
                message += f"\n\n🇬🇧 <i>{title}</i>"
                if summary_clean:
                    message += f"\n\n{summary_clean}"
                message += f"\n\n{link}"

                send_to_telegram(message)

            seen.add(article_id)

    save_seen(seen)
    print("Patikrinimas baigtas.")


if __name__ == "__main__":
    main()
