"""Module A -- entity_reference.

The register anchor, without a register.

Borrowed Badge holds no SEBI data. That is a deliberate constraint, not an
oversight: SEBI's portal detects and blocks automated access ("Unauthorized
Request Blocked", with a security contact for appeals) and publishes no API.
Scraping it would be circumventing an access control, so the tool does not.

What replaces the seed dataset is a *hand-off*: the user is sent to SEBI's own
listing for the registration number, reads the entity's official details, and
supplies them here. Those supplied details become the anchor.

That distinction matters for what the tool may claim. With an anchor, the
original question is answerable -- "is this channel attached to the entity that
holds this registration?" Without one, it is not, and the engine says so
rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import Settings, settings as default_settings
from .textmatch import normalize_text, similarity, strip_company_noise
from .types import PAYMENT, PHONE, WEBSITE

#: Where a reference came from.  Only SEBI's own listing is authoritative; the
#: value is carried through to the UI so a verdict can never be mistaken for a
#: regulator's answer.
SOURCE_SEBI_LISTING = "sebi-listing"
SOURCE_UNSUPPLIED = "unsupplied"


def normalize_reg_no(reg_no: str) -> str:
    """Registration numbers are compared case- and space-insensitively."""
    return "".join(ch for ch in (reg_no or "").upper() if ch.isalnum())


def looks_like_sebi_reg_no(value: str) -> bool:
    """Shape check for a SEBI registration number.

    Real numbers are 12 characters: a three-letter series prefix (INZ stock
    broker, INA investment adviser, INH research analyst -- also INB/INF/INE
    for older broker registrations) followed by nine digits.  This validates
    *format* only.  A well-formed number is trivially forged, which is the
    entire premise of this tool.
    """
    token = normalize_reg_no(value)
    if len(token) != 12:
        return False
    prefix, digits = token[:3], token[3:]
    return prefix.startswith("IN") and prefix.isalpha() and digits.isdigit()


@dataclass(frozen=True)
class Entity:
    """The entity as described by the user, read off SEBI's official listing.

    Field names deliberately mirror the register vocabulary so the analysers
    did not have to change shape when the data source disappeared.
    """

    name: str = ""
    registration_number: str = ""
    entity_type: str = "unknown"          # stock_broker | investment_adviser | research_analyst | unknown
    status: str = "unknown"               # active | suspended | expired | unknown
    registered_address: str = ""
    official_domains: tuple[str, ...] = ()
    official_phone_numbers: tuple[str, ...] = ()
    known_valid_upi_handles: tuple[str, ...] = ()
    official_bank_payee_name: str | None = None
    official_bank_accounts: tuple[str, ...] = ()
    source: str = SOURCE_UNSUPPLIED

    # -- what the record can actually support ------------------------------ #

    @property
    def provided(self) -> bool:
        """True when the user supplied anything usable as an anchor."""
        return bool(
            self.name or self.registration_number or self.official_domains
            or self.official_phone_numbers or self.known_valid_upi_handles
            or self.official_bank_payee_name or self.official_bank_accounts
        )

    @property
    def is_active(self) -> bool:
        return self.status.strip().lower() == "active"

    @property
    def richness(self) -> dict[str, bool]:
        """Which channel types the reference actually pins down.

        This is the safeguard that keeps the tool honest about small firms.
        If SEBI's listing shows no website for an entity, a website the user
        was sent to cannot be compared to anything, so the honest answer is
        CANNOT VERIFY -- not NOT CONNECTED.  Accusing a legitimate one-person
        advisory because SEBI's record is sparse would be a false accusation.
        """
        return {
            WEBSITE: bool(self.official_domains),
            PHONE: bool(self.official_phone_numbers),
            PAYMENT: bool(
                self.known_valid_upi_handles
                or self.official_bank_accounts
                or self.official_bank_payee_name
            ),
        }

    def has_channel_data(self, channel: str) -> bool:
        return self.richness.get(channel, False)

    def matches_name(self, candidate: str) -> float:
        """Similarity of `candidate` to this entity's name (0.0-1.0)."""
        if not candidate:
            return 0.0
        return max(
            similarity(candidate, self.name),
            similarity(strip_company_noise(candidate), strip_company_noise(self.name)),
        )

    def to_public_dict(self) -> dict:
        """What may be shown back to the user.

        Full bank account numbers are excluded -- matching uses them, display
        does not need them.
        """
        return {
            "name": self.name,
            "registration_number": self.registration_number,
            "entity_type": self.entity_type,
            "status": self.status,
            "registered_address": self.registered_address,
            "official_domains": list(self.official_domains),
            "official_phone_numbers": list(self.official_phone_numbers),
            "known_valid_upi_handles": list(self.known_valid_upi_handles),
            "official_bank_payee_name": self.official_bank_payee_name,
            "channel_data_available": self.richness,
            "source": self.source,
        }


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #

def _clean_tuple(values: Iterable[str] | None) -> tuple[str, ...]:
    """Drop blanks and duplicates, preserving order."""
    if not values:
        return ()
    seen: list[str] = []
    for value in values:
        text = (value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return tuple(seen)


def build_reference(
    *,
    name: str = "",
    registration_number: str = "",
    entity_type: str = "unknown",
    status: str = "unknown",
    registered_address: str = "",
    official_website: str | None = None,
    official_upi: str | None = None,
    official_phone: str | None = None,
    official_payee_name: str | None = None,
    official_bank_account: str | None = None,
) -> Entity:
    """Assemble the anchor from details the user read off SEBI's listing.

    Everything is optional.  What the user leaves blank simply cannot be
    checked, and the engine will say CANNOT VERIFY for that channel rather
    than implying a mismatch.
    """
    return Entity(
        name=(name or "").strip(),
        registration_number=normalize_reg_no(registration_number),
        entity_type=(entity_type or "unknown").strip() or "unknown",
        status=(status or "unknown").strip() or "unknown",
        registered_address=(registered_address or "").strip(),
        official_domains=_clean_tuple([official_website] if official_website else []),
        official_phone_numbers=_clean_tuple([official_phone] if official_phone else []),
        known_valid_upi_handles=_clean_tuple([official_upi] if official_upi else []),
        official_bank_payee_name=(official_payee_name or "").strip() or None,
        official_bank_accounts=_clean_tuple([official_bank_account] if official_bank_account else []),
        source=SOURCE_SEBI_LISTING,
    )


def sebi_check_url(registration_number: str = "", cfg: Settings | None = None) -> str:
    """The authoritative place to confirm a registration.

    SEBI Check accepts no query parameters -- it is a POST form -- so the user
    carries the number across.  The UI shows it with a copy button beside this
    link rather than pretending the value is prefilled.
    """
    cfg = cfg or default_settings
    return cfg.sebi_check_url
