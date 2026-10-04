"""Module C -- payment_analyzer.

Where the money would actually go.  This is the channel that matters most: a
real registration number costs nothing to copy, but redirecting a payment to a
personal account is the end goal of most retail investment fraud.

Three things are checked:
  * Is the UPI handle a validated investor-facing handle (`@valid`)?
  * Does the handle / account appear in the register for this entity?
  * Does the payee name actually correspond to the registered entity, or is it
    an unrelated individual?

No amount of name-matching makes a payment channel "safe" to use -- which is
why the module also returns a guided manual step pointing at SEBI's own lookup.
A prototype guessing at payment destinations should always defer to the
regulator for the final word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings, settings as default_settings
from .entity_reference import Entity
from .textmatch import (
    looks_like_person_name,
    name_similarity,
    strip_company_noise,
)
from .types import (
    CANNOT_VERIFY,
    CONNECTED,
    HIGH,
    LOW,
    MEDIUM,
    MISMATCH,
    NOT_CONNECTED,
    PAYMENT,
    ChannelResult,
    Evidence,
)

#: SEBI / NPCI validated handle suffix for registered investor-facing
#: intermediaries.  Modelled as a strong-but-not-conclusive signal: an
#: impersonator can also obtain one, so it never overrides a payee-name
#: conflict.
VALID_HANDLE_SUFFIX = "@valid"

IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

#: A UPI address is 'localpart@psp'.  The local part allows letters, digits,
#: dot, hyphen and underscore.
UPI_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,60}@[a-z][a-z0-9]{1,30}$")

#: UPI PSP handles in use in India.  A handle ending in something outside this
#: set is not a valid UPI address.  The list goes stale as PSPs launch, so an
#: unknown suffix is raised as a *low* flag rather than a verdict -- a
#: legitimate new PSP must not be able to produce an accusation.
UPI_PSP_HANDLES = {
    # NPCI verified merchant handles
    "valid",
    # PhonePe
    "ybl", "ibl", "axl",
    # Google Pay
    "okaxis", "okhdfcbank", "okicici", "oksbi",
    # Paytm
    "paytm", "ptyes", "ptaxis", "ptsbi", "pthdfc",
    # Amazon Pay / others
    "apl", "yapl", "abfspay", "freecharge", "jio", "jiopay",
    "airtel", "airtelpaymentsbank", "pingpay", "rapl", "timecosmos",
    # Bank-issued handles
    "axisb", "barodampay", "cnrb", "idfcbank", "indus", "kotak", "kmbl",
    "kbl", "mahb", "pnb", "punb", "rbl", "sbi", "sibl", "unionbankofindia",
    "utbi", "vijb", "yesbankltd", "federal", "fbl", "dbs", "equitas",
    "fino", "idbi", "nyes", "tjsb", "waaxis", "waicici", "wasbi",
    "naviaxis", "slice", "cred", "hdfcbank", "icici", "indianbank",
    "centralbank", "cboi", "uco", "united", "karb", "hsbc", "sc",
    "upi", "bob", "boi", "cbin", "idbibank", "jkb", "kvb", "lvbank",
}


@dataclass
class PaymentInputs:
    """Whatever the user pasted.  Everything is optional on its own."""

    upi_id: str | None = None
    bank_account: str | None = None
    ifsc: str | None = None
    payee_name: str | None = None

    @property
    def any_supplied(self) -> bool:
        return any([self.upi_id, self.bank_account, self.payee_name])


def normalize_upi(value: str) -> str:
    return (value or "").strip().lower()


def normalize_account(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _upi_local_part(upi: str) -> str:
    return upi.split("@", 1)[0] if "@" in upi else upi


def check_upi_shape(upi: str) -> tuple[bool, str | None, bool]:
    """Validate a UPI address's *syntax*, not its ownership.

    Returns `(well_formed, psp_handle, psp_is_recognised)`.  This catches a
    handle that is not a UPI address at all ('kavithamenon') or names a payment
    provider that does not exist ('kavitha@notabank').  It says nothing about
    who owns the handle -- SEBI publishes no UPI mapping, so that cannot be
    checked from here.
    """
    upi = normalize_upi(upi)
    if not UPI_PATTERN.match(upi):
        return False, None, False
    psp = upi.rsplit("@", 1)[1]
    return True, psp, psp in UPI_PSP_HANDLES


def analyze_payment(
    entity: Entity | None,
    inputs: PaymentInputs,
    *,
    cfg: Settings | None = None,
) -> ChannelResult | None:
    """Decide whether the payment details are attached to the registered entity.

    Returns None when the user supplied no payment information at all, so the
    verdict engine can leave the channel out entirely rather than inventing a
    CANNOT_VERIFY for something nobody asked about.
    """
    cfg = cfg or default_settings
    if not inputs.any_supplied:
        return None

    evidence: list[Evidence] = []
    reasons: list[str] = []
    rules: list[str] = []
    flags: list[str] = []

    upi = normalize_upi(inputs.upi_id)
    account = normalize_account(inputs.bank_account)
    payee = (inputs.payee_name or "").strip()

    # ---- what we were given ---------------------------------------------- #
    if upi:
        evidence.append(Evidence(PAYMENT, "UPI ID supplied", upi, "input"))
    if account:
        evidence.append(Evidence(
            PAYMENT, "Bank account supplied",
            f"{account} (IFSC {inputs.ifsc or 'not given'})", "input",
        ))
    if inputs.ifsc:
        valid_ifsc = bool(IFSC_PATTERN.match(inputs.ifsc.strip().upper()))
        evidence.append(Evidence(
            PAYMENT, "IFSC format check",
            f"{inputs.ifsc.upper()} is {'a valid' if valid_ifsc else 'not a valid'} IFSC format",
            "heuristic",
        ))
        if not valid_ifsc:
            flags.append("INVALID_IFSC_FORMAT")

    if entity is None:
        rules.append("ENTITY_UNKNOWN")
        return ChannelResult(
            channel=PAYMENT, verdict=CANNOT_VERIFY, confidence=LOW,
            reasons=["These payment details could not be compared to a registered "
                     "entity, because no matching entity was found."],
            evidence=evidence, rules=rules, flags=flags,
        )

    # ---- signal 0: is this even a UPI address? --------------------------- #
    if upi:
        well_formed, psp, psp_known = check_upi_shape(upi)
        if not well_formed:
            rules.append("UPI_SYNTAX_INVALID")
            flags.append("UPI_SYNTAX_INVALID")
            evidence.append(Evidence(
                PAYMENT, "UPI address format",
                f"'{upi}' is not a valid UPI address. A UPI ID is a name, an "
                "'@', then a payment provider handle -- e.g. name@okhdfcbank.",
                "heuristic",
            ))
            reasons.append(
                f"'{upi}' is not shaped like a UPI address at all, so no "
                "payment can reach it as written."
            )
        elif not psp_known:
            rules.append("UPI_PSP_UNRECOGNISED")
            flags.append("UPI_PSP_UNRECOGNISED")
            evidence.append(Evidence(
                PAYMENT, "UPI payment provider",
                f"'{psp}' is not a payment provider handle we recognise",
                "heuristic",
            ))
            reasons.append(
                f"The UPI ID ends in '@{psp}', which is not a payment provider "
                "we recognise. New providers do launch, so treat this as worth "
                "double-checking rather than conclusive."
            )
        else:
            evidence.append(Evidence(
                PAYMENT, "UPI address format",
                f"{upi} is well-formed, using the recognised provider '@{psp}'",
                "heuristic",
            ))

    # ---- signal 1: does the register itself list this identifier? --------- #
    registered_upis = {normalize_upi(u) for u in entity.known_valid_upi_handles}
    registered_accounts = {normalize_account(a) for a in entity.official_bank_accounts}

    upi_in_registry = bool(upi) and upi in registered_upis
    account_in_registry = bool(account) and account in registered_accounts
    identifier_confirmed = upi_in_registry or account_in_registry

    if upi_in_registry:
        evidence.append(Evidence(PAYMENT, "UPI ID against the register",
                                 f"{upi} is listed in the register for this entity", "registry"))
    elif upi:
        evidence.append(Evidence(
            PAYMENT, "UPI ID against the register",
            f"{upi} is not among the handles listed for this entity "
            f"({', '.join(sorted(registered_upis)) or 'none listed'})",
            "registry", missing=not registered_upis,
        ))

    if account_in_registry:
        evidence.append(Evidence(PAYMENT, "Bank account against the register",
                                 f"Account ending {account[-4:]} is listed in the register", "registry"))
    elif account:
        evidence.append(Evidence(
            PAYMENT, "Bank account against the register",
            "The account is not among those listed for this entity"
            if registered_accounts else
            "The register holds no bank account for this entity to compare against",
            "registry", missing=not registered_accounts,
        ))

    # ---- signal 2: is the handle a validated investor-facing handle? ------ #
    upi_is_valid = upi.endswith(VALID_HANDLE_SUFFIX)
    if upi_is_valid:
        evidence.append(Evidence(
            PAYMENT, "Validated handle suffix",
            f"{upi} ends in {VALID_HANDLE_SUFFIX}, the validated handle suffix "
            "used by registered investor-facing intermediaries",
            "heuristic",
        ))
        rules.append("UPI_VALID_SUFFIX")
    elif upi:
        evidence.append(Evidence(
            PAYMENT, "Validated handle suffix",
            f"{upi} does not end in {VALID_HANDLE_SUFFIX}", "heuristic",
        ))

    # ---- signal 3: does the payee name correspond to the entity? ---------- #
    payee_score: float | None = None
    if payee:
        candidates = [entity.name]
        if entity.official_bank_payee_name:
            candidates.append(entity.official_bank_payee_name)
        payee_score = max(name_similarity(payee, c) for c in candidates)
        evidence.append(Evidence(
            PAYMENT, "Payee name against the register",
            f"'{payee}' scores {payee_score:.2f} against the registered name "
            f"'{entity.name}' (threshold {cfg.payee_match_threshold:.2f} to match, "
            f"below {cfg.payee_mismatch_threshold:.2f} to count as unrelated)",
            "heuristic",
        ))
    else:
        evidence.append(Evidence(
            PAYMENT, "Payee name against the register",
            "No payee name was supplied, so the account holder could not be compared",
            "input", missing=True,
        ))

    payee_conflict = payee_score is not None and payee_score < cfg.payee_mismatch_threshold
    payee_match = payee_score is not None and payee_score >= cfg.payee_match_threshold

    # Character-level similarity is unreliable on short strings: 'Anita Bose'
    # scores 0.59 against 'Vantage Broking' purely from shared letters, which
    # would leave a personal account sitting in the undecided band.  A payee
    # that shares *no* distinguishing token with the entity is a different
    # party no matter what the character ratio says.
    if payee and not payee_conflict:
        payee_tokens = set(strip_company_noise(payee).split())
        entity_tokens = set(strip_company_noise(entity.name).split())
        if entity.official_bank_payee_name:
            entity_tokens |= set(strip_company_noise(entity.official_bank_payee_name).split())
        if payee_tokens and payee_tokens.isdisjoint(entity_tokens):
            payee_conflict = True
            evidence.append(Evidence(
                PAYMENT, "Payee name token overlap",
                f"'{payee}' shares no name token with the registered entity, "
                "which points to an unrelated party rather than an abbreviation",
                "heuristic",
            ))

    # ---- precedence ------------------------------------------------------- #
    # 1. The register itself lists this identifier: strongest positive.
    if identifier_confirmed:
        rules.append("PAYMENT_IDENTIFIER_IN_LISTING")
        if upi_in_registry:
            reasons.append(f"The UPI ID {upi} appears in the register for this entity.")
        if account_in_registry:
            reasons.append("The bank account appears in the register for this entity.")
        if payee_conflict:
            # Contradictory evidence, surfaced rather than hidden.
            flags.append("PAYEE_NAME_DIFFERS_FROM_REGISTER")
            reasons.append(
                f"Note: the payee name '{payee}' does not look like the registered "
                "name, even though the identifier is listed. Confirm the account "
                "holder before paying."
            )
        return ChannelResult(
            channel=PAYMENT, verdict=CONNECTED, confidence=HIGH,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    # 2. Payee is clearly somebody else: the impersonation signature.
    if payee_conflict:
        rules.append("PAYEE_NAME_MISMATCH")
        personal = looks_like_person_name(payee)
        if personal:
            rules.append("PAYEE_PERSONAL_ACCOUNT")
            flags.append("PERSONAL_ACCOUNT_PAYEE")
            reasons.append(
                f"The payee name '{payee}' reads as an individual's name, not a firm, "
                "and does not correspond to the registered entity "
                f"'{entity.name}'."
            )
        else:
            reasons.append(
                f"The payee name '{payee}' does not correspond to the registered "
                f"entity '{entity.name}' (similarity {payee_score:.2f})."
            )
        if upi and not upi_is_valid:
            reasons.append(
                f"The UPI ID {upi} is not a validated handle and is not listed "
                "in the register."
            )
        return ChannelResult(
            channel=PAYMENT, verdict=MISMATCH,
            confidence=HIGH if personal else MEDIUM,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    # 3. Validated handle with a payee name that is not contradicted.
    if upi_is_valid and (payee_match or not payee):
        rules.append("PAYMENT_VALID_HANDLE_MATCH")
        reasons.append(
            f"{upi} is a validated investor-facing handle and the payee name is "
            "consistent with the registered entity."
            if payee else
            f"{upi} is a validated investor-facing handle."
        )
        reasons.append(
            "The handle is not itself listed in the register, so confirm against "
            "SEBI's own listing before transferring funds."
        )
        return ChannelResult(
            channel=PAYMENT, verdict=CONNECTED, confidence=MEDIUM,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    # 4. Nothing to compare against -- honest CANNOT_VERIFY.  Checked before the
    #    payee-name fallback below, because with an empty register record there
    #    is no list for the handle to be "missing" from, and saying so would
    #    imply a check we could not actually perform.
    if not entity.has_channel_data(PAYMENT):
        rules.append("NO_SEBI_PAYMENT_REFERENCE")
        reasons.append(
            "The register holds no UPI handle or bank account for this entity, so "
            "the destination cannot be confirmed or ruled out."
        )
        return ChannelResult(
            channel=PAYMENT, verdict=CANNOT_VERIFY, confidence=LOW,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    # 5. Payee name matches, but the handle is unlisted and unvalidated.
    if payee_match:
        rules.append("PAYEE_MATCHES_BUT_HANDLE_UNLISTED")
        reasons.append(
            f"The payee name matches the registered entity, but the UPI ID is "
            "neither listed in the register nor a validated handle, so the "
            "destination itself cannot be confirmed."
        )
        return ChannelResult(
            channel=PAYMENT, verdict=CANNOT_VERIFY, confidence=MEDIUM,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    # 6. Register is rich, and these details are not part of it.
    rules.append("PAYMENT_NOT_THE_LISTED_ONE")
    reasons.append(
        "The register lists payment identifiers for this entity, and the supplied "
        "details are not among them."
    )
    return ChannelResult(
        channel=PAYMENT, verdict=NOT_CONNECTED, confidence=MEDIUM,
        reasons=reasons, evidence=evidence, rules=rules, flags=flags,
    )
