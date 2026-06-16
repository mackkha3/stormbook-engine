"""Per-client configuration loader.

Two-layer scheme:

* Layer 1 — non-secret config, resolved in this order:
    1. `CLIENTS_JSON` env var — JSON blob, canonical production source.
    2. `data/clients.json` (gitignored) — local dev.
    3. `data/clients.example.json` — last-resort committed template.
* Layer 2 — secrets as env vars, one per client:
  `GHL_API_KEY_<CLIENT_ID_UPPERCASED_WITH_UNDERSCORES>`.

In production (ENVIRONMENT=production) the config is loaded once at startup.
In development it's re-read on every lookup so iteration is fast.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from intake_api.lib.logger import get

log = get(__name__)

# `data/` lives at the project root: <pkg>/src/intake_api/lib/client_config.py
# → up four levels to apps/intake-api/.
_PKG_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = _PKG_ROOT / "data"
CLIENTS_JSON_FILE = DATA_DIR / "clients.json"
CLIENTS_EXAMPLE_JSON = DATA_DIR / "clients.example.json"
CLIENTS_JSON_ENV_VAR = "CLIENTS_JSON"
# Sentinel `Path` returned alongside data parsed from the env var. Lets the
# dev "source changed" log line distinguish env-sourced config from files.
_ENV_SOURCE = Path("<CLIENTS_JSON env>")


class ClientConfig(BaseModel):
    client_id: str
    ghl_location_id: str
    ghl_api_key: str
    custom_field_ids: dict[str, str]
    service_area_zips: list[str]


_lock = threading.Lock()
_cached: dict[str, dict[str, Any]] | None = None
_cached_source: Path | None = None


def _is_production() -> bool:
    return os.environ.get("ENVIRONMENT", "development").lower() == "production"


def _load_json_file() -> tuple[dict[str, dict[str, Any]], Path]:
    # Env var wins — canonical production source. Empty string is treated as
    # unset so an operator can clear it without redeploying just to fall back
    # to a file.
    env_val = os.environ.get(CLIENTS_JSON_ENV_VAR)
    if env_val:
        try:
            return json.loads(env_val), _ENV_SOURCE
        except json.JSONDecodeError as e:
            # Don't crash the app — handlers will see an empty registry and
            # log `client_not_found` per request, which is how operators will
            # notice the misconfiguration.
            log.error("clients_env_var_invalid", extra={"error": str(e)})
            return {}, _ENV_SOURCE
    if CLIENTS_JSON_FILE.exists():
        with CLIENTS_JSON_FILE.open() as f:
            return json.load(f), CLIENTS_JSON_FILE
    if CLIENTS_EXAMPLE_JSON.exists():
        log.warning(
            "clients_json_missing_using_example",
            extra={"path": str(CLIENTS_EXAMPLE_JSON)},
        )
        with CLIENTS_EXAMPLE_JSON.open() as f:
            return json.load(f), CLIENTS_EXAMPLE_JSON
    log.error("clients_config_missing", extra={"data_dir": str(DATA_DIR)})
    return {}, DATA_DIR


def _source_label(source: Path) -> str:
    return "env" if source == _ENV_SOURCE else str(source)


def _load() -> dict[str, dict[str, Any]]:
    global _cached, _cached_source
    if _is_production():
        with _lock:
            if _cached is None:
                _cached, _cached_source = _load_json_file()
                log.info(
                    "clients_config_loaded",
                    extra={"source": _source_label(_cached_source), "client_count": len(_cached)},
                )
            return _cached
    # Dev: always re-read.
    data, source = _load_json_file()
    if _cached_source != source:
        log.info(
            "clients_config_loaded",
            extra={"source": _source_label(source), "client_count": len(data)},
        )
        _cached_source = source
    return data


def env_var_name(client_id: str) -> str:
    """`henderson-roofing` → `GHL_API_KEY_HENDERSON_ROOFING`."""
    return "GHL_API_KEY_" + client_id.upper().replace("-", "_")


def get_client(client_id: str) -> ClientConfig | None:
    """Look up client config + load its GHL API key from env. None if missing."""
    clients = _load()
    raw = clients.get(client_id)
    if raw is None:
        return None

    api_key_var = env_var_name(client_id)
    api_key = os.environ.get(api_key_var)
    if not api_key:
        log.error(
            "client_ghl_api_key_missing",
            extra={"client_id": client_id, "expected_env_var": api_key_var},
        )
        return None

    return ClientConfig(
        client_id=client_id,
        ghl_location_id=raw["ghl_location_id"],
        ghl_api_key=api_key,
        custom_field_ids=raw.get("custom_field_ids", {}),
        service_area_zips=raw.get("service_area_zips", []),
    )


def reset_cache_for_tests() -> None:
    """Test helper — force the next lookup to re-read the JSON file."""
    global _cached, _cached_source
    with _lock:
        _cached = None
        _cached_source = None
