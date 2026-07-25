"""
DEX Screener Boost sekimo botas (WebSocket, realus laikas) -> Telegram
--------------------------------------------------------------------------
NAUJA VERSIJA, naudojanti DexScreener WebSocket API vietoj periodinio
(polling) tikrinimo. Tai leidžia gauti pranešimus BEVEIK AKIMIRKSNIU,
kai tik DexScreener savo pusėje užregistruoja naują apmokėtą boost'ą -
be jokio "laukimo iki kito tikrinimo".

SVARBU: Šis scriptas turi veikti KAIP NUOLATINIS PROCESAS (ne per cron!),
nes WebSocket reikalauja palaikyti atvirą ryšį. Naudoti per systemd
service, kad VPS automatiškai jį paleistų/perkrautų.

Ta pati sąžininga pastaba kaip anksčiau:
- Tai TIK INFORMACINIS botas. Jis NIEKO neperka automatiškai.
- Boost apmokėjimas NĖRA projekto kokybės garantija - dažnai naudojamas
  prieš "rug pull". Sprendimą visada priimi pats, savo rizika.
"""

import os
import json
import time
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import websockets

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["DEX_BOOSTS_CHAT_ID"]

WS_URL = "wss://api.dexscreener.com/token-boosts/latest/v1"
TARGET_CHAIN = "robinhood"
TOKEN_INFO_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BoostWatchBot-WS/1.0)"}

STATE_FILE = "dex_boosts_ws_seen.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dex_boosts_ws")


# ---------------------------------------------------------------------------
# BŪSENOS VALDYMAS (kad neprarastume "matytų" boost'ų perkraunant procesą)
# ---------------------------------------------------------------------------

def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)


# ---------------------------------------------------------------------------
# DEXSCREENER PAPILDOMA INFORMACIJA (kaina, likvidumas, market cap, nuotrauka)
# ---------------------------------------------------------------------------

def fetch_token_info(chain_id: str, address: str) -> dict:
    try:
        url = TOKEN_INFO_URL.format(address=address)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        for pair in pairs:
            if pair.get("chainId") == chain_id:
                return {
                    "price_usd": pair.get("priceUsd"),
                    "liquidity_usd": pair.get("liquidity", {}).get("usd"),
                    "market_cap": pair.get("marketCap") or pair.get("fdv"),
                    "pair_url": pair.get("url"),
                    "pair_address": pair.get("pairAddress"),
                    "symbol": pair.get("baseToken", {}).get("symbol"),
                    "name": pair.get("baseToken", {}).get("name"),
                    "image_url": pair.get("info", {}).get("imageUrl"),
                }
    except Exception as e:
        log.warning(f"Nepavyko gauti token info: {e}")
    return {}


# ---------------------------------------------------------------------------
# TELEGRAM SIUNTIMAS
# ---------------------------------------------------------------------------

