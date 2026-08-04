"""
DEX Boost 24h duomenų bendros statistikos analizė
------------------------------------------------------
Vienkartinis analizės įrankis - naudoja JAU TURIMUS peak_tracking.json
duomenis (su "finalized": true žyma - t.y. token'us, kuriems jau praėjo
24 val. nuo pirmo pastebėjimo), kad atsakytų:
1) Kiek % token'ų PUMPINO (2x+), kiek "NUMIRĖ" (žemas likvidumas)?
2) Ar SERIJINIŲ kūrėjų token'ai pasirodo GERIAU ar BLOGIAU nei vienkartinių?
3) TOP 5 geriausi/blogiausi rezultatai
"""

import json

PEAK_FILE = "peak_tracking.json"
DEPLOYER_FILE = "known_deployers.json"
DEAD_LIQUIDITY_THRESHOLD = 500


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

    finalized = {k: v for k, v in tracking.items() if v.get("finalized")}
    print(f"Analizuojama {len(finalized)} baigtų (24h) sekimų iš {len(tracking)} iš viso.\n")

    if not finalized:
        print("Nėra pakankamai baigtų duomenų analizei.")
        return

    peak_gains = []
    final_gains = []
    alive_count = 0
    dead_count = 0
    pumped_2x_count = 0

    deployer_stats = {}

    for token_address, info in finalized.items():
        initial_mcap = info.get("initial_mcap") or 0
        peak_mcap = info.get("peak_mcap") or 0
        last_mcap = info.get("last_mcap") or 0
        last_liq = info.get("last_liquidity") or 0
        deployer = info.get("deployer")

        peak_pct = pct_change(initial_mcap, peak_mcap)
        final_pct = pct_change(initial_mcap, last_mcap)

        if peak_pct is not None:
            peak_gains.append(peak_pct)
        if final_pct is not None:
            final_gains.append(final_pct)

        is_alive = last_liq >= DEAD_LIQUIDITY_THRESHOLD
        if is_alive:
            alive_count += 1
        else:
            dead_count += 1

        if peak_pct is not None and peak_pct >= 100:
            pumped_2x_count += 1

        if deployer:
            d = deployer_stats.setdefault(deployer, {"count": 0, "peak_gains": [], "alive": 0})
            d["count"] += 1
            if peak_pct is not None:
                d["peak_gains"].append(peak_pct)
            if is_alive:
                d["alive"] += 1

    total = len(finalized)
    avg_peak_gain = sum(peak_gains) / len(peak_gains) if peak_gains else 0
    median_peak_gain = sorted(peak_gains)[len(peak_gains) // 2] if peak_gains else 0
    avg_final_gain = sum(final_gains) / len(final_gains) if final_gains else 0

    print("=" * 60)
    print("BENDRA STATISTIKA (visi baigti 24h sekimai)")
    print("=" * 60)
    print(f"Iš viso analizuota: {total}")
    print(f"Vidutinis PIKO augimas: {avg_peak_gain:+.0f}%")
    print(f"Medianinis PIKO augimas: {median_peak_gain:+.0f}%")
    print(f"Vidutinis GALUTINIS pokytis (po 24h): {avg_final_gain:+.0f}%")
    print(f"Pumpino 2x+ (>=100% piko augimas): {pumped_2x_count} ({100*pumped_2x_count/total:.0f}%)")
    print(f"'Gyvi' (likvidumas >= ${DEAD_LIQUIDITY_THRESHOLD}) po 24h: {alive_count} ({100*alive_count/total:.0f}%)")
    print(f"'Numirę' (žemas likvidumas): {dead_count} ({100*dead_count/total:.0f}%)")

    repeat_deployer_gains = []
    single_deployer_gains = []
    repeat_alive = 0
    repeat_total = 0
    single_alive = 0
    single_total = 0

    for dep, stats in deployer_stats.items():
        total_launches_by_dep = len(deployers.get(dep, []))
        is_repeat = total_launches_by_dep > 1
        for g in stats["peak_gains"]:
            if is_repeat:
                repeat_deployer_gains.append(g)
            else:
                single_deployer_gains.append(g)
        if is_repeat:
            repeat_alive += stats["alive"]
            repeat_total += stats["count"]
        else:
            single_alive += stats["alive"]
            single_total += stats["count"]

    print("\n" + "=" * 60)
    print("PAKARTOTINIAI vs VIENKARTINIAI KŪRĖJAI")
    print("=" * 60)
    if repeat_deployer_gains:
        avg_repeat = sum(repeat_deployer_gains) / len(repeat_deployer_gains)
        print(f"\nPAKARTOTINIŲ kūrėjų token'ai (n={len(repeat_deployer_gains)}):")
        print(f"  Vidutinis piko augimas: {avg_repeat:+.0f}%")
        if repeat_total:
            print(f"  'Gyvi' po 24h: {repeat_alive}/{repeat_total} ({100*repeat_alive/repeat_total:.0f}%)")
    else:
        print("\nNėra pakankamai duomenų pakartotiniams kūrėjams.")

    if single_deployer_gains:
        avg_single = sum(single_deployer_gains) / len(single_deployer_gains)
        print(f"\nVIENKARTINIŲ kūrėjų token'ai (n={len(single_deployer_gains)}):")
        print(f"  Vidutinis piko augimas: {avg_single:+.0f}%")
        if single_total:
            print(f"  'Gyvi' po 24h: {single_alive}/{single_total} ({100*single_alive/single_total:.0f}%)")

    results = []
    for token_address, info in finalized.items():
        initial_mcap = info.get("initial_mcap") or 0
        peak_mcap = info.get("peak_mcap") or 0
        peak_pct = pct_change(initial_mcap, peak_mcap)
        if peak_pct is not None:
            results.append((info.get("name", "?"), info.get("symbol", "?"), peak_pct))

    results.sort(key=lambda x: -x[2])
    print("\n" + "=" * 60)
    print("TOP 5 GERIAUSI (didžiausias piko augimas)")
    print("=" * 60)
    for name, symbol, pct in results[:5]:
        print(f"  {name} ({symbol}): {pct:+.0f}%")

    print("\n" + "=" * 60)
    print("TOP 5 BLOGIAUSI (mažiausias/neigiamas piko augimas)")
    print("=" * 60)
    for name, symbol, pct in results[-5:]:
        print(f"  {name} ({symbol}): {pct:+.0f}%")


if __name__ == "__main__":
    main()
