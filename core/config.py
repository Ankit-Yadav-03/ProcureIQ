from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM Provider ──────────────────────────────────────────────
    LLM_PROVIDER: str = "gemini"          # only "gemini"

    # Gemini (cloud)
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ── App Config ────────────────────────────────────────────────
    SCRAPING_RETRY_COUNT: int = 3
    SCRAPING_DELAY_MIN: float = 1.5
    SCRAPING_DELAY_MAX: float = 4.0
    OUTLIER_IQR_MULTIPLIER: float = 2.5
    MIN_VALID_RESPONSES: int = 3
    DB_PATH: str = "data/procurement.db"
    LOG_LEVEL: str = "INFO"
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = "procurement_webhook_token"
    WHATSAPP_APP_SECRET: str = ""
    MAINTENANCE_RETENTION_RESPONSES_DAYS: int = 90
    MAINTENANCE_RETENTION_OUTREACH_DAYS: int = 30
    MAINTENANCE_RETENTION_FAILED_DAYS: int = 60


settings = Settings()

