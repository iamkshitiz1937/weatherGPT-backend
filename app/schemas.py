"""
Pydantic schemas — these define what valid requests/responses look like and
give you automatic validation + interactive docs at /docs for free.
"""
from typing import Optional, Literal
from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    # If you already know the user's preferred language (e.g. from a UI toggle),
    # pass it here. Otherwise the LLM will detect it from the message itself.
    language: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    language: str
    location_name: Optional[str] = None
    query_type: Optional[str] = None
    raw_data: Optional[dict] = None


class WeatherCurrentResponse(BaseModel):
    location_name: str
    latitude: float
    longitude: float
    temperature_c: float
    windspeed_kmh: float
    condition: Optional[str] = None


class TranslateRequest(BaseModel):
    text: str
    source_language: str = "auto"
    target_language: str = "hi-IN"


class TranslateResponse(BaseModel):
    translated_text: str


class WeatherQueryType(BaseModel):
    """The structured shape the LLM must extract from a free-text query."""
    location: Optional[str] = None
    locations: Optional[list[str]] = None
    query_type: Literal["current", "forecast", "historical", "alerts"]
    forecast_days: Optional[int] = 5
    historical_start: Optional[str] = None  # YYYY-MM-DD
    historical_end: Optional[str] = None
