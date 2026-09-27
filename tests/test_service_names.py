from services.service_names import canonical_service_name, service_match_terms


def test_common_service_labels_are_canonicalized_conservatively():
    assert canonical_service_name("mens Cuts") == "Men's Cut"
    assert canonical_service_name("Womens cut") == "Women's Cut"
    assert canonical_service_name("blow wave") == "Blow Wave"
    assert canonical_service_name("colour") == "Colour"


def test_custom_service_names_are_preserved():
    assert canonical_service_name("Balayage Deluxe") == "Balayage Deluxe"


def test_voice_aliases_include_common_asr_variant():
    terms = service_match_terms("mens Cuts")
    assert "mens cut" in terms
    assert "means cut" in terms
