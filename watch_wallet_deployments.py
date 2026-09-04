"""
Stebimų piniginių NAUJO TOKEN'O SUKŪRIMO sekimo botas -> Telegram
------------------------------------------------------------------------
Seka KONKREČIŲ, jau ištirtų piniginių transakcijas per Blockscout API ir
praneša AKIMIRKSNIU, kai tik piniginė SUKURIA (deploy'ina) naują token'o
kontraktą - DAR PRIEŠ jam gaunant DexScreener boost'ą.

Tai GREIČIAUSIAS įmanomas signalas apie šios piniginės naują projektą,
nes kontrakto sukūrimas įvyksta ANKSČIAU nei boost apmokėjimas.

SVARBU: Tai TIK INFORMACINIS botas. Jis NIEKO neperka automatiškai.
Kartu su pranešimu rodoma ISTORINĖ šios piniginės statistika (iš
atgalinio testavimo), kad sprendimas būtų informuotas, bet sprendimą
VISADA priima pats vartotojas.
"""

import os
import json
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["DEX_BOOSTS_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WalletWatchBot/1.0)"}
BLOCKSCOUT_BASE = "https://robinhoodchain.blockscout.com/api/v2"

# ERC20 "Transfer(address,address,uint256)" įvykio parašas ir nulinis
# adresas - kartu sudaro "mint" (naujo token'o išleidimo) požymį, kuris
# veikia NEPRIKLAUSOMAI nuo to, ar token'as sukurtas tiesiogiai, ar per
# launchpad/fabrikos kontraktą (dauguma atvejų - per fabriką).
TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS_TOPIC = "0x" + "0" * 64

STATE_FILE = "wallet_watch_seen.json"

# Stebimos piniginės su ISTORINE statistika (iš atgalinio testavimo,
# analyze_single_deployer.py rezultatų). Prideėk naujas pinigines čia,
# kai ištirsi jų track record.
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


def load_seen() -> dict:
    """wallet_address -> set(seen_tx_hashes)"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            raw = json.load(f)
            return {k: set(v) for k, v in raw.items()}
    return {}


def save_seen(seen: dict):
    with open(STATE_FILE, "w") as f:
        json.dump({k: list(v) for k, v in seen.items()}, f)


def fetch_wallet_transactions(wallet_address: str) -> list:
    try:
        url = f"{BLOCKSCOUT_BASE}/addresses/{wallet_address}/transactions"
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti {wallet_address} transakcijų: {e}")
        return []


def fetch_mint_token_address(tx_hash: str) -> str:
    """
    Patikrina transakcijos LOGUS dėl ERC20 'mint' (Transfer nuo nulinio
    adreso) įvykio - tai UNIVERSALUS naujo token'o sukūrimo požymis,
    veikiantis NEPRIKLAUSOMAI nuo to, ar tai buvo tiesioginis kontrakto
    sukūrimas, ar per launchpad/fabrikos kontraktą (kaip DAUGUMA atvejų).
    Grąžina naujo token'o adresą arba None.
    """
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
        print(f"[ĮSPĖJIMAS] Nepavyko patikrinti {tx_hash} logų: {e}")
    return None


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
        print("[OK] Naujo token'o sukūrimo pranešimas išsiųstas.")


def main():
    seen = load_seen()

    for wallet_address, wallet_info in WATCHED_WALLETS.items():
        is_first_check = wallet_address not in seen
        wallet_seen = seen.setdefault(wallet_address, set())

        transactions = fetch_wallet_transactions(wallet_address)

        for tx in transactions:
            tx_hash = tx.get("hash")
            if not tx_hash or tx_hash in wallet_seen:
                continue
            wallet_seen.add(tx_hash)

            if is_first_check:
                continue  # pirmą kartą tik užsirašom, nesiunčiam senos istorijos

            token_address = fetch_mint_token_address(tx_hash)
            if not token_address:
                continue  # tai ne naujo token'o sukūrimo (mint) transakcija

            axiom_url = f"https://axiom.trade/meme/{token_address}"
            message = (
                f"🟣 <b>ETAPAS 1: TOKEN'AS SUKURTAS</b> (dar NE boost'as)\n\n"
                f"Piniginė: <code>{wallet_address}</code>\n"
                f"({wallet_info['label']})\n\n"
                f"📊 <b>Istorinė statistika:</b>\n{wallet_info['stats']}\n\n"
                f"Naujas kontraktas: <code>{token_address}</code>\n\n"
                f"👉 <a href=\"https://dexscreener.com/robinhood/{token_address}\">Dexscreener</a> | "
                f"<a href=\"https://gmgn.ai/robinhood/token/{token_address}\">GMGN</a> | "
                f"<a href=\"{axiom_url}\">Axiom</a> | "
                f"<a href=\"https://robinhoodchain.blockscout.com/address/{token_address}\">Blockscout</a>\n\n"
                f"<i>⚠️ Tai TIK informacija apie NAUJĄ kontraktą - dar GALI neturėti "
                f"likvidumo/boost'o (Axiom/GMGN nuorodos gali dar neveikti, kol "
                f"nesukurta prekybos pora). Praeities statistika NEGARANTUOJA "
                f"ateities rezultato. Sprendimą priimk pats, savo rizika.</i>"
            )
            send_to_telegram(message)

        if is_first_check:
            print(f"Pirmas patikrinimas {wallet_address} - {len(wallet_seen)} senų tx užsirašyta.")

    save_seen(seen)
    print("Patikrinimas baigtas.")


if __name__ == "__main__":
    main()
