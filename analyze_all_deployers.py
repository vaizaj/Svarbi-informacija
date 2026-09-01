"""
VISŲ pakartotinių kūrėjų palyginimo analizė
------------------------------------------------
Peržiūri VISUS žinomus pakartotinius (2+) kūrėjus iš known_deployers.json
ir peak_tracking.json, apskaičiuoja kiekvieno "track record" (kiek % token'ų
pasiekė +30% piką, koks vidutinis piko augimas, kiek "gyvų"), ir SURIKIUOJA
juos nuo GERIAUSIO iki BLOGIAUSIO.

Tikslas: rasti, ar yra KITŲ, GERESNIŲ kandidatų sekimui, be jau žinomo
178-token'ų kūrėjo.
"""

import json

PEAK_FILE = "peak_tracking.json"
DEPLOYER_FILE = "known_deployers.json"
DEAD_LIQUIDITY_THRESHOLD = 500
MIN_TOKENS_FOR_ANALYSIS = 2  # tik pakartotiniai kūrėjai


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

    print(f"Iš viso sekamų token'ų: {len(tracking)}")
    print(f"Iš viso žinomų kūrėjų: {len(deployers)}\n")

    # Sugrupuojam peak_tracking duomenis pagal deployer adresą
    deployer_performance = {}  # deployer -> list of (name, symbol, peak_pct, is_alive)

    for token_address, info in tracking.items():
        deployer = info.get("deployer")
        if not deployer:
            continue

        initial_mcap = info.get("initial_mcap") or 0
        peak_mcap = info.get("peak_mcap") or info.get("last_mcap") or initial_mcap
        last_liq = info.get("last_liquidity", info.get("liquidity_usd", 0)) or 0

        peak_pct = pct_change(initial_mcap, peak_mcap)
        is_alive = last_liq >= DEAD_LIQUIDITY_THRESHOLD

        deployer_performance.setdefault(deployer, []).append(
            (info.get("name", "?"), info.get("symbol", "?"), peak_pct, is_alive)
        )

    # Filtruojam TIK pakartotinius kūrėjus (2+ token'ų su duomenimis)
    results = []
    for deployer, tokens in deployer_performance.items():
        total_known = len(deployers.get(deployer, []))
        if len(tokens) < MIN_TOKENS_FOR_ANALYSIS:
            continue

        valid_pcts = [t[2] for t in tokens if t[2] is not None]
        if not valid_pcts:
            continue

        avg_peak = sum(valid_pcts) / len(valid_pcts)
        median_peak = sorted(valid_pcts)[len(valid_pcts) // 2]
        hit_30_count = sum(1 for p in valid_pcts if p >= 30)
        hit_30_pct = 100 * hit_30_count / len(valid_pcts)
        alive_count = sum(1 for t in tokens if t[3])
        alive_pct = 100 * alive_count / len(tokens)

        results.append({
            "deployer": deployer,
            "analyzed_tokens": len(tokens),
            "total_known_tokens": total_known,
            "avg_peak_pct": avg_peak,
            "median_peak_pct": median_peak,
            "hit_30_pct": hit_30_pct,
            "alive_pct": alive_pct,
        })

    # Surikiuojam pagal MEDIANINĮ piko augimą (patikimesnis rodiklis nei
    # vidurkis, kurį iškraipo pavieniai ekstremalūs atvejai)
    results.sort(key=lambda x: -x["median_peak_pct"])

    print("=" * 90)
    print(f"{'Kūrėjas':<44} {'N':>4} {'Vid.pikas':>10} {'Med.pikas':>10} {'+30% rate':>10} {'Gyvi %':>8}")
    print("=" * 90)
    for r in results:
        print(
            f"{r['deployer']:<44} {r['analyzed_tokens']:>4} "
            f"{r['avg_peak_pct']:>+9.0f}% {r['median_peak_pct']:>+9.0f}% "
            f"{r['hit_30_pct']:>9.0f}% {r['alive_pct']:>7.0f}%"
        )

    print("\n" + "=" * 90)
    print("REKOMENDACIJOS")
    print("=" * 90)

    if not results:
        print("Nerasta pakankamai duomenų analizei.")
        return

    best = results[0]
    print(f"\n🏆 GERIAUSIAS pagal medianą: {best['deployer']}")
    print(f"   {best['analyzed_tokens']} analizuotų token'ų, "
          f"medianinis pikas {best['median_peak_pct']:+.0f}%, "
          f"{best['hit_30_pct']:.0f}% pasiekia +30%")

    # Jau sekami kūrėjai (iš dabartinio WATCHED_WALLETS sąrašo)
    already_watched = {"0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB".lower()}

    new_candidates = [r for r in results if r["deployer"].lower() not in already_watched]
    if new_candidates:
        print(f"\n📋 NAUJI kandidatai (dar nesekami), surikiuoti pagal medianą:")
        for r in new_candidates[:5]:
            print(f"   {r['deployer']} - {r['analyzed_tokens']} token'ų, "
                  f"mediana {r['median_peak_pct']:+.0f}%, +30% rate {r['hit_30_pct']:.0f}%")
    else:
        print("\nVisi rasti pakartotiniai kūrėjai jau sekami arba nėra naujų kandidatų.")


if __name__ == "__main__":
    main()
