from fastapi import APIRouter, HTTPException

from app.services.sachet import fetch_alert_by_identifier, fetch_active_alerts

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(lat: float | None = None, lon: float | None = None, radius_km: float | None = None):
    """Returns currently active disaster alerts. Pass lat/lon to compute distance
    and optionally filter to alerts within radius_km (e.g. 200km) of that point."""
    return await fetch_active_alerts(near_lat=lat, near_lon=lon, radius_km=radius_km)


@router.get("/{identifier}") 
async def get_alert(identifier: str):
    """Fetch one specific alert's full CAP detail by identifier."""
    alert = await fetch_alert_by_identifier(identifier)
    if not alert:
        raise HTTPException(status_code=404, detail=f"Alert '{identifier}' not found")
    return alert