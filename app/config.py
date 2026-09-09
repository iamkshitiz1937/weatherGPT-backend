"""
Central app configuration. Loads everything from environment variables (.env).
Import `settings` anywhere you need a config value — never read os.environ directly
elsewhere in the app, so there's exactly one place to check when something's missing.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM provider (OpenAI-compatible; swap base_url to use Gemini/Groq/etc. instead)
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # A lighter/faster model just for intent extraction (structured JSON, low
    # reasoning need). Defaults to the same model if you don't set one — but
    # setting a smaller model here (e.g. Gemini's flash-lite / 8b variant) is
    # one of the biggest latency wins available, since this call happens on
    # every single chat message.
    llm_intent_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"

    # Weather sources
    openweathermap_api_key: str = ""

    # Translation
    sarvam_api_key: str = ""

    # Database
    database_url: str = "sqlite:///./weathergpt.db"

    # CORS — add your deployed frontend URL here later
    frontend_origins: str = "http://localhost:3000"

    class Config:
        env_file = ".env"

    @property
    def intent_model(self) -> str:
        return self.llm_intent_model or self.llm_model


settings = Settings()