"""
Geocoding cache — the fastest, lowest-risk win of the three optimizations.

Every /chat call needs lat/long for a location name. Without caching, that's
a full network round-trip to Open-Meteo's geocoding API on every single
message, even when 50 different users all ask about "Delhi" today. This
checks your database first.
"""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import SavedLocation
from app.services import open_meteo

logger = logging.getLogger("weathergpt.geocache")


async def get_or_geocode(db: Session, location_name: str) -> dict | None:
    """Returns {'name', 'latitude', 'longitude', 'country'} — from cache if we've
    seen this location before (case-insensitive match), otherwise geocodes fresh
    and saves it for next time."""
    normalized = location_name.strip().lower()

    cached = (
        db.query(SavedLocation)
        .filter(SavedLocation.name == normalized)
        .first()
    )
    if cached:
        cached.last_queried_at = datetime.utcnow()
        db.commit()
        return {
            "name": location_name,
            "latitude": cached.latitude,
            "longitude": cached.longitude,
            "country": cached.country or "India",
        }

    geo = await open_meteo.geocode_location(location_name)
    if not geo:
        return None

    try:
        db.add(SavedLocation(
            name=normalized,
            latitude=geo["latitude"],
            longitude=geo["longitude"],
            country=geo.get("country", "India"),
        ))
        db.commit()
    except Exception:
        # Don't let a caching failure (e.g. race with another request) break
        # the actual response — the geocode result itself is still valid.
        # But DO log it loudly, so a silent failure isn't invisible.
        logger.exception("Failed to save SavedLocation for '%s'", normalized)
        db.rollback()

    return geo