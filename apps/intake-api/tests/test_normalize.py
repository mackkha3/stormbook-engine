from intake_api.lib.normalize import normalize_address, normalize_phone


def test_phone_us_formatted():
    assert normalize_phone("(903) 555-0199") == "+19035550199"


def test_phone_already_e164_idempotent():
    assert normalize_phone("+19035550199") == "+19035550199"


def test_phone_with_leading_one():
    assert normalize_phone("1-903-555-0199") == "+19035550199"


def test_phone_invalid_returns_none():
    assert normalize_phone("abc") is None
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_address_collapses_whitespace():
    assert normalize_address("  123  Elm   St\nTyler TX  ") == "123 Elm St Tyler TX"


def test_address_empty_returns_empty():
    assert normalize_address("") == ""
    assert normalize_address(None) == ""
