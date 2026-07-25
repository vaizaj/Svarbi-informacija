"""
BTC Whale + Strategy (Saylor) pirkimų sekimo botas -> Telegram
------------------------------------------------------------------
1) Seka dideles Bitcoin transakcijas (>~$10mln vertės) per viešą
   blockchain.info API (nemokama, be rakto)
2) Seka Strategy (buvusi MicroStrategy, tiktoris MSTR) naujus SEC 8-K
   dokumentus, kuriuose minimas bitcoin pirkimas, per SEC EDGAR
   Full-Text Search API (nemokama, be rakto)

SVARBU: Blockchain duomenys rodo tik adresus ir sumas, NE tapatybes -
"whale" įspėjimas reiškia "didelis sandoris tinkle", o ne "tai konkretaus
žinomo asmens pinigai", nebent adresas yra viešai/oficialiai pažymėtas.
"""

import os
import json
import time
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = "whale_state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; WhaleBot/1.0)"}

# Minimali sandorio vertė USD, kad būtų laikoma "whale" transakcija
MIN_USD_VALUE = 10_000_000


def get_btc_price_usd() -> float:
    """Gauna dabartinę BTC kainą iš Kraken (nemokama, be rakto, neblokuoja JAV serverių)."""
    url = "https://api.kraken.com/0/public/Ticker?pair=XBTUSD"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    result = data["result"]
    pair_key = list(result.keys())[0]
    return float(result[pair_key]["c"][0])  # paskutinė kaina


