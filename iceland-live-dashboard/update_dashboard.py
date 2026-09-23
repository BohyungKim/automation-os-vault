from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import re
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent
OUTPUT = ROOT / "index.html"

TRIPS = [
    {"date": "2026-10-07", "day": "Day 1", "location": "Hella", "lat": 63.8348, "lon": -20.4008},
    {"date": "2026-10-08", "day": "Day 2", "location": "Vík", "lat": 63.4194, "lon": -19.0090},
    {"date": "2026-10-09", "day": "Day 3", "location": "Höfn", "lat": 64.2539, "lon": -15.2082},
    {"date": "2026-10-10", "day": "Day 4", "location": "Kirkjubæjarklaustur", "lat": 63.7908, "lon": -18.0607},
    {"date": "2026-10-11", "day": "Day 5", "location": "Hvolsvöllur", "lat": 63.7507, "lon": -20.2245},
    {"date": "2026-10-12", "day": "Day 6", "location": "Reykjavík", "lat": 64.1466, "lon": -21.9426},
    {"date": "2026-10-13", "day": "Day 7", "location": "KEF / departure", "lat": 63.9850, "lon": -22.6056},
]

MONTHS = {
    "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
    "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
}


def get_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "IcelandTripDashboard/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8")


def get_json(url: str):
    return json.loads(get_text(url))


def next_date(value: str) -> str:
    day = dt.date.fromisoformat(value) + dt.timedelta(days=1)
    return day.isoformat()


def fetch_weather(stop: dict) -> dict:
    params = {
        "latitude": stop["lat"],
        "longitude": stop["lon"],
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_gusts_10m_max",
        "hourly": "cloud_cover",
        "timezone": "Atlantic/Reykjavik",
        "forecast_days": 16,
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)

    try:
        payload = get_json(url)
        daily = payload.get("daily", {})
        times = daily.get("time", [])
        if stop["date"] not in times:
            return {"available": False, "reason": "Outside Open-Meteo 16-day forecast window"}

        idx = times.index(stop["date"])
        hourly = payload.get("hourly", {})
        htimes = hourly.get("time", [])
        hcloud = hourly.get("cloud_cover", [])
        tomorrow = next_date(stop["date"])
        samples = []

        for timestamp, cloud in zip(htimes, hcloud):
            if cloud is None:
                continue
            day = timestamp[:10]
            hour = int(timestamp[11:13])
            if (day == stop["date"] and hour >= 20) or (day == tomorrow and hour <= 2):
                samples.append(float(cloud))

        return {
            "available": True,
            "code": daily.get("weather_code", [None])[idx],
            "max_temp": daily.get("temperature_2m_max", [None])[idx],
            "min_temp": daily.get("temperature_2m_min", [None])[idx],
            "rain": daily.get("precipitation_probability_max", [None])[idx],
            "gust": daily.get("wind_gusts_10m_max", [None])[idx],
            "night_cloud": round(sum(samples) / len(samples)) if samples else None,
            "cloud_samples": len(samples),
        }
    except Exception as exc:
        return {"available": False, "reason": f"Weather source unavailable ({type(exc).__name__})"}


def fetch_long_kp() -> dict[str, float]:
    result: dict[str, float] = {}
    try:
        text = get_text("https://services.swpc.noaa.gov/text/27-day-outlook.txt")
        pattern = re.compile(r"^\s*(\d{4})\s+([A-Z][a-z]{2})\s+(\d{2})\s+\d+\s+\d+\s+(\d+)")
        for line in text.splitlines():
            match = pattern.match(line)
            if not match:
                continue
            year, month_name, day, kp = match.groups()
            month = MONTHS.get(month_name)
            if month:
                result[f"{year}-{month}-{day}"] = float(kp)
    except Exception:
        pass
    return result


def fetch_short_kp() -> dict[str, float]:
    result: dict[str, float] = {}
    try:
        payload = get_json("https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json")
        for row in payload:
            if not isinstance(row, dict) or row.get("observed") != "predicted":
                continue
            timestamp = row.get("time_tag")
            kp = row.get("kp")
            if not timestamp or not isinstance(kp, (int, float)):
                continue
            day = timestamp[:10]
            result[day] = max(result.get(day, float("-inf")), float(kp))
    except Exception:
        pass
    return result


def weather_label(code) -> tuple[str, str]:
    if code == 0:
        return "☀️", "Clear"
    if code in (1, 2):
        return "🌤️", "Partly clear"
    if code == 3:
        return "☁️", "Overcast"
    if code in (45, 48):
        return "🌫️", "Fog"
    if code in (51, 53, 55, 56, 57):
        return "🌦️", "Drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "🌧️", "Rain / showers"
    if code in (71, 73, 75, 77, 85, 86):
        return "🌨️", "Snow / showers"
    if code in (95, 96, 99):
        return "⛈️", "Thunder"
    return "🌦️", "Forecast"


