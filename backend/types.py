"""Shared vocabulary for Borrowed Badge.

Every module speaks in these four verdicts and nothing else.  Two of them are
deliberately easy to confuse in English, so they are kept strictly apart in
code and in copy:

    NOT_CONNECTED   we looked, and the link genuinely is not there.
                    (Only used when the registry record is rich enough that a
                    real link *would* have shown up.)
    CANNOT_VERIFY   we could not look properly -- thin data, unknown entity.
                    This is the honest answer for small firms with a sparse
                    footprint, and it is NOT an accusation.

Wording rule (spec section 5): the word "safe" is never emitted.  There is a
test that enforces this across every string the API can return.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Verdicts
# --------------------------------------------------------------------------- #

CONNECTED = "CONNECTED"
NOT_CONNECTED = "NOT_CONNECTED"
MISMATCH = "MISMATCH"
CANNOT_VERIFY = "CANNOT_VERIFY"

ALL_VERDICTS = (CONNECTED, NOT_CONNECTED, MISMATCH, CANNOT_VERIFY)

#: Human-facing copy.  Note there is no word "safe" anywhere in this file.
VERDICT_LABELS: dict[str, str] = {
    CONNECTED: "Connected - verified link found",
    NOT_CONNECTED: "Not connected - no link found",
    MISMATCH: "Mismatch / likely impersonation",
    CANNOT_VERIFY: "Can't verify",
}

#: "Overall verdict = worst per-channel result".  Higher is worse.
#: NOT_CONNECTED outranks CANNOT_VERIFY because it is a definite finding,
#: while CANNOT_VERIFY is an absence of one.
SEVERITY: dict[str, int] = {
    CONNECTED: 0,
    CANNOT_VERIFY: 1,
    NOT_CONNECTED: 2,
    MISMATCH: 3,
}

# --------------------------------------------------------------------------- #
# Confidence
# --------------------------------------------------------------------------- #

HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"

CONFIDENCE_RANK = {LOW: 0, MEDIUM: 1, HIGH: 2}

# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #

WEBSITE = "website"
PAYMENT = "payment"
PHONE = "phone"

ALL_CHANNELS = (WEBSITE, PAYMENT, PHONE)


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #

@dataclass
class Evidence:
    """One thing we checked, what came back, and where it came from.

    The evidence list is the product.  A verdict a user cannot inspect is a
    verdict they cannot act on.
    """

    channel: str
    check: str          # what was checked, in plain language
    found: str          # what we actually observed
    source: str         # registry | rdap-mock | rdap-live | link-graph | heuristic
    missing: bool = False   # True when the check could not be completed

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "check": self.check,
            "found": self.found,
            "source": self.source,
            "missing": self.missing,
        }


@dataclass
class ChannelResult:
    """The outcome for a single channel (website / payment / phone)."""

    channel: str
    verdict: str
    confidence: str
    reasons: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "verdict": self.verdict,
            "label": VERDICT_LABELS[self.verdict],
            "confidence": self.confidence,
            "reasons": self.reasons,
            "evidence": [e.to_dict() for e in self.evidence],
            "rules_fired": self.rules,
            "flags": self.flags,
        }


def worst(verdicts: list[str]) -> str:
    """Return the most severe verdict from a list (spec: overall = worst)."""
    if not verdicts:
        return CANNOT_VERIFY
    return max(verdicts, key=lambda v: SEVERITY[v])


def no_safe_word(text: str) -> bool:
    """True when `text` avoids the forbidden reassurance word.

    Checks whole words only, so 'safeguard' and 'safety' do not trip it --
    though in practice we avoid those too when talking about a verdict.
    """
    import re

    return re.search(r"\bsafe\b", text, flags=re.IGNORECASE) is None
