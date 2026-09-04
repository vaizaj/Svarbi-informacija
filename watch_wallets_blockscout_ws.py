"""
REALAUS LAIKO piniginių sekimo botas -> Telegram (Blockscout WebSocket)
--------------------------------------------------------------------------
NAUJA VERSIJA - naudoja Blockscout PAČIO NEMOKAMĄ WebSocket API (Phoenix
Channels protokolas), NE Alchemy - taip išvengiama Alchemy Compute Unit
limito problemos, kurią patyrėme anksčiau.

Prenumeruoja "addresses:{wallet}" temą KIEKVIENAI stebimai piniginei -
Blockscout PATS siunčia pilną transakcijos informaciją, kai tik stebima
piniginė atlieka BET KOKIĄ transakciją.

SVARBU: Phoenix Channels naudoja SAVO pranešimų formatą (NE standartinį
JSON-RPC): [join_ref, ref, topic, event, payload] sąrašo formatas.

SVARBU: Tai TIK INFORMACINIS botas. NIEKO neperka automatiškai.
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import websockets

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["DEX_BOOSTS_CHAT_ID"]

WS_URL = "wss://robinhoodchain.blockscout.com/socket/v2/websocket?vsn=2.0.0"
BLOCKSCOUT_BASE = "https://robinhoodchain.blockscout.com/api/v2"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BlockscoutWalletWatch/1.0)"}

STATE_FILE = "wallet_watch_bs_seen.json"

WATCHED_WALLETS = {
    "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB": {
        "label": "Serijinis kūrėjas #1 (200+ token'ų)",
        "stats": "Istoriškai: 50% atvejų pasiekia +30% piką, mediana +33%, 100% 'gyvi'.",
    },
    "0x000000e200088D55C39a11F609E5F667729ad49b": {
        "label": "Serijinis kūrėjas #2 (101 token'as, GERESNIS track record)",
        "stats": "Istoriškai: 65% atvejų pasiekia +30% piką, mediana +52%, 99% 'gyvi'.",
    },
    "0x5bd1Fbe78a78fe8236fa00CF48fbEBA74ae34661": {
        "label": "Serijinis kūrėjas #3 (38 token'ai)",
        "stats": "Istoriškai: 53% atvejų pasiekia +30% piką, mediana +39%, 100% 'gyvi'.",
    },
    "0x0c37a24F5D23A486FA692d1500881d698B1F77a4": {
        "label": "Serijinis kūrėjas #4 (7 token'ai, AUKŠČIAUSIAS +30% rate)",
        "stats": "Istoriškai: 86% atvejų pasiekia +30% piką, mediana +162%. Mažesnė imtis.",
    },
}
WATCHED_WALLETS_LOWER = {k.lower(): v for k, v in WATCHED_WALLETS.items()}

TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS_TOPIC = "0x" + "0" * 64

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("wallet_watch_bs")


def load_seen() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(seen), f)


def fetch_mint_token_address(tx_hash: str) -> str:
    try:
        url = f"{BLOCKSCOUT_BASE}/transactions/{tx_hash}/logs"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for log_entry in data.get("items", []):
            topics = log_entry.get("topics", [])
            if (
                len(topics) >= 2
                and topics[0]
                and topics[0].lower() == TRANSFER_EVENT_TOPIC
                and topics[1]
                and topics[1].lower() == ZERO_ADDRESS_TOPIC
            ):
                address_field = log_entry.get("address")
                if isinstance(address_field, dict):
                    return address_field.get("hash")
                return address_field
    except Exception as e:
        log.warning(f"Nepavyko patikrinti {tx_hash} logų: {e}")
    return None


def fetch_token_market_data(token_address: str) -> dict:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []
        for pair in pairs:
            if pair.get("chainId") == "robinhood":
                return {"market_cap": pair.get("marketCap") or pair.get("fdv")}
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


def process_transaction(wallet_address: str, tx_hash: str, seen: set):
    if not tx_hash or tx_hash in seen:
        return
    seen.add(tx_hash)

    wallet_info = WATCHED_WALLETS_LOWER.get(wallet_address.lower())
    if not wallet_info:
        return

    token_address = fetch_mint_token_address(tx_hash)
    if not token_address:
        return

    original_wallet = next(w for w in WATCHED_WALLETS if w.lower() == wallet_address.lower())
    detected_str = datetime.now(ZoneInfo("Europe/Vilnius")).strftime("%Y-%m-%d %H:%M:%S")

    market = fetch_token_market_data(token_address)
    mcap_line = (
        f"Market Cap: ${market['market_cap']:,.0f}\n"
        if market.get("market_cap")
        else "Market Cap: dar nėra prekybos poros / duomenų\n"
    )

    axiom_url = f"https://axiom.trade/meme/{token_address}"
    message = (
        f"🟣⚡ <b>ETAPAS 1: TOKEN'AS SUKURTAS (REALUS LAIKAS - Blockscout WS)</b>\n\n"
        f"Piniginė: <code>{original_wallet}</code>\n"
        f"({wallet_info['label']})\n\n"
        f"📊 <b>Istorinė statistika:</b>\n{wallet_info['stats']}\n\n"
        f"🕐 Aptikta: {detected_str} (LT laikas)\n"
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


async def heartbeat_loop(ws):
    ref = 1000
    while True:
        await asyncio.sleep(30)
        try:
            msg = [None, str(ref), "phoenix", "heartbeat", {}]
            await ws.send(json.dumps(msg))
            ref += 1
        except Exception:
            return


async def listen_forever():
    seen = load_seen()
    reconnect_delay = 5

    while True:
        try:
            log.info("Jungiuosi prie Blockscout WebSocket (Phoenix Channels)...")
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                join_ref = 1
                for wallet_address in WATCHED_WALLETS:
                    topic = f"addresses:{wallet_address}"
                    join_msg = [str(join_ref), str(join_ref), topic, "phx_join", {}]
                    await ws.send(json.dumps(join_msg))
                    join_ref += 1

                log.info(f"Prisijungimo užklausos išsiųstos {len(WATCHED_WALLETS)} pinigėms.")
                heartbeat_task = asyncio.create_task(heartbeat_loop(ws))
                reconnect_delay = 5

                async for raw_message in ws:
                    try:
                        msg = json.loads(raw_message)
                    except json.JSONDecodeError:
                        continue

                    if not isinstance(msg, list) or len(msg) != 5:
                        continue
                    _, _, topic, event, payload = msg

                    if event == "phx_reply":
                        status = payload.get("status") if isinstance(payload, dict) else None
                        log.info(f"Atsakymas '{topic}': {status}")
                        continue

                    if not topic or not topic.startswith("addresses:"):
                        continue

                    wallet_address = topic.split(":", 1)[1]

                    tx_hash = None
                    if isinstance(payload, dict):
                        tx_hash = (
                            payload.get("hash")
                            or (payload.get("transaction") or {}).get("hash")
                        )

                    if not tx_hash:
                        log.info(f"Gautas '{event}' įvykis iš '{topic}' be aiškaus tx hash: {json.dumps(payload)[:300]}")
                        continue

                    log.info(f"Nauja transakcija iš stebimos piniginės: {tx_hash}")
                    try:
                        process_transaction(wallet_address, tx_hash, seen)
                        save_seen(seen)
                    except Exception as e:
                        log.error(f"Klaida apdorojant transakciją: {e}")

                heartbeat_task.cancel()

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
