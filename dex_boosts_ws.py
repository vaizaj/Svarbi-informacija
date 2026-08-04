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

STATE_FILE = "wallet_watch_seen.json"

# Stebimos piniginės su ISTORINE statistika (iš atgalinio testavimo,
# analyze_single_deployer.py rezultatų). Prideėk naujas pinigines čia,
# kai ištirsi jų track record.
WATCHED_WALLETS = {
    "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB": {
        "label": "Serijinis kūrėjas #1 (178+ token'ų)",
        "stats": "Istoriškai: 50% atvejų pasiekia +30% piką, mediana 1.5h iki piko, 24% iškart krenta.",
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

            created_contract = tx.get("created_contract_address_hash") or (
                tx.get("created_contract") or {}
            ).get("hash")

            if not created_contract:
                continue  # tai ne kontrakto sukūrimo transakcija

            token_address = created_contract
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
