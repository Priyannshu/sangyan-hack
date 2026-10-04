"""Configuration for Borrowed Badge.

Everything tunable lives here and can be overridden with an environment
variable, so nothing is magic-numbered deep inside a rule.  Thresholds are
deliberately conservative: this tool's worst failure mode is accusing an
honest firm.

There is no dataset path here.  The app holds no registry data at all -- the
entity anchor comes from what the user reads off SEBI's own listing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    # ---- local SEBI registry ------------------------------------------- #
    # Built from listing files the operator saves from SEBI in a normal
    # browser.  Never fetched automatically -- see sebi_registry.py.  Absence
    # is fine: the app falls back to the manual hand-off.
    registry_db: Path = field(
        default_factory=lambda: Path(
            os.environ.get("BB_REGISTRY_DB", str(ROOT / "data" / "sebi_registry.db"))
        )
    )

    # ---- network ------------------------------------------------------- #
    # RDAP is a public protocol meant for exactly this kind of lookup, so live
    # lookups are on by default.  The timeout is short and every failure
    # degrades to "no registration data" rather than raising.
    http_timeout_seconds: float = field(
        default_factory=lambda: _env_float("BB_HTTP_TIMEOUT", 6.0)
    )
    rdap_base_url: str = field(
        default_factory=lambda: os.environ.get("BB_RDAP_BASE", "https://rdap.org/domain/")
    )
    #: How much of an official site's homepage to pull when checking for a
    #: reverse link.  One request, capped, not a crawl.
    max_homepage_bytes: int = field(
        default_factory=lambda: _env_int("BB_MAX_HOMEPAGE_BYTES", 300_000)
    )

    # ---- thresholds ----------------------------------------------------- #
    payee_match_threshold: float = field(
        default_factory=lambda: _env_float("BB_PAYEE_THRESHOLD", 0.82)
    )
    payee_mismatch_threshold: float = field(
        default_factory=lambda: _env_float("BB_PAYEE_MISMATCH", 0.55)
    )
    lookalike_threshold: float = field(
        default_factory=lambda: _env_float("BB_LOOKALIKE_THRESHOLD", 0.80)
    )
    lookalike_max_edit_distance: int = field(
        default_factory=lambda: _env_int("BB_LOOKALIKE_EDIT_DISTANCE", 4)
    )
    #: A domain younger than this is worth calling out in the reasons.
    young_domain_days: int = field(
        default_factory=lambda: _env_int("BB_YOUNG_DOMAIN_DAYS", 90)
    )
    #: Below this, a displayed name belongs to a different firm.
    name_conflict_threshold: float = field(
        default_factory=lambda: _env_float("BB_NAME_CONFLICT_THRESHOLD", 0.55)
    )

    # ---- privacy -------------------------------------------------------- #
    # Spec section 5: no persistence of user inputs.  This flag is documented
    # so the choice is visible rather than accidental.
    persist_user_inputs: bool = field(
        default_factory=lambda: _env_bool("BB_PERSIST_INPUTS", False)
    )

    # ---- output --------------------------------------------------------- #
    sebi_check_url: str = "https://siportal.sebi.gov.in/intermediary/sebi-check"
    api_title: str = "Borrowed Badge API"


settings = Settings()
