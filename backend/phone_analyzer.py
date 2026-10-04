"""Module D -- phone_analyzer.

Does this phone number belong to the registered entity, and what does the
*shape* of the number tell us?

Indian financial-services firms are expected to make service and transactional
calls from the 1600 series; a plain 10-digit mobile dialling you about an
investment is the shape cold-call fraud takes.  That is recorded as a risk
flag, never as proof of a bad connection -- plenty of genuine relationships
start from a mobile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings, settings as default_settings
from .entity_reference import Entity
from .types import (
    CANNOT_VERIFY,
    CONNECTED,
    HIGH,
    LOW,
    MEDIUM,
    NOT_CONNECTED,
    PHONE,
    ChannelResult,
    Evidence,
)

#: 1600-series: the range allocated for service / transactional calls.
SERVICE_SERIES_PREFIX = "1600"

#: Consumer mobile numbers in India start 6-9 and are 10 digits.
MOBILE_PATTERN = re.compile(r"^[6-9]\d{9}$")


@dataclass
class PhoneShape:
    """What kind of number this is, derived from its digits alone."""

    national: str          # 10-digit national number, or best effort
    is_service_series: bool
    is_mobile: bool
    is_landline: bool
    description: str


def normalize_phone(value: str) -> str:
    """Reduce any Indian phone format to its 10-digit national number.

    '+91 98765 43210', '098765-43210' and '9876543210' all collapse to
    '9876543210'.  Landlines keep their STD code but lose the trunk '0'.
    """
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("0091"):
        digits = digits[4:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11 and digits[1] in "6789":
        # A trunk '0' in front of a mobile subscriber number is dropped.
        # A leading '0' in a landline is part of the STD code (022, 011, 033)
        # and must be kept, or 02261234567 would collapse onto a 10-digit
        # number and start colliding with mobiles.
        digits = digits[1:]
    return digits


def classify_phone(national: str) -> PhoneShape:
    """Describe the number's shape so the verdict can explain itself."""
    if national.startswith(SERVICE_SERIES_PREFIX) and len(national) == 10:
        return PhoneShape(national, True, False, False,
                          "a 1600-series service number")
    if MOBILE_PATTERN.match(national):
        return PhoneShape(national, False, True, False,
                          "a regular 10-digit mobile number")
    if 10 <= len(national) <= 11:
        return PhoneShape(national, False, False, True,
                          "a landline number")
    return PhoneShape(national, False, False, False,
                      "a number whose format could not be classified")


def analyze_phone(
    entity: Entity | None,
    raw_phone: str,
    *,
    cfg: Settings | None = None,
) -> ChannelResult | None:
    """Decide whether this phone number belongs to the registered entity."""
    cfg = cfg or default_settings
    if not (raw_phone or "").strip():
        return None

    evidence: list[Evidence] = []
    reasons: list[str] = []
    rules: list[str] = []
    flags: list[str] = []

    national = normalize_phone(raw_phone)
    shape = classify_phone(national)
    evidence.append(Evidence(
        PHONE, "Number format",
        f"{raw_phone} normalises to {national or 'nothing usable'} and is "
        f"{shape.description}",
        "heuristic",
    ))

    if not national:
        return ChannelResult(
            channel=PHONE, verdict=CANNOT_VERIFY, confidence=LOW,
            reasons=["The phone number could not be parsed."],
            evidence=evidence, rules=["PHONE_UNPARSEABLE"], flags=flags,
        )

    # Risk flag, never a verdict: a mobile is the cold-call shape.
    if shape.is_mobile:
        flags.append("MOBILE_NUMBER_COLD_CALL")
        reasons.append(
            "This is a regular 10-digit mobile number. Registered entities are "
            "expected to use the 1600 series for service and transactional calls, "
            "so a mobile contacting you about an investment is worth questioning."
        )

    if entity is None:
        rules.append("ENTITY_UNKNOWN")
        return ChannelResult(
            channel=PHONE, verdict=CANNOT_VERIFY, confidence=LOW,
            reasons=reasons + ["This number could not be compared to a registered "
                               "entity, because no matching entity was found."],
            evidence=evidence, rules=rules, flags=flags,
        )

    listed = {normalize_phone(p) for p in entity.official_phone_numbers}

    if national in listed:
        rules.append("PHONE_IS_THE_LISTED_ONE")
        evidence.append(Evidence(PHONE, "Number against the register",
                                 f"{national} is listed in the register for this entity",
                                 "registry"))
        reasons.append("The number is listed in the register for this entity.")
        return ChannelResult(
            channel=PHONE, verdict=CONNECTED, confidence=HIGH,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    if listed:
        rules.append("PHONE_NOT_THE_LISTED_ONE")
        evidence.append(Evidence(
            PHONE, "Number against the register",
            f"{national} is not among the numbers listed for this entity "
            f"({', '.join(sorted(listed))})",
            "registry",
        ))
        reasons.append(
            "The register lists other numbers for this entity, and this is not one "
            "of them."
        )
        return ChannelResult(
            channel=PHONE, verdict=NOT_CONNECTED, confidence=MEDIUM,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    rules.append("NO_SEBI_PHONE_REFERENCE")
    evidence.append(Evidence(
        PHONE, "Number against the register",
        "The register holds no phone number for this entity to compare against",
        "registry", missing=True,
    ))
    reasons.append(
        "The register holds no phone number for this entity, so a link cannot be "
        "confirmed or ruled out."
    )
    return ChannelResult(
        channel=PHONE, verdict=CANNOT_VERIFY, confidence=LOW,
        reasons=reasons, evidence=evidence, rules=rules, flags=flags,
    )