def send_to_telegram(text: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    for attempt in range(retries):
        resp = requests.post(url, data=payload, timeout=15)
        if resp.ok:
            return True
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            log.warning(f"Telegram rate limit - laukiu {retry_after}s")
            time.sleep(retry_after + 1)
            continue
        log.error(f"Nepavyko išsiųsti į Telegram: {resp.text}")
        return False
    return False


def send_photo_with_caption(image_url: str, caption: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    resp = requests.post(url, data=payload, timeout=20)
    if not resp.ok:
        log.warning(f"Nepavyko išsiųsti nuotraukos: {resp.text}")
        return False
    return True


# ---------------------------------------------------------------------------
# NAUJO BOOST'O APDOROJIMAS
# ---------------------------------------------------------------------------

def process_boost(boost: dict, seen: set):
    chain_id = boost.get("chainId")
    token_address = boost.get("tokenAddress", "")
    key = f"{chain_id}:{token_address}:{boost.get('amount')}"

    if key in seen:
        return
    seen.add(key)

    if chain_id != TARGET_CHAIN:
        return  # sekam TIK Robinhood grandinę

    received_at = datetime.now(ZoneInfo("Europe/Vilnius"))
    log.info(f"NAUJAS Robinhood boost aptiktas: {token_address} ({received_at})")

    info = fetch_token_info(chain_id, token_address)
    name = info.get("name") or "Nežinomas"
    symbol = info.get("symbol") or "?"
    price = info.get("price_usd")
    liquidity = info.get("liquidity_usd")
    mcap = info.get("market_cap")
    pair_address = info.get("pair_address") or token_address
    pair_url = info.get("pair_url") or boost.get("url") or f"https://dexscreener.com/{chain_id}/{token_address}"
    axiom_url = f"https://axiom.trade/meme/{pair_address}"
    gmgn_url = f"https://gmgn.ai/{chain_id}/token/{token_address}"
    blockscout_url = f"https://robinhoodchain.blockscout.com/address/{token_address}"

    amount = boost.get("amount", "?")
    total_amount = boost.get("totalAmount", "?")
    detected_at_str = received_at.strftime("%Y-%m-%d %H:%M:%S")

    image_url = info.get("image_url") or boost.get("icon")
    if image_url:
        short_caption = f"⚡ <b>{name}</b> ({symbol}) - Robinhood"
        if mcap:
            short_caption += f" | MC: ${mcap:,.0f}"
        send_photo_with_caption(image_url, short_caption)

    message = (
        f"⚡ <b>[REALUS LAIKAS] Naujas apmokėtas DEX Boost - Robinhood</b>\n\n"
        f"<b>{name}</b> ({symbol})\n"
        f"Boost suma: {amount} / {total_amount}\n"
        f"Aptikta: {detected_at_str} (LT laikas, WebSocket)\n"
    )
    if price:
        message += f"Kaina: ${float(price):.8f}\n"
    if liquidity:
        message += f"Likvidumas: ${liquidity:,.0f}\n"
    if mcap:
        message += f"Market Cap: ${mcap:,.0f}\n"

    message += f"\nAdresas: <code>{token_address}</code>\n\n"
    message += (
        f"👉 <a href=\"{pair_url}\">Dexscreener</a> | "
        f"<a href=\"{gmgn_url}\">GMGN</a> | "
        f"<a href=\"{axiom_url}\">Axiom</a> | "
        f"<a href=\"{blockscout_url}\">Blockscout</a>\n\n"
    )
    message += (
        "<i>⚠️ Tai TIK informacija, ne rekomendacija. Boost apmokėjimas "
        "nerodo projekto kokybės - dažnai naudojamas prieš 'rug pull'. "
        "Sprendimą priimk pats, savo rizika.</i>"
    )

    send_to_telegram(message)
    log.info(f"Pranešimas išsiųstas: {name} ({symbol})")


# ---------------------------------------------------------------------------
# WEBSOCKET PAGRINDINĖ LOGIKA (su automatiniu reconnect)
# ---------------------------------------------------------------------------

async def listen_forever():
    seen = load_seen()
    is_first_message = not seen
    reconnect_delay = 5

    while True:
        try:
            log.info(f"Jungiuosi prie {WS_URL} ...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                log.info("Prisijungta! Laukiu boost'ų srauto...")
                reconnect_delay = 5  # sėkmingai prisijungus, atstatom delsimo laikroditi

                async for raw_message in ws:
                    try:
                        data = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    boosts = data.get("data", data) if isinstance(data, dict) else data
                    if not isinstance(boosts, list):
                        continue

                    for boost in boosts:
                        if is_first_message:
                            # pirmas gautas pranešimas - tik užsirašom, nesiunčiam,
                            # kad neužtvindytų senais duomenimis paleidimo metu
                            key = f"{boost.get('chainId')}:{boost.get('tokenAddress')}:{boost.get('amount')}"
                            seen.add(key)
                        else:
                            process_boost(boost, seen)

                    if is_first_message:
                        is_first_message = False
                        log.info(f"Pirmas pranešimas apdorotas (užsirašyta {len(seen)} esamų boost'ų). Nuo dabar - realus sekimas.")

                    save_seen(seen)

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            log.warning(f"WebSocket ryšys nutrūko ({e}). Bandau iš naujo po {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)  # eksponentinis atgalinis laikas, max 60s
        except Exception as e:
            log.error(f"Netikėta klaida: {e}")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


if __name__ == "__main__":
    asyncio.run(listen_forever())
