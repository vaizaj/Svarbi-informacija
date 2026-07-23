"""
Federalinio rezervo (Fed) palūkanų normų pasikeitimo botas -> Telegram
--------------------------------------------------------------------------
Kartą per dieną tikrina JAV Fed Funds Rate tikslinį diapazoną (viršutinę
ir apatinę ribas) per FRED (St. Louis Federal Reserve) API - tą patį
raktą, kurį jau naudoja weekly_econ_calendar.py.

Siunčia žinutę, kai:
1. Normos PASIKEIČIA (palyginus su paskutine žinoma verte)
2. FOMC posėdžio dieną normos PALIEKAMOS nepakeistos (kad žinotum
   apie kiekvieną sprendimą, ne tik pokyčius)
"""

import os
import json
import requests
from datetime import datetime, timezone

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
FRED_API_KEY = os.environ["FRED_API_KEY"]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FedRateBot/1.0)"}
STATE_FILE = "fed_rates_state.json"

# Oficialus 2026 m. FOMC posėdžių grafikas (federalreserve.gov)
# Sprendimo diena - antra kiekvienos poros data
FOMC_MEETINGS_2026 = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

# FRED serijos: Federal Funds Target Range viršutinė ir apatinė riba
FRED_SERIES = {
    "upper": "DFEDTARU",
    "lower": "DFEDTARL",
}


def fetch_latest_value(series_id: str) -> dict:
    """Gauna naujausią reikšmę iš FRED API."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    obs = data["observations"][0]
    return {"value": float(obs["value"]), "date": obs["date"]}


def subtract_months(d, months: int):
    """Atima nurodytą mėnesių skaičių nuo datos (be papildomų bibliotekų)."""
    month = d.month - months
    year = d.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(d.day, 28)  # saugu visiems mėnesiams
    return d.replace(year=year, month=month, day=day)


def fetch_value_as_of(series_id: str, target_date) -> dict:
    """Gauna reikšmę, galiojusią artimiausią datą IKI (arba lygią) target_date."""
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "observation_end": target_date.isoformat(),
        "limit": 1,
    }
    resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    obs = data["observations"][0]
    return {"value": float(obs["value"]), "date": obs["date"]}


def build_historical_comparison(current_upper: float, current_lower: float = None) -> str:
    """Sukuria 3/6/12 mėn. pokyčio santrauką, lyginant su dabartine viršutine riba."""
    today = datetime.now(timezone.utc).date()
    lines = []
    for months, label in [(3, "3 mėn."), (6, "6 mėn."), (12, "12 mėn.")]:
        try:
            past_date = subtract_months(today, months)
            past = fetch_value_as_of(FRED_SERIES["upper"], past_date)
            diff_bp = (current_upper - past["value"]) * 100
            if diff_bp > 0:
                arrow = f"🔺 +{diff_bp:.0f}bp"
            elif diff_bp < 0:
                arrow = f"🔻 {diff_bp:.0f}bp"
            else:
                arrow = "➡️ 0bp"
            lines.append(f"{label}: {arrow} ({past['value']:.2f}% → {current_upper:.2f}%)")
        except Exception as e:
            print(f"[ĮSPĖJIMAS] Nepavyko gauti {label} istorijos: {e}")
    if not lines:
        return ""
    return "\n\n<b>📊 Pokytis per pastarąjį laikotarpį:</b>\n" + "\n".join(lines)


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
        print("[OK] Fed pranešimas išsiųstas.")


def main():
    state = load_state()
    is_first_run = not state

    try:
        upper = fetch_latest_value(FRED_SERIES["upper"])
        lower = fetch_latest_value(FRED_SERIES["lower"])
    except Exception as e:
        print(f"[KLAIDA] Nepavyko gauti Fed normų: {e}")
        return

    prev_upper = state.get("upper", {}).get("value")
    prev_lower = state.get("lower", {}).get("value")

    new_state = {
        "upper": upper,
        "lower": lower,
        "reported_meetings": state.get("reported_meetings", []),
    }

    changed = (
        not is_first_run
        and prev_upper is not None
        and (prev_upper != upper["value"] or prev_lower != lower["value"])
    )

    save_state(new_state)

    if is_first_run:
        print("Pirmas paleidimas - būsena užsirašyta, pranešimai nesiunčiami.")
        return

    if changed:
        diff_bp = abs(upper["value"] - prev_upper) * 100
        direction = "🔺 PAKELTA" if upper["value"] > prev_upper else "🔻 SUMAŽINTA"
        message = (
            "<b>🇺🇸 Fed palūkanų normos pasikeitė!</b>\n\n"
            f"{direction} <b>Fed Funds Rate</b>\n"
            f"Buvo: {prev_lower:.2f}%-{prev_upper:.2f}%\n"
            f"👉 <b>Dabartinis tikslinis diapazonas: {lower['value']:.2f}%-{upper['value']:.2f}%</b>\n"
            f"Pokytis: {diff_bp:.0f} bazinių punktų\n"
            f"Data: {upper['date']}"
        )
        message += build_historical_comparison(upper["value"], lower["value"])
        send_to_telegram(message)
        return

    # Jei pokyčių nebuvo - patikrinam, ar šiandien FOMC sprendimo diena
    today_str = datetime.now(timezone.utc).date().isoformat()
    reported_meetings = set(new_state.get("reported_meetings", []))

    if today_str in FOMC_MEETINGS_2026 and today_str not in reported_meetings:
        message = (
            "<b>🇺🇸 FOMC posėdžio rezultatas</b>\n\n"
            "➡️ Fed Funds Rate <b>PALIKTA NEPAKEISTA</b>\n"
            f"Tikslinis diapazonas: {lower['value']:.2f}%-{upper['value']:.2f}%\n"
            f"Data: {today_str}"
        )
        message += build_historical_comparison(upper["value"], lower["value"])
        send_to_telegram(message)
        reported_meetings.add(today_str)
        new_state["reported_meetings"] = list(reported_meetings)
        save_state(new_state)
        return

    print("Fed normos nepasikeitė, ir šiandien nėra FOMC sprendimo diena.")


if __name__ == "__main__":
    main()
