"""
DeFi/Staking APY palyginimo botas -> Telegram
------------------------------------------------
Kas savaitę (arba pagal poreikį) palygina GERIAUSIAS SOL, ETH ir
stablecoin'ų (USDC/USDT/DAI) palūkanų normas tarp PATIKIMŲ, žinomų
DeFi protokolų - naudojant NEMOKAMĄ, be rakto DeFiLlama API.

Šaltinis: https://yields.llama.fi/pools (viešas, be autentifikacijos,
be griežto limito - apima 20,000+ DeFi "pool'ų" iš visų grandinių)

SVARBU: Rodomos TIK aukšto TVL (likvidumo), ŽINOMŲ, patikimų protokolų
normos - NE bet kokie mažo, nepatikrinto protokolo pasiūlymai, kad
išvengtume rekomenduoti rizikingus/nepatikrintus variantus.
"""

import os
import requests

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; APYCompareBot/1.0)"}
POOLS_URL = "https://yields.llama.fi/pools"

MIN_TVL_USD = 10_000_000  # tik dideli, patikimi "pool'ai" (>=$10mln likvidumo)

# Tikslūs simboliai, kuriuos ieškome (case-insensitive, TIKSLUS atitikimas,
# kad išvengtume LP porų kaip "USDC-USDT" ar panašiai)
TARGET_ASSETS = {
    "SOL": ["SOL"],
    "ETH": ["ETH", "STETH", "WSTETH", "RETH", "CBETH"],
    "USDC": ["USDC"],
    "USDT": ["USDT"],
    "DAI": ["DAI"],
}

# Žinomi, patikimi protokolai (mažina riziką rekomenduoti nepatikrintus projektus)
TRUSTED_PROJECTS = [
    "aave-v3", "aave-v2", "compound-v3", "compound-v2", "lido",
    "kamino-lend", "kamino-liquidity", "marinade", "marinade-liquid-staking",
    "jito", "sky-lending", "makerdao", "spark", "morpho-blue", "rocket-pool",
    "benqi-lending", "solend", "binance-staked-eth", "coinbase-wrapped-staked-eth",
]


def fetch_all_pools() -> list:
    try:
        resp = requests.get(POOLS_URL, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti DeFiLlama duomenų: {e}")
        return []


def find_best_pools_for_asset(all_pools: list, symbol_variants: list, top_n: int = 3) -> list:
    matches = []
    symbol_variants_upper = [s.upper() for s in symbol_variants]

    for pool in all_pools:
        pool_symbol = (pool.get("symbol") or "").upper()
        project = (pool.get("project") or "").lower()
        tvl = pool.get("tvlUsd") or 0
        apy = pool.get("apy")

        if pool_symbol not in symbol_variants_upper:
            continue
        if project not in TRUSTED_PROJECTS:
            continue
        if tvl < MIN_TVL_USD:
            continue
        if apy is None or apy <= 0:
            continue

        matches.append({
            "project": pool.get("project"),
            "chain": pool.get("chain"),
            "symbol": pool.get("symbol"),
            "apy": apy,
            "apy_base": pool.get("apyBase"),
            "apy_reward": pool.get("apyReward"),
            "tvl": tvl,
        })

    matches.sort(key=lambda x: -x["apy"])
    return matches[:top_n]


def format_pool_line(pool: dict) -> str:
    reward_note = " (dalis - reward token'ai)" if pool.get("apy_reward") else ""
    return (
        f"  • <b>{pool['project']}</b> ({pool['chain']}): "
        f"{pool['apy']:.2f}% APY{reward_note} | TVL ${pool['tvl']:,.0f}"
    )


def build_message(all_pools: list) -> str:
    message = "<b>💰 DeFi/Staking APY palyginimas</b>\n"
    message += "<i>(tik patikimi protokolai, TVL ≥ $10mln)</i>\n\n"

    any_results = False
    for asset_label, variants in TARGET_ASSETS.items():
        best = find_best_pools_for_asset(all_pools, variants, top_n=3)
        if not best:
            continue
        any_results = True
        message += f"<b>{asset_label}</b>\n"
        for pool in best:
            message += format_pool_line(pool) + "\n"
        message += "\n"

    if not any_results:
        message += "Nepavyko rasti duomenų, atitinkančių kriterijus."

    message += (
        "<i>⚠️ APY normos KEIČIASI dažnai ir NĖRA garantuotos. "
        "Tai TIK informacija, ne finansinis patarimas. Visada patikrink "
        "protokolą pats prieš investuojant, ir žinok, kad DeFi turi "
        "SUTARTIES (smart contract) riziką - net patikimi protokolai gali "
        "turėti pažeidžiamumų.</i>\n\n"
        "<i>Šaltinis: DeFiLlama (yields.llama.fi)</i>"
    )
    return message


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] APY palyginimas išsiųstas.")


def main():
    all_pools = fetch_all_pools()
    if not all_pools:
        print("Nepavyko gauti jokių duomenų - stabdome.")
        return

    print(f"Gauta {len(all_pools)} 'pool'ų iš DeFiLlama, filtruojame...")
    message = build_message(all_pools)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
