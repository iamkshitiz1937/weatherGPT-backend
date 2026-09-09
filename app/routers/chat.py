"""
The main endpoint(s). Two versions of the same pipeline:

- POST /chat        — original, full JSON response. Use this for non-English
                       queries (translation needs the complete text first).
- POST /chat/stream  — streams the answer as it's generated. Use this for
                       English queries where the frontend wants low perceived
                       latency (start rendering text immediately instead of
                       waiting for the whole thing).

Both follow the same grounded pipeline from the architecture doc:
  user message -> LLM (understand) -> geocode (cached) -> fetch real data ->
  LLM (phrase answer) -> translate if needed -> log -> respond
"""
import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import get_db
from app.models import QueryLog
from app.schemas import ChatRequest, ChatResponse
from app.services import open_meteo, sarvam, sachet
from app.services.geocache import get_or_geocode
from app.services.llm import understand_query, generate_response, generate_response_stream

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger("weathergpt.chat")

LANGUAGE_MAP = {
    "hindi": "hi-IN", "hi": "hi-IN",
    "tamil": "ta-IN", "ta": "ta-IN",
    "telugu": "te-IN", "te": "te-IN",
    "bengali": "bn-IN", "bn": "bn-IN",
    "marathi": "mr-IN", "mr": "mr-IN",
    "english": "en-IN", "en": "en-IN",
}


def _is_english(target_lang: str) -> bool:
    return target_lang.lower() in ("en", "en-in", "english", "")


async def _understand_and_fetch(message: str, db: Session):
    """Shared by both endpoints: parse intent, geocode (cached), fetch real data.
    Returns (parsed, geo, weather_data, query_type)."""
    try:
        parsed = await understand_query(message)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Couldn't understand the query: {e}")

    query_type = parsed.get("query_type", "current")
    locations = parsed.get("locations") or []
    if not locations and parsed.get("location"):
        locations = [parsed["location"]]

    if query_type == "alerts":
        loc_name = locations[0] if locations else "India"
        if not loc_name or loc_name.lower() in ("india", "all", "nationwide", "all india"):
            alerts = await sachet.get_alerts_for_location("India")
            geo = {"name": "India", "country": "India"}
            target_disp = "India"
        else:
            geo = await get_or_geocode(db, loc_name)
            lat = geo["latitude"] if geo else None
            lon = geo["longitude"] if geo else None
            display_name = geo["name"] if geo else loc_name
            alerts = await sachet.get_alerts_for_location(loc_name, lat=lat, lon=lon)
            target_disp = display_name
            if not geo:
                geo = {"name": display_name, "country": "India"}

        red_count = sum(1 for a in alerts if (a.get("severity_color") or "").lower() == "red" or (a.get("severity") or "").lower() in ("red", "severe", "extreme"))
        orange_count = sum(1 for a in alerts if (a.get("severity_color") or "").lower() == "orange" or "orange" in (a.get("severity") or "").lower())
        yellow_count = sum(1 for a in alerts if (a.get("severity_color") or "").lower() == "yellow" or "yellow" in (a.get("severity") or "").lower())

        weather_data = {
            "target_location": target_disp,
            "disaster_alerts": alerts,
            "total_active_alerts": len(alerts),
            "severity_summary": {
                "red_severe_alerts_count": red_count,
                "orange_alerts_count": orange_count,
                "yellow_watch_count": yellow_count,
                "total_alerts": len(alerts),
            },
        }
        return parsed, geo, weather_data, query_type

    # Multi-location comparison
    if len(locations) > 1:
        weather_data = {}
        names = []
        for loc in locations:
            geo_item = await get_or_geocode(db, loc)
            if not geo_item:
                weather_data[loc] = {"error": f"Location '{loc}' could not be found."}
                names.append(loc)
                continue
            loc_disp = geo_item["name"]
            names.append(loc_disp)
            if query_type == "current":
                weather_data[loc_disp] = await open_meteo.get_current_weather(
                    geo_item["latitude"], geo_item["longitude"]
                )
            elif query_type == "forecast":
                days = parsed.get("forecast_days") or 5
                if days > 16:
                    weather_data[loc_disp] = {"error": f"Forecasts are only available up to 16 days. A {days}-day forecast is beyond the supported forecast horizon."}
                else:
                    try:
                        weather_data[loc_disp] = await open_meteo.get_daily_forecast(
                            geo_item["latitude"], geo_item["longitude"], days
                        )
                    except Exception as e:
                        weather_data[loc_disp] = {"error": f"Forecast data unavailable: {e}"}
            else:  # historical
                start = parsed.get("historical_start") or (date.today() - timedelta(days=7)).isoformat()
                end = parsed.get("historical_end") or date.today().isoformat()
                try:
                    weather_data[loc_disp] = await open_meteo.get_historical(
                        geo_item["latitude"], geo_item["longitude"], start, end, compact_if_long=True
                    )
                except Exception as e:
                    weather_data[loc_disp] = {"error": f"Historical weather data unavailable for the specified range: {e}"}
        return parsed, {"name": ", ".join(names), "country": "India"}, weather_data, query_type

    # Single location
    location_name = locations[0] if locations else (parsed.get("location") or "Delhi")
    geo = await get_or_geocode(db, location_name)
    if not geo:
        return parsed, {"name": location_name, "country": "Unknown"}, {"error": f"Location '{location_name}' could not be found or resolved in the geocoding database."}, query_type

    if query_type == "current":
        try:
            weather_data = await open_meteo.get_current_weather(geo["latitude"], geo["longitude"])
        except Exception as e:
            weather_data = {"error": f"Current weather data unavailable: {e}"}
    elif query_type == "forecast":
        days = parsed.get("forecast_days") or 5
        if days > 16:
            weather_data = {"error": f"Forecasts are only available up to 16 days. A {days}-day forecast is beyond the supported forecast horizon."}
        else:
            try:
                weather_data = await open_meteo.get_daily_forecast(geo["latitude"], geo["longitude"], days)
            except Exception as e:
                weather_data = {"error": f"Forecast data unavailable: {e}"}
    else:  # historical
        is_yoy = parsed.get("compare_to_last_year") or "last year" in message.lower() or "same period" in message.lower()
        start = parsed.get("historical_start") or date(date.today().year, date.today().month, 1).isoformat()
        end = parsed.get("historical_end") or date.today().isoformat()

        if is_yoy:
            try:
                start_dt = date.fromisoformat(start)
                end_dt = date.fromisoformat(end)
                last_year_start = start_dt.replace(year=start_dt.year - 1).isoformat()
                last_year_end = end_dt.replace(year=end_dt.year - 1).isoformat()
            except Exception:
                last_year_start = (date.today() - timedelta(days=365 + 7)).isoformat()
                last_year_end = (date.today() - timedelta(days=365)).isoformat()

            try:
                this_year_data = await open_meteo.get_historical(geo["latitude"], geo["longitude"], start, end, compact_if_long=True)
                last_year_data = await open_meteo.get_historical(geo["latitude"], geo["longitude"], last_year_start, last_year_end, compact_if_long=True)
                weather_data = {
                    "comparison_type": "year_over_year",
                    "this_year": {
                        "period": f"{start} to {end}",
                        "summary": this_year_data.get("summary"),
                    },
                    "last_year": {
                        "period": f"{last_year_start} to {last_year_end}",
                        "summary": last_year_data.get("summary"),
                    },
                }
            except Exception as e:
                weather_data = {"error": f"Failed to fetch year-over-year comparison data: {e}"}
        else:
            try:
                weather_data = await open_meteo.get_historical(geo["latitude"], geo["longitude"], start, end, compact_if_long=True)
            except Exception as e:
                weather_data = {"error": f"Historical weather data unavailable for the specified range: {e}"}

    return parsed, geo, weather_data, query_type


