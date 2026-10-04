"""Module B -- domain_analyzer.

Answers one question: **is this website actually attached to the entity that
holds the registration?**  Not "does it look like the right site".

Nothing here is seeded.  Two live signals, in descending order of strength:

  1. Reverse link  -- the entity's own site (as listed by SEBI) is fetched and
     searched for a reference to this domain.  Two-way confirmation is the one
     signal an impersonator cannot fake without controlling the listed site.
  2. Registration data -- RDAP, the public registry protocol, gives the real
     registration date, registrar and status of the candidate domain.  A
     domain registered nine days ago, offered as a registered broker's portal,
     is a fact worth acting on.

Everything else (lookalike construction, homoglyphs, TLD swaps) is local
reasoning over those two facts.
"""

from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Protocol

from .config import Settings, settings as default_settings
from .entity_reference import Entity
from .textmatch import levenshtein, similarity
from .types import (
    CANNOT_VERIFY,
    CONNECTED,
    HIGH,
    LOW,
    MEDIUM,
    MISMATCH,
    NOT_CONNECTED,
    WEBSITE,
    ChannelResult,
    Evidence,
)

# --------------------------------------------------------------------------- #
# Domain normalisation
# --------------------------------------------------------------------------- #

MULTI_PART_TLDS = {"co.in", "org.in", "net.in", "co.uk", "com.au", "co.jp", "ac.in", "gov.in"}

KEYWORD_ADDITIONS = {
    "invest", "investment", "investing", "pro", "online", "official", "india",
    "capital", "trade", "trading", "advisor", "adviser", "advisers", "group",
    "vip", "gain", "gains", "profit", "profits", "secure", "verify", "help",
    "support", "care", "service", "services", "live", "app", "portal", "bank",
    "finance", "financial", "wealth", "money", "growth", "nifty", "banknifty",
}

