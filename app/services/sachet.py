"""
SACHET (NDMA) disaster alert service — Common Alerting Protocol data from
India's National Disaster Management Authority.

Both endpoints below are CONFIRMED WORKING against live captured responses
(see docs/SACHET_Implementation_Plan.md for the test evidence):

1. FetchAllAlertDetails (POST) — returns the full current alert list as JSON.
   Requires a session cookie obtained by first visiting the homepage — this
   is basic session-based access control, not a login. We bootstrap it with
   a single httpx.AsyncClient so the cookie carries from the GET to the POST.

2. FetchXMLFile (GET, ?identifier=) — returns one alert's full CAP 1.2 XML.
   Not needed for the list view (FetchAllAlertDetails already has everything
   useful), but kept here in case you want the fuller CAP structure for a
   single alert's detail view later.
"""
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from math import radians, sin, cos, sqrt, atan2
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger("weathergpt.sachet")

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))

BASE_URL = "https://sachet.ndma.gov.in"
LIST_URL = f"{BASE_URL}/cap_public_website/FetchAllAlertDetails"
SINGLE_URL = f"{BASE_URL}/cap_public_website/FetchXMLFile"
CAP_NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}


def _parse_ist_datetime(s: str) -> datetime | None:
    """Parses SACHET's Java Date.toString()-style format:
    'Tue Sep 01 14:04:00 IST 2026'"""
    if not s:
        return None
    try:
        cleaned = s.replace(" IST ", " ")
        naive = datetime.strptime(cleaned, "%a %b %d %H:%M:%S %Y")
        return naive.replace(tzinfo=IST)
    except ValueError:
        logger.warning("Could not parse SACHET datetime: %r", s)
        return None


def _parse_centroid(centroid: str) -> tuple[float, float] | None:
    """SACHET returns 'longitude,latitude' — verified against known India
    coordinate ranges during testing. Returns (lat, lon)."""
    if not centroid:
        return None
    try:
        lon_str, lat_str = centroid.split(",")
        return float(lat_str), float(lon_str)
    except (ValueError, AttributeError):
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return R * 2 * atan2(sqrt(a), sqrt(1 - a))


def _normalize(raw: dict) -> dict:
    """Converts SACHET's raw field names/formats into a clean schema."""
    lat_lon = _parse_centroid(raw.get("centroid", ""))
    return {
        "identifier": str(raw.get("identifier")),
        "event": raw.get("disaster_type"),
        "severity": raw.get("severity"),
        "severity_color": raw.get("severity_color"),
        "severity_level": raw.get("severity_level"),
        "headline": raw.get("warning_message"),
        "area_description": raw.get("area_description"),
        "source": raw.get("alert_source"),
        "language": raw.get("actual_lang"),
        "effective_start": _parse_ist_datetime(raw.get("effective_start_time")),
        "effective_end": _parse_ist_datetime(raw.get("effective_end_time")),
        "latitude": lat_lon[0] if lat_lon else None,
        "longitude": lat_lon[1] if lat_lon else None,
        "distance_km": None,
    }


