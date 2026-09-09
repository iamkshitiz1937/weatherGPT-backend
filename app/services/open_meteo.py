"""
Open-Meteo service — your primary weather data source. No API key needed.

Now async (httpx.AsyncClient instead of requests) so this call doesn't block
the event loop while waiting on the network — matters once multiple users
chat at the same time, and stacks with the other async service calls in the
/chat pipeline to shrink total wall-clock time.

Includes geocoding: turning a location NAME ("Delhi", "Ranchi") into lat/long.
See geocache.py for the caching layer that sits in front of this.
"""
import httpx

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://archive-api.open-meteo.com/v1/archive"


async def geocode_location(name: str) -> dict | None:
    """Returns {'name', 'latitude', 'longitude', 'country', 'admin1'} for the best match, prioritizing India."""
    params = {"name": name, "count": 10, "language": "en", "format": "json"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(GEOCODING_URL, params=params)
    r.raise_for_status()
    data = r.json()
    results = data.get("results")
    if not results:
        return None
    # Prioritize India matches
    in_results = [
        res for res in results
        if res.get("country_code") == "IN" or (res.get("country") or "").lower() == "india"
    ]
    top = in_results[0] if in_results else results[0]
    return {
        "name": top["name"],
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "country": top.get("country", ""),
        "admin1": top.get("admin1", ""),
    }


async def reverse_geocode(lat: float, lon: float) -> dict:
    """Attempts to reverse geocode lat/lon into a human-readable city/district name."""
    try:
        url = "https://nominatim.openstreetmap.org/reverse"
        headers = {"User-Agent": "WeatherGPT/1.0 (weather app)"}
        params = {"lat": lat, "lon": lon, "format": "json"}
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code == 200:
                data = r.json()
                address = data.get("address", {})
                name = (
                    address.get("city")
                    or address.get("town")
                    or address.get("village")
                    or address.get("suburb")
                    or address.get("state_district")
                    or address.get("county")
                    or data.get("name")
                    or "Your Location"
                )
                state = address.get("state", "")
                country = address.get("country", "India")
                display = f"{name}, {state}" if state and name != state else name
                return {
                    "name": display,
                    "latitude": lat,
                    "longitude": lon,
                    "country": country,
                }
    except Exception:
        pass

    return {
        "name": "Your Location",
        "latitude": lat,
        "longitude": lon,
        "country": "India",
    }


async def get_current_weather(lat: float, lon: float) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,surface_pressure,wind_speed_10m,wind_direction_10m",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FORECAST_URL, params=params)
    r.raise_for_status()
    payload = r.json()
    curr = payload.get("current", {})

    temp = curr.get("temperature_2m", 0.0)
    wind = curr.get("wind_speed_10m", 0.0)
    code = curr.get("weather_code", 0)

    return {
        "temperature": temp,
        "windspeed_kmh": wind,  # Open-Meteo wind_speed_10m default unit is km/h (no wind_speed_unit param sent)
        "winddirection": curr.get("wind_direction_10m", 0),
        "weathercode": code,
        "humidity": curr.get("relative_humidity_2m", 0),
        "apparent_temperature": curr.get("apparent_temperature", temp),
        "precipitation": curr.get("precipitation", 0.0),
        "surface_pressure": curr.get("surface_pressure"),
        "is_day": curr.get("is_day", 1),
        "time": curr.get("time"),
    }


async def get_daily_forecast(lat: float, lon: float, days: int = 5) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "Asia/Kolkata",
        "forecast_days": days,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FORECAST_URL, params=params)
    r.raise_for_status()
    return r.json()["daily"]


def compute_historical_summary(daily: dict) -> dict:
    """Computes server-side statistical aggregates over daily weather arrays."""
    times = daily.get("time") or []
    max_temps = daily.get("temperature_2m_max") or []
    min_temps = daily.get("temperature_2m_min") or []
    precips = daily.get("precipitation_sum") or []

    valid_max = [(t, v) for t, v in zip(times, max_temps) if v is not None]
    valid_min = [(t, v) for t, v in zip(times, min_temps) if v is not None]
    valid_precip = [(t, v) for t, v in zip(times, precips) if v is not None]

    summary = {
        "range_days": len(times),
        "days_with_data": len(valid_max),
    }

    if valid_max:
        max_item = max(valid_max, key=lambda x: x[1])
        summary["max_temp"] = round(max_item[1], 1)
        summary["max_temp_date"] = max_item[0]
        summary["avg_max_temp"] = round(sum(v for _, v in valid_max) / len(valid_max), 1)

    if valid_min:
        min_item = min(valid_min, key=lambda x: x[1])
        summary["min_temp"] = round(min_item[1], 1)
        summary["min_temp_date"] = min_item[0]
        summary["avg_min_temp"] = round(sum(v for _, v in valid_min) / len(valid_min), 1)

    if valid_precip:
        summary["total_precip_mm"] = round(sum(v for _, v in valid_precip), 1)
        summary["avg_daily_precip_mm"] = round(summary["total_precip_mm"] / len(valid_precip), 2)
        max_precip_item = max(valid_precip, key=lambda x: x[1])
        summary["max_daily_precip_mm"] = round(max_precip_item[1], 1)
        summary["max_precip_date"] = max_precip_item[0]

    return summary


async def get_historical(lat: float, lon: float, start_date: str, end_date: str, compact_if_long: bool = False) -> dict:
    """Dates as 'YYYY-MM-DD' strings. Automatically attaches pre-computed summary aggregates."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date,
        "end_date": end_date,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
        "timezone": "Asia/Kolkata",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(HISTORICAL_URL, params=params)
    r.raise_for_status()
    daily = r.json().get("daily", {})
    summary = compute_historical_summary(daily)

    # For long date ranges (> 30 days) in LLM chat contexts, provide the pre-computed summary and compact sample
    if compact_if_long and len(daily.get("time", [])) > 30:
        return {
            "summary": summary,
            "period": f"{start_date} to {end_date}",
            "sample_daily": {
                "first_3_days": {k: v[:3] for k, v in daily.items()},
                "last_3_days": {k: v[-3:] for k, v in daily.items()},
            },
        }

    return {
        "summary": summary,
        **daily,
    }