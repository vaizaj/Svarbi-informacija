"""
DEX Boost 24h piko sekimo botas -> Telegram
------------------------------------------------
Paleidžiamas PERIODIŠKAI (per cron, kas ~1 val.). Kiekvienam neseniai
(per pastarąsias 24 val.) aptiktam Robinhood boost'ui:
1) Patikrina DABARTINĘ kainą/market cap/likvidumą
2) Atnaujina PIKO (aukščiausio taško) reikšmę, jei dabartinė didesnė
3) Kai sueina 24 val. nuo pirmo aptikimo - "užbaigia" sekimą ir
   išsiunčia santrauką: kiek kilo iki piko, kokia galutinė būsena
   (ar likvidumas dar "gyvas", ar token'as jau "numiręs")

Duomenų šaltinis: boost_history.jsonl (rašomas dex_boosts_ws.py bote)
Būsena: peak_tracking.json
"""

import os
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["DEX_BOOSTS_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PeakTrackerBot/1.0)"}
TOKEN_INFO_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"

BOOST_HISTORY_FILE = "boost_history.jsonl"
PEAK_STATE_FILE = "peak_tracking.json"
DEPLOYER_STATE_FILE = "known_deployers.json"

TRACKING_WINDOW_HOURS = 24
DEAD_LIQUIDITY_THRESHOLD_USD = 500  # jei likvidumas žemiau šito - laikom "numirusiu"


def load_jsonl(path: str) -> list:
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return default


def save_json(path: str, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_current_data(chain_id: str, token_address: str) -> dict:
    try:
        url = TOKEN_INFO_URL.format(address=token_address)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        for pair in pairs:
            if pair.get("chainId") == chain_id:
                return {
                    "price_usd": float(pair.get("priceUsd") or 0),
                    "market_cap": pair.get("marketCap") or pair.get("fdv") or 0,
                    "liquidity_usd": pair.get("liquidity", {}).get("usd") or 0,
                }
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko gauti dabartinių duomenų {token_address}: {e}")
    return {}


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] 24h rezultatų santrauka išsiųsta.")


def format_pct(initial: float, current: float) -> str:
    if not initial or initial == 0:
        return "N/A"
    pct = ((current - initial) / initial) * 100
    arrow = "🟢" if pct >= 0 else "🔴"
    sign = "+" if pct >= 0 else ""
    return f"{arrow} {sign}{pct:.0f}%"


def main():
    now = datetime.now(ZoneInfo("Europe/Vilnius"))
    history = load_jsonl(BOOST_HISTORY_FILE)
    tracking = load_json(PEAK_STATE_FILE, {})
    deployers = load_json(DEPLOYER_STATE_FILE, {})

    # 1) Inicializuojam sekimą naujiems (dar nematytiems) įrašams
    for entry in history:
        token_address = entry["token_address"]
        if token_address not in tracking:
            tracking[token_address] = {
                "name": entry.get("name"),
                "symbol": entry.get("symbol"),
                "deployer": entry.get("deployer"),
                "chain_id": entry.get("chain_id"),
                "first_detected_at": entry.get("detected_at"),
                "initial_price": float(entry.get("price_usd_at_detection") or 0),
                "initial_mcap": entry.get("market_cap_at_detection") or 0,
                "peak_price": float(entry.get("price_usd_at_detection") or 0),
                "peak_mcap": entry.get("market_cap_at_detection") or 0,
                "peak_time": entry.get("detected_at"),
                "finalized": False,
            }

    # 2) Atnaujinam AKTYVIAI sekamus (dar nefinalizuotus, dar 24h lange)
    updated_count = 0
    finalized_count = 0

    for token_address, info in tracking.items():
        if info.get("finalized"):
            continue

        try:
            first_detected = datetime.strptime(
                info["first_detected_at"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=ZoneInfo("Europe/Vilnius"))
        except Exception:
            continue

        age = now - first_detected

        current = fetch_current_data(info["chain_id"], token_address)
        if current:
            if current["market_cap"] and current["market_cap"] > (info.get("peak_mcap") or 0):
                info["peak_mcap"] = current["market_cap"]
                info["peak_price"] = current["price_usd"]
                info["peak_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
            info["last_liquidity"] = current.get("liquidity_usd", 0)
            info["last_mcap"] = current.get("market_cap", 0)
            info["last_price"] = current.get("price_usd", 0)
            updated_count += 1

        time.sleep(0.3)  # mandagumo pauzė

        # 3) Jei sueina 24h - finalizuojam ir siunčiam santrauką
        if age >= timedelta(hours=TRACKING_WINDOW_HOURS):
            info["finalized"] = True
            finalized_count += 1

            deployer_count = len(deployers.get(info.get("deployer") or "", []))
            is_alive = (info.get("last_liquidity") or 0) >= DEAD_LIQUIDITY_THRESHOLD_USD

            peak_change = format_pct(info["initial_mcap"], info["peak_mcap"])
            final_change = format_pct(info["initial_mcap"], info.get("last_mcap") or 0)

            message = (
                f"📊 <b>24h rezultatas: {info['name']} ({info['symbol']})</b>\n\n"
                f"Pradinis MC: ${info['initial_mcap']:,.0f}\n"
                f"🔝 Piko MC: ${info['peak_mcap']:,.0f} ({peak_change})\n"
                f"Dabartinis MC: ${info.get('last_mcap', 0):,.0f} ({final_change})\n"
                f"Statusas: {'🟢 Gyvas' if is_alive else '☠️ Panašu, kad numiręs (žemas likvidumas)'}\n"
            )
            if deployer_count > 1:
                message += f"\n⚠️ Kūrėjas paleido iš viso {deployer_count} boostintus token'us."

            send_to_telegram(message)

    save_json(PEAK_STATE_FILE, tracking)

    print(f"Atnaujinta: {updated_count}, Finalizuota (24h praėjo): {finalized_count}, "
          f"Iš viso sekamų: {len(tracking)}")


if __name__ == "__main__":
    main()
