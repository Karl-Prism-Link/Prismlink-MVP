from __future__ import annotations

import sys
from pathlib import Path

QA_DIR = Path(__file__).parents[1] / "qa" / "voice-testing"
sys.path.insert(0, str(QA_DIR))

from run_t001 import require_tailscale_target
from t001_assertions import assess_t001


def test_t001_accepts_expected_greeting():
    result = assess_t001(
        "Hello, thanks for calling Luxe Hair and Beauty. "
        "I'm the AI receptionist. How can I help you today?"
    )
    assert result["status"] == "PASS"
    assert all(result["checks"].values())


def test_t001_fails_if_ai_disclosure_is_missing():
    result = assess_t001(
        "Hello, thanks for calling Luxe Hair and Beauty. How can I help you today?"
    )
    assert result["status"] == "FAIL"
    assert result["checks"]["ai_disclosed"] is False


def test_t001_flags_extra_factual_claims_for_review():
    result = assess_t001(
        "Hello, welcome to Luxe Hair and Beauty. I'm the AI receptionist. "
        "We're open until 6 pm. How can I help?"
    )
    assert result["status"] == "NEEDS_REVIEW"


def test_linphone_target_must_be_aurora_tailscale_ip():
    assert require_tailscale_target("sip:qa@100.105.4.49:5060")
    for target in (
        "sip:qa@example.com",
        "sip:qa@8.8.8.8:5060",
        "sip:qa@192.168.1.10:5060",
    ):
        try:
            require_tailscale_target(target)
        except ValueError:
            continue
        raise AssertionError(f"Unsafe SIP target was accepted: {target}")