def _log(db: Session, message: str, lang: str, location: str, query_type: str, response_text: str):
    try:
        db.add(QueryLog(
            user_message=message,
            detected_language=lang,
            location_name=location,
            query_type=query_type,
            response_text=response_text,
        ))
        db.commit()
    except Exception:
        logger.exception("Failed to save QueryLog for message: %r", message)
        db.rollback()


@router.post("", response_model=ChatResponse)
async def chat(req: ChatRequest, db: Session = Depends(get_db)):
    parsed, geo, weather_data, query_type = await _understand_and_fetch(req.message, db)
    country = geo.get("country", "India")

    answer_en = await generate_response(req.message, weather_data, geo["name"], country=country)

    target_lang = req.language or "en"
    final_answer = answer_en
    if not _is_english(target_lang):
        target_code = LANGUAGE_MAP.get(target_lang.lower(), target_lang)
        final_answer = await run_in_threadpool(
            sarvam.translate, answer_en, "en-IN", target_code
        )

    _log(db, req.message, target_lang, geo["name"], query_type, final_answer)

    return ChatResponse(
        response=final_answer,
        language=target_lang,
        location_name=geo["name"],
        query_type=query_type,
        raw_data=weather_data if isinstance(weather_data, dict) else None,
    )


@router.post("/stream")
async def chat_stream(req: ChatRequest, db: Session = Depends(get_db)):
    parsed, geo, weather_data, query_type = await _understand_and_fetch(req.message, db)
    country = geo.get("country", "India")
    target_lang = req.language or "en"

    async def event_generator():
        collected = []

        if _is_english(target_lang):
            async for chunk in generate_response_stream(req.message, weather_data, geo["name"], country=country):
                collected.append(chunk)
                yield f"data: {json.dumps({'delta': chunk})}\n\n"
        else:
            # Translation needs the complete English text first, so this path
            # can't stream token-by-token — it generates fully, translates,
            # then sends the whole translated answer as one final chunk. If
            # token-level streamed translation matters later, check Sarvam's
            # streaming support specifically before assuming it's unavailable.
            answer_en = await generate_response(req.message, weather_data, geo["name"], country=country)
            target_code = LANGUAGE_MAP.get(target_lang.lower(), target_lang)
            final_answer = await run_in_threadpool(
                sarvam.translate, answer_en, "en-IN", target_code
            )
            collected.append(final_answer)
            yield f"data: {json.dumps({'delta': final_answer})}\n\n"

        full_text = "".join(collected)
        _log(db, req.message, target_lang, geo["name"], query_type, full_text)
        yield f"data: {json.dumps({'done': True, 'location_name': geo['name'], 'query_type': query_type})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")