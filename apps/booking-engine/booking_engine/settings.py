"""Environment variable loading. The only module that reads os.environ."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = APP_ROOT / "config"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    database_url: str
    debounce_seconds: float
    environment: str
    ghl_webhook_secret: str

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(APP_ROOT / ".env")
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
            database_url=os.environ.get(
                "DATABASE_URL", f"sqlite:///{APP_ROOT / 'booking_engine.db'}"
            ).strip(),
            debounce_seconds=float(os.environ.get("DEBOUNCE_SECONDS", "8")),
            environment=os.environ.get("ENVIRONMENT", "development").strip(),
            ghl_webhook_secret=os.environ.get("GHL_WEBHOOK_SECRET", "").strip(),
        )


def env_is_set(name: str) -> bool:
    """True if the named env var holds a non-empty value. Never returns the value."""
    return bool(os.environ.get(name, "").strip())
