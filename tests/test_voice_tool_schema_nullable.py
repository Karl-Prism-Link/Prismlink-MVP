from pathlib import Path


def test_optional_voice_tool_strings_accept_explicit_null():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert '"anyOf": [' in source
    assert '{"type": "null"}' in source
    assert '"staff_name": _nullable_string_property(' in source
    assert '"customer_name": _nullable_string_property(' in source
    assert '"starts_at_local": _nullable_string_property(' in source
