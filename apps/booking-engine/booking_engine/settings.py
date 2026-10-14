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
    admin_secret: str = ""      # unset = the admin routes reject every request

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
            admin_secret=os.environ.get("ADMIN_SECRET", "").strip(),
        )

    def missing_for_server(self) -> list[str]:
        """Env vars the webhook server cannot run without and that are not set."""
        required = {
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "GHL_WEBHOOK_SECRET": self.ghl_webhook_secret,
        }
        return [name for name, value in required.items() if not value]


def env_is_set(name: str) -> bool:
    """True if the named env var holds a non-empty value. Never returns the value."""
    return bool(os.environ.get(name, "").strip())


def secret_from_env(name: str) -> str:
    """The value of a secret env var (a client's GHL token). Never log the result."""
    return os.environ.get(name, "").strip()
