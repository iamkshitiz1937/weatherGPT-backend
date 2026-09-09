"""
Sarvam AI Translation service — Indian language translation using Sarvam AI API.
Endpoint: https://api.sarvam.ai/translate
Docs: https://docs.sarvam.ai/
"""
import logging
import httpx

from app.config import settings

logger = logging.getLogger("weathergpt.sarvam")

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"


def translate(
    text: str,
    source_language: str = "auto",
    target_language: str = "hi-IN",
    model: str = "mayura:v1",
    mode: str = "formal",
) -> str:
    """
    Translates text using Sarvam AI Translate API.

    Synchronous function — designed to be called via run_in_threadpool in FastAPI
    to prevent event loop blocking.
    """
    if not text or not text.strip():
        return text

    api_key = settings.sarvam_api_key
    if not api_key:
        logger.error("SARVAM_API_KEY is not configured in settings")
        raise RuntimeError(
            "SARVAM_API_KEY is not set. Please set SARVAM_API_KEY in your .env file."
        )

    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "input": text,
        "source_language_code": source_language,
        "target_language_code": target_language,
        "model": model,
        "mode": mode,
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(SARVAM_TRANSLATE_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            translated = data.get("translated_text", "")
            return translated
    except httpx.HTTPStatusError as e:
        logger.error(
            "Sarvam AI translation HTTP error %s: %s",
            e.response.status_code,
            e.response.text,
        )
        raise RuntimeError(
            f"Sarvam AI Translation failed with status {e.response.status_code}: {e.response.text}"
        ) from e
    except Exception as e:
        logger.exception("Sarvam AI translation unexpected error")
        raise RuntimeError(f"Sarvam AI Translation error: {e}") from e


async def translate_async(
    text: str,
    source_language: str = "auto",
    target_language: str = "hi-IN",
    model: str = "mayura:v1",
    mode: str = "formal",
) -> str:
    """
    Translates text using Sarvam AI Translate API asynchronously.
    """
    if not text or not text.strip():
        return text

    api_key = settings.sarvam_api_key
    if not api_key:
        logger.error("SARVAM_API_KEY is not configured in settings")
        raise RuntimeError(
            "SARVAM_API_KEY is not set. Please set SARVAM_API_KEY in your .env file."
        )

    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "input": text,
        "source_language_code": source_language,
        "target_language_code": target_language,
        "model": model,
        "mode": mode,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(SARVAM_TRANSLATE_URL, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data.get("translated_text", "")
    except httpx.HTTPStatusError as e:
        logger.error(
            "Sarvam AI translation HTTP error %s: %s",
            e.response.status_code,
            e.response.text,
        )
        raise RuntimeError(
            f"Sarvam AI Translation failed with status {e.response.status_code}: {e.response.text}"
        ) from e
    except Exception as e:
        logger.exception("Sarvam AI translation unexpected error")
        raise RuntimeError(f"Sarvam AI Translation error: {e}") from e