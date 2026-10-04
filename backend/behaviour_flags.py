"""Module E -- behaviour_flags.

Rule-based red-flag detection over text the user pasted (a WhatsApp forward, a
Telegram pitch, an SMS).  Deliberately transparent: a list of regex rules with
human-readable explanations, no classifier, no scoring model.

Hard constraint from the spec, enforced in the verdict engine and tested:
**these flags never change a connection verdict on their own.** A message can
be pushy and still come from a genuinely registered entity; a message can be
polite and come from a fraudster.  Behaviour is reported alongside the
connection verdict, never merged into it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class Rule:
    """One red-flag pattern."""

    id: str
    label: str
    severity: str                      # low | medium | high
    patterns: tuple[str, ...]          # regexes, matched case-insensitively
    explanation: str


RULES: tuple[Rule, ...] = (
    Rule(
        id="GUARANTEED_RETURNS",
        label="Guaranteed or assured returns",
        severity="high",
        patterns=(
            r"\bguarantee[ds]?\b[^.]{0,40}\b(return|profit|gain|income|money)\b",
            r"\b(return|profit|gain)s?\b[^.]{0,25}\b(guarantee[ds]?|assured|fixed)\b",
            r"\bassured\s+(return|profit|gain)",
            r"\brisk[- ]free\b",
            r"\bno\s+loss\b",
            r"\bfixed\s+\d{1,3}\s*%\s*(monthly|per\s+month|annual)",
        ),
        explanation=(
            "Guaranteed or assured returns are not something a registered "
            "intermediary is permitted to promise. Returns carry risk by "
            "definition."
        ),
    ),
    Rule(
        id="UNREALISTIC_MULTIPLIER",
        label="Unrealistic multiplier claims",
        severity="high",
        patterns=(
            r"\b(double|triple|quadruple)\s+(your\s+)?(money|capital|investment)",
            r"\b\d{1,3}\s*x\s+(returns?|profit|in\s+\d+\s+(days|weeks|months))",
            r"\bturn\s+₹?\s*\d[\d,]*\s+into\s+₹?\s*\d",
        ),
        explanation=(
            "Promises to multiply capital by a set multiple in a set time are a "
            "classic investment-fraud pattern."
        ),
    ),
    Rule(
        id="VIP_GROUP",
        label="Exclusive or VIP group",
        severity="medium",
        patterns=(
            r"\bvip\s+(group|club|channel|membership|batch)\b",
            r"\bpremium\s+(group|club|channel|batch)\b",
            r"\bexclusive\s+(group|club|channel|tips)\b",
            r"\bpaid\s+(group|channel|batch)\b",
            r"\bjoin\s+(my|our|the)\s+(group|channel|batch)\b",
        ),
        explanation=(
            "Invitations to a 'VIP' or exclusive group are used to move a "
            "target somewhere the conversation cannot be observed."
        ),
    ),
    Rule(
        id="MOVE_TO_OTHER_APP",
        label="Pressure to continue on another app",
        severity="medium",
        patterns=(
            r"\b(whats\s?app|telegram|signal)\b[^.]{0,30}\b(number|contact|dm|message|add)\b",
            r"\b(dm|pm|message)\s+me\s+(on|at)\b",
            r"\bcontact\s+me\s+on\s+(telegram|whatsapp|signal)\b",
            r"\bcontinue\s+(this\s+)?(chat\s+)?on\s+(telegram|whatsapp)",
        ),
        explanation=(
            "Moving the conversation to a private app removes the paper trail "
            "and is a common step before a payment request."
        ),
    ),
    Rule(
        id="PAY_PERSONAL_ACCOUNT",
        label="Request to pay a personal account",
        severity="high",
        patterns=(
            r"\bpay\s+(to\s+)?(my|this)\s+(personal|individual|own)\s+account\b",
            r"\btransfer\s+(the\s+)?(amount|money|funds)\s+to\s+(my|this)\s+(account|upi)\b",
            r"\bsend\s+(the\s+)?(money|amount|funds)\s+to\s+(my|this)\s+upi\b",
            r"\bpay\s+to\s+[a-z]+\s+[a-z]+\s*@(ybl|ok|paytm|ibl)",
            r"\b(account\s+number|a/?c\s+no)\s*[:\-]?\s*\d{9,18}\b",
        ),
        explanation=(
            "Payments to a personal rather than a firm account are the single "
            "most reliable indicator that funds are not reaching a registered "
            "entity."
        ),
    ),
    Rule(
        id="URGENCY",
        label="Urgency or scarcity pressure",
        severity="medium",
        patterns=(
            r"\b(act\s+now|hurry|immediately|last\s+chance|limited\s+(seats?|slots?|time)|"
            r"today\s+only|offer\s+ends?|expir(es?|ing)\s+(today|soon)|"
            r"before\s+it'?s\s+too\s+late)\b",
            r"\bonly\s+\d+\s+(seats?|slots?|places?|spots?)\s+(left|remaining)\b",
        ),
        explanation=(
            "Time pressure is used to prevent a target from checking credentials "
            "or consulting anyone."
        ),
    ),
    Rule(
        id="SIDELOADED_APP",
        label="Install an app from outside the app store",
        severity="high",
        patterns=(
            r"\b(apk|\.apk)\b",
            r"\bdownload\s+(the\s+)?app\s+(from|using)\s+(this\s+)?link\b",
            r"\binstall\s+(this\s+)?(apk|app)\s+from\s+(the\s+)?link\b",
            r"\benable\s+(unknown\s+sources|install\s+from\s+unknown)\b",
            r"\bclick\s+(this\s+)?link\s+to\s+(install|download)\b",
        ),
        explanation=(
            "Apps installed from a link rather than an official app store can "
            "harvest credentials and payment data without review."
        ),
    ),
    Rule(
        id="LOTTERY_OR_FEE",
        label="Advance fee before returns",
        severity="high",
        patterns=(
            r"\b(processing|registration|activation|membership|joining|clearance)\s+fee\b",
            r"\bpay\s+(a\s+)?(small\s+)?(fee|charge|amount)\s+(first|to\s+(release|unlock|activate))\b",
            r"\b(gst|tax|tds)\s+(payment|deposit)\s+(to\s+)?(release|withdraw)\b",
            r"\bdeposit\s+₹?\s*\d[\d,]*\s+to\s+(unlock|activate|start)\b",
        ),
        explanation=(
            "Being asked to pay a fee, tax or deposit before money can be "
            "withdrawn or returns released is the structure of an advance-fee "
            "scam."
        ),
    ),
    Rule(
        id="SEBI_CLAIM_UNCITED",
        label="Claims SEBI registration without a number",
        severity="low",
        patterns=(
            r"\bsebi\s+(registered|approved|certified|recognised|recognized)\b",
            r"\bregistered\s+with\s+sebi\b",
        ),
        explanation=(
            "A claim of SEBI registration is only meaningful with the actual "
            "registration number, which can then be checked."
        ),
    ),
    Rule(
        id="AVOID_VERIFICATION",
        label="Discourages independent checking",
        severity="high",
        patterns=(
            r"\b(don'?t|no\s+need\s+to|do\s+not)\s+(verify|check|ask|confirm)\b",
            r"\b(trust\s+me|believe\s+me)\b[^.]{0,30}\b(without|no\s+need)\b",
            r"\bkeep\s+(this|it)\s+(confidential|between\s+us|secret)\b",
        ),
        explanation=(
            "Attempts to discourage verification are a strong indicator that "
            "verification would fail."
        ),
    ),
    Rule(
        id="STOCK_TIP_PRESSURE",
        label="Tip-based trading pressure",
        severity="medium",
        patterns=(
            r"\b(intraday|sure\s?shot|jackpot|multibagger)\s+(tip|call|stock|suggestion)\b",
            r"\b(sure|confirmed|jackpot)\s+(shot|tip|call)\b",
            r"\b(buy|sell)\s+[A-Z]{2,12}\s+(today|now|at\s+\d)\b",
            r"\b\d{1,3}\s*%\s+(profit|return)\s+in\s+\d+\s+(day|week|month)s?\b",
        ),
        explanation=(
            "Unsolicited buy/sell calls with a target percentage are a typical "
            "pump-and-dump or unregistered-advisory pattern."
        ),
    ),
)


@dataclass
class BehaviourFlag:
    """A red flag found in pasted text, with the snippet that triggered it."""

    id: str
    label: str
    severity: str
    explanation: str
    matches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "severity": self.severity,
            "explanation": self.explanation,
            "matched_text": self.matches,
        }


@dataclass
class BehaviourReport:
    """The full result of scanning one message."""

    flags: list[BehaviourFlag] = field(default_factory=list)
    risk_level: str = "none"           # none | low | medium | high
    scanned: bool = False

    def to_dict(self) -> dict:
        return {
            "scanned": self.scanned,
            "risk_level": self.risk_level,
            "flags": [f.to_dict() for f in self.flags],
            "note": (
                "These signals describe the message, not the connection. "
                "They do not change whether a channel is linked to the "
                "registered entity."
                if self.flags else
                "No pressure or red-flag language was detected in the message."
            ),
        }


#: Cap on how much text we will scan, so a pasted novel cannot stall a request.
MAX_SCAN_CHARS = 20_000


def scan_message(text: str) -> BehaviourReport:
    """Scan pasted text for red-flag language.

    The text is examined in memory and discarded; this function stores nothing
    and returns only the matched patterns, never the whole message.
    """
    if not text or not text.strip():
        return BehaviourReport(flags=[], risk_level="none", scanned=False)

    haystack = text[:MAX_SCAN_CHARS]
    found: list[BehaviourFlag] = []

    for rule in RULES:
        matches: list[str] = []
        for pattern in rule.patterns:
            for match in re.finditer(pattern, haystack, flags=re.IGNORECASE):
                snippet = match.group(0).strip()
                if snippet and snippet not in matches:
                    matches.append(snippet[:120])
        if matches:
            found.append(BehaviourFlag(
                id=rule.id, label=rule.label, severity=rule.severity,
                explanation=rule.explanation, matches=matches[:5],
            ))

    # Risk level = worst single signal, nudged up when several fire together.
    if not found:
        risk_level = "none"
    else:
        risk_level = max(
            (f.severity for f in found), key=lambda s: SEVERITY_RANK[s]
        )
        if len(found) >= 3 and risk_level != "high":
            risk_level = "high"
        elif len(found) == 2 and risk_level == "low":
            risk_level = "medium"

    return BehaviourReport(flags=found, risk_level=risk_level, scanned=True)
