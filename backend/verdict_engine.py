"""Module F -- verdict_engine.

Transparent, deterministic, no model.  Every verdict carries the rules that
produced it, so a user (or a regulator, or a court) can follow the reasoning
from inputs to conclusion.

The engine works in one of two modes, and which one is active is decided by
whether the user supplied what SEBI's listing shows.

**Anchored** (official details supplied).  The original question is answerable:

    entity status not active              -> MISMATCH   (a real number with a
                                                        dead credential -- the
                                                        'borrowed badge')
    displayed name is a different firm    -> MISMATCH
    otherwise                             -> worst per-channel verdict

**Unanchored** (nothing from SEBI supplied).  The tool cannot say whether a
channel is attached to the entity, and must not pretend otherwise.  It reports
the live facts it gathered (domain age and registrar from RDAP, UPI structure,
phone shape, message red flags), scores a risk level from them, and says
plainly that only SEBI can confirm the registration.

The asymmetry in the channel rules is deliberate.  Saying NOT CONNECTED is a
claim that a link should have existed and did not; it is only made when the
reference pins that channel down.  Where the listing is silent the answer is
CANNOT VERIFY, which protects honest small firms from being wrongly accused.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .behaviour_flags import BehaviourReport, scan_message
from .config import Settings, settings as default_settings
from .domain_analyzer import analyze_website, build_intel_provider, build_link_checker
from .entity_reference import (
    Entity,
    build_reference,
    looks_like_sebi_reg_no,
    normalize_reg_no,
    sebi_check_url,
)
from .payment_analyzer import PaymentInputs, analyze_payment
from .phone_analyzer import analyze_phone
from .sebi_registry import TYPE_BY_REG_PREFIX, get_registry
from .textmatch import similarity, strip_company_noise
from .types import (
    CANNOT_VERIFY,
    CONFIDENCE_RANK,
    CONNECTED,
    HIGH,
    LOW,
    MEDIUM,
    MISMATCH,
    NOT_CONNECTED,
    VERDICT_LABELS,
    ChannelResult,
    Evidence,
    worst,
)

HEADLINES: dict[str, str] = {
    CONNECTED: (
        "A verified connection was found between the details you supplied and "
        "the entity described in SEBI's listing."
    ),
    NOT_CONNECTED: (
        "These details are not connected to the entity in SEBI's listing. The "
        "registration may still be genuine, but this channel does not belong "
        "to it."
    ),
    MISMATCH: (
        "These details do not match the entity in SEBI's listing. Treat this as "
        "a likely impersonation attempt."
    ),
    CANNOT_VERIFY: (
        "A connection could not be confirmed or ruled out with the information "
        "available."
    ),
}

#: Signals that mean "act on this", mapped to the risk they carry.  Includes
#: the entity-level rules as well as per-channel flags -- a suspended
#: registration is the most serious finding the tool can produce, so it must
#: not be scored as "no risk".
RISK_BY_FLAG: dict[str, str] = {
    "ENTITY_NOT_ACTIVE": "high",
    "DISPLAYED_NAME_CONTRADICTS_LISTING": "high",
    "REG_NO_NOT_IN_SEBI_LISTING": "high",
    "LOOKALIKE_DOMAIN": "high",
    "DOMAIN_NOT_REGISTERED": "high",
    "PAYEE_NAME_MISMATCH": "high",
    "PAYEE_PERSONAL_ACCOUNT": "high",
    "PERSONAL_ACCOUNT_PAYEE": "high",
    "REG_NO_FORMAT_UNRECOGNISED": "medium",
    "RECENTLY_REGISTERED_DOMAIN": "medium",
    "MOBILE_NUMBER_COLD_CALL": "medium",
    "RUNNING_SCAM_MESSAGE": "high",
    "DOMAIN_STATUS_SUSPECT": "low",
    "DOMAIN_NO_REGISTRATION_RECORD": "medium",
    "PAYEE_NAME_DIFFERS_FROM_REGISTER": "medium",
    "INVALID_IFSC_FORMAT": "low",
}

RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class VerificationInput:
    """Everything the user can supply.

    Two groups, and the distinction matters: the channel details are what they
    were handed, and the `sebi_*` fields are what they read back off SEBI's
    official listing.  Without the latter the tool has no anchor.
    """

    # ---- what you were given ------------------------------------------- #
    website: str | None = None
    upi_id: str | None = None
    bank_account: str | None = None
    ifsc: str | None = None
    payee_name: str | None = None
    phone: str | None = None
    message_text: str | None = None

    # ---- the identity you were shown ------------------------------------ #
    registration_number: str | None = None
    entity_name: str | None = None

    # ---- what SEBI's listing shows (the anchor) -------------------------- #
    sebi_entity_name: str | None = None
    sebi_entity_type: str | None = None
    sebi_status: str | None = None
    sebi_website: str | None = None
    sebi_upi: str | None = None
    sebi_phone: str | None = None
    sebi_payee_name: str | None = None

    def supplied_channels(self) -> list[str]:
        channels = []
        if (self.website or "").strip():
            channels.append("website")
        if any((self.upi_id, self.bank_account, self.payee_name)):
            channels.append("payment")
        if (self.phone or "").strip():
            channels.append("phone")
        return channels

    def build_anchor(self) -> Entity:
        """Assemble the SEBI-listed reference, if the user supplied one."""
        return build_reference(
            name=self.sebi_entity_name or "",
            registration_number=self.registration_number or "",
            entity_type=self.sebi_entity_type or "unknown",
            status=self.sebi_status or "unknown",
            official_website=self.sebi_website,
            official_upi=self.sebi_upi,
            official_phone=self.sebi_phone,
            official_payee_name=self.sebi_payee_name,
        )


@dataclass
class Verdict:
    """The complete, explainable answer."""

    overall_verdict: str
    confidence: str
    headline: str
    #: True when the user supplied enough from SEBI's listing to anchor the
    #: comparison.  False means the result is a risk assessment only.
    reference_supplied: bool = False
    risk_level: str = "none"
    risk_flags: list[str] = field(default_factory=list)
    entity: dict | None = None
    channels: list[ChannelResult] = field(default_factory=list)
    behaviour: BehaviourReport = field(default_factory=BehaviourReport)
    rules_fired: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    sebi_check_url: str = ""

    @property
    def label(self) -> str:
        return VERDICT_LABELS[self.overall_verdict]

    def to_dict(self) -> dict:
        return {
            "overall_verdict": self.overall_verdict,
            "verdict_label": self.label,
            "confidence": self.confidence,
            "headline": self.headline,
            "reference_supplied": self.reference_supplied,
            "risk_level": self.risk_level,
            "risk_flags": self.risk_flags,
            "entity": self.entity,
            "channels": [c.to_dict() for c in self.channels],
            "behaviour": self.behaviour.to_dict(),
            "rules_fired": self.rules_fired,
            "reasons": self.reasons,
            "evidence": [e.to_dict() for e in self.evidence],
            "recommended_next_steps": self.next_steps,
            "sebi_check_url": self.sebi_check_url,
        }


# --------------------------------------------------------------------------- #
# Next-step guidance
# --------------------------------------------------------------------------- #

SEBI_CHECK_STEP = (
    "Open SEBI Check (link below) and enter the registration number yourself. "
    "SEBI's listing is the only authoritative source; this tool holds no SEBI "
    "data."
)

NEXT_STEPS: dict[str, list[str]] = {
    CONNECTED: [
        "Confirm the registration directly on SEBI's portal before transferring "
        "any funds, and pay only to the account SEBI's listing names.",
        SEBI_CHECK_STEP,
    ],
    NOT_CONNECTED: [
        "Do not act on the details you supplied. Contact the firm using only the "
        "details from SEBI's official listing.",
        "If you were approached through this channel, report it to SEBI's "
        "complaint portal and to cybercrime.gov.in.",
        SEBI_CHECK_STEP,
    ],
    MISMATCH: [
        "Do not transfer any money to these details.",
        "Do not install any app or share documents, OTPs or screen access with "
        "this contact.",
        "Report the channel to SEBI's complaint portal, and to cybercrime.gov.in "
        "if money has already moved.",
        SEBI_CHECK_STEP,
    ],
    CANNOT_VERIFY: [
        "Confirm the registration on SEBI's portal and compare the official "
        "website, phone and payment details it shows against what you were given.",
        SEBI_CHECK_STEP,
    ],
}

#: Used when no SEBI reference was supplied.  The generic CANNOT_VERIFY steps
#: assume an entity record was available; here none was.
UNANCHORED_NEXT_STEPS: list[str] = [
    "Open SEBI Check (link below) and look up the registration number. SEBI's "
    "listing is the only authoritative source, and this tool holds no SEBI data.",
    "Copy the official website, phone number and payment details SEBI shows for "
    "that registration, paste them into the 'From SEBI's listing' fields, and "
    "run the check again to see whether what you were given actually belongs to "
    "the entity.",
    "Do not transfer money until that comparison has been made.",
]


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

class VerdictEngine:
    """Holds its live data sources so tests can inject fakes."""

    def __init__(
        self,
        cfg: Settings | None = None,
        intel_provider=None,
        link_checker=None,
    ) -> None:
        self.cfg = cfg or default_settings
        self.intel_provider = intel_provider or build_intel_provider(self.cfg)
        self.link_checker = link_checker or build_link_checker(self.cfg)

    # -- public API -------------------------------------------------------- #

    def verify(self, payload: VerificationInput) -> Verdict:
        anchor = payload.build_anchor()
        registry_record, registry_note, registry_rule = self._registry_lookup(payload, anchor)
        registry_conflicts: list[str] = []
        if registry_record:
            anchor, registry_conflicts = self._merge_registry(anchor, registry_record)
        anchored = self._is_anchored(anchor)

        channels = self._run_channels(anchor if anchored else None, payload)
        behaviour = scan_message(payload.message_text or "")

        rules: list[str] = []
        reasons: list[str] = []
        evidence: list[Evidence] = []
        headline_override: str | None = None

        # ---- a check that needs no data source at all -------------------- #
        if (payload.registration_number or "").strip():
            reg_no = normalize_reg_no(payload.registration_number)
            if looks_like_sebi_reg_no(reg_no):
                evidence.append(Evidence(
                    "identity", "Registration number format",
                    f"{reg_no} is well-formed (a 12-character SEBI registration "
                    "number). Format alone proves nothing -- a real number is "
                    "exactly what an impersonator borrows.",
                    "heuristic",
                ))
            else:
                rules.append("REG_NO_FORMAT_UNRECOGNISED")
                evidence.append(Evidence(
                    "identity", "Registration number format",
                    f"'{payload.registration_number}' is not in SEBI's "
                    "registration number format (three letters plus nine digits)",
                    "heuristic",
                ))
                reasons.append(
                    "The number you were given is not in SEBI's registration "
                    "number format. If the firm supplied it, it is not a SEBI "
                    "registration number; if you typed it, check for a typo."
                )

        # ---- entity-level rules, applied before channel aggregation ------ #
        if registry_note is not None:
            evidence.append(registry_note)
            if registry_rule:
                rules.append(registry_rule)

        # Anything the user typed that disagrees with the listing file.  Shown
        # rather than silently resolved, because a disagreement here is itself
        # worth the user's attention.
        if registry_conflicts:
            rules.append("USER_INPUT_DIFFERS_FROM_LISTING")
            reasons.extend(registry_conflicts)

        if anchored and not anchor.is_active and anchor.status != "unknown":
            rules.append("ENTITY_NOT_ACTIVE")
            reasons.append(
                f"SEBI's listing shows the status of this registration as "
                f"'{anchor.status}', not active. A displayed credential that is "
                "no longer valid is exactly the 'borrowed badge' case."
            )
            overall, confidence = MISMATCH, HIGH

        elif anchored and self._name_contradicts(anchor, payload):
            rules.append("DISPLAYED_NAME_CONTRADICTS_LISTING")
            reasons.append(
                f"The name you were shown does not correspond to the entity this "
                f"registration belongs to in SEBI's listing ({anchor.name})."
            )
            overall, confidence = MISMATCH, HIGH

        elif not channels and not anchored:
            rules.append("NOTHING_TO_CHECK")
            reasons.append(
                "No channel details and nothing from SEBI's listing were "
                "supplied, so there was nothing to check."
            )
            overall, confidence = CANNOT_VERIFY, LOW

        elif not channels:
            # The record was found but nobody asked about a channel.  Report
            # the registration as the finding it is rather than as a failure.
            rules.append("NO_CHANNEL_SUPPLIED")
            reasons.append(
                f"SEBI's record for this registration was found: {anchor.name} "
                f"({anchor.registration_number}), status '{anchor.status}'. "
                "No website, payment detail, phone number or message was "
                "supplied, so there is nothing to compare against the record."
            )
            if anchor.official_phone_numbers:
                reasons.append(
                    "Contact number on SEBI's record: "
                    + ", ".join(anchor.official_phone_numbers)
                    + ". Compare it against whatever the caller gave you."
                )
            reasons.append(
                "The registration itself checks out. That is not the same as the "
                "channel being genuine -- a real registration number is exactly "
                "what an impersonator borrows."
            )
            overall, confidence = CANNOT_VERIFY, LOW
            headline_override = (
                f"Registration found: {anchor.name} holds "
                f"{anchor.registration_number} (status {anchor.status})."
            )

        else:
            overall = worst([c.verdict for c in channels]) if channels else CANNOT_VERIFY
            confidence = self._aggregate_confidence(channels, overall)
            reasons.extend(self._explain_overall(overall, channels, anchored))

        rules.extend(r for c in channels for r in c.rules)
        rules = list(dict.fromkeys(rules))

        evidence.extend(e for c in channels for e in c.evidence)

        # ---- risk assessment over every signal we gathered ---------------- #
        # Entity-level rules count too, not just per-channel flags: a lapsed
        # registration or a contradicted name are findings in their own right.
        channel_flags = [f for c in channels for f in c.flags]
        rule_flags = [r for r in rules if r in RISK_BY_FLAG]
        risk_flags = list(dict.fromkeys(channel_flags + rule_flags))
        risk_level, risk_reasons = self._risk(risk_flags, behaviour)
        reasons.extend(risk_reasons)

        # ---- headline ------------------------------------------------------ #
        if not anchored:
            headline = self._unanchored_headline(risk_level)
        elif headline_override:
            headline = headline_override
        else:
            headline = HEADLINES[overall]
            if behaviour.flags and overall in (CONNECTED, CANNOT_VERIFY):
                headline += (
                    " The pasted message also contains pressure or red-flag "
                    "language, which is reported separately below."
                )

        return Verdict(
            overall_verdict=overall,
            confidence=confidence,
            headline=headline,
            reference_supplied=anchored,
            risk_level=risk_level,
            risk_flags=risk_flags,
            entity=anchor.to_public_dict() if anchored else None,
            channels=channels,
            behaviour=behaviour,
            rules_fired=rules,
            reasons=reasons,
            evidence=evidence,
            next_steps=UNANCHORED_NEXT_STEPS if not anchored else NEXT_STEPS[overall],
            sebi_check_url=sebi_check_url(payload.registration_number or "", self.cfg),
        )

    # -- internals --------------------------------------------------------- #

    @staticmethod
    def _is_anchored(entity: Entity) -> bool:
        """Is there enough of a record to compare a channel against?"""
        return entity.provided and bool(
            entity.official_domains
            or entity.known_valid_upi_handles
            or entity.official_phone_numbers
            or entity.official_bank_payee_name
            or entity.status not in ("", "unknown")
        )

    def _registry_lookup(
        self, payload: VerificationInput, anchor: Entity
    ) -> tuple[dict | None, Evidence | None, str]:
        """Consult the locally imported SEBI listing, if one has been loaded.

        This is the only place the app reads real registry data, and that data
        arrived as a file a human saved -- nothing is fetched.  Absence of a
        registration number is only treated as meaningful when the relevant
        category has actually been imported; otherwise it just means the
        operator has not loaded that category yet.
        """
        reg_no = normalize_reg_no(payload.registration_number or "")
        if not reg_no:
            return None, None, ""

        try:
            registry = get_registry()
            record = registry.lookup(reg_no)
        except Exception:  # pragma: no cover - registry is optional
            return None, None, ""

        if record:
            imported = (record.get("imported_at") or "")[:10]
            return record, Evidence(
                "identity", "Locally imported SEBI listing",
                f"{reg_no} found: {record['name']} "
                f"(status {record['status']}, validity {record['validity_raw'] or 'not stated'})"
                f"{'; listing imported ' + imported if imported else ''}",
                "sebi-listing-file",
            ), ""

        # Not found.  Say something only if we can fairly say it.
        expected_type = TYPE_BY_REG_PREFIX.get(reg_no[:3], "")
        try:
            covered = registry.count_by_type()
            complete = registry.is_complete_for(expected_type) if expected_type else False
            partial = [
                e for e in registry.coverage()
                if e["entity_type"] == expected_type and not e["complete"]
            ]
        except Exception:  # pragma: no cover
            covered, complete, partial = {}, False, []

        if expected_type and covered.get(expected_type) and complete:
            return None, Evidence(
                "identity", "Locally imported SEBI listing",
                f"{reg_no} is not present in the imported SEBI listing, which "
                f"holds the complete set of {covered[expected_type]} "
                f"{expected_type.replace('_', ' ')} records",
                "sebi-listing-file",
            ), "REG_NO_NOT_IN_SEBI_LISTING"

        # Some data for this type, but not all of it.  Absence proves nothing:
        # SEBI publishes brokers per segment and the segments overlap, so a
        # broker registered only in a segment we have not imported would be
        # wrongly reported as unregistered.
        if expected_type and covered.get(expected_type):
            try:
                missing = registry.missing_categories(expected_type)
            except Exception:  # pragma: no cover
                missing = []
            held = sum(e["imported_count"] for e in registry.coverage()
                       if e["entity_type"] == expected_type)
            detail = f"{held} {expected_type.replace('_', ' ')} records are loaded"
            if missing:
                detail += (
                    f", but {len(missing)} of SEBI's listings for this type have "
                    f"not been imported (category {', '.join(missing)})"
                )
            else:
                detail += ", but at least one listing is only partly imported"
            return None, Evidence(
                "identity", "Locally imported SEBI listing",
                f"{reg_no} was not found. {detail}, so its absence means nothing "
                "either way",
                "sebi-listing-file", missing=True,
            ), ""

        return None, Evidence(
            "identity", "Locally imported SEBI listing",
            f"{reg_no} could not be checked against an imported listing "
            "(that category has not been loaded)",
            "sebi-listing-file", missing=True,
        ), ""

    @staticmethod
    def _merge_registry(anchor: Entity, record: dict) -> tuple[Entity, list[str]]:
        """Fold the registry record into the anchor.  Returns (entity, conflicts).

        The registry is **authoritative** for the factual fields it holds: it is
        machine-read from SEBI's own file, not typed by hand.  A typed value
        must not be able to override it, because doing so can silence a real
        finding -- a user who puts 'active' over a suspended record would turn
        a MISMATCH into "no warning sign found".  That is the borrowed-badge
        case being waved through by a mistyped form.

        Where the two disagree the registry's value is kept and the conflict is
        reported, so the disagreement is visible instead of resolved silently.

        Only the fields the registry cannot supply -- website, UPI handle,
        payee name -- are taken from the user.
        """
        conflicts: list[str] = []

        registry_name = (record.get("name") or "").strip()
        registry_status = (record.get("status") or "").strip()

        if anchor.name and registry_name:
            score = similarity(strip_company_noise(anchor.name),
                               strip_company_noise(registry_name))
            if score < default_settings.name_conflict_threshold:
                conflicts.append(
                    f"The name you entered from SEBI's listing ('{anchor.name}') "
                    f"does not match the registered name on record "
                    f"('{registry_name}'). The record has been used."
                )

        if (anchor.status not in ("", "unknown")
                and registry_status
                and anchor.status.lower() != registry_status.lower()):
            conflicts.append(
                f"You recorded the status as '{anchor.status}', but SEBI's "
                f"listing file says '{registry_status}'. The listing file has "
                "been used -- re-download it if you believe it is out of date."
            )

        phones = tuple(anchor.official_phone_numbers)
        registry_phone = (record.get("telephone") or "").strip()
        # The registry's number is preferred; a user-typed one is a fallback.
        if registry_phone:
            if phones and phones[0] != registry_phone:
                conflicts.append(
                    f"You recorded the phone as {phones[0]}, but SEBI's listing "
                    f"file says {registry_phone}."
                )
            phones = (registry_phone,)

        return Entity(
            name=registry_name or anchor.name,
            registration_number=record.get("registration_number") or anchor.registration_number,
            entity_type=(
                record.get("entity_type")
                if record.get("entity_type") not in (None, "", "unknown")
                else anchor.entity_type
            ),
            status=registry_status or anchor.status,
            registered_address=(record.get("address") or "").strip() or anchor.registered_address,
            official_domains=anchor.official_domains,
            official_phone_numbers=phones,
            known_valid_upi_handles=anchor.known_valid_upi_handles,
            official_bank_payee_name=anchor.official_bank_payee_name,
            official_bank_accounts=anchor.official_bank_accounts,
            source="sebi-listing-file",
        ), conflicts

    def _run_channels(
        self, anchor: Entity | None, payload: VerificationInput
    ) -> list[ChannelResult]:
        """Run only the channels the user asked about.

        `anchor` is None when there is nothing to compare against; the
        analysers then report live facts with a CANNOT_VERIFY verdict rather
        than inventing a comparison.
        """
        results: list[ChannelResult] = []

        if (payload.website or "").strip():
            results.append(analyze_website(
                anchor, payload.website, cfg=self.cfg,
                intel_provider=self.intel_provider,
                link_checker=self.link_checker,
            ))

        payment = analyze_payment(
            anchor,
            PaymentInputs(
                upi_id=payload.upi_id,
                bank_account=payload.bank_account,
                ifsc=payload.ifsc,
                payee_name=payload.payee_name,
            ),
            cfg=self.cfg,
        )
        if payment is not None:
            results.append(payment)

        phone = analyze_phone(anchor, payload.phone or "", cfg=self.cfg)
        if phone is not None:
            results.append(phone)

        return results

    @staticmethod
    def _name_contradicts(anchor: Entity, payload: VerificationInput) -> bool:
        """Is the displayed name a different firm from the listed one?

        Compares the *distinguishing* tokens only.  Two firms called
        'Gokul Broking Pvt Ltd' and 'Dhanvarsha Broking Pvt Ltd' share
        'Broking Pvt Ltd', which drags a whole-string comparison up to ~0.68
        and would hide that they are entirely different businesses.
        """
        claimed = (payload.entity_name or "").strip()
        listed = (anchor.name or "").strip()
        if not claimed or not listed:
            return False
        score = similarity(strip_company_noise(claimed), strip_company_noise(listed))
        return score < default_settings.name_conflict_threshold

    @staticmethod
    def _aggregate_confidence(channels: list[ChannelResult], overall: str) -> str:
        """Confidence of the worst verdict, lifted when it repeats.

        CANNOT_VERIFY is capped at Medium by design.  Confidence here reads as
        "how sure are you of this finding", and a finding of *absence* should
        never present as a confident claim.
        """
        matching = [c for c in channels if c.verdict == overall]
        if not matching:
            return LOW
        base = max((c.confidence for c in matching), key=lambda x: CONFIDENCE_RANK[x])
        if len(matching) >= 2 and base == MEDIUM:
            base = HIGH
        if overall == CANNOT_VERIFY and base == HIGH:
            base = MEDIUM
        return base

    @staticmethod
    def _explain_overall(
        overall: str, channels: list[ChannelResult], anchored: bool
    ) -> list[str]:
        parts: list[str] = []
        if overall == CONNECTED:
            confirmed = ", ".join(c.channel for c in channels if c.verdict == CONNECTED)
            parts.append(
                f"Every channel supplied ({confirmed}) was confirmed as belonging "
                "to the entity in SEBI's listing."
            )
        elif overall == MISMATCH:
            bad = [c for c in channels if c.verdict == MISMATCH]
            if bad:
                parts.append(
                    "At least one channel is incompatible with the entity in "
                    "SEBI's listing: "
                    + "; ".join(f"{c.channel} - {c.reasons[0]}" for c in bad if c.reasons) + "."
                )
        elif overall == NOT_CONNECTED:
            bad = ", ".join(c.channel for c in channels if c.verdict == NOT_CONNECTED)
            parts.append(
                f"No link was found for: {bad}. SEBI's listing records details "
                "for these channels, which is why this is recorded as definitely "
                "not connected rather than unverified."
            )
        else:
            unclear = ", ".join(c.channel for c in channels if c.verdict == CANNOT_VERIFY)
            if unclear:
                parts.append(
                    f"A connection could not be confirmed or ruled out for: {unclear}."
                )
            if not anchored:
                parts.append(
                    "Nothing from SEBI's listing was supplied, so there was no "
                    "authoritative record to compare any channel against."
                )
        return parts

    @staticmethod
    def _risk(risk_flags: list[str], behaviour: BehaviourReport) -> tuple[str, list[str]]:
        """Fold every signal into one risk level, with the reasons why."""
        level = "none"
        reasons: list[str] = []

        for flag in risk_flags:
            mapped = RISK_BY_FLAG.get(flag, "low")
            if RISK_RANK[mapped] > RISK_RANK[level]:
                level = mapped

        if behaviour.risk_level != "none":
            mapped = "high" if behaviour.risk_level == "high" else behaviour.risk_level
            if RISK_RANK[mapped] > RISK_RANK[level]:
                level = mapped

        if level == "high":
            reasons.append(
                "Risk assessment: high. At least one strong impersonation signal "
                "was found in what you supplied."
            )
        elif level == "medium":
            reasons.append(
                "Risk assessment: medium. Something in what you supplied is worth "
                "questioning before you act."
            )
        elif level == "low":
            reasons.append(
                "Risk assessment: low. One minor signal was noted, but nothing "
                "conclusive."
            )
        else:
            reasons.append(
                "Risk assessment: no specific warning sign was found in what you "
                "supplied. That is not confirmation that the channel is genuine."
            )
        return level, reasons

    @staticmethod
    def _unanchored_headline(risk_level: str) -> str:
        if risk_level == "high":
            return (
                "Risk indicators found, and the registration could not be "
                "confirmed. Confirm it on SEBI's listing before doing anything."
            )
        if risk_level == "medium":
            return (
                "Some details are worth questioning, and the registration could "
                "not be confirmed. Confirm it on SEBI's listing."
            )
        return (
            "No specific warning sign was found, but the registration could not "
            "be confirmed. A registration number can only be confirmed on SEBI's "
            "own listing."
        )


# --------------------------------------------------------------------------- #
# Convenience
# --------------------------------------------------------------------------- #

_default_engine: VerdictEngine | None = None


def get_engine() -> VerdictEngine:
    """Process-wide engine, built lazily so imports stay cheap."""
    global _default_engine
    if _default_engine is None:
        _default_engine = VerdictEngine()
    return _default_engine


def verify(payload: VerificationInput) -> Verdict:
    return get_engine().verify(payload)
