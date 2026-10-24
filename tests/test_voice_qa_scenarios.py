import json
from pathlib import Path

SCENARIOS = Path(__file__).parents[1] / "scenarios.json"


def test_voice_qa_registry_has_all_50_ordered_cases():
    suite = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    cases = suite["scenarios"]

    assert suite["suite_id"] == "prism-link-voice-qa-v1"
    assert len(cases) == 50
    assert [case["id"] for case in cases] == [f"T{i:03}" for i in range(1, 51)]


def test_each_case_has_script_and_verifiable_expectations():
    suite = json.loads(SCENARIOS.read_text(encoding="utf-8"))

    for case in suite["scenarios"]:
        assert case["category"]
        assert case["priority"] in {"Critical", "High", "Medium", "Low"}
        assert case["caller_script"]
        assert case["preconditions"]
        assert case["expected_behaviour"]
        assert case["pass_criteria"]
