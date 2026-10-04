"""Small text-matching toolbox shared by the analysers.

Uses `rapidfuzz` when it is installed, and falls back to the standard library's
`difflib` otherwise -- the fallback is slightly less generous on abbreviated
names but keeps the project runnable with zero optional dependencies.
"""

from __future__ import annotations

import re
import unicodedata

try:  # optional, faster and better at partial matches
    from rapidfuzz import fuzz as _rf_fuzz

    HAVE_RAPIDFUZZ = True
except ImportError:  # pragma: no cover - exercised on a bare install
    _rf_fuzz = None
    HAVE_RAPIDFUZZ = False

from difflib import SequenceMatcher

# Corporate noise words that carry no identity information.
COMPANY_STOPWORDS = {
    "pvt", "private", "ltd", "limited", "llp", "inc", "co", "company",
    "and", "the", "of", "securities", "broking", "capital", "markets",
    "financial", "services", "advisers", "advisors", "advisory",
    "investment", "investments", "research", "analytics", "wealth",
    "consultancy", "consultants", "solutions", "group", "enterprises",
}


def normalize_text(value: str) -> str:
    """Lower-case, strip accents, collapse punctuation and whitespace."""
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9& ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def strip_company_noise(value: str) -> str:
    """Remove legal-suffix and sector words, leaving distinguishing tokens.

    'Hemvati Advisers LLP'  -> 'hemvati'
    'Zenith Capital Markets Pvt Ltd' -> 'zenith'
    """
    tokens = [t for t in normalize_text(value).split() if t not in COMPANY_STOPWORDS]
    return " ".join(tokens) or normalize_text(value)


def similarity(a: str, b: str) -> float:
    """Return a 0.0-1.0 similarity between two strings.

    Blends whole-string ratio with a token-sorted ratio, so that
    'Meridian Capital Broking' still scores well against
    'Broking Meridian Capital' -- bank payee fields are frequently reordered.
    """
    a_norm, b_norm = normalize_text(a), normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0

    if HAVE_RAPIDFUZZ:
        whole = _rf_fuzz.ratio(a_norm, b_norm) / 100.0
        token = _rf_fuzz.token_sort_ratio(a_norm, b_norm) / 100.0
        partial = _rf_fuzz.partial_ratio(a_norm, b_norm) / 100.0
        return max(whole, token, partial * 0.95)

    whole = SequenceMatcher(None, a_norm, b_norm).ratio()
    token = SequenceMatcher(
        None, " ".join(sorted(a_norm.split())), " ".join(sorted(b_norm.split()))
    ).ratio()
    return max(whole, token)


def name_similarity(candidate: str, official: str) -> float:
    """Similarity tuned for entity names, where legal suffixes are noise.

    Compares both the raw strings and the noise-stripped cores, and takes the
    better score.  A payee name of 'ZENITH CAPITAL' against the registered
    'Zenith Capital Markets Pvt Ltd' should read as a strong match, not a
    three-word shortfall.
    """
    direct = similarity(candidate, official)
    core = similarity(strip_company_noise(candidate), strip_company_noise(official))
    return max(direct, core)


def levenshtein(a: str, b: str) -> int:
    """Classic edit distance (insert / delete / substitute)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,          # deletion
                    current[j - 1] + 1,       # insertion
                    previous[j - 1] + (ca != cb),  # substitution
                )
            )
        previous = current
    return previous[-1]


def looks_like_person_name(value: str) -> bool:
    """Heuristic: does this payee name look like an individual, not a firm?

    Indian retail fraud almost always routes money to a personal account.  A
    payee with 2-3 tokens and no corporate marker is a strong signal, but this
    stays a *signal* -- the verdict engine decides what to do with it.
    """
    tokens = normalize_text(value).split()
    if not tokens or len(tokens) > 4:
        return False
    corporate_markers = {
        "pvt", "ltd", "limited", "llp", "inc", "co", "corp", "broking",
        "securities", "capital", "advisers", "advisors", "research", "finance",
        "financial", "services", "markets", "wealth", "invest", "trading",
    }
    if any(t in corporate_markers for t in tokens):
        return False
    # Common Indian given-name / surname shapes are hard to enumerate; the
    # absence of any corporate marker plus a short token count is the signal.
    return len(tokens) <= 3
