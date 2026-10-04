"""UPI ownership verification, via a commercial VPA lookup.

The gap this closes
-------------------
A UPI ID's *shape* can be checked locally (see payment_analyzer), but not its
*ownership*: SEBI publishes no UPI-to-entity mapping, and SEBI Check's
"CHECK VERIFIED UPI ID" tool sits behind bot protection with no API.  So a
well-formed handle belonging to somebody else was indistinguishable from the
entity's own handle.

Closing that needs a source that resolves a VPA to its registered holder.  That
exists commercially: Razorpay, Eko and Juspay all offer VPA verification that
queries the NPCI network and returns the account holder's name.

This module is the integration point.  It is **off unless configured**, because
every provider requires a KYC'd account and an API key.

    BB_UPI_VERIFIER=razorpay
    BB_RAZORPAY_KEY_ID=...
    BB_RAZORPAY_KEY_SECRET=...

Nothing here talks to SEBI.  Nothing runs unless you supply credentials.

Status of the adapter
---------------------
The Razorpay request shape below follows their published `/v1/fund_accounts/
validations` (penny-drop) contract.  It has **not been exercised against a live
account**, so treat the first run as a test: log the raw response, confirm the
field names, and adjust.  Every other part of the system already works with the
verifier absent -- this only adds a stronger answer where one is available.
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .config import Settings, settings as default_settings

USER_AGENT = "BorrowedBadge/1.0 (+https://sangyan.getn.space)"


@dataclass
class UpiVerification:
    """What a VPA lookup told us."""

    vpa: str
    reachable: bool | None = None        # None = could not determine
    registered_name: str | None = None   # who the handle actually belongs to
    name_match_score: float | None = None
    detail: str = ""
    source: str = "unavailable"          # razorpay | eko | unavailable

    @property
    def usable(self) -> bool:
        return self.registered_name is not None or self.reachable is not None


class UpiVerifier(Protocol):
    def verify(self, vpa: str, expected_name: str | None = None) -> UpiVerification: ...


# --------------------------------------------------------------------------- #
# Razorpay
# --------------------------------------------------------------------------- #

class RazorpayUpiVerifier:
    """VPA verification through Razorpay's account-validation API.

    Uses the penny-drop validation, which returns the VPA's registered name and
    a name-match score when an expected name is supplied.
    """

    def __init__(self, key_id: str, key_secret: str, cfg: Settings) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.cfg = cfg

    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.key_id}:{self.key_secret}".encode()).decode()
        return "Basic " + token

    def verify(self, vpa: str, expected_name: str | None = None) -> UpiVerification:
        body = {
            "account_number": vpa,
            "fund_account": {"account_type": "vpa", "vpa": {"address": vpa}},
            "amount": 100,                      # ₹1 penny drop
            "currency": "INR",
            "notes": {"purpose": "borrowed-badge-pre-payment-check"},
        }
        if expected_name:
            body["name"] = expected_name

        request = urllib.request.Request(
            f"{self.cfg.razorpay_base_url}/v1/fund_accounts/validations",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": self._auth_header(),
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.http_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            return UpiVerification(vpa=vpa, detail=f"HTTP {exc.code}: {detail}", source="razorpay")
        except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError, OSError) as exc:
            return UpiVerification(vpa=vpa, detail=type(exc).__name__, source="razorpay")

        status = (payload.get("status") or "").lower()
        # Razorpay nests the answer under `validation_results` (confirmed against
        # their published response schema for /v1/fund_accounts/validations).
        result = payload.get("validation_results") or payload.get("results") or {}
        registered = result.get("registered_name") or payload.get("registered_name")
        score = result.get("name_match_score", payload.get("name_match_score"))
        account_status = (result.get("account_status") or "").lower()

        reachable: bool | None = None
        if account_status == "active" or status in {"completed", "valid"}:
            reachable = True
        elif account_status == "invalid" or status in {"failed", "invalid"}:
            reachable = False

        return UpiVerification(
            vpa=vpa,
            reachable=reachable,
            registered_name=registered,
            name_match_score=float(score) if isinstance(score, (int, float)) else None,
            detail=f"status={status or 'unknown'}",
            source="razorpay",
        )


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

def build_verifier(cfg: Settings | None = None) -> UpiVerifier | None:
    """Return a configured verifier, or None to run without one.

    Absence is the normal state: without credentials the payment channel falls
    back to local validation plus the payee-name comparison, and says
    CANNOT VERIFY where ownership cannot be established.
    """
    cfg = cfg or default_settings
    choice = (cfg.upi_verifier or "").strip().lower()

    if choice == "razorpay" and cfg.razorpay_key_id and cfg.razorpay_key_secret:
        return RazorpayUpiVerifier(cfg.razorpay_key_id, cfg.razorpay_key_secret, cfg)
    return None
