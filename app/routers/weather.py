"""
Direct weather endpoints — now async, using the geocoding cache.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import open_meteo
from app.services.geocache import get_or_geocode

router = APIRouter(prefix="/weather", tags=["weather"])


@router.get("/current")
async def current_weather(
    location: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    db: Session = Depends(get_db),
):
    if lat is not None and lon is not None:
        geo = await open_meteo.reverse_geocode(lat, lon)
        data = await open_meteo.get_current_weather(lat, lon)
        return {"location": geo, "current_weather": data}

    if not location:
        raise HTTPException(status_code=400, detail="Must provide either 'location' or both 'lat' and 'lon'")

    geo = await get_or_geocode(db, location)
    if not geo:
        raise HTTPException(status_code=404, detail=f"Location '{location}' not found")
    data = await open_meteo.get_current_weather(geo["latitude"], geo["longitude"])
    return {"location": geo, "current_weather": data}


@router.get("/forecast")
async def forecast(location: str, days: int = 5, db: Session = Depends(get_db)):
    geo = await get_or_geocode(db, location)
    if not geo:
        raise HTTPException(status_code=404, detail=f"Location '{location}' not found")
    data = await open_meteo.get_daily_forecast(geo["latitude"], geo["longitude"], days)
    return {"location": geo, "forecast": data}


@router.get("/historical")
async def historical(location: str, start_date: str, end_date: str, db: Session = Depends(get_db)):
    geo = await get_or_geocode(db, location)
    if not geo:
        raise HTTPException(status_code=404, detail=f"Location '{location}' not found")
    data = await open_meteo.get_historical(geo["latitude"], geo["longitude"], start_date, end_date)
    return {"location": geo, "historical": data}