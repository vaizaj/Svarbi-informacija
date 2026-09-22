"""
Savaitės investavimo naujienų apžvalgos botas -> Telegram
------------------------------------------------------------------
VEIKIMO PRINCIPAS (vienas scriptas, du režimai):
1) KAUPIMAS: kiekvieną paleidimą (per cron, kelis kartus per dieną) tikrina
   RSS šaltinius (Investing.com, Yahoo Finance) ir KAUPIA svarbius straipsnius
   į buferį (weekly_digest_buffer.jsonl)
2) SANTRAUKA: SEKMADIENĮ, po nurodytos valandos, SUDARO ir IŠSIUNČIA savaitės
   apžvalgą iš sukaupto buferio, tada IŠVALO buferį naujai savaitei

Šaltiniai:
- Investing.com: Economy News, Economic Indicators (VISI įtraukiami - jau
  savaime svarbūs), Stock Market News, Earnings (FILTRUOJAMI pagal
  svarbos raktažodžius, nes didelis kiekis)
- Yahoo Finance: bendros finansų naujienos (FILTRUOJAMOS)
"""

import os
import re
import json
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import feedparser
import requests
import time
from deep_translator import MyMemoryTranslator


def translate_safe(text: str, retries: int = 2) -> str:
    """Verčia per MyMemory (nemokama, be kortelės, be Google IP blokavimo rizikos)."""
    if not text:
        return text
    for attempt in range(retries + 1):
        try:
            result = MyMemoryTranslator(source="en-US", target="lt-LT").translate(text)
            time.sleep(0.3)
            return result
        except Exception as e:
            if "too many requests" in str(e).lower() and attempt < retries:
                print(f"[ĮSPĖJIMAS] Vertimo limitas - laukiu 3s ({attempt + 1}/{retries})...")
                time.sleep(3)
                continue
            print(f"[ĮSPĖJIMAS] Nepavyko išversti: {e}")
            return text
    return text

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WeeklyReviewBot/1.0)"}
BUFFER_FILE = "weekly_digest_buffer.jsonl"
STATE_FILE = "weekly_digest_state.json"

DIGEST_WEEKDAY = 6  # 0=pirmadienis ... 6=sekmadienis (Python weekday())
DIGEST_HOUR_LT = 18  # kurią valandą (LT laiku) siųsti santrauką

HIGH_PRIORITY_FEEDS = {
    "Investing.com - Ekonomika": "https://www.investing.com/rss/news_14.rss",
    "Investing.com - Ekonominiai rodikliai": "https://www.investing.com/rss/news_95.rss",
}

FILTERED_FEEDS = {
    "Investing.com - Akcijų rinka": "https://www.investing.com/rss/news_25.rss",
    "Investing.com - Pelno ataskaitos": "https://www.investing.com/rss/news_1062.rss",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}

IMPORTANCE_KEYWORDS = [
    "fed", "federal reserve", "ecb", "central bank", "interest rate", "rate cut", "rate hike",
    "inflation", "cpi", "gdp", "recession", "unemployment", "jobs report", "nonfarm payrolls",
    "earnings beat", "earnings miss", "guidance", "crash", "rally", "sell-off", "selloff",
    "bear market", "bull market", "correction", "all-time high", "record high", "yields",
    "treasury", "tariff", "trade war", "s&p 500", "nasdaq", "dow jones", "volatility",
    "surge", "plunge", "soar", "tumble",
]

# YPAČ svarbūs raktažodžiai - tokie straipsniai pateks į atskirą "TOP" sekciją
# viršuje, ne tik į bendrą kategorijos sąrašą.
TOP_TIER_KEYWORDS = [
    "fed cuts", "fed hikes", "fed raises", "rate decision", "recession", "crash",
    "record high", "all-time high", "war", "invasion", "bankruptcy", "default",
    "black swan", "market meltdown", "circuit breaker", "emergency",
]


