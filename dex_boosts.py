"""
DEX Screener Boost sekimo botas (Robinhood Chain) -> Telegram
------------------------------------------------------------------
Seka NAUJAI APMOKĖTUS DEX Screener boost'us Robinhood grandinėje (chainId
"robinhood") per oficialų, nemokamą DexScreener API - be jokio rakto.

SVARBU (perskaityk prieš naudojant):
- Tai TIK INFORMACINIS botas. Jis NIEKO neperka automatiškai.
- Techniškai NEĮMANOMA gauti informacijos apie boost UŽSAKYMĄ PRIEŠ jam
  esant apmokėtam - ta informacija privati, kol neįvyksta apmokėjimas.
  Šis botas praneša TIK apie JAU apmokėtus (t.y. viešai matomus) boost'us,
  kaip įmanoma greičiau po apmokėjimo momento.
- Boost apmokėjimas NĖRA projekto kokybės ar saugumo garantija - dažnai
  tai reklaminis įrankis, naudojamas dirbtiniam susidomėjimui sukurti.
  Statistiškai dauguma naujų memecoin projektų praranda didžiąją dalį
  vertės arba pasirodo esantys apgaulė ("rug pull"). Tai NĖRA finansinis
  patarimas - sprendimą visada priimi pats, savo rizika.
"""

import os
import json
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["DEX_BOOSTS_CHAT_ID"]

HEADERS = {"Accept": "*/*", "User-Agent": "Mozilla/5.0 (compatible; BoostWatchBot/1.0)"}
STATE_FILE = "dex_boosts_state.json"

TARGET_CHAIN = "robinhood"
BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
TOKEN_INFO_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"


def fetch_latest_boosts() -> list:
    """Gauna naujausių apmokėtų boost'ų sąrašą (visoms grandinėms)."""
    resp = requests.get(BOOSTS_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    # API gali grąžinti arba sąrašą, arba {"data": [...]}
    if isinstance(data, dict):
        return data.get("data", [])
    return data


def fetch_token_info(chain_id: str, address: str) -> dict:
    """Gauna papildomus token'o duomenis (likvidumas, kaina, mcap) kontekstui."""
    try:
        url = TOKEN_INFO_URL.format(address=address)
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        # imam pirmą porą, atitinkančią mūsų grandinę
        for pair in pairs:
            if pair.get("chainId") == chain_id:
                return {
                    "price_usd": pair.get("priceUsd"),
                    "liquidity_usd": pair.get("liquidity", {}).get("usd"),
                    "market_cap": pair.get("marketCap") or pair.get("fdv"),
                    "pair_url": pair.get("url"),
                    "symbol": pair.get("baseToken", {}).get("symbol"),
                    "name": pair.get("baseToken", {}).get("name"),
                }
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko gauti token info: {e}")
    return {}


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


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
        print("[OK] Boost pranešimas išsiųstas.")


def main():
    state = load_state()
    is_first_run = not state
    seen = set(state.get("seen_boosts", []))

    try:
        boosts = fetch_latest_boosts()
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti boost'ų sąrašo: {e}")
        return

    robinhood_boosts = [b for b in boosts if b.get("chainId") == TARGET_CHAIN]
    print(f"Rasta {len(boosts)} boost'ų iš viso, {len(robinhood_boosts)} Robinhood grandinėje.")

    new_boosts = []
    for boost in robinhood_boosts:
        key = f"{boost.get('chainId')}:{boost.get('tokenAddress')}:{boost.get('amount')}"
        if key in seen:
            continue
        seen.add(key)
        new_boosts.append(boost)

    state["seen_boosts"] = list(seen)[-500:]
    save_state(state)

    if is_first_run:
        print("Pirmas paleidimas - būsena užsirašyta, pranešimai nesiunčiami.")
        return

    if not new_boosts:
        print("Naujų Robinhood grandinės boost'ų nerasta.")
        return

    for boost in new_boosts:
        token_address = boost.get("tokenAddress", "")
        amount = boost.get("amount", "?")
        total_amount = boost.get("totalAmount", "?")

        info = fetch_token_info(TARGET_CHAIN, token_address)

        name = info.get("name") or "Nežinomas"
        symbol = info.get("symbol") or "?"
        price = info.get("price_usd")
        liquidity = info.get("liquidity_usd")
        mcap = info.get("market_cap")
        pair_url = info.get("pair_url") or boost.get("url") or f"https://dexscreener.com/{TARGET_CHAIN}/{token_address}"

        message = (
            f"🚀 <b>Naujas apmokėtas DEX Boost - Robinhood Chain</b>\n\n"
            f"<b>{name}</b> ({symbol})\n"
            f"Boost suma: {amount} / {total_amount}\n"
        )
        if price:
            message += f"Kaina: ${float(price):.8f}\n"
        if liquidity:
            message += f"Likvidumas: ${liquidity:,.0f}\n"
        if mcap:
            message += f"Market Cap: ${mcap:,.0f}\n"

        message += f"\nAdresas: <code>{token_address}</code>\n"
        message += f"{pair_url}\n\n"
        message += (
            "<i>⚠️ Tai TIK informacija, ne rekomendacija. Boost apmokėjimas "
            "nerodo projekto kokybės - dažnai naudojamas prieš 'rug pull'. "
            "Dauguma naujų memecoin projektų praranda didžiąją dalį vertės. "
            "Sprendimą priimk pats, savo rizika.</i>"
        )

        send_to_telegram(message)


if __name__ == "__main__":
    main()
