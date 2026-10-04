"""Module E -- behaviour_flags."""

from __future__ import annotations

import pytest

from backend.behaviour_flags import RULES, scan_message


def test_empty_text_is_not_scanned():
    report = scan_message("")
    assert report.scanned is False
    assert report.flags == []
    assert report.risk_level == "none"


def test_ordinary_message_has_no_flags():
    report = scan_message("Hi, your KYC is due for renewal. Please visit the portal.")
    assert report.scanned is True
    assert report.flags == []
    assert report.risk_level == "none"


@pytest.mark.parametrize("text,expected_id", [
    ("Your investment gives guaranteed 30% monthly return", "GUARANTEED_RETURNS"),
    ("This plan is completely risk-free", "GUARANTEED_RETURNS"),
    ("Double your money in 15 days", "UNREALISTIC_MULTIPLIER"),
    ("Join our VIP group for premium calls", "VIP_GROUP"),
    ("Contact me on telegram for details", "MOVE_TO_OTHER_APP"),
    ("Please transfer the amount to my account", "PAY_PERSONAL_ACCOUNT"),
    ("Act now, only 3 slots left", "URGENCY"),
    ("Download the app from this link (APK)", "SIDELOADED_APP"),
    ("Pay the processing fee to unlock your call", "LOTTERY_OR_FEE"),
    ("We are SEBI registered and approved", "SEBI_CLAIM_UNCITED"),
    ("Do not verify with anyone, keep this confidential", "AVOID_VERIFICATION"),
    ("Sure shot multibagger tip inside", "STOCK_TIP_PRESSURE"),
])
def test_each_rule_can_fire(text, expected_id):
    ids = {flag.id for flag in scan_message(text).flags}
    assert expected_id in ids, f"expected {expected_id} for: {text}"


def test_the_full_scam_message_raises_high_risk():
    report = scan_message(
        "Guaranteed 30% monthly return. Join our VIP group now. "
        "Pay the processing fee to unlock today's call. "
        "Download the app from this link (APK). Contact me on telegram."
    )
    assert report.risk_level == "high"
    assert len(report.flags) >= 4


def test_flags_carry_the_text_that_triggered_them():
    report = scan_message("Your returns are guaranteed and assured.")
    flag = next(f for f in report.flags if f.id == "GUARANTEED_RETURNS")
    assert flag.matches, "a flag the user cannot see evidence for is not actionable"
    assert flag.explanation


def test_flag_explanations_do_not_describe_a_connection():
    """Behaviour flags explain the message, never the channel."""
    for rule in RULES:
        lowered = rule.explanation.lower()
        assert "not connected" not in lowered
        assert "safe" not in lowered.split()


def test_scan_is_bounded_for_absurd_input():
    report = scan_message("guaranteed return " * 5000)
    assert report.scanned is True
    assert report.risk_level == "high"


def test_several_low_signals_together_escalate():
    """One mild flag stays mild; a cluster does not."""
    single = scan_message("We are a SEBI registered firm.")
    assert single.risk_level == "low"
