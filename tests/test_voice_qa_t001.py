from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

QA_DIR = Path(__file__).parents[1] / "qa" / "voice-testing"


def load_qa_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load QA module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


assertions = load_qa_module("t001_assertions", QA_DIR / "t001_assertions.py")
runner = load_qa_module("run_t001", QA_DIR / "run_t001.py")
assess_t001 = assertions.assess_t001
require_tailscale_target = runner.require_tailscale_target


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