def metric(label: str, value: str) -> str:
    return f'<div class="metric"><div class="label">{html.escape(label)}</div><div class="metric-value">{html.escape(value)}</div></div>'


def render_card(stop: dict, weather: dict, kp_value: float | None, kp_source: str) -> str:
    date_value = dt.date.fromisoformat(stop["date"])
    date_label = date_value.strftime("%a · %b %d").replace(" 0", " ")

    if weather.get("available"):
        icon, condition = weather_label(weather.get("code"))
        temp = f'{round(weather["max_temp"])}° / {round(weather["min_temp"])}°C'
        rain = "—" if weather.get("rain") is None else f'{round(weather["rain"])}%'
        gust = "—" if weather.get("gust") is None else f'{round(weather["gust"])} km/h'
        cloud = "—" if weather.get("night_cloud") is None else f'{weather["night_cloud"]}%'
    else:
        icon, condition = "🗓️", "Forecast not available yet"
        temp = weather.get("reason", "Forecast unavailable")
        rain = gust = cloud = "—"

    night_cloud = weather.get("night_cloud") if weather.get("available") else None

    if night_cloud is None:
        decision_class = "pending"
        decision = "CLOUD DATA PENDING"
        note = weather.get("reason", "Night cloud data is not available yet.")
    elif kp_value is None:
        decision_class = "pending"
        decision = "KP DATA PENDING"
        note = f"Night cloud ~{night_cloud}% is available, but NOAA Kp is not."
    elif night_cloud <= 35:
        decision_class = "good"
        decision = "GOOD CLEAR-SKY WINDOW"
        note = f"Night cloud ~{night_cloud}% · Kp {kp_value:.1f}. Re-check IMO before driving."
    elif night_cloud <= 65:
        decision_class = "mixed"
        decision = "MIXED · FIND CLEAR GAP"
        note = f"Night cloud ~{night_cloud}% · Kp {kp_value:.1f}. Compare nearby clear gaps."
    else:
        decision_class = "blocked"
        decision = "CLOUD RISK"
        note = f"Night cloud ~{night_cloud}% · Kp {kp_value:.1f}. Aurora may be hidden by cloud."

    samples = weather.get("cloud_samples", 0) if weather.get("available") else 0
    if 0 < samples < 7:
        note += f" Cloud average is partial ({samples}/7 hourly samples available)."

    kp_text = "—" if kp_value is None else f"{kp_value:.1f}"

    return f"""
<section class="card">
  <div class="head">
    <div><div class="date">{html.escape(date_label)}</div><div class="location">📍 {html.escape(stop["location"])}</div></div>
    <div class="day">{html.escape(stop["day"])}</div>
  </div>
  <div class="weather">
    <div class="icon">{icon}</div>
    <div><div class="condition">{html.escape(condition)}</div><div class="temp">{html.escape(temp)}</div></div>
  </div>
  <div class="metrics">{metric("Rain", rain)}{metric("Gust", gust)}{metric("Night cloud", cloud)}</div>
  <div class="aurora">
    <div class="row"><div class="kp">🌌 Kp {kp_text}</div><div class="source">{html.escape(kp_source)}</div></div>
    <div class="decision {decision_class}">{html.escape(decision)}</div>
    <div class="note">{html.escape(note)}</div>
  </div>
</section>
"""


def build() -> None:
    long_kp = fetch_long_kp()
    short_kp = fetch_short_kp()
    weather_rows = [fetch_weather(stop) for stop in TRIPS]

    cards = []
    for stop, weather in zip(TRIPS, weather_rows):
        if stop["date"] in short_kp:
            kp_value = short_kp[stop["date"]]
            kp_source = "NOAA 72-hour predicted Kp"
        elif stop["date"] in long_kp:
            kp_value = long_kp[stop["date"]]
            kp_source = "NOAA 27-day outlook"
        else:
            kp_value = None
            kp_source = "NOAA forecast not published for this date"

        cards.append(render_card(stop, weather, kp_value, kp_source))

    now = dt.datetime.now(dt.timezone.utc)
    updated = now.strftime("%Y-%m-%d %H:%M UTC")

    document = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="3600">
