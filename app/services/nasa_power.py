"""
NASA POWER service — climate-trend stretch feature. Now async.
"""
import httpx

BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"


async def get_climate_data(lat: float, lon: float, start: str, end: str) -> dict:
    """Dates as 'YYYYMMDD' strings (NASA POWER's format, not ISO)."""
    params = {
        "parameters": "T2M,PRECTOTCORR",
        "community": "RE",
        "longitude": lon,
        "latitude": lat,
        "start": start,
        "end": end,
        "format": "JSON",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(BASE_URL, params=params)
    r.raise_for_status()
    return r.json()["properties"]["parameter"]