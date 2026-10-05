"""Per-client config loading + validation.

One YAML file per roofer in config/clients/. Loading is strict about shape
(every field present, right type, real timezone). `require_live=True` — used
by the webhook server — additionally refuses "..." / "XXXX" placeholders and
a missing GHL token, so a half-filled client can never go live. The simulator
loads with require_live=False because it never touches GHL.
"""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from booking_engine.settings import CONFIG_DIR, env_is_set


class ConfigError(Exception):
    """A client config is missing, malformed, or not ready to go live."""


class StageIds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texted: str = Field(min_length=1)
    replied: str = Field(min_length=1)
    booked: str = Field(min_length=1)
    no_response: str = Field(min_length=1)


class ClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    company_name: str = Field(min_length=1)
    company_phone: str = Field(min_length=1)
    ghl_location_id: str = Field(min_length=1)
    ghl_token_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    calendar_id: str = Field(min_length=1)
    pipeline_id: str = Field(min_length=1)
    stage_ids: StageIds
    timezone: str
    inspection_duration_minutes: int = Field(default=60, gt=0, le=480)
    slots_offered: int = Field(default=2, ge=1, le=5)
    min_lead_time_minutes: int = Field(ge=0)
    search_window_days: int = Field(ge=1, le=60)
    service_area_text: str = Field(min_length=1)
    handoff_notify: list[str] = Field(min_length=1)

    @field_validator("timezone")
    @classmethod
    def _real_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def placeholder_fields(self) -> list[str]:
        """Fields still holding a '...' / 'XXXX' placeholder."""
        flat = {
            "company_phone": self.company_phone,
            "ghl_location_id": self.ghl_location_id,
            "calendar_id": self.calendar_id,
            "pipeline_id": self.pipeline_id,
            **{f"stage_ids.{k}": v for k, v in self.stage_ids.model_dump().items()},
        }
        return [k for k, v in flat.items() if "..." in v or "XXXX" in v.upper()]


def _clients_dir(config_dir: Path | None) -> Path:
    return (config_dir or CONFIG_DIR) / "clients"


def load_client(
    slug: str, *, config_dir: Path | None = None, require_live: bool = False
) -> ClientConfig:
    path = _clients_dir(config_dir) / f"{slug}.yaml"
    if not path.is_file():
        raise ConfigError(f"client config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: expected a mapping of settings")
    try:
        config = ClientConfig.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"{path.name}: invalid client config: {problems}") from exc
    if config.client_slug != slug:
        raise ConfigError(
            f"{path.name}: client_slug is {config.client_slug!r} but the file is named {slug!r}"
        )
    if require_live:
        _require_live(config, path.name)
    return config


def _require_live(config: ClientConfig, filename: str) -> None:
    placeholders = config.placeholder_fields()
    if placeholders:
        raise ConfigError(
            f"{filename}: these fields still hold placeholders, fill them in from GHL: "
            + ", ".join(placeholders)
        )
    if not env_is_set(config.ghl_token_env):
        raise ConfigError(
            f"{filename}: env var {config.ghl_token_env} (named by ghl_token_env) is not set"
        )


def load_all_clients(
    *, config_dir: Path | None = None, require_live: bool = False
) -> dict[str, ClientConfig]:
    """Load every client, keyed by GHL location id (how webhooks are routed)."""
    paths = sorted(_clients_dir(config_dir).glob("*.yaml"))
    if not paths:
        raise ConfigError(f"no client configs found in {_clients_dir(config_dir)}")
    by_location: dict[str, ClientConfig] = {}
    for path in paths:
        config = load_client(path.stem, config_dir=config_dir, require_live=require_live)
        other = by_location.get(config.ghl_location_id)
        if other is not None and require_live:
            raise ConfigError(
                f"{path.name}: ghl_location_id is already used by client {other.client_slug!r}"
            )
        by_location[config.ghl_location_id] = config
    return by_location
