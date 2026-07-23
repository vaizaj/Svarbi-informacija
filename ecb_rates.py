"""
ECB palūkanų normų pasikeitimo botas -> Telegram
----------------------------------------------------
Kartą per dieną tikrina ECB pagrindines palūkanų normas (Deposit Facility
Rate, Main Refinancing Rate, Marginal Lending Facility Rate) per oficialų,
nemokamą ECB SDMX 2.1 API (be jokio rakto).

Siunčia žinutę, kai:
1. Normos PASIKEIČIA (palyginus su paskutine žinoma verte) - kartu su
   aiškiu dabartinio (naujo) diapazono akcentu ir 3/6/12 mėn. istorija
2. ECB posėdžio dieną normos PALIEKAMOS nepakeistos - kartu su
   3/6/12 mėn. istorija
"""

import os
import json
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (compatible; ECBBot/1.0)"}
STATE_FILE = "ecb_rates_state.json"

# Oficialios 2026 m. ECB pinigų politikos posėdžių sprendimų datos (Day 2)
# Šaltinis: ecb.europa.eu/press/calendars/mgcgc
ECB_MEETING_DATES_2026 = [
    "2026-02-05",
    "2026-03-19",
    "2026-04-30",
    "2026-06-11",
    "2026-07-23",
    "2026-09-10",
    "2026-10-29",
    "2026-12-17",
]

# ECB SDMX serijų raktai pagrindinėms normoms
SERIES = {
    "Indėlių palūkanų norma (DFR)": "D.U2.EUR.4F.KR.DFR.LEV",
    "Pagrindinių refinansavimo operacijų norma (MRO)": "D.U2.EUR.4F.KR.MRR_FR.LEV",
    "Ribinio skolinimosi galimybės norma (MLF)": "D.U2.EUR.4F.KR.MLFR.LEV",
}
DFR_KEY = SERIES["Indėlių palūkanų norma (DFR)"]

BASE_URL = "https://sdw-wsrest.ecb.europa.eu/service/data/FM"


def subtract_months(d, months: int):
    """Atima nurodytą mėnesių skaičių nuo datos (be papildomų bibliotekų)."""
    month = d.month - months
    year = d.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(d.day, 28)
    return d.replace(year=year, month=month, day=day)


def fetch_latest_rate(series_key: str) -> dict:
    """Gauna naujausią normos reikšmę ir datą iš ECB SDMX API."""
    url = f"{BASE_URL}/{series_key}"
    params = {"lastNObservations": 1, "format": "jsondata"}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return _parse_sdmx_response(resp.json())


def fetch_value_as_of(series_key: str, target_date) -> dict:
    """Gauna reikšmę, galiojusią artimiausią datą IKI (arba lygią) target_date."""
    url = f"{BASE_URL}/{series_key}"
    params = {
        "lastNObservations": 1,
        "format": "jsondata",
        "endPeriod": target_date.isoformat(),
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return _parse_sdmx_response(resp.json())


def _parse_sdmx_response(data: dict) -> dict:
    series_data = data["dataSets"][0]["series"]
    first_series_key = list(series_data.keys())[0]
    observations = series_data[first_series_key]["observations"]
    obs_index = list(observations.keys())[-1]
    value = observations[obs_index][0]
    time_values = data["structure"]["dimensions"]["observation"][0]["values"]
    date_str = time_values[int(obs_index)]["id"]
    return {"value": float(value), "date": date_str}


def build_historical_comparison(current_value: float) -> str:
    """Sukuria 3/6/12 mėn. pokyčio santrauką DFR normai."""
    today = datetime.now(timezone.utc).date()
    lines = []
    for months, label in [(3, "3 mėn."), (6, "6 mėn."), (12, "12 mėn.")]:
        try:
            past_date = subtract_months(today, months)
            past = fetch_value_as_of(DFR_KEY, past_date)
            diff_bp = (current_value - past["value"]) * 100
            if diff_bp > 0:
                arrow = f"🔺 +{diff_bp:.0f}bp"
            elif diff_bp < 0:
                arrow = f"🔻 {diff_bp:.0f}bp"
            else:
                arrow = "➡️ 0bp"
            lines.append(f"{label}: {arrow} ({past['value']:.2f}% → {current_value:.2f}%)")
        except Exception as e:
            print(f"[ĮSPĖJIMAS] Nepavyko gauti {label} istorijos: {e}")
    if not lines:
        return ""
    return "\n\n<b>📊 Pokytis per pastarąjį laikotarpį (DFR):</b>\n" + "\n".join(lines)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] ECB pranešimas išsiųstas.")


def main():
    state = load_state()
    is_first_run = not state
    changes = []

    new_state = {}
    for name, series_key in SERIES.items():
        try:
            result = fetch_latest_rate(series_key)
        except Exception as e:
            print(f"[KLAIDA] Nepavyko gauti '{name}': {e}")
            continue

        prev_value = state.get(series_key, {}).get("value")
        new_state[series_key] = result

        if not is_first_run and prev_value is not None and prev_value != result["value"]:
            diff = result["value"] - prev_value
            direction = "🔺 PAKELTA" if diff > 0 else "🔻 SUMAŽINTA"
            diff_bp = abs(diff) * 100
            changes.append(
                f"{direction} <b>{name}</b>\n"
                f"Buvo: {prev_value:.2f}%\n"
                f"👉 <b>Dabar: {result['value']:.2f}%</b>\n"
                f"Pokytis: {diff_bp:.0f} bazinių punktų\n"
                f"Įsigaliojo: {result['date']}"
            )

    new_state["reported_meetings"] = state.get("reported_meetings", [])
    save_state(new_state)

    if is_first_run:
        print("Pirmas paleidimas - būsena užsirašyta, pranešimai nesiunčiami.")
        return

    dfr_now = new_state.get(DFR_KEY, {}).get("value")

    if changes:
        message = "<b>🏦 ECB palūkanų normos pasikeitė!</b>\n\n" + "\n\n".join(changes)
        if dfr_now is not None:
            message += build_historical_comparison(dfr_now)
        send_to_telegram(message)
        return

    # Jei pokyčių nebuvo - patikrinam, ar šiandien yra ŽINOMA posėdžio sprendimo diena,
    # apie kurią dar nepranešėme - jei taip, praneškime, kad normos PALIKTOS nepakeistos
    today_str = datetime.now(timezone.utc).date().isoformat()
    reported_meetings = set(new_state.get("reported_meetings", []))

    if today_str in ECB_MEETING_DATES_2026 and today_str not in reported_meetings:
        message = (
            "<b>🏦 ECB posėdžio rezultatas</b>\n\n"
            "➡️ Palūkanų normos <b>PALIKTOS NEPAKEISTOS</b>\n"
            f"👉 <b>Indėlių palūkanų norma (DFR): {dfr_now:.2f}%</b>\n"
            f"Data: {today_str}"
        )
        if dfr_now is not None:
            message += build_historical_comparison(dfr_now)
        send_to_telegram(message)
        reported_meetings.add(today_str)
        new_state["reported_meetings"] = list(reported_meetings)
        save_state(new_state)
        return

    print("ECB normos nepasikeitė, ir šiandien nėra posėdžio diena.")


if __name__ == "__main__":
    main()