def check_btc_whales(state: dict) -> list:
    """Tikrina neseniai patvirtintus didelius BTC blokus dideliems sandoriams."""
    messages = []
    btc_price = get_btc_price_usd()
    min_btc = MIN_USD_VALUE / btc_price

    # Naudojam /latestblock - stabilesnis endpoint'as nei /blocks/
    url = "https://blockchain.info/latestblock"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        latest = resp.json()
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti naujausio bloko: {e}")
        return messages

    if not latest:
        return messages

    latest_block_height = latest["height"]
    latest_block_hash = latest["hash"]
    last_seen_block = state.get("last_seen_block", 0)

    if latest_block_height <= last_seen_block:
        print("Nėra naujų blokų nuo paskutinio patikrinimo.")
        return messages

    # Tikrinam tik naujausią bloką, kad neapkrautume API per daug užklausų
    try:
        block_url = f"https://blockchain.info/rawblock/{latest_block_hash}"
        resp = requests.get(block_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        block_data = resp.json()
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti bloko duomenų: {e}")
        state["last_seen_block"] = latest_block_height
        return messages

    for tx in block_data.get("tx", []):
        total_out_satoshi = sum(out.get("value", 0) for out in tx.get("out", []))
        total_out_btc = total_out_satoshi / 100_000_000
        if total_out_btc >= min_btc:
            usd_value = total_out_btc * btc_price
            tx_hash = tx.get("hash", "")
            messages.append(
                f"🐋 <b>BTC Whale sandoris</b>\n"
                f"Suma: {total_out_btc:,.1f} BTC (~${usd_value:,.0f})\n"
                f"https://www.blockchain.com/explorer/transactions/btc/{tx_hash}"
            )

    state["last_seen_block"] = latest_block_height
    return messages


STRATEGY_CIK = "0001050446"  # MicroStrategy Incorporated d/b/a "Strategy" (MSTR)


def filing_mentions_bitcoin(filing_url: str) -> bool:
    """Atsidaro patį 8-K dokumentą ir patikrina, ar jame minimas bitcoin/BTC pirkimas."""
    try:
        sec_headers = {"User-Agent": "VaidasCryptoBot contact@example.com"}
        resp = requests.get(filing_url, headers=sec_headers, timeout=20)
        resp.raise_for_status()
        text_lower = resp.text.lower()
        return "bitcoin" in text_lower or "btc update" in text_lower
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko patikrinti dokumento turinio: {e}")
        # jei nepavyksta patikrinti - saugiau vis tiek parodyti, nei praleisti
        return True


def check_strategy_filings(state: dict, reset_only: bool = False) -> list:
    """
    Tikrina naujus Strategy (MSTR, CIK 1050446) 8-K dokumentus per SEC
    submissions API. Siunčia TIK tuos, kurie mini bitcoin pirkimą.

    Turi SAVO NEPRIKLAUSOMĄ "pirmo paleidimo" logiką (raktas "seen_filings"
    buvimas state'e), kad pridėjus šią funkciją VĖLIAU (po to, kai kita dalis
    - pvz. BTC whale - jau turėjo savo būseną), ji nesiųstų visos istorijos
    iš karto kaip "naujų" pranešimų.

    reset_only=True: tik užpildo seen_filings VISAIS dabartiniais dokumentais,
    NIEKO nesiunčia - naudojama vieną kartą, kad "nustatytume nulinę liniją"
    nuo šiandien, nesvarbu kokia buvo ankstesnė (galimai sugadinta) būsena.
    """
    messages = []
    url = f"https://data.sec.gov/submissions/CIK{STRATEGY_CIK}.json"
    sec_headers = {"User-Agent": "VaidasCryptoBot contact@example.com"}

    is_new_feature = "seen_filings" not in state

    try:
        resp = requests.get(url, headers=sec_headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[KLAIDA] Nepavyko patikrinti SEC EDGAR: {e}")
        return messages

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    seen_filings = set(state.get("seen_filings", []))

    for i, form in enumerate(forms):
        if form != "8-K":
            continue

        accession = accessions[i]
        if accession in seen_filings:
            continue

        seen_filings.add(accession)

        # Nesiunčiam, jei tai: (a) visai pirmas šios funkcijos paleidimas,
        # arba (b) tai priverstinis "reset" paleidimas
        if is_new_feature or reset_only:
            continue

        filing_date = dates[i]
        doc = primary_docs[i]
        accession_nodash = accession.replace("-", "")
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(STRATEGY_CIK)}/{accession_nodash}/{doc}"
        description = descriptions[i] if i < len(descriptions) else "8-K dokumentas"

        # Tikrinam turinį - siunčiam TIK jei minimas bitcoin pirkimas
        if not filing_mentions_bitcoin(filing_url):
            print(f"[INFO] 8-K {accession} nemini bitcoin - praleidžiam.")
            continue

        messages.append(
            f"📋 <b>Strategy (MSTR) naujas SEC 8-K</b>\n"
            f"Data: {filing_date}\n"
            f"{description}\n"
            f"{filing_url}"
        )

    state["seen_filings"] = list(seen_filings)[-200:]
    return messages


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_to_telegram(text: str, retries: int = 3):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(retries):
        resp = requests.post(url, data=payload, timeout=15)
        if resp.ok:
            print("[OK] Žinutė išsiųsta.")
            return True
        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            print(f"[ĮSPĖJIMAS] Telegram rate limit - laukiu {retry_after}s ir bandau vėl...")
            time.sleep(retry_after + 1)
            continue
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
        return False
    print("[KLAIDA] Nepavyko išsiųsti po kelių bandymų (rate limit).")
    return False


RESET_STRATEGY = os.environ.get("RESET_STRATEGY", "false").lower() == "true"


def main():
    state = load_state()
    is_first_run = not state

    all_messages = []
    all_messages += check_btc_whales(state)
    all_messages += check_strategy_filings(state, reset_only=RESET_STRATEGY)

    save_state(state)

    if RESET_STRATEGY:
        print("Strategy istorija nustatyta iš naujo - nuo dabar bus siunčiami tik NAUJI dokumentai.")
        return

    if is_first_run:
        print("Pirmas paleidimas - praleidžiam siuntimą, tik užsirašom būseną.")
        return

    if not all_messages:
        print("Naujų whale sandorių ar Strategy dokumentų nerasta.")
        return

    for msg in all_messages:
        send_to_telegram(msg)
        time.sleep(2)  # pauzė tarp žinučių, kad išvengtume rate limit


if __name__ == "__main__":
    main()
