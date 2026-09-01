"""
TOP token'ų (+1000%+ piko augimas) analizė su kūrėjų kontekstu
------------------------------------------------------------------
Peržiūri VISUS sekamus token'us (nepriklausomai nuo kūrėjo grupavimo),
randa tuos, kurie pasiekė BENT +1000% piką nuo pradinio (boost momento)
market cap, ir PARODO, kiek IŠ VISO token'ų tas pats kūrėjas yra paleidęs
- kad matytume, ar tai "vienkartinė sėkmė", ar galimai vertas sekimo
kandidatas su daugiau istorijos.
"""

import json

PEAK_FILE = "peak_tracking.json"
DEPLOYER_FILE = "known_deployers.json"
THRESHOLD_PCT = 1000


def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def pct_change(initial, current):
    if not initial or initial == 0:
        return None
    return ((current - initial) / initial) * 100


def main():
    tracking = load_json(PEAK_FILE, {})
    deployers = load_json(DEPLOYER_FILE, {})

    top_tokens = []
    for token_address, info in tracking.items():
        initial_mcap = info.get("initial_mcap") or 0
        peak_mcap = info.get("peak_mcap") or info.get("last_mcap") or initial_mcap
        peak_pct = pct_change(initial_mcap, peak_mcap)

        if peak_pct is not None and peak_pct >= THRESHOLD_PCT:
            deployer = info.get("deployer", "?")
            total_by_deployer = len(deployers.get(deployer, []))
            top_tokens.append({
                "name": info.get("name", "?"),
                "symbol": info.get("symbol", "?"),
                "peak_pct": peak_pct,
                "initial_mcap": initial_mcap,
                "peak_mcap": peak_mcap,
                "deployer": deployer,
                "deployer_total_tokens": total_by_deployer,
            })

    top_tokens.sort(key=lambda x: -x["peak_pct"])

    print(f"Rasta {len(top_tokens)} token'ų su +{THRESHOLD_PCT}%+ piku iš {len(tracking)} sekamų.\n")
    print("=" * 100)

    for t in top_tokens:
        print(f"\n{t['name']} ({t['symbol']}) - PIKAS: +{t['peak_pct']:,.0f}%")
        print(f"  Pradinis MC: ${t['initial_mcap']:,.0f} -> Piko MC: ${t['peak_mcap']:,.0f}")
        print(f"  Kūrėjas: {t['deployer']}")
        if t["deployer_total_tokens"] > 1:
            print(f"  ⭐ ŠIS KŪRĖJAS turi IŠ VISO {t['deployer_total_tokens']} paleistų token'ų "
                  f"- GALIMAI VERTAS TOLESNIO SEKIMO")
        elif t["deployer_total_tokens"] == 1:
            print(f"  (vienkartinis kūrėjas - tik šis vienas žinomas token'as)")
        else:
            print(f"  (kūrėjas nežinomas/neaptiktas)")

    # Suvestinė - kurie kūrėjai TURI daugiau token'ų (kandidatai giluminei analizei)
    multi_token_deployers = set()
    for t in top_tokens:
        if t["deployer_total_tokens"] >= 5:
            multi_token_deployers.add((t["deployer"], t["deployer_total_tokens"]))

    print("\n" + "=" * 100)
    print("KŪRĖJAI SU +1000%+ TOKEN'U IR 5+ IŠ VISO PALEISTŲ TOKEN'Ų (verta analyze_single_deployer.py)")
    print("=" * 100)
    if multi_token_deployers:
        for dep, count in sorted(multi_token_deployers, key=lambda x: -x[1]):
            print(f"  {dep} - {count} token'ų iš viso")
    else:
        print("  Nerasta - visi +1000%+ token'ai yra iš vienkartinių/mažų kūrėjų.")


if __name__ == "__main__":
    main()