def matches_top_tier(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for kw in TOP_TIER_KEYWORDS:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            return True
    return False


def matches_importance(title: str, summary: str) -> bool:
    text = (title + " " + summary).lower()
    for kw in IMPORTANCE_KEYWORDS:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            return True
    return False


def clean_text(raw: str, max_chars: int = 200) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def load_buffer() -> list:
    entries = []
    if os.path.exists(BUFFER_FILE):
        with open(BUFFER_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return entries


def append_to_buffer(entry: dict):
    with open(BUFFER_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def clear_buffer():
    if os.path.exists(BUFFER_FILE):
        os.remove(BUFFER_FILE)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def collect_new_articles():
    state = load_state()
    seen_links = set(state.get("seen_links", []))
    new_count = 0

    all_feeds = {**HIGH_PRIORITY_FEEDS, **FILTERED_FEEDS}
    is_high_priority = set(HIGH_PRIORITY_FEEDS.keys())

    for source_name, feed_url in all_feeds.items():
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[KLAIDA] Nepavyko nuskaityti {source_name}: {e}")
            continue

        for entry in feed.entries[:20]:
            link = entry.get("link", entry.get("id", entry.get("title")))
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            title = entry.get("title", "")
            summary = entry.get("summary", "")

            if source_name not in is_high_priority:
                if not matches_importance(title, summary):
                    continue

            append_to_buffer({
                "source": source_name,
                "title": clean_text(title, 150),
                "summary": clean_text(summary, 200),
                "link": link,
                "is_top_tier": matches_top_tier(title, summary),
            })
            new_count += 1

    state["seen_links"] = list(seen_links)[-2000:]
    save_state(state)
    print(f"Pridėta {new_count} naujų straipsnių į savaitės buferį.")


def check_recent_rate_changes() -> list:
    """
    Patikrina JAU TURIMUS ecb_rates_state.json / fed_rates_state.json failus -
    jei ten esanti data patenka į pastarąsias 7 dienas, tai PATVIRTINTAS,
    tikras normų pasikeitimas šią savaitę (ne spėjimas iš naujienų teksto).
    """
    highlights = []
    now = datetime.now(ZoneInfo("Europe/Vilnius"))
    cutoff = now - timedelta(days=7)

    try:
        with open("ecb_rates_state.json", "r") as f:
            ecb_state = json.load(f)
        dfr = ecb_state.get("D.U2.EUR.4F.KR.DFR.LEV", {})
        if dfr.get("date"):
            dfr_date = datetime.strptime(dfr["date"], "%Y-%m-%d").replace(tzinfo=ZoneInfo("Europe/Vilnius"))
            if dfr_date >= cutoff:
                highlights.append(f"🏦 ECB indėlių norma: {dfr.get('value')}% (nuo {dfr['date']})")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    try:
        with open("fed_rates_state.json", "r") as f:
            fed_state = json.load(f)
        upper = fed_state.get("upper", {})
        if upper.get("date"):
            fed_date = datetime.strptime(upper["date"], "%Y-%m-%d").replace(tzinfo=ZoneInfo("Europe/Vilnius"))
            if fed_date >= cutoff:
                lower_val = fed_state.get("lower", {}).get("value")
                highlights.append(f"🇺🇸 Fed diapazonas: {lower_val}%-{upper.get('value')}% (nuo {upper['date']})")
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass

    return highlights


def categorize(source: str) -> str:
    if "Ekonomika" in source or "Ekonominiai" in source:
        return "📊 Makroekonomika"
    if "Pelno" in source:
        return "💼 Pelno ataskaitos"
    return "📈 Rinkos naujienos"


def send_weekly_digest():
    entries = load_buffer()
    if not entries:
        print("Buferis tuščias - nėra ką siųsti šią savaitę.")
        return

    grouped = {}
    for e in entries:
        cat = categorize(e["source"])
        grouped.setdefault(cat, []).append(e)

    today = datetime.now(ZoneInfo("Europe/Vilnius"))
    week_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    week_end = today.strftime("%Y-%m-%d")

    message = f"<b>📰 SAVAITĖS INVESTAVIMO APŽVALGA</b>\n{week_start} – {week_end}\n\n"

    # --- TOP sekcija: patikrinti centrinių bankų sprendimai + ypač svarbūs straipsniai ---
    top_items = [e for e in entries if e.get("is_top_tier")]
    rate_highlights = check_recent_rate_changes()

    if rate_highlights or top_items:
        message += "<b>🔥 SVARBIAUSI SAVAITĖS ĮVYKIAI</b>\n"
        for h in rate_highlights:
            message += f"• {h}\n"
        for e in top_items[:4]:
            title_lt = translate_safe(e["title"])
            message += f"• {title_lt}\n"
        message += "\n"

    category_order = ["📊 Makroekonomika", "📈 Rinkos naujienos", "💼 Pelno ataskaitos"]
    max_per_category = 5

    for cat in category_order:
        items = [e for e in grouped.get(cat, []) if not e.get("is_top_tier")]
        if not items:
            continue
        message += f"<b>{cat}</b>\n"
        for e in items[:max_per_category]:
            title_lt = translate_safe(e["title"])
            message += f"• {title_lt}\n"
        if len(items) > max_per_category:
            message += f"  <i>...ir dar {len(items) - max_per_category} straipsnių šia tema</i>\n"
        message += "\n"

    message += "<i>Šaltiniai: Investing.com, Yahoo Finance</i>"

    send_to_telegram(message)
    clear_buffer()
    print("Savaitės apžvalga išsiųsta, buferis išvalytas.")


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
        print("[OK] Savaitės apžvalga išsiųsta į Telegram.")


def main():
    collect_new_articles()

    now = datetime.now(ZoneInfo("Europe/Vilnius"))
    state = load_state()
    last_digest_date = state.get("last_digest_date")
    today_str = now.strftime("%Y-%m-%d")

    is_digest_time = now.weekday() == DIGEST_WEEKDAY and now.hour >= DIGEST_HOUR_LT
    already_sent_today = last_digest_date == today_str

    if is_digest_time and not already_sent_today:
        send_weekly_digest()
        state["last_digest_date"] = today_str
        save_state(state)
    else:
        print("Ne santraukos siuntimo laikas - tik kaupiame duomenis.")


if __name__ == "__main__":
    main()
