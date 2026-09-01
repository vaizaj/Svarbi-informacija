"""
REALAUS LAIKO piniginių sekimo botas -> Telegram (Alchemy WebSocket)
--------------------------------------------------------------------------
Klausosi Robinhood Chain NAUJŲ BLOKŲ per Alchemy WebSocket (~100ms greitis)
ir praneša AKIMIRKSNIU, kai stebima piniginė SUKURIA naują token'o kontraktą.

Tai GREIČIAUSIAS įmanomas signalas - tiesioginis blockchain sekimas, ne
periodinis API tikrinimas (skirtingai nuo watch_wallet_deployments.py,
kuris tikrina per Blockscout kas 1 min.).

Veikimo principas:
1) Prisijungia prie Alchemy WebSocket, užsiprenumeruoja "newHeads" (naujus blokus)
2) Kiekvienam naujam blokui - gauna PILNĄ bloko turinį (visas transakcijas)
3) Tikrina, ar kuri nors transakcija SIŲSTA iš stebimos piniginės IR yra
   kontrakto sukūrimas (to=null)
4) Jei taip - gauna transakcijos "receipt", kad sužinotų SUKURTO kontrakto
   adresą, ir IŠKART siunčia pranešimą su Lietuvos laiku ir (jei jau yra)
   market cap

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

# Stebimos piniginės su ISTORINE statistika (iš atgalinio testavimo).
# Pridėk naujas pinigines čia, kai ištirsi jų track record per
# analyze_single_deployer.py.
WATCHED_WALLETS = {
    "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB": {
        "label": "Serijinis kūrėjas #1 (178+ token'ų)",
        "stats": "Istoriškai: 50% atvejų pasiekia +30% piką, mediana 1.5h iki piko, 24% iškart krenta.",
    },
}
WATCHED_WALLETS_LOWER = {k.lower(): v for k, v in WATCHED_WALLETS.items()}

# ERC20 "Transfer(address,address,uint256)" įvykio parašas (keccak256 hash'as)
# ir nulinis adresas (32 baitų, "topic" formatas) - kartu sudaro "mint"
# (naujo token'o išleidimo) požymį, kuris veikia NEPRIKLAUSOMAI nuo to,
# ar token'as sukurtas tiesiogiai, ar per launchpad/fabrikos kontraktą.
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
    """Vienas HTTP JSON-RPC kvietimas į Alchemy."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = requests.post(HTTP_URL, headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("result")


def fetch_token_market_data(token_address: str) -> dict:
    """Bando gauti market cap - GALI dar neegzistuoti, jei nėra prekybos poros."""
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


def handle_block(block_hash: str, seen: set):
    """Gauna pilną bloką ir tikrina visas transakcijas dėl stebimų piniginių."""
    block = rpc_call("eth_getBlockByHash", [block_hash, True])
    if not block:
        return

    block_timestamp_hex = block.get("timestamp", "0x0")
    block_time_utc = datetime.fromtimestamp(int(block_timestamp_hex, 16), tz=timezone.utc)
    block_time_lt = block_time_utc.astimezone(ZoneInfo("Europe/Vilnius"))

    for tx in block.get("transactions", []):
        tx_hash = tx.get("hash")
        from_addr = (tx.get("from") or "").lower()

        wallet_info = WATCHED_WALLETS_LOWER.get(from_addr)
        if not wallet_info:
            continue

        if tx_hash in seen:
            continue
        seen.add(tx_hash)

        # SVARBU: netikrinam vien "to == null" (tiesioginis kontrakto
        # sukūrimas), nes DAUGUMA memecoin paleidimų vyksta PER launchpad/
        # fabrikos kontraktą - piniginės transakcija eina Į FABRIKĄ, o
        # naujas token'as sukuriamas VIDINIU būdu. Vietoj to ieškom
        # UNIVERSALAUS požymio - ERC20 "Transfer" įvykio NUO NULINIO adreso
        # (tai standartinis "mint" - naujo token'o išleidimo - įvykis,
        # veikiantis NEPRIKLAUSOMAI nuo to, KAIP token'as buvo sukurtas).
        receipt = rpc_call("eth_getTransactionReceipt", [tx_hash])
        if not receipt:
            continue

        token_address = None
        for log_entry in receipt.get("logs", []):
            topics = log_entry.get("topics", [])
            if (
                len(topics) >= 2
                and topics[0].lower() == TRANSFER_EVENT_TOPIC
                and topics[1].lower() == ZERO_ADDRESS_TOPIC
            ):
                token_address = log_entry.get("address")
                break

        if not token_address:
            continue

        original_wallet = next(
            w for w in WATCHED_WALLETS if w.lower() == from_addr
        )

        market = fetch_token_market_data(token_address)
        mcap_line = (
            f"Market Cap: ${market['market_cap']:,.0f}\n"
            if market.get("market_cap")
            else "Market Cap: dar nėra prekybos poros / duomenų\n"
        )

        detected_str = block_time_lt.strftime("%Y-%m-%d %H:%M:%S")
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
            log.info(f"Jungiuosi prie Robinhood Chain WebSocket...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                sub_request = {"jsonrpc": "2.0", "id": 1, "method": "eth_subscribe", "params": ["newHeads"]}
                await ws.send(json.dumps(sub_request))
                confirmation = await ws.recv()
                log.info(f"Prenumerata patvirtinta: {confirmation}")
                log.info("Klausau naujų blokų...")
                reconnect_delay = 5

                async for raw_message in ws:
                    try:
                        msg = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    params = msg.get("params", {})
                    block_header = params.get("result", {})
                    block_hash = block_header.get("hash")
                    if not block_hash:
                        continue

                    try:
                        handle_block(block_hash, seen)
                        save_seen(seen)
                    except Exception as e:
                        log.error(f"Klaida apdorojant bloką: {e}")

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