<title>Iceland Live Weather + Aurora</title>
<style>
:root{{--line:rgba(255,255,255,.11);--muted:#9fb5ca;--text:#eef7ff;--green:#77efb5;--cyan:#65d9ff;--yellow:#ffd36f;--red:#ff8996}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(145deg,#06101d,#0a1830 58%,#071523);color:var(--text);font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}}.shell{{max-width:1280px;margin:auto;padding:18px}}.top{{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}}.title{{font-size:22px;font-weight:800}}.sub{{margin-top:5px;font-size:11px;color:var(--muted)}}.live{{display:flex;gap:8px;align-items:center;font-size:10px;color:#d8edf8}}.dot{{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 5px rgba(119,239,181,.08)}}.summary{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px;margin:12px 0}}.box,.card{{border:1px solid var(--line);border-radius:13px;background:rgba(255,255,255,.04)}}.box{{padding:10px}}.label{{font-size:9px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}}.big{{margin-top:4px;font-size:13px;font-weight:760}}.small{{margin-top:3px;font-size:10px;line-height:1.4;color:var(--muted)}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.card{{position:relative;overflow:hidden;min-height:280px;padding:12px;background:linear-gradient(180deg,rgba(15,35,57,.98),rgba(9,24,41,.98))}}.card:before{{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--cyan);opacity:.75}}.head{{display:flex;justify-content:space-between;gap:8px}}.date{{font-size:14px;font-weight:800}}.location{{margin-top:2px;font-size:11px;color:var(--muted)}}.day{{height:max-content;padding:4px 7px;border:1px solid var(--line);border-radius:999px;font-size:9px}}.weather{{display:flex;align-items:center;gap:9px;margin-top:11px}}.icon{{font-size:27px}}.condition{{font-size:12px;font-weight:760}}.temp{{margin-top:2px;font-size:11px;color:var(--muted)}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:10px}}.metric{{padding:7px;border:1px solid var(--line);border-radius:9px;background:rgba(255,255,255,.025)}}.metric-value{{margin-top:2px;font-size:12px;font-weight:760}}.aurora{{margin-top:10px;padding-top:9px;border-top:1px solid var(--line)}}.row{{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}}.kp{{font-size:13px;font-weight:800}}.source{{max-width:52%;font-size:8.5px;color:var(--muted);text-align:right}}.decision{{display:inline-block;margin-top:7px;padding:5px 7px;border-radius:8px;font-size:9px;font-weight:800}}.pending{{background:rgba(255,211,111,.13);color:var(--yellow)}}.good{{background:rgba(119,239,181,.13);color:var(--green)}}.mixed{{background:rgba(101,217,255,.12);color:var(--cyan)}}.blocked{{background:rgba(255,137,150,.12);color:var(--red)}}.note{{margin-top:6px;font-size:9.5px;line-height:1.4;color:var(--muted)}}.links{{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}}.links a{{padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:rgba(101,217,255,.07);color:#eaf7ff;font-size:10px;text-decoration:none}}.footer{{margin-top:10px;padding:10px;border:1px solid var(--line);border-radius:11px;background:rgba(255,255,255,.03);color:var(--muted);font-size:9.5px;line-height:1.5}}
@media(max-width:980px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}@media(max-width:640px){{.shell{{padding:12px}}.summary,.grid{{grid-template-columns:1fr}}}}
</style>
</head>
<body><main class="shell">
<div class="top"><div><div class="title">🌦️ Iceland Live Weather + Aurora</div><div class="sub">Server-generated forecast · Atlantic/Reykjavik · no browser API dependency</div></div><div class="live"><span class="dot"></span><span>Updated {html.escape(updated)}</span></div></div>
<div class="summary">
<div class="box"><div class="label">Weather source</div><div class="big">Open-Meteo · GitHub Actions</div><div class="small">Temperature · precipitation probability · gust · hourly cloud cover. Up to 16 forecast days.</div></div>
<div class="box"><div class="label">Aurora source</div><div class="big">NOAA SWPC Kp</div><div class="small">72-hour predicted Kp automatically overrides the 27-day outlook when available.</div></div>
<div class="box"><div class="label">Night cloud</div><div class="big">20:00 → 02:00 local</div><div class="small">Average total cloud cover across the available overnight hourly forecast.</div></div>
</div>
<div class="grid">{''.join(cards)}</div>
<div class="links"><a href="https://en.vedur.is/weather/forecasts/aurora/" target="_blank">🌌 IMO Aurora + Cloud</a><a href="https://en.vedur.is/weather/forecasts/areas/" target="_blank">🌦️ IMO Weather</a><a href="https://umferdin.is/en" target="_blank">🚗 Roads</a><a href="https://safetravel.is/" target="_blank">⚠️ SafeTravel</a></div>
<div class="footer"><b>Near-live dashboard:</b> GitHub Actions refreshes the source data every hour. This static page contains the fetched numbers directly, so Notion does not need permission to call Open-Meteo or NOAA. Dates outside the 16-day weather horizon remain explicitly unavailable.</div>
</main></body></html>"""

    OUTPUT.write_text(document, encoding="utf-8")


if __name__ == "__main__":
    build()
