"""
Bitcoin ir Ethereum Spot ETF dienos srautų botas -> Telegram
------------------------------------------------------------------
Kartą per dieną tikrina naujausius JAV Spot BTC ir ETH ETF grynuosius
srautus (inflows/outflows) iš Farside Investors - nemokamo, viešai
prieinamo šaltinio (farside.co.uk), kuris renka oficialius kiekvieno
ETF fondo duomenis.

Siunčia žinutę tik kai atsiranda NAUJA diena su duomenimis (kad
nepersiųstų tos pačios dienos kelis kartus).
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ETFFlowBot/1.0)"}
STATE_FILE = "etf_flows_state.json"

DATE_PATTERN = re.compile(r"^\d{1,2} \w{3} \d{4}$")


def parse_number(text: str):
    """Konvertuoja '(44.5)' -> -44.5, '209.4' -> 209.4, '0.0' -> 0.0"""
    text = text.strip().replace(",", "")
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None


def fetch_latest_flow(url: str) -> dict:
    """
    Nuskaito Farside lentelę ir grąžina paskutinės dienos su duomenimis
    informaciją: data, bendras (Total) grynasis srautas, ir 2 didžiausi
    individualūs fondai tą dieną.
    """
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table")
    if not table:
        raise RuntimeError("Nerasta lentelė puslapyje.")

    rows = table.find_all("tr")

    # Randam ticker eilutę (antra eilutė, su fondų kodais pvz. IBIT, FBTC...)
    ticker_row = None
    for row in rows:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) > 3 and all(c.isupper() or c == "" for c in cells[:3] if c):
            ticker_row = cells
            break

    # Randam VISAS datos eilutes, imam PASKUTINĘ (naujausią)
    latest_row_cells = None
    latest_date_text = None
    for row in rows:
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if cells and DATE_PATTERN.match(cells[0]):
            latest_date_text = cells[0]
            latest_row_cells = cells

    if not latest_row_cells:
        raise RuntimeError("Nerasta jokia datos eilutė.")

    total_value = parse_number(latest_row_cells[-1])

    # Individualūs fondų srautai (be pirmo stulpelio - datos, ir paskutinio - Total)
    fund_flows = []
    if ticker_row:
        tickers = ticker_row[1:len(latest_row_cells) - 1]
        values = latest_row_cells[1:-1]
        for ticker, val in zip(tickers, values):
            num = parse_number(val)
            if num is not None and ticker:
                fund_flows.append((ticker, num))

    fund_flows.sort(key=lambda x: abs(x[1]), reverse=True)

    return {
        "date": latest_date_text,
        "total": total_value,
        "top_funds": fund_flows[:3],
    }


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def format_usd_millions(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:,.1f}M"


def build_section(name: str, data: dict) -> str:
    lines = [f"<b>{name} ETF</b> ({data['date']})"]
    lines.append(f"Bendras grynasis srautas: {format_usd_millions(data['total'])}")
    if data["top_funds"]:
        top_str = ", ".join(f"{t}: {format_usd_millions(v)}" for t, v in data["top_funds"])
        lines.append(f"Didžiausi indėliai: {top_str}")
    return "\n".join(lines)


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] ETF srautų žinutė išsiųsta.")


def main():
    state = load_state()

    try:
        btc_data = fetch_latest_flow("https://farside.co.uk/btc/")
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti BTC ETF duomenų: {e}")
        btc_data = None

    try:
        eth_data = fetch_latest_flow("https://farside.co.uk/eth/")
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti ETH ETF duomenų: {e}")
        eth_data = None

    if not btc_data and not eth_data:
        print("Nepavyko gauti jokių duomenų.")
        return

    last_btc_date = state.get("last_btc_date")
    last_eth_date = state.get("last_eth_date")

    btc_is_new = btc_data and btc_data["date"] != last_btc_date
    eth_is_new = eth_data and eth_data["date"] != last_eth_date

    if not btc_is_new and not eth_is_new:
        print("Nėra naujų ETF srautų duomenų (ta pati diena, kaip anksčiau).")
        return

    sections = ["<b>💰 Spot ETF dienos srautai</b>\n"]
    if btc_is_new:
        sections.append(build_section("₿ Bitcoin", btc_data))
    if eth_is_new:
        sections.append(build_section("Ξ Ethereum", eth_data))

    message = "\n\n".join(sections)
    message += "\n\n<i>Šaltinis: Farside Investors (farside.co.uk)</i>"

    send_to_telegram(message)

    if btc_data:
        state["last_btc_date"] = btc_data["date"]
    if eth_data:
        state["last_eth_date"] = eth_data["date"]
    save_state(state)


if __name__ == "__main__":
    main()
