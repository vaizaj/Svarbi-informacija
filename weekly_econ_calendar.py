"""
Savaitės ekonominio kalendoriaus botas -> Telegram (pirmadieniais)
------------------------------------------------------------------
Kas pirmadienį rodo, KAS LAUKIA per ATEINANČIĄ savaitę - FOMC (JAV) IR
ECB (ES) posėdžiai, bei FRED ekonominių rodiklių (CPI, NFP, PCE, GDP)
paskelbimo datos. Formatuojama DIENA PO DIENOS, ne plokščiu sąrašu.

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

ECB_MEETING_DATES_2026 = [
    date(2026, 2, 5),
    date(2026, 3, 19),
    date(2026, 4, 30),
    date(2026, 6, 11),
    date(2026, 7, 23),
    date(2026, 9, 10),
    date(2026, 10, 29),
    date(2026, 12, 17),
]

FRED_RELEASES = {
    "CPI (infliacija, JAV)": 10,
    "Darbo vietų ataskaita (NFP, JAV)": 50,
    "PCE (Fed infliacijos rodiklis)": 21,
    "BVP (GDP, JAV)": 53,
}

WEEKDAY_NAMES_LT = ["Pirmadienis", "Antradienis", "Trečiadienis", "Ketvirtadienis", "Penktadienis", "Šeštadienis", "Sekmadienis"]


def get_week_range() -> tuple:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def collect_events_by_day(monday: date, sunday: date) -> dict:
    events_by_day = {monday + timedelta(days=i): [] for i in range(7)}

    for start, end, has_projections in FOMC_MEETINGS_2026:
        decision_day = end
        if monday <= decision_day <= sunday:
            note = " (su ekon. prognozių atnaujinimu)" if has_projections else ""
            events_by_day[decision_day].append(f"🇺🇸 <b>FOMC sprendimas</b>{note} (14:00 ET)")
        if start != end and monday <= start <= sunday:
            events_by_day[start].append("🇺🇸 FOMC posėdis prasideda")

    for meeting_date in ECB_MEETING_DATES_2026:
        if monday <= meeting_date <= sunday:
            events_by_day[meeting_date].append("🇪🇺 <b>ECB sprendimas</b> (14:15 CET)")

    if not FRED_API_KEY:
        print("[ĮSPĖJIMAS] FRED_API_KEY nenustatytas - praleidžiam FRED duomenis.")
    else:
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
                    event_date = date.fromisoformat(release_date.get("date"))
                    if monday <= event_date <= sunday:
                        events_by_day[event_date].append(f"📊 {name}")
            except Exception as e:
                print(f"[KLAIDA] Nepavyko gauti {name} datos: {e}")

    return events_by_day


def format_message(monday: date, sunday: date, events_by_day: dict) -> str:
    header = (
        f"<b>📅 ATEINANČIOS SAVAITĖS APŽVALGA</b>\n"
        f"{monday.strftime('%Y-%m-%d')} – {sunday.strftime('%Y-%m-%d')}\n\n"
    )

    has_any_events = any(events_by_day.values())
    if not has_any_events:
        return header + "Šią savaitę reikšmingų JAV/ES ekonominių įvykių nenumatyta."

    lines = []
    for i in range(7):
        day = monday + timedelta(days=i)
        day_name = WEEKDAY_NAMES_LT[i]
        day_events = events_by_day.get(day, [])
        if day_events:
            lines.append(f"<b>{day_name} ({day.strftime('%m-%d')})</b>")
            for ev in day_events:
                lines.append(f"  • {ev}")
        else:
            lines.append(f"{day_name} ({day.strftime('%m-%d')}): —")
        lines.append("")

    return header + "\n".join(lines).strip()


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
    events_by_day = collect_events_by_day(monday, sunday)
    message = format_message(monday, sunday, events_by_day)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