async def fetch_active_alerts(
    near_lat: float | None = None,
    near_lon: float | None = None,
    radius_km: float = 200,
) -> list[dict]:
    """
    Fetches all current alerts from SACHET, filters out expired ones, and
    optionally filters to those within `radius_km` of (near_lat, near_lon).

    Confirmed working pattern: GET the homepage first (to receive the
    session cookie), then POST to FetchAllAlertDetails reusing the same
    client — httpx.AsyncClient carries cookies across requests made with
    the same instance automatically.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Step 1: bootstrap session cookie
            await client.get(BASE_URL + "/")

            # Step 2: fetch the alert list (empty POST body, per captured request)
            headers = {"Accept": "application/json, text/plain, */*"}
            r = await client.post(LIST_URL, headers=headers)
            r.raise_for_status()
            raw_alerts = r.json()
    except Exception:
        logger.exception("Failed to fetch SACHET alert list")
        return []

    now = datetime.now(IST)
    alerts = []
    for raw in raw_alerts:
        alert = _normalize(raw)
        # Skip expired alerts
        if alert["effective_end"] and alert["effective_end"] < now:
            continue
        # Distance calculation and optional filter
        if near_lat is not None and near_lon is not None:
            if alert["latitude"] is not None and alert["longitude"] is not None:
                dist = haversine_km(near_lat, near_lon, alert["latitude"], alert["longitude"])
                alert["distance_km"] = round(dist, 1)
                if radius_km is not None and dist > radius_km:
                    continue
            else:
                # If coords are missing from the alert centroid, skip when strict radius requested
                continue
        alerts.append(alert)

    if near_lat is not None and near_lon is not None:
        alerts.sort(key=lambda a: (a["distance_km"] if a["distance_km"] is not None else float("inf")))

    return alerts


async def fetch_alert_by_identifier(identifier: str) -> dict | None:
    """Fetches one alert's full CAP XML detail — useful for a detail view,
    not needed for the list. Confirmed working during initial testing."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(SINGLE_URL, params={"identifier": identifier})
    if r.status_code != 200:
        logger.warning("SACHET single-alert fetch failed for %s: HTTP %s", identifier, r.status_code)
        return None

    root = ET.fromstring(r.text)
    info = root.find("cap:info", CAP_NS)
    if info is None:
        return None

    def text(el, path, default=None):
        node = el.find(path, CAP_NS)
        return node.text if node is not None and node.text else default

    area = info.find("cap:area", CAP_NS)
    area_desc = text(area, "cap:areaDesc") if area is not None else None

    return {
        "identifier": text(root, "cap:identifier"),
        "sender": text(root, "cap:sender"),
        "sent": text(root, "cap:sent"),
        "event": text(info, "cap:event"),
        "severity": text(info, "cap:severity"),
        "urgency": text(info, "cap:urgency"),
        "certainty": text(info, "cap:certainty"),
        "effective": text(info, "cap:effective"),
        "expires": text(info, "cap:expires"),
        "headline": text(info, "cap:headline"),
        "instruction": text(info, "cap:instruction"),
        "area_desc": area_desc,
    }


def _format_alert_for_json(alert: dict) -> dict:
    res = dict(alert)
    if isinstance(res.get("effective_start"), datetime):
        res["effective_start"] = res["effective_start"].isoformat()
    if isinstance(res.get("effective_end"), datetime):
        res["effective_end"] = res["effective_end"].isoformat()
    return res


async def get_alerts_for_location(
    location_name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 200,
) -> list[dict]:
    """
    Fetches active alerts tailored for a location query:
    - If location is empty or 'India', returns all active alerts.
    - If lat/lon given, first filters within radius_km.
    - If radius filter yields nothing or for regional/state queries, searches text in area_description/headline.
    - Attaches CAP XML detail (like instruction) if available.
    - Formats all datetime objects as strings for JSON serialization.
    """
    loc_clean = (location_name or "").strip().lower()
    is_general_india = not loc_clean or loc_clean in ("india", "all", "all india", "nationwide")

    if is_general_india:
        alerts = await fetch_active_alerts()
    elif lat is not None and lon is not None:
        alerts = await fetch_active_alerts(near_lat=lat, near_lon=lon, radius_km=radius_km)
        # Fallback to text matching if radius search yielded no alerts for a region/state
        if not alerts:
            all_alerts = await fetch_active_alerts()
            alerts = [
                a for a in all_alerts
                if loc_clean in (a.get("area_description") or "").lower()
                or loc_clean in (a.get("headline") or "").lower()
            ]
    else:
        all_alerts = await fetch_active_alerts()
        alerts = [
            a for a in all_alerts
            if loc_clean in (a.get("area_description") or "").lower()
            or loc_clean in (a.get("headline") or "").lower()
        ]

    # Enrich top alerts with CAP XML detail (e.g. instruction, sender)
    for a in alerts[:5]:
        try:
            detail = await fetch_alert_by_identifier(a["identifier"])
            if detail:
                a["instruction"] = detail.get("instruction")
                if not a.get("source") and detail.get("sender"):
                    a["source"] = detail.get("sender")
        except Exception:
            pass

    return [_format_alert_for_json(a) for a in alerts]