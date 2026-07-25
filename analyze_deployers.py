"""
Deployer (kūrėjo) piniginių analizės įrankis
------------------------------------------------
Peržiūri mūsų jau sukauptus Robinhood grandinės DEX Boost duomenis
(dex_boosts_ws_seen.json), ir kiekvienam token'ui per Blockscout API
suranda KŪRĖJO (deployer) piniginės adresą.

Jei ta pati piniginė pasirodo KELIUOSE skirtinguose boostintuose
token'uose, tai stiprus signalas - "pakartotinis kūrėjas".

Tai VIENKARTINĖ analizė (ne nuolat veikiantis botas) - paleidi ranka,
kai nori pamatyti dabartinę situaciją.
"""

import json
import time
import requests

STATE_FILE = "dex_boosts_ws_seen.json"
BLOCKSCOUT_BASE = "https://robinhoodchain.blockscout.com/api/v2"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DeployerAnalysis/1.0)"}


def load_robinhood_token_addresses() -> set:
    """Ištraukia unikalius Robinhood token adresus iš seen_boosts failo."""
    addresses = set()
    try:
        with open(STATE_FILE, "r") as f:
            seen = json.load(f)
    except FileNotFoundError:
        print(f"[KLAIDA] Nerastas failas: {STATE_FILE}")
        return addresses

    for key in seen:
        # formatas: "chainId:tokenAddress:amount"
        parts = key.split(":")
        if len(parts) >= 2 and parts[0] == "robinhood":
            addresses.add(parts[1])

    return addresses


def get_creator_address(token_address: str) -> str:
    """Gauna kontrakto kūrėjo (deployer) adresą per Blockscout API."""
    try:
        url = f"{BLOCKSCOUT_BASE}/addresses/{token_address}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("creator_address_hash")
    except Exception as e:
        print(f"[ĮSPĖJIMAS] Nepavyko gauti kūrėjo {token_address}: {e}")
        return None


def get_token_name(token_address: str) -> str:
    """Gauna token'o pavadinimą/simbolį per Blockscout API."""
    try:
        url = f"{BLOCKSCOUT_BASE}/tokens/{token_address}"
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return f"{data.get('name', '?')} ({data.get('symbol', '?')})"
    except Exception:
        return token_address[:10] + "..."


def main():
    addresses = load_robinhood_token_addresses()
    print(f"Rasta {len(addresses)} unikalių Robinhood token adresų analizei.\n")

    deployer_map = {}  # deployer_address -> [token_address, ...]

    for i, token_address in enumerate(addresses, 1):
        print(f"[{i}/{len(addresses)}] Tikrinu {token_address} ...")
        creator = get_creator_address(token_address)
        if creator:
            deployer_map.setdefault(creator, []).append(token_address)
        time.sleep(0.5)  # mandagumo pauzė, nepersistengiam su API

    print("\n" + "=" * 60)
    print("REZULTATAI")
    print("=" * 60)

    repeat_deployers = {d: tokens for d, tokens in deployer_map.items() if len(tokens) > 1}

    if not repeat_deployers:
        print("\nNerasta jokių PAKARTOTINIŲ kūrėjų - kiekvienas token'as turi skirtingą kūrėją.")
    else:
        print(f"\nRasta {len(repeat_deployers)} PAKARTOTINIŲ kūrėjų (kurie paleido 2+ boostintus token'us):\n")
        for deployer, tokens in sorted(repeat_deployers.items(), key=lambda x: -len(x[1])):
            print(f"\n🔁 Kūrėjas: {deployer}")
            print(f"   Paleido {len(tokens)} boostintus token'us:")
            for t in tokens:
                name = get_token_name(t)
                print(f"     - {name} ({t})")
                time.sleep(0.3)

    print(f"\n\nIš viso unikalių kūrėjų: {len(deployer_map)}")
    print(f"Iš jų pakartotinių (2+): {len(repeat_deployers)}")

    # Išsaugom rezultatą failui, kad galėtume naudoti vėliau (pvz. live bote)
    with open("deployer_analysis.json", "w") as f:
        json.dump(deployer_map, f, indent=2)
    print("\nRezultatai išsaugoti: deployer_analysis.json")


if __name__ == "__main__":
    main()
