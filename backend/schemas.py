"""Pydantic request/response models for the HTTP API.

These mirror the dicts produced by `Verdict.to_dict()`.  They exist so the
OpenAPI schema is accurate and so a malformed request is rejected at the edge
rather than deep inside a rule.

Note the two-group shape of the request: `website`/`upi_id`/... are the
channel details the user was handed, and `sebi_*` are what they read back off
SEBI's official listing.  The second group is the anchor; without it the tool
can describe risk but cannot confirm a connection.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #

class VerifyRequest(BaseModel):
    # ---- what you were given ------------------------------------------- #
    website: str | None = Field(
        default=None, max_length=2048, description="Website or domain to check."
    )
    upi_id: str | None = Field(default=None, max_length=120)
    bank_account: str | None = Field(default=None, max_length=32)
    ifsc: str | None = Field(default=None, max_length=16)
    payee_name: str | None = Field(
        default=None, max_length=200,
        description="The account holder name as it appears in your banking app.",
    )
    phone: str | None = Field(default=None, max_length=24)
    message_text: str | None = Field(
        default=None, max_length=20000,
        description=(
            "Optional pasted message. Scanned in memory for red-flag language "
            "and never stored."
        ),
    )

    # ---- the identity you were shown ------------------------------------ #
    registration_number: str | None = Field(
        default=None, max_length=64,
        description="The SEBI registration number you were given.",
        examples=["INZ000031633"],
    )
    entity_name: str | None = Field(
        default=None, max_length=200,
        description="The firm name you were shown.",
    )

    # ---- what SEBI's listing shows (the anchor) -------------------------- #
    sebi_entity_name: str | None = Field(
        default=None, max_length=200,
        description="Entity name as shown on SEBI's own listing.",
    )
    sebi_entity_type: str | None = Field(
        default=None, max_length=64,
        description="stock_broker | investment_adviser | research_analyst",
    )
    sebi_status: str | None = Field(
        default=None, max_length=32, description="active | suspended | expired"
    )
    sebi_website: str | None = Field(
        default=None, max_length=2048, description="Official website per SEBI."
    )
    sebi_upi: str | None = Field(
        default=None, max_length=120, description="Official UPI handle per SEBI."
    )
    sebi_phone: str | None = Field(
        default=None, max_length=24, description="Official phone number per SEBI."
    )
    sebi_payee_name: str | None = Field(
        default=None, max_length=200,
        description="Official account payee name per SEBI.",
    )

    @model_validator(mode="after")
    def _require_something_to_check(self) -> "VerifyRequest":
        supplied = [
            self.website, self.upi_id, self.bank_account, self.payee_name,
            self.phone, self.registration_number, self.entity_name,
        ]
        if not any((value or "").strip() for value in supplied):
            raise ValueError(
                "Supply at least one detail to check: a website, a payment "
                "detail, a phone number, or the registration number you were given."
            )
        return self


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #

class EvidenceModel(BaseModel):
    channel: str
    check: str
    found: str
    source: str
    missing: bool = False


class ChannelModel(BaseModel):
    channel: str
    verdict: str
    label: str
    confidence: str
    reasons: list[str]
    evidence: list[EvidenceModel]
    rules_fired: list[str]
    flags: list[str]


class BehaviourFlagModel(BaseModel):
    id: str
    label: str
    severity: str
    explanation: str
    matched_text: list[str]


class BehaviourModel(BaseModel):
    scanned: bool
    risk_level: str
    flags: list[BehaviourFlagModel]
    note: str


class VerdictResponse(BaseModel):
    overall_verdict: str
    verdict_label: str
    confidence: str
    headline: str
    reference_supplied: bool = False
    risk_level: str = "none"
    risk_flags: list[str] = []
    entity: dict[str, Any] | None = None
    channels: list[ChannelModel]
    behaviour: BehaviourModel
    rules_fired: list[str]
    reasons: list[str]
    evidence: list[EvidenceModel]
    recommended_next_steps: list[str]
    sebi_check_url: str


class HealthResponse(BaseModel):
    status: str
    mode: str
    data_source: str
    sebi_registry: dict[str, Any] = {}
    disclaimer: str
