"""
OpenWeatherMap service — secondary weather source. Now async, same reasoning
as open_meteo.py.
"""
import httpx

from app.config import settings

CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


async def get_current_weather(lat: float, lon: float) -> dict:
    params = {"lat": lat, "lon": lon, "appid": settings.openweathermap_api_key, "units": "metric"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(CURRENT_URL, params=params)
    r.raise_for_status()
    return r.json()


async def get_forecast(lat: float, lon: float) -> dict:
    params = {"lat": lat, "lon": lon, "appid": settings.openweathermap_api_key, "units": "metric"}
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(FORECAST_URL, params=params)
    r.raise_for_status()
    return r.json()