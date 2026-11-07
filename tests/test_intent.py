import pytest

from netpipe.intent import load_raw_intent


def test_loads_real_intent_file():
    intent = load_raw_intent("intent/man1.yaml")
    assert intent["site"] == "MAN1"
    assert len(intent["devices"]) == 2


def test_missing_file_is_a_clear_error():
    with pytest.raises(FileNotFoundError, match="Intent file not found"):
        load_raw_intent("intent/nope.yaml")


def test_no_credentials_in_intent():
    """Intent is committed to git. Secrets must never appear in it."""
    with open("intent/man1.yaml", encoding="utf-8") as handle:
        content = handle.read().lower()

    for forbidden in ("password", "secret", "enable_pass", "api_key", "token"):
        assert forbidden not in content, f"'{forbidden}' found in intent file"
