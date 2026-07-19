"""
Savaitės ekonominių įvykių kalendorius -> Telegram
------------------------------------------------------
Kiekvieną pirmadienį siunčia savaitės (pirmadienis-sekmadienis) svarbius
JAV ekonominius įvykius:
- FOMC (Federal Reserve) posėdžiai - datos žinomos iš anksto, atnaujinamos
  kasmet rankiniu būdu (žr. FOMC_MEETINGS_2026)
- CPI, darbo vietų ataskaita (NFP), PCE, GDP - gaunama per FRED
  (St. Louis Federal Reserve) nemokamą API

REIKIA: FRED_API_KEY environment variable (nemokamas raktas iš
https://fred.stlouisfed.org/docs/api/api_key.html)
"""

import os
import requests
from datetime import date, timedelta

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EconCalendarBot/1.0)"}

# Oficialus 2026 m. FOMC posėdžių grafikas (federalreserve.gov)
# Formatas: (pradžios data, pabaigos data, ar yra Summary of Economic Projections)
FOMC_MEETINGS_2026 = [
    (date(2026, 1, 27), date(2026, 1, 28), False),
    (date(2026, 3, 17), date(2026, 3, 18), True),
    (date(2026, 4, 28), date(2026, 4, 29), False),
    (date(2026, 6, 16), date(2026, 6, 17), True),
    (date(2026, 7, 28), date(2026, 7, 29), False),
    (date(2026, 9, 15), date(2026, 9, 16), True),
    (date(2026, 10, 27), date(2026, 10, 28), False),
    (date(2026, 12, 8), date(2026, 12, 9), True),
]

# FRED release ID'ai svarbiausiems rodikliams
FRED_RELEASES = {
    "CPI (infliacija)": 10,
    "Darbo vietų ataskaita (NFP)": 50,
    "PCE (Fed mėgstamas infliacijos rodiklis)": 21,
    "BVP (GDP)": 53,
}


def get_week_range() -> tuple:
    """Grąžina einamosios savaitės pirmadienį ir sekmadienį."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def check_fomc_this_week(monday: date, sunday: date) -> list:
    events = []
    for start, end, has_projections in FOMC_MEETINGS_2026:
        if monday <= end <= sunday or monday <= start <= sunday:
            projection_note = " (su ekonominių prognozių atnaujinimu)" if has_projections else ""
            if start == end:
                events.append(f"🏛️ <b>FOMC posėdis</b> - {start.strftime('%m-%d')}{projection_note}")
            else:
                events.append(
                    f"🏛️ <b>FOMC posėdis</b> - {start.strftime('%m-%d')} iki {end.strftime('%m-%d')}"
                    f"{projection_note}\nSprendimas skelbiamas {end.strftime('%m-%d')} 14:00 ET"
                )
    return events


def check_fred_releases(monday: date, sunday: date) -> list:
    """Tikrina FRED release_dates API dėl artėjančių svarbių duomenų paskelbimų."""
    events = []
    if not FRED_API_KEY:
        print("[ĮSPĖJIMAS] FRED_API_KEY nenustatytas - praleidžiam FRED duomenis.")
        return events

    for name, release_id in FRED_RELEASES.items():
        url = (
            f"https://api.stlouisfed.org/fred/release/dates"
            f"?release_id={release_id}&realtime_start={monday.isoformat()}"
            f"&realtime_end={sunday.isoformat()}&api_key={FRED_API_KEY}&file_type=json"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            for release_date in data.get("release_dates", []):
                event_date = release_date.get("date")
                events.append(f"📊 <b>{name}</b> - {event_date}")
        except Exception as e:
            print(f"[KLAIDA] Nepavyko gauti {name} datos: {e}")

    return events


def send_to_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    resp = requests.post(url, data=payload, timeout=15)
    if not resp.ok:
        print(f"[KLAIDA] Nepavyko išsiųsti į Telegram: {resp.text}")
    else:
        print("[OK] Savaitės kalendorius išsiųstas.")


def main():
    monday, sunday = get_week_range()

    events = []
    events += check_fomc_this_week(monday, sunday)
    events += check_fred_releases(monday, sunday)

    header = f"<b>📅 Savaitės ekonominiai įvykiai</b>\n{monday.strftime('%Y-%m-%d')} - {sunday.strftime('%Y-%m-%d')}\n\n"

    if not events:
        message = header + "Šią savaitę reikšmingų JAV ekonominių įvykių nenumatyta."
    else:
        message = header + "\n".join(events)

    send_to_telegram(message)


if __name__ == "__main__":
    main()
