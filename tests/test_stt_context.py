from services.voice.stt_context import (
    build_stt_keyterms,
    build_stt_keywords,
    stt_vocabulary_settings,
)


def test_stt_keyterms_include_service_apostrophe_variant():
    terms = build_stt_keyterms(
        "Studio One Hair",
        ["Men's Cut", "Women's Cut", "Blow Wave"],
        ["Scott"],
    )
    assert "Men's Cut" in terms
    assert "Mens Cut" in terms
    assert "Women's Cut" in terms
    assert "Womens Cut" in terms
    assert "Studio One Hair" in terms
    assert "Scott" in terms


def test_stt_keyterms_dedupe_case_insensitively():
    terms = build_stt_keyterms("Studio One Hair", ["Colour", "colour"], [])
    assert sum(term.casefold() == "colour" for term in terms) == 1


def test_nova2_uses_keywords_not_keyterm():
    mode, keyterm, keywords = stt_vocabulary_settings(
        "nova-2-general", "Studio One Hair", ["Men's Cut", "Blow Wave"], ["Scott"]
    )
    assert mode == "keywords"
    assert keyterm is None
    assert keywords
    assert any(item.startswith("Men's:") for item in keywords)
    assert not any("Men's Cut" in item for item in keywords)


def test_nova3_uses_keyterm_not_keywords():
    mode, keyterm, keywords = stt_vocabulary_settings(
        "nova-3-general", "Studio One Hair", ["Men's Cut"], []
    )
    assert mode == "keyterm"
    assert "Men's Cut" in (keyterm or [])
    assert keywords is None


def test_nova2_keywords_are_individual_and_moderately_boosted():
    keywords = build_stt_keywords("Studio One Hair", ["Women's Cut"], [])
    assert "Women's:1.2" in keywords
    assert "Cut:1.2" in keywords
    assert all(" " not in item.split(":", 1)[0] for item in keywords)
