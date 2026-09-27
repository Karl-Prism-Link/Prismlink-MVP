from pathlib import Path

from services.voice.speech_cleanup import spoken_segment_key


def test_spoken_segment_key_matches_punctuation_and_case_variants():
    assert spoken_segment_key("What's your name and phone number?") == spoken_segment_key(
        "WHAT'S your name and phone number!"
    )


def test_live_pipeline_includes_sentence_aggregation_and_duplicate_suppressor():
    source = (Path(__file__).parents[1] / "apps" / "voice" / "bot.py").read_text()
    assert "LLMTextProcessor()" in source
    assert "DuplicateSpeechSuppressor()" in source
    assert "llm_text_processor," in source
    assert "speech_deduper," in source