HOMOGLYPHS = {"0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "$": "s", "@": "a"}
HOMOGLYPH_SEQUENCES = [("vv", "w"), ("rn", "m"), ("cl", "d")]

#: Sent on every outbound request.  A descriptive UA is the polite convention
#: for automated clients, and RDAP servers reject the Python default.
USER_AGENT = "BorrowedBadge/1.0 (pre-payment verifier; +https://sangyan.getn.space)"


def normalize_domain(value: str) -> str:
    """Reduce any URL-ish string to a bare lower-case hostname.

    'https://www.ZenithCapital.co.in/kyc?id=3' -> 'zenithcapital.co.in'
    """
    if not value:
        return ""
    raw = value.strip()
    if "//" not in raw:
        raw = "//" + raw
    parsed = urllib.parse.urlparse(raw, scheme="https")
    host = parsed.hostname or parsed.path.split("/")[0]
    host = host.strip().strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def split_tld(domain: str) -> tuple[str, str]:
    """'zenithcapital.co.in' -> ('zenithcapital', 'co.in')."""
    parts = domain.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_PART_TLDS:
        return ".".join(parts[:-2]), ".".join(parts[-2:])
    if len(parts) < 2:
        return domain, ""
    return ".".join(parts[:-1]), parts[-1]


def registrable_label(domain: str) -> str:
    """The brand-carrying part, subdomains dropped."""
    label, _tld = split_tld(domain)
    return label.split(".")[-1] if label else ""


def registrable_domain(domain: str) -> str:
    """Strip subdomains down to the domain that is actually registered.

    'kyc.example.com'      -> 'example.com'
    'a.b.example.co.in'    -> 'example.co.in'

    Registries hold records for the registrable domain only, so looking up
    'kyc.example.com' returns "no such registration" -- which would be a
    spectacular false alarm on a perfectly ordinary subdomain.
    """
    domain = normalize_domain(domain)
    label, tld = split_tld(domain)
    if not tld or not label:
        return domain
    return f"{label.split('.')[-1]}.{tld}"


def is_subdomain_or_equal(candidate: str, official: str) -> bool:
    """True for exact matches and genuine subdomains -- and only those.

    'kyc.zenithcapital.co.in' vs 'zenithcapital.co.in' -> True
    'evilzenithcapital.co.in'  vs 'zenithcapital.co.in' -> False
    """
    if not candidate or not official:
        return False
    return candidate == official or candidate.endswith("." + official)


def _fold_homoglyphs(text: str, one_as: str = "l") -> str:
    """Replace confusable characters with their canonical letter.

    `one_as` resolves the genuine ambiguity of the digit '1', which stands in
    for either 'l' or 'i' depending on the word, so callers try both.
    """
    out = text
    for sequence, repl in HOMOGLYPH_SEQUENCES:
        out = out.replace(sequence, repl)
    for symbol, letter in HOMOGLYPHS.items():
        out = out.replace(symbol, one_as if symbol == "1" else letter)
    return out


def _fold_variants(text: str) -> set[str]:
    """Both readings of an ambiguous homoglyph string."""
    return {_fold_homoglyphs(text, "l"), _fold_homoglyphs(text, "i")}


# --------------------------------------------------------------------------- #
# Live RDAP
# --------------------------------------------------------------------------- #

@dataclass
class DomainIntel:
    """What the public registry says about a domain's registration."""

    domain: str
    exists: bool = False
    age_days: int | None = None
    registrar: str | None = None
    created: str | None = None
    statuses: tuple[str, ...] = ()
    source: str = "none"          # rdap-live | fixture | none
    error: str | None = None

    @property
    def is_young(self) -> bool:
        return self.age_days is not None


class DomainIntelProvider(Protocol):
    def lookup(self, domain: str) -> DomainIntel: ...


def _registrar_from_vcard(payload: dict) -> str | None:
    """Pull the registrar's display name out of an RDAP entity."""
    for entity in payload.get("entities", []) or []:
        if "registrar" not in (entity.get("roles") or []):
            continue
        for item in (entity.get("vcardArray") or [[], []])[1]:
            if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
                return str(item[3])
    return None


class LiveDomainIntelProvider:
    """Real RDAP lookup against the public registry.

    `rdap.org` is a bootstrap service: it redirects to whichever registry is
    authoritative for the TLD, and urllib follows that automatically.  One
    request per domain, memoised in-process, short timeout, and every failure
    degrades to 'no registration data' rather than raising -- an unreachable
    registry is a missing fact, not a reason to accuse anyone.
    """

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._cache: dict[str, DomainIntel] = {}

    def lookup(self, domain: str) -> DomainIntel:
        domain = normalize_domain(domain)
        if not domain:
            return DomainIntel(domain=domain, source="none", error="empty domain")
        if domain in self._cache:
            return self._cache[domain]

        result = self._fetch(domain)
        self._cache[domain] = result
        return result

    def _fetch(self, domain: str) -> DomainIntel:
        url = self.cfg.rdap_base_url + urllib.parse.quote(domain)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/rdap+json", "User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.cfg.http_timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # A definitive "no such registration" -- itself a real finding.
                return DomainIntel(domain=domain, exists=False, source="rdap-live")
            return DomainIntel(domain=domain, source="none", error=f"HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError, OSError) as exc:
            return DomainIntel(domain=domain, source="none", error=type(exc).__name__)

        events = {e.get("eventAction"): e.get("eventDate") for e in payload.get("events", [])}
        created = events.get("registration")
        age_days = None
        if created:
            try:
                stamp = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - stamp).days
            except (ValueError, TypeError):
                age_days = None

        return DomainIntel(
            domain=domain,
            exists=True,
            age_days=age_days,
            registrar=_registrar_from_vcard(payload),
            created=created,
            statuses=tuple(payload.get("status") or ()),
            source="rdap-live",
        )


class FixtureDomainIntelProvider:
    """Deterministic answers from a dict -- used only by the test suite.

    Never used by the running app; the app has no fixture data at all.
    """

    def __init__(self, entries: dict[str, dict] | None = None) -> None:
        self._data = entries or {}

    def lookup(self, domain: str) -> DomainIntel:
        domain = normalize_domain(domain)
        row = self._data.get(domain)
        if not row:
            return DomainIntel(domain=domain, source="none")
        return DomainIntel(
            domain=domain,
            exists=row.get("exists", True),
            age_days=row.get("age_days"),
            registrar=row.get("registrar"),
            created=row.get("created"),
            statuses=tuple(row.get("statuses") or ()),
            source="fixture",
        )


def build_intel_provider(cfg: Settings | None = None) -> DomainIntelProvider:
    """Always live.  There is no seeded fallback by design."""
    return LiveDomainIntelProvider(cfg or default_settings)


# --------------------------------------------------------------------------- #
# Live reverse-link check
# --------------------------------------------------------------------------- #

class LinkChecker(Protocol):
    def official_links_to(self, official_domain: str, candidate_domain: str) -> bool | None: ...


class LiveLinkChecker:
    """Fetch the entity's own site and look for a reference to the candidate.

    One request to the entity's homepage -- not a crawl.  Returns None when the
    check could not be completed, so 'we could not look' never masquerades as
    'we looked and it was not there'.
    """

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self._cache: dict[str, str | None] = {}

    def _fetch(self, domain: str) -> str | None:
        if domain in self._cache:
            return self._cache[domain]
        body: str | None = None
        for scheme in ("https", "http"):
            try:
                request = urllib.request.Request(
                    f"{scheme}://{domain}/",
                    headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
                )
                with urllib.request.urlopen(request, timeout=self.cfg.http_timeout_seconds) as response:
                    body = response.read(self.cfg.max_homepage_bytes).decode("utf-8", errors="replace")
                break
            except (urllib.error.URLError, TimeoutError, socket.timeout, ValueError, OSError):
                continue
        self._cache[domain] = body
        return body

    def official_links_to(self, official_domain: str, candidate_domain: str) -> bool | None:
        body = self._fetch(normalize_domain(official_domain))
        if body is None:
            return None
        haystack = body.lower()
        candidate = normalize_domain(candidate_domain)
        cand_label, cand_tld = split_tld(candidate)
        # Look for the full domain, and for bare mentions of the label.
        if candidate in haystack:
            return True
        if cand_label and re.search(rf"\b{re.escape(cand_label)}\b", haystack) and cand_tld in haystack:
            return True
        return False


class FixtureLinkChecker:
    """Deterministic link map for tests only."""

    def __init__(self, graph: dict[str, list[str]] | None = None) -> None:
        self._graph = graph or {}

    def official_links_to(self, official_domain: str, candidate_domain: str) -> bool | None:
        linked = {normalize_domain(d) for d in self._graph.get(normalize_domain(official_domain), [])}
        return normalize_domain(candidate_domain) in linked


def build_link_checker(cfg: Settings | None = None) -> LinkChecker:
    return LiveLinkChecker(cfg or default_settings)


# --------------------------------------------------------------------------- #
# Lookalike detection
# --------------------------------------------------------------------------- #

@dataclass
class LookalikeHit:
    kind: str
    detail: str


def classify_lookalike(candidate: str, official: str) -> list[LookalikeHit]:
    """Explain *how* the candidate resembles the official domain.

    Explaining the mechanism matters: "registered 9 days ago and differs from
    the official domain only by a homoglyph" is far more actionable than a
    bare similarity number.
    """
    hits: list[LookalikeHit] = []
    cand_label, cand_tld = split_tld(normalize_domain(candidate))
    off_label, off_tld = split_tld(normalize_domain(official))
    if not cand_label or not off_label:
        return hits

    if cand_label == off_label and cand_tld != off_tld:
        hits.append(LookalikeHit(
            "tld_swap",
            f"Same name as the official domain but a different ending "
            f"(.{cand_tld} instead of .{off_tld})",
        ))

    if off_label in _fold_variants(cand_label) and cand_label != off_label:
        hits.append(LookalikeHit(
            "homoglyph",
            f"'{cand_label}' becomes '{off_label}' when lookalike characters "
            f"(0/o, 1/l, 1/i, 5/s) are normalised",
        ))

    if off_label in cand_label and cand_label != off_label:
        extra = cand_label.replace(off_label, "").strip("-")
        extra_words = {w for w in extra.replace("-", " ").split() if w}
        if extra_words & KEYWORD_ADDITIONS:
            hits.append(LookalikeHit(
                "keyword",
                f"Official name with marketing words added: '{sorted(extra_words)}'",
            ))

    if cand_label.replace("-", "") == off_label.replace("-", "") and cand_label != off_label:
        hits.append(LookalikeHit(
            "hyphenation",
            f"Identical to the official name once hyphens are removed "
            f"('{cand_label}' vs '{off_label}')",
        ))

    distance = levenshtein(cand_label, off_label)
    if 0 < distance <= 3 and not hits:
        hits.append(LookalikeHit(
            "typosquat",
            f"Differs from the official name '{off_label}' by only "
            f"{distance} character{'s' if distance > 1 else ''}",
        ))

    return hits


def lookalike_score(candidate: str, official: str) -> float:
    """0.0-1.0 resemblance between a candidate and an official domain."""
    cand_label = registrable_label(normalize_domain(candidate))
    off_label = registrable_label(normalize_domain(official))
    if not cand_label or not off_label:
        return 0.0

    direct = similarity(cand_label, off_label)
    folded = max(similarity(variant, off_label) for variant in _fold_variants(cand_label))
    contain = 0.0
    if off_label in cand_label or cand_label in off_label:
        shorter = min(len(cand_label), len(off_label))
        longer = max(len(cand_label), len(off_label))
        contain = 0.85 + 0.15 * (shorter / longer)
    return max(direct, folded, contain)


# --------------------------------------------------------------------------- #
# Channel analysis
# --------------------------------------------------------------------------- #

def analyze_website(
    entity: Entity | None,
    raw_url: str,
    *,
    cfg: Settings | None = None,
    intel_provider: DomainIntelProvider | None = None,
    link_checker: LinkChecker | None = None,
) -> ChannelResult:
    """Decide whether the given website is attached to the registered entity."""
    cfg = cfg or default_settings
    intel_provider = intel_provider or build_intel_provider(cfg)
    link_checker = link_checker or build_link_checker(cfg)

    candidate = normalize_domain(raw_url)
    evidence: list[Evidence] = []
    reasons: list[str] = []
    rules: list[str] = []
    flags: list[str] = []

    if not candidate:
        return ChannelResult(
            channel=WEBSITE, verdict=CANNOT_VERIFY, confidence=LOW,
            reasons=["No usable website address was supplied."],
            evidence=[Evidence(WEBSITE, "Parse the website address",
                               "Could not extract a domain name", "heuristic", missing=True)],
            rules=["WEBSITE_UNPARSEABLE"],
        )

    # ---- live registry data, always gathered ----------------------------- #
    # Looked up on the registrable domain: registries hold no record for
    # subdomains, so querying 'kyc.example.com' directly would report the
    # domain as unregistered and raise a false alarm on an ordinary subdomain.
    lookup_target = registrable_domain(candidate)
    intel = intel_provider.lookup(lookup_target)
    if lookup_target != candidate:
        evidence.append(Evidence(
            WEBSITE, "Registrable domain",
            f"{candidate} is a subdomain of {lookup_target}, which is the domain "
            "the registry record covers",
            "heuristic",
        ))
    if intel.source == "none":
        evidence.append(Evidence(
            WEBSITE, "Domain registration data",
            f"Registry lookup for {lookup_target} did not complete"
            + (f" ({intel.error})" if intel.error else "")
            + ", so its age and registrar are unknown",
            "rdap-live", missing=True,
        ))
    elif not intel.exists:
        rules.append("DOMAIN_NOT_REGISTERED")
        flags.append("DOMAIN_NO_REGISTRATION_RECORD")
        evidence.append(Evidence(
            WEBSITE, "Domain registration data",
            f"The public registry has no registration record for {lookup_target}. "
            "The domain is very likely unregistered or expired.",
            "rdap-live",
        ))
        reasons.append(
            f"The public registry holds no record for {lookup_target}, which "
            "means the domain is probably not registered at all."
        )
    else:
        age_text = (
            f"first registered {intel.age_days} days ago ({intel.created})"
            if intel.age_days is not None else "registration date unavailable"
        )
        evidence.append(Evidence(
            WEBSITE, "Domain registration data",
            f"{lookup_target} {age_text}; registrar {intel.registrar or 'unknown'}; "
            f"status {', '.join(intel.statuses) or 'unknown'}",
            "rdap-live",
        ))
        if intel.age_days is not None and intel.age_days <= cfg.young_domain_days:
            rules.append("DOMAIN_YOUNG")
            flags.append("RECENTLY_REGISTERED_DOMAIN")
            reasons.append(
                f"The domain was registered only {intel.age_days} days ago. "
                "Established firms do not usually launch a brand-new domain to "
                "collect investments."
            )
        if intel.registrar:
            reasons.append(f"The domain is held through {intel.registrar}.")
        low_trust = [s for s in intel.statuses if s in {"pending delete", "redemption period", "inactive"}]
        if low_trust:
            flags.append("DOMAIN_STATUS_SUSPECT")
            reasons.append(
                f"The registry status is '{', '.join(low_trust)}', which suggests "
                "the registration is lapsing."
            )

    # ---- no anchor: report the live findings, claim nothing more ---------- #
    if entity is None or not entity.provided or not entity.official_domains:
        rules.append("NO_SEBI_REFERENCE_FOR_WEBSITE")
        evidence.append(Evidence(
            WEBSITE, "Comparison against the entity's official site",
            "No official website was supplied from SEBI's listing, so there is "
            "nothing to compare this domain against",
            "user-input", missing=True,
        ))
        reasons.append(
            "This domain's registration facts are above, but whether it belongs "
            "to the entity you are checking cannot be established without the "
            "official website from SEBI's listing."
        )
        return ChannelResult(
            channel=WEBSITE,
            verdict=CANNOT_VERIFY,
            confidence=LOW if not flags else MEDIUM,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    official_domains = list(entity.official_domains)

    # ---- signal 1: exact match or genuine subdomain ---------------------- #
    # Checked before the reverse link because it is definitive and costs no
    # network call -- fetching the official site to see whether it links to
    # itself would be both wasteful and confusing.
    for official in official_domains:
        if is_subdomain_or_equal(candidate, official):
            rules.append("DOMAIN_EXACT_MATCH")
            evidence.append(Evidence(
                WEBSITE, "Match against the official domain",
                f"{candidate} matches the official domain {official} supplied "
                "from SEBI's listing", "user-input",
            ))
            reasons.append(
                f"{candidate} is the domain recorded for this entity"
                + (" (a subdomain of it)." if candidate != official else ".")
            )
            return ChannelResult(
                channel=WEBSITE, verdict=CONNECTED, confidence=HIGH,
                reasons=reasons, evidence=evidence, rules=rules, flags=flags,
            )

    # ---- signal 2: reverse link from the entity's own site --------------- #
    for official in official_domains:
        linked = link_checker.official_links_to(official, candidate)
        if linked is True:
            rules.append("DOMAIN_REVERSE_LINK")
            evidence.append(Evidence(
                WEBSITE, "Reverse link from the official site",
                f"{official} references {candidate}", "live-fetch",
            ))
            reasons.append(
                f"The official site {official} links directly to {candidate}, so "
                "the connection is confirmed from both directions."
            )
            return ChannelResult(
                channel=WEBSITE, verdict=CONNECTED, confidence=HIGH,
                reasons=reasons, evidence=evidence, rules=rules, flags=flags,
            )
        if linked is False:
            evidence.append(Evidence(
                WEBSITE, "Reverse link from the official site",
                f"{official} was fetched and does not reference {candidate}",
                "live-fetch",
            ))

    # ---- signal 3: lookalike --------------------------------------------- #
    best_score, best_official, best_hits = 0.0, None, []
    for official in official_domains:
        score = lookalike_score(candidate, official)
        if score > best_score:
            best_score, best_official, best_hits = score, official, classify_lookalike(candidate, official)

    distance = (
        levenshtein(registrable_label(candidate), registrable_label(best_official))
        if best_official else 99
    )
    if best_hits and (
        best_score >= cfg.lookalike_threshold or distance <= cfg.lookalike_max_edit_distance
    ):
        rules.append("DOMAIN_LOOKALIKE")
        flags.append("LOOKALIKE_DOMAIN")
        evidence.append(Evidence(
            WEBSITE, "Resemblance to the official domain",
            f"{candidate} resembles {best_official} "
            f"(similarity {best_score:.2f}, edit distance {distance})",
            "heuristic",
        ))
        for hit in best_hits:
            evidence.append(Evidence(WEBSITE, f"Lookalike pattern: {hit.kind}", hit.detail, "heuristic"))
            reasons.append(hit.detail)
        reasons.append(
            "The official site does not link to this domain, so the resemblance "
            "is not backed by any confirmation."
        )
        return ChannelResult(
            channel=WEBSITE, verdict=MISMATCH,
            confidence=HIGH if "DOMAIN_YOUNG" in rules else MEDIUM,
            reasons=reasons, evidence=evidence, rules=rules, flags=flags,
        )

    # ---- signal 4: the entity's site is known, and this is not it -------- #
    rules.append("DOMAIN_NOT_THE_OFFICIAL_SITE")
    evidence.append(Evidence(
        WEBSITE, "Match against the official domain",
        f"{candidate} is neither {', '.join(official_domains)} nor a recognisable "
        "imitation of it", "user-input",
    ))
    reasons.append(
        f"SEBI's listing records {', '.join(official_domains)} for this entity. "
        f"{candidate} is neither that site nor a recognisable imitation of it."
    )
    return ChannelResult(
        channel=WEBSITE, verdict=NOT_CONNECTED, confidence=MEDIUM,
        reasons=reasons, evidence=evidence, rules=rules, flags=flags,
    )
