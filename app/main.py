"""
App entrypoint. Run with: uvicorn app.main:app --reload
Interactive docs land at http://localhost:8000/docs automatically — use that
to test every endpoint by hand before wiring up the frontend.
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import Base, engine
from app.routers import weather, chat, translate, alerts

# Without this, logger.exception() calls in the services/routers go nowhere —
# this is what makes DB write failures actually show up in your terminal
# instead of failing completely silently.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Creates weathergpt.db (SQLite) and its tables on first run — no migration
# tool needed for a hackathon timeline.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="WeatherGPT API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(weather.router)
app.include_router(chat.router)
app.include_router(translate.router)
app.include_router(alerts.router)


@app.get("/health")
def health():
    return {"status": "ok"}