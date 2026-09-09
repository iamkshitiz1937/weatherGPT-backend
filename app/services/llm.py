"""
LLM service — the actual "AI" in WeatherGPT. Two jobs, kept strictly separate:

1. understand_query(): free text -> structured params (location, query_type, dates).
   Uses `settings.intent_model` — a lighter/faster model, since this is a simple
   extraction task run on EVERY message. This is one of the two biggest latency
   wins: don't pay your heaviest model's latency for a job a small model does fine.
2. generate_response() / generate_response_stream(): structured weather data ->
   natural language answer, using your main `settings.llm_model`. The streaming
   variant yields text chunks as they're generated, so the frontend can start
   rendering before the full answer is ready — this fixes perceived latency even
   when total generation time is unchanged.

Both never let the model invent weather numbers — real data is always injected
into the prompt first.

Uses AsyncOpenAI so the whole /chat pipeline can run without blocking the event
loop. Works against any OpenAI-compatible endpoint (see .env.example for Gemini/
Groq notes) — swapping providers is a .env change, not a code change.
"""
import asyncio
import json
import logging
import re
from datetime import date

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("weathergpt.llm")
client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)


async def _chat_completion_with_retry(**kwargs):
    for attempt in range(5):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "503" in err_str:
                match = re.search(r"retry in ([\d\.]+)s", err_str, re.IGNORECASE)
                wait_sec = float(match.group(1)) + 1.0 if match else (10.0 * (attempt + 1))
                logger.warning("Rate limit on %s, retrying after %.1fs (attempt %d/5)", kwargs.get("model"), wait_sec, attempt + 1)
                await asyncio.sleep(wait_sec)
            else:
                raise
    return await client.chat.completions.create(**kwargs)


WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather_query",
        "description": "Extract the location and type of weather info the user wants.",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Single place name if only one place is asked about, e.g. 'Delhi', 'Ranchi'. If asking about alerts nationwide across India, use 'India'.",
                },
                "locations": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of place names if multiple locations are asked about or compared, e.g. ['Bengaluru', 'Hyderabad'].",
                },
                "query_type": {
                    "type": "string",
                    "enum": ["current", "forecast", "historical", "alerts"],
                    "description": "current = right now, forecast = next few days, historical = past dates/trend, alerts = disaster warnings/alerts (floods, cyclones, heavy rain, NDMA/SACHET alerts).",
                },
                "forecast_days": {
                    "type": "integer",
                    "description": "Only for forecast queries — how many days ahead (default 5).",
                },
                "historical_start": {
                    "type": "string",
                    "description": "Only for historical queries — start date YYYY-MM-DD.",
                },
                "historical_end": {
                    "type": "string",
                    "description": "Only for historical queries — end date YYYY-MM-DD.",
                },
                "compare_to_last_year": {
                    "type": "boolean",
                    "description": "Set to true if user asks to compare current period/month/dates to the same period last year (year-over-year comparison), e.g. 'Compare this month's average temperature in Chennai to the same period last year'.",
                },
            },
            "required": ["query_type"],
        },
    },
}


async def understand_query(user_message: str) -> dict:
    """Returns structured params extracted from free text. Raises if the LLM
    doesn't call the tool (e.g. an off-topic message) — the router handles that.
    Uses the lighter intent_model — swap LLM_INTENT_MODEL in .env if extraction
    quality suffers, but a small model is usually plenty for this structured task."""
    response = await _chat_completion_with_retry(
        model=settings.intent_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract weather and disaster alert query details from user messages, which may be "
                    "in English or any Indian language. Always call get_weather_query.\n"
                    "If comparing multiple cities, provide them in 'locations'.\n"
                    "If comparing to the same period last year (e.g. 'Compare this month's average temperature in Chennai to the same period last year', 'vs last year', 'same period last year'), set query_type='historical', set historical_start and historical_end for the current period (e.g. start of this month to today), and set compare_to_last_year=true.\n"
                    "If asking about alerts with no specific city or nationwide across India, set location to 'India' and query_type to 'alerts'.\n"
                    f"Today's date is {date.today().isoformat()}."
                ),
            },
            {"role": "user", "content": user_message},
        ],
        tools=[WEATHER_TOOL],
        tool_choice="required",
    )
    tool_call = response.choices[0].message.tool_calls[0]
    parsed = json.loads(tool_call.function.arguments)

    # Fallback heuristic for year-over-year phrasing if tool missed the boolean
    msg_low = user_message.lower()
    if "last year" in msg_low or "same period" in msg_low or "same time" in msg_low:
        parsed["compare_to_last_year"] = True
        if parsed.get("query_type") != "historical":
            parsed["query_type"] = "historical"

    locs = parsed.get("locations")
    single_loc = parsed.get("location")
    if locs and isinstance(locs, list):
        parsed["locations"] = [str(l).strip() for l in locs if str(l).strip()]
        if not parsed.get("location") and parsed["locations"]:
            parsed["location"] = parsed["locations"][0]
    elif single_loc:
        parsed["locations"] = [single_loc.strip()]
        parsed["location"] = single_loc.strip()
    elif parsed.get("query_type") == "alerts":
        parsed["location"] = "India"
        parsed["locations"] = ["India"]
    else:
        parsed["locations"] = []
    return parsed


