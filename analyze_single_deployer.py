"""
Konkrečios piniginės (deployer) DETALI analizė
------------------------------------------------
Analizuoja VISUS token'us iš NURODYTOS piniginės ir parodo:
1) Kiek % pasiekė bent +30%, +40%, +50%, +100% piką (t.y. "ar pelno
   tikslas realistiškas")
2) Per KIEK LAIKO (vidutiniškai/medianiškai) pasiekiamas piko taškas
3) Pilną kiekvieno token'o rezultatą individualiai
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

PEAK_FILE = "peak_tracking.json"

# Piniginė, kurią analizuojame - pakeisk, jei nori kitą
TARGET_DEPLOYER = "0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB"

PROFIT_TARGETS = [20, 30, 40, 50, 75, 100]


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


def parse_time(t_str):
    try:
        return datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Vilnius"))
    except Exception:
        return None


def main():
    tracking = load_json(PEAK_FILE, {})

    tokens = {
        addr: info for addr, info in tracking.items()
        if info.get("deployer") == TARGET_DEPLOYER and info.get("finalized")
    }

    print(f"Piniginė: {TARGET_DEPLOYER}")
    print(f"Rasta {len(tokens)} baigtų (24h) token'ų iš šios piniginės.\n")

    if not tokens:
        print("Nėra duomenų šiai piniginei.")
        return

    peak_pcts = []
    time_to_peak_hours = []
    individual_results = []

    for addr, info in tokens.items():
        initial_mcap = info.get("initial_mcap") or 0
        peak_mcap = info.get("peak_mcap") or 0
        peak_pct = pct_change(initial_mcap, peak_mcap)

        first_detected = parse_time(info.get("first_detected_at", ""))
        peak_time = parse_time(info.get("peak_time", ""))

        hours_to_peak = None
        if first_detected and peak_time:
            hours_to_peak = (peak_time - first_detected).total_seconds() / 3600

        if peak_pct is not None:
            peak_pcts.append(peak_pct)
            individual_results.append((info.get("name", "?"), info.get("symbol", "?"), peak_pct, hours_to_peak))
        if hours_to_peak is not None and hours_to_peak > 0:
            time_to_peak_hours.append(hours_to_peak)

    total = len(peak_pcts)

    print("=" * 60)
    print(f"AR PELNO TIKSLAS PASIEKIAMAS? (iš {total} token'ų)")
    print("=" * 60)
    for target in PROFIT_TARGETS:
        reached = sum(1 for p in peak_pcts if p >= target)
        print(f"  Pasiekė bent +{target}% piką: {reached}/{total} ({100*reached/total:.0f}%)")

    if time_to_peak_hours:
        avg_hours = sum(time_to_peak_hours) / len(time_to_peak_hours)
        median_hours = sorted(time_to_peak_hours)[len(time_to_peak_hours) // 2]
        print(f"\nVidutinis laikas iki piko: {avg_hours:.1f} val.")
        print(f"Medianinis laikas iki piko: {median_hours:.1f} val.")

    print("\n" + "=" * 60)
    print("VISI TOKEN'AI (rikiuota pagal piko augimą)")
    print("=" * 60)
    individual_results.sort(key=lambda x: -x[2])
    for name, symbol, pct, hours in individual_results:
        hours_str = f"{hours:.1f}h" if hours is not None else "?"
        print(f"  {name} ({symbol}): {pct:+.0f}% (per {hours_str})")


if __name__ == "__main__":
    main()
