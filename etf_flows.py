"""
Bitcoin ir Ethereum Spot ETF dienos srautų botas -> Telegram
------------------------------------------------------------------
Kartą per dieną tikrina naujausius JAV Spot BTC ir ETH ETF grynuosius
srautus (inflows/outflows) per OFICIALŲ SoSoValue API
(https://sosovalue.gitbook.io/soso-value-api-doc/).

Reikalauja SOSO_API_KEY - nemokamo, bet patvirtinimo reikalaujančio
API rakto iš https://sosovalue.com/developer/dashboard

Siunčia žinutę tik kai atsiranda NAUJA diena su duomenimis (kad
nepersiųstų tos pačios dienos kelis kartus).
"""

import os
import json
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SOSO_API_KEY = os.environ["SOSO_API_KEY"]

BASE_URL = "https://openapi.sosovalue.com/api/v1"
STATE_FILE = "etf_flows_state.json"


def fetch_latest_flow(symbol: str) -> dict:
    """Gauna naujausią dienos ETF srautų suvestinę konkrečiai monetai (BTC/ETH)."""
    url = f"{BASE_URL}/etfs/summary-history"
    headers = {"x-soso-api-key": SOSO_API_KEY}
    params = {"symbol": symbol, "country_code": "US", "limit": 3}

    resp = requests.get(url, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    # API gali grąžinti arba tiesiog sąrašą, arba {"data": [...]}
    records = data.get("data", data) if isinstance(data, dict) else data
    if not records:
        raise RuntimeError(f"Tuščias atsakymas iš SoSoValue ({symbol})")

    latest = records[0]  # naujausia data pirma (reverse chronological)
    return {
        "date": latest["date"],
        "net_inflow": float(latest["total_net_inflow"]),
        "net_assets": float(latest.get("total_net_assets", 0)),
        "cum_inflow": float(latest.get("cum_net_inflow", 0)),
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
    millions = value / 1_000_000
    if millions >= 0:
        return f"+${millions:,.1f}M"
    return f"-${abs(millions):,.1f}M"


def build_section(name: str, data: dict) -> str:
    lines = [f"<b>{name} ETF</b> ({data['date']})"]
    lines.append(f"Grynasis srautas: {format_usd_millions(data['net_inflow'])}")
    lines.append(f"Bendras turtas (AUM): ${data['net_assets'] / 1_000_000_000:,.2f}B")
    lines.append(f"Kaupiamasis srautas nuo starto: ${data['cum_inflow'] / 1_000_000_000:,.2f}B")
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
        btc_data = fetch_latest_flow("BTC")
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti BTC ETF duomenų: {e}")
        btc_data = None

    try:
        eth_data = fetch_latest_flow("ETH")
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
    message += "\n\n<i>Šaltinis: SoSoValue</i>"

    send_to_telegram(message)

    if btc_data:
        state["last_btc_date"] = btc_data["date"]
    if eth_data:
        state["last_eth_date"] = eth_data["date"]
    save_state(state)


if __name__ == "__main__":
    main()
