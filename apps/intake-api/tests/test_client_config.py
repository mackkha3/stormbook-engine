import json
import os

import pytest

from intake_api.lib import client_config


@pytest.fixture(autouse=True)
def _isolate_clients_json_env(monkeypatch):
    """Keep file-based tests immune to a CLIENTS_JSON value leaking in from
    the developer's shell. Per-test overrides still work via monkeypatch."""
    monkeypatch.delenv("CLIENTS_JSON", raising=False)
    client_config.reset_cache_for_tests()


def test_known_client_with_env_returns_config():
    cfg = client_config.get_client("henderson-roofing")
    assert cfg is not None
    assert cfg.client_id == "henderson-roofing"
    assert cfg.ghl_location_id == "loc_abc123"
    assert cfg.ghl_api_key == "test_ghl_key_henderson"
    assert "damage_type" in cfg.custom_field_ids


def test_unknown_client_returns_none():
    assert client_config.get_client("not-a-real-client") is None


def test_known_client_missing_env_returns_none(monkeypatch):
    monkeypatch.delenv("GHL_API_KEY_HENDERSON_ROOFING", raising=False)
    client_config.reset_cache_for_tests()
    assert client_config.get_client("henderson-roofing") is None
    # restore for any later tests in the same session
    os.environ["GHL_API_KEY_HENDERSON_ROOFING"] = "test_ghl_key_henderson"


def test_env_var_name_uppercases_and_underscores():
    assert client_config.env_var_name("henderson-roofing") == "GHL_API_KEY_HENDERSON_ROOFING"
    assert client_config.env_var_name("beacon-storm-pros") == "GHL_API_KEY_BEACON_STORM_PROS"


def test_env_var_takes_precedence_over_file(monkeypatch, tmp_path):
    # File on disk advertises one config; env var advertises another. Env wins.
    file_cfg = {
        "henderson-roofing": {
            "ghl_location_id": "loc_FROM_FILE",
            "custom_field_ids": {"damage_type": "cf_file"},
            "service_area_zips": ["00000"],
        }
    }
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps(file_cfg))
    monkeypatch.setattr(client_config, "CLIENTS_JSON_FILE", clients_file)

    env_cfg = {
        "client-a": {
            "ghl_location_id": "loc_FROM_ENV",
            "custom_field_ids": {"damage_type": "cf_env"},
            "service_area_zips": ["75701"],
        }
    }
    monkeypatch.setenv("CLIENTS_JSON", json.dumps(env_cfg))
    monkeypatch.setenv("GHL_API_KEY_CLIENT_A", "fake_key")
    client_config.reset_cache_for_tests()

    cfg = client_config.get_client("client-a")
    assert cfg is not None
    assert cfg.ghl_location_id == "loc_FROM_ENV"
    assert cfg.custom_field_ids == {"damage_type": "cf_env"}
    # And the file-only client is unreachable while the env var is set.
    assert client_config.get_client("henderson-roofing") is None


def test_env_var_invalid_json_falls_back_safely(monkeypatch, caplog):
    monkeypatch.setenv("CLIENTS_JSON", "{not valid")
    client_config.reset_cache_for_tests()

    with caplog.at_level("ERROR"):
        assert client_config.get_client("anything") is None

    assert any(r.getMessage() == "clients_env_var_invalid" for r in caplog.records)


def test_env_var_missing_uses_file(monkeypatch):
    # Explicit: with CLIENTS_JSON unset the loader must fall through to the
    # example file (file-based path that the rest of the suite depends on).
    monkeypatch.delenv("CLIENTS_JSON", raising=False)
    client_config.reset_cache_for_tests()

    cfg = client_config.get_client("henderson-roofing")
    assert cfg is not None
    assert cfg.ghl_location_id == "loc_abc123"


def test_env_var_empty_string_treated_as_unset(monkeypatch):
    monkeypatch.setenv("CLIENTS_JSON", "")
    client_config.reset_cache_for_tests()

    cfg = client_config.get_client("henderson-roofing")
    assert cfg is not None
    assert cfg.ghl_location_id == "loc_abc123"
