from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from app.schemas import TranslateRequest, TranslateResponse
from app.services import sarvam

router = APIRouter(prefix="/translate", tags=["translate"])


@router.post("", response_model=TranslateResponse)
async def translate_text(req: TranslateRequest):
    # sarvam.translate is sync (SDK has no confirmed async client) — run it in
    # a threadpool so it doesn't block the event loop.
    translated = await run_in_threadpool(
        sarvam.translate, req.text, req.source_language, req.target_language
    )
    return TranslateResponse(translated_text=translated)