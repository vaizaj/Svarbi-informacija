"""
REALAUS LAIKO piniginių sekimo botas -> Telegram (Alchemy WebSocket)
--------------------------------------------------------------------------
Klausosi Robinhood Chain naujo token'o "mint" įvykių PER "logs" prenumeratą
ir praneša AKIMIRKSNIU, kai stebima piniginė SUKURIA naują token'o kontraktą.

SVARBI ARCHITEKTŪROS PASTABA (v2, po rate-limit klaidos):
Ankstesnė versija prenumeravo "newHeads" (KIEKVIENĄ naują bloką) ir darė
atskirą API kvietimą KIEKVIENAM blokui, kad patikrintų jo transakcijas.
Kadangi Robinhood Chain turi ~100ms blokus (~600 blokų/min.), tai GREITAI
viršydavo Alchemy nemokamo plano limitą (HTTP 429 klaida, prenumerata
apskritai nepavykdavo).

ŠI versija vietoj to prenumeruoja TIESIOGIAI "logs" su topics filtru,
atitinkančiu ERC20 "mint" (Transfer nuo nulinio adreso) įvykius VISAME
tinkle - Alchemy FILTRUOJA SERVERIO PUSĖJE, tad mes gauname PRANEŠIMUS
TIK kai TIKRAI įvyksta naujo token'o išleidimas (retas įvykis - kelios
dešimtys per dieną visame tinkle), NE kiekvienam blokui. Tai sumažina
API kvietimų kiekį ~100-500 kartų.

SVARBU: Tai TIK INFORMACINIS botas. NIEKO neperka automatiškai.
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import websockets

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["DEX_BOOSTS_CHAT_ID"]
ALCHEMY_API_KEY = os.environ["ALCHEMY_API_KEY"]

WS_URL = f"wss://robinhood-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
HTTP_URL = f"https://robinhood-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"

STATE_FILE = "wallet_watch_realtime_seen.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RealtimeWalletWatch/1.0)", "Content-Type": "application/json"}

WATCHED_WALLETS = {
    "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB": {
        "label": "Serijinis kūrėjas #1 (200+ token'ų)",
        "stats": "Istoriškai: 50% atvejų pasiekia +30% piką, mediana +33%, 100% 'gyvi' po analizei.",
    },
    "0x000000e200088D55C39a11F609E5F667729ad49b": {
        "label": "Serijinis kūrėjas #2 (101 token'as, GERESNIS track record)",
        "stats": "Istoriškai: 65% atvejų pasiekia +30% piką, mediana +52%, 99% 'gyvi'. Didelė, patikima imtis.",
    },
    "0x5bd1Fbe78a78fe8236fa00CF48fbEBA74ae34661": {
        "label": "Serijinis kūrėjas #3 (38 token'ai)",
        "stats": "Istoriškai: 53% atvejų pasiekia +30% piką, mediana +39%, 100% 'gyvi'.",
    },
    "0x0c37a24F5D23A486FA692d1500881d698B1F77a4": {
        "label": "Serijinis kūrėjas #4 (7 token'ai, AUKŠČIAUSIAS +30% rate)",
        "stats": "Istoriškai: 86% atvejų pasiekia +30% piką (aukščiausias iš visų!), mediana +162%. Mažesnė imtis (N=7), tad didesnis netikrumas nei kitų kandidatų.",
    },
}
WATCHED_WALLETS_LOWER = {k.lower(): v for k, v in WATCHED_WALLETS.items()}

TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS_TOPIC = "0x" + "0" * 64

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("wallet_watch_rt")


def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)


def rpc_call(method: str, params: list) -> dict:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = requests.post(HTTP_URL, headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("result")


def fetch_token_market_data(token_address: str) -> dict:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        for pair in pairs:
            if pair.get("chainId") == "robinhood":
                return {
                    "market_cap": pair.get("marketCap") or pair.get("fdv"),
                    "price_usd": pair.get("priceUsd"),
                    "liquidity_usd": pair.get("liquidity", {}).get("usd"),
                }
    except Exception:
        pass
    return {}


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        log.error(f"Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        log.info("Pranešimas išsiųstas.")


def handle_mint_log(mint_log: dict, seen: set):
    token_address = mint_log.get("address")
    tx_hash = mint_log.get("transactionHash")
    if not token_address or not tx_hash:
        return

    if tx_hash in seen:
        return
    seen.add(tx_hash)

    tx = rpc_call("eth_getTransactionByHash", [tx_hash])
    if not tx:
        return

    from_addr = (tx.get("from") or "").lower()
    wallet_info = WATCHED_WALLETS_LOWER.get(from_addr)
    if not wallet_info:
        return

    original_wallet = next(w for w in WATCHED_WALLETS if w.lower() == from_addr)

    block_hash = mint_log.get("blockHash")
    block_time_lt = None
    if block_hash:
        block = rpc_call("eth_getBlockByHash", [block_hash, False])
        if block and block.get("timestamp"):
            block_time_utc = datetime.fromtimestamp(int(block["timestamp"], 16), tz=timezone.utc)
            block_time_lt = block_time_utc.astimezone(ZoneInfo("Europe/Vilnius"))

    detected_str = (
        block_time_lt.strftime("%Y-%m-%d %H:%M:%S")
        if block_time_lt
        else datetime.now(ZoneInfo("Europe/Vilnius")).strftime("%Y-%m-%d %H:%M:%S")
    )

    market = fetch_token_market_data(token_address)
    mcap_line = (
        f"Market Cap: ${market['market_cap']:,.0f}\n"
        if market.get("market_cap")
        else "Market Cap: dar nėra prekybos poros / duomenų\n"
    )

    axiom_url = f"https://axiom.trade/meme/{token_address}"

    message = (
        f"🟣⚡ <b>ETAPAS 1: TOKEN'AS SUKURTAS (REALUS LAIKAS)</b>\n\n"
        f"Piniginė: <code>{original_wallet}</code>\n"
        f"({wallet_info['label']})\n\n"
        f"📊 <b>Istorinė statistika:</b>\n{wallet_info['stats']}\n\n"
        f"🕐 Sukurta: {detected_str} (LT laikas)\n"
        f"{mcap_line}\n"
        f"Naujas kontraktas: <code>{token_address}</code>\n\n"
        f"👉 <a href=\"https://dexscreener.com/robinhood/{token_address}\">Dexscreener</a> | "
        f"<a href=\"https://gmgn.ai/robinhood/token/{token_address}\">GMGN</a> | "
        f"<a href=\"{axiom_url}\">Axiom</a> | "
        f"<a href=\"https://robinhoodchain.blockscout.com/address/{token_address}\">Blockscout</a>\n\n"
        f"<i>⚠️ Tai TIK informacija. Praeities statistika NEGARANTUOJA "
        f"ateities rezultato. Sprendimą priimk pats, savo rizika.</i>"
    )
    send_to_telegram(message)


async def listen_forever():
    seen = load_seen()
    reconnect_delay = 5

    while True:
        try:
            log.info("Jungiuosi prie Robinhood Chain WebSocket (logs prenumerata)...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                sub_request = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_subscribe",
                    "params": ["logs", {"topics": [TRANSFER_EVENT_TOPIC, ZERO_ADDRESS_TOPIC]}],
                }
                await ws.send(json.dumps(sub_request))
                confirmation = await ws.recv()
                log.info(f"Prenumerata patvirtinta: {confirmation}")
                log.info("Klausau naujų token'ų 'mint' įvykių visame tinkle...")
                reconnect_delay = 5

                async for raw_message in ws:
                    try:
                        msg = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    params = msg.get("params", {})
                    mint_log = params.get("result")
                    if not mint_log:
                        continue

                    try:
                        handle_mint_log(mint_log, seen)
                        save_seen(seen)
                    except Exception as e:
                        log.error(f"Klaida apdorojant mint įvykį: {e}")

        except (websockets.exceptions.ConnectionClosed, OSError) as e:
            log.warning(f"WebSocket ryšys nutrūko ({e}). Bandau iš naujo po {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)
        except Exception as e:
            log.error(f"Netikėta klaida: {e}")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 60)


if __name__ == "__main__":
    asyncio.run(listen_forever())