def _response_messages(user_message: str, weather_data: dict, location_name: str, country: str = "India"):
    return [
        {
            "role": "system",
            "content": (
                "You are WeatherGPT, a factual, grounded weather and disaster alert assistant for India.\n\n"
                "Strict Grounding Rules:\n"
                "1. Answer ONLY using the real data provided below. Never invent, estimate, or extrapolate numbers, dates, temperatures, wind speeds, or percentages.\n"
                "2. Location Transparency: If the resolved location's country is NOT India (or is ambiguous), you MUST explicitly state which place and country was resolved (e.g., 'I found Kochi in Japan — did you mean Kochi, Kerala?') rather than silently answering as if it were the intended Indian city.\n"
                "3. Disaster Alert Safety Instructions: You may ONLY present safety instructions as coming from the issuing agency if the alert object has a non-empty, specific 'instruction' field. If 'instruction' is null, empty, or missing, state clearly that the alert does not include specific safety instructions from the issuing agency. Never invent or attribute generic boilerplate (such as 'please follow SDMA guidelines') to an agency. If you offer general precautions of your own, frame them strictly as general suggestions (e.g., 'As a general precaution, consider...').\n"
                "4. Long-Range Historical Aggregation: When 'summary' fields are provided in historical weather data (e.g. 'total_precip_mm', 'avg_max_temp', 'max_temp', 'min_temp', 'range_days'), use these pre-computed summary figures directly. Never claim 'I do not have that data' when the requested statistic is present in the summary.\n"
                "5. Year-over-Year Comparisons: When 'this_year' and 'last_year' datasets are provided, report the values from both periods and state the exact difference between them directly from the data.\n"
                "6. Zero/Empty Counting vs. Missing Data: Before saying data is 'unavailable' or 'I don't have that,' check if the field exists in the payload. If the field is present, count or filter over it — never use 'I don't have that data' when the result of filtering is zero. Example: When asked 'How many red/severe alerts are active across India right now?', check the active alerts or 'severity_summary'. If there are 0 red alerts, answer: 'There are currently 0 active red or severe alerts across India.' Do NOT say 'I do not have data for red alerts.'\n"
                "7. Missing Attributes & Capabilities: If a field is genuinely absent from the schema (such as UV index, air quality/AQI, hourly forecasts, or historical disaster alerts), explicitly state that you do not have that data.\n"
                "8. Limits & Traps: If a forecast is requested beyond 16 days, state that forecasts only extend up to 16 days. If geocoding fails to resolve a location (the payload will contain an error field), state that the location could not be found — but never claim a location is fictional if the weather data payload contains real values (temperature, windspeed_kmh, etc.), regardless of the place name.\n"
                "8b. Wind Speed Unit: The field 'windspeed_kmh' is ALWAYS in kilometres per hour (km/h). Never state a different unit for this field.\n"
                "9. Formatting: Plain text only — no markdown, no asterisks, no bullet points, no headers. Be concise and warm."
            ),
        },
        {
            "role": "user",
            "content": (
                f"User asked: {user_message}\n"
                f"Location: {location_name} (Country: {country})\n"
                f"Real weather data: {json.dumps(weather_data)}\n"
                "Write a short, natural answer strictly adhering to the rules."
            ),
        },
    ]


async def generate_response(user_message: str, weather_data: dict, location_name: str, country: str = "India") -> str:
    """Non-streaming — used when the final answer needs translation afterwards,
    since Sarvam AI translates a complete text, not a live token stream."""
    response = await _chat_completion_with_retry(
        model=settings.llm_model,
        messages=_response_messages(user_message, weather_data, location_name, country),
    )
    return response.choices[0].message.content


async def generate_response_stream(user_message: str, weather_data: dict, location_name: str, country: str = "India"):
    """Streaming — used for English-language responses, where there's no
    translation step in the way. Yields text chunks as the model produces them."""
    stream = await client.chat.completions.create(
        model=settings.llm_model,
        messages=_response_messages(user_message, weather_data, location_name, country),
        stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta