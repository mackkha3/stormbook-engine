from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from booking_engine.config_loader import ConfigError, load_all_clients, load_client

TOKEN_ENV = "GHL_TOKEN_CONFIG_LOADER_TEST"


def _settings(slug: str = "acme-roofing", **overrides) -> dict:
    return {
        "client_slug": slug,
        "company_name": "Acme Roofing",
        "company_phone": "+19035550100",
        "ghl_location_id": f"loc-{slug}",
        "ghl_token_env": TOKEN_ENV,
        "calendar_id": "cal-1",
        "pipeline_id": "pipe-1",
        "stage_ids": {
            "texted": "stage-1",
            "replied": "stage-2",
            "booked": "stage-3",
            "no_response": "stage-4",
        },
        "timezone": "America/Chicago",
        "search_window_days": 14,
        "service_area_text": "East Texas",
        "handoff_notify": ["+19035550101"],
        **overrides,
    }


def _write(config_dir: Path, name: str, content: dict | str) -> None:
    clients = config_dir / "clients"
    clients.mkdir(exist_ok=True)
    text = content if isinstance(content, str) else yaml.safe_dump(content)
    (clients / f"{name}.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def token_set(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "secret-token-value")


# --- load_client ---


def test_load_client_happy_path(tmp_path):
    _write(tmp_path, "acme-roofing", _settings())

    config = load_client("acme-roofing", config_dir=tmp_path)

    assert config.company_name == "Acme Roofing"
    assert str(config.tz) == "America/Chicago"


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(ConfigError, match=r"client config not found: .*nope\.yaml"):
        load_client("nope", config_dir=tmp_path)


def test_invalid_yaml_names_the_file(tmp_path):
    _write(tmp_path, "acme-roofing", "company_name: [unclosed")

    with pytest.raises(ConfigError, match=r"acme-roofing\.yaml: not valid YAML"):
        load_client("acme-roofing", config_dir=tmp_path)


@pytest.mark.parametrize("content", ["- a\n- list\n", "just a string\n", ""])
def test_non_mapping_yaml_is_rejected(tmp_path, content):
    _write(tmp_path, "acme-roofing", content)

    with pytest.raises(ConfigError, match="expected a mapping of settings"):
        load_client("acme-roofing", config_dir=tmp_path)


def test_validation_errors_name_the_nested_field(tmp_path):
    settings = _settings()
    del settings["stage_ids"]["booked"]
    _write(tmp_path, "acme-roofing", settings)

    with pytest.raises(ConfigError, match=r"invalid client config: stage_ids\.booked: "):
        load_client("acme-roofing", config_dir=tmp_path)


def test_unknown_timezone_is_rejected(tmp_path):
    _write(tmp_path, "acme-roofing", _settings(timezone="Mars/Olympus"))

    with pytest.raises(ConfigError, match="unknown timezone 'Mars/Olympus'"):
        load_client("acme-roofing", config_dir=tmp_path)


def test_slug_must_match_the_filename(tmp_path):
    _write(tmp_path, "other-name", _settings())

    with pytest.raises(ConfigError, match="client_slug is 'acme-roofing'.*named 'other-name'"):
        load_client("other-name", config_dir=tmp_path)


# --- _require_live ---


def test_require_live_passes_with_real_values_and_token(tmp_path, token_set):
    _write(tmp_path, "acme-roofing", _settings())

    assert load_client("acme-roofing", config_dir=tmp_path, require_live=True)


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"calendar_id": "..."}, "calendar_id"),
        ({"pipeline_id": "xxxx-1"}, "pipeline_id"),
        ({"stage_ids": {"texted": "...", "replied": "r", "booked": "b", "no_response": "n"}},
         "stage_ids.texted"),
    ],
)
def test_require_live_refuses_placeholders(tmp_path, token_set, override, field):
    _write(tmp_path, "acme-roofing", _settings(**override))

    with pytest.raises(ConfigError, match=f"still hold placeholders.*{field}"):
        load_client("acme-roofing", config_dir=tmp_path, require_live=True)


def test_placeholders_are_allowed_when_not_going_live(tmp_path):
    _write(tmp_path, "acme-roofing", _settings(calendar_id="..."))

    assert load_client("acme-roofing", config_dir=tmp_path).calendar_id == "..."


@pytest.mark.parametrize("value", [None, "   "])
def test_require_live_refuses_an_unset_token_without_echoing_it(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(TOKEN_ENV, raising=False)
    else:
        monkeypatch.setenv(TOKEN_ENV, value)
    _write(tmp_path, "acme-roofing", _settings())

    with pytest.raises(ConfigError, match=f"env var {TOKEN_ENV} .* is not set"):
        load_client("acme-roofing", config_dir=tmp_path, require_live=True)


# --- load_all_clients ---


def test_load_all_clients_keys_by_location(tmp_path):
    _write(tmp_path, "acme-roofing", _settings("acme-roofing"))
    _write(tmp_path, "best-roofing", _settings("best-roofing"))

    clients = load_all_clients(config_dir=tmp_path)

    assert set(clients) == {"loc-acme-roofing", "loc-best-roofing"}
    assert clients["loc-best-roofing"].client_slug == "best-roofing"


@pytest.mark.parametrize("make_dir", [True, False])
def test_load_all_clients_with_nothing_to_load(tmp_path, make_dir):
    if make_dir:
        (tmp_path / "clients").mkdir()

    with pytest.raises(ConfigError, match="no client configs found"):
        load_all_clients(config_dir=tmp_path)


def test_load_all_clients_propagates_a_bad_file(tmp_path):
    _write(tmp_path, "acme-roofing", _settings("acme-roofing"))
    _write(tmp_path, "broken", "company_name: [unclosed")

    with pytest.raises(ConfigError, match=r"broken\.yaml"):
        load_all_clients(config_dir=tmp_path)


def test_load_all_clients_passes_require_live_through(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "acme-roofing", _settings())

    assert load_all_clients(config_dir=tmp_path)
    with pytest.raises(ConfigError, match="is not set"):
        load_all_clients(config_dir=tmp_path, require_live=True)


def test_duplicate_location_is_refused_when_live(tmp_path, token_set):
    _write(tmp_path, "acme-roofing", _settings("acme-roofing", ghl_location_id="loc-shared"))
    _write(tmp_path, "best-roofing", _settings("best-roofing", ghl_location_id="loc-shared"))

    with pytest.raises(ConfigError, match="already used by client 'acme-roofing'"):
        load_all_clients(config_dir=tmp_path, require_live=True)


def test_duplicate_location_keeps_the_last_when_not_live(tmp_path):
    _write(tmp_path, "acme-roofing", _settings("acme-roofing", ghl_location_id="loc-shared"))
    _write(tmp_path, "best-roofing", _settings("best-roofing", ghl_location_id="loc-shared"))

    clients = load_all_clients(config_dir=tmp_path)

    assert clients["loc-shared"].client_slug == "best-roofing"
