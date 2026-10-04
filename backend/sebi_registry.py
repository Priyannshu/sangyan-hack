"""Local SEBI intermediary registry, built from files the operator supplies.

Why a file and not a fetch: SEBI's portal blocks automated interaction.  A GET
of the listing page returns 25 records, but paging past them and the "Show All
Records" control both go through a POST that their WAF rejects (HTTP 530
BLOCKED), and the SEBI Check lookup form returns "Unauthorized Request
Blocked".  So this module never talks to SEBI.  A human fetches the listing in
their own browser; this parses what they saved.

Two consequences worth stating plainly:

  * Nothing here is scraped.  The data is exactly what SEBI published, obtained
    by a person using the site the way it is meant to be used.
  * The registry is only ever as fresh as the last file imported.  Every record
    carries `imported_at` and every lookup can report staleness, so a verdict
    never silently leans on year-old data.

The registry is **internal**.  It backs lookups in the app; it is not a
user-facing feature and there is no endpoint that exposes the raw table.
"""

from __future__ import annotations

import html as html_mod
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Category map
# --------------------------------------------------------------------------- #

#: SEBI's own category ids, from the intmId query parameter.  Only the three
#: entity types this tool reasons about are mapped; anything else is still
#: stored but reported as an unrecognised category rather than guessed at.
CATEGORY_BY_INTM_ID: dict[str, tuple[str, str]] = {
    "13": ("investment_adviser", "Investment Adviser"),
    "14": ("research_analyst", "Research Analyst"),
    "30": ("stock_broker", "Stock Broker (equity)"),
    "31": ("stock_broker", "Stock Broker (equity derivatives)"),
    "32": ("stock_broker", "Stock Broker (currency derivatives)"),
    "37": ("stock_broker", "Stock Broker (debt)"),
    "38": ("stock_broker", "Stock Broker (interest rate derivatives)"),
    "2":  ("stock_broker", "Stock Broker (commodity derivatives)"),
}

#: Fallback: infer the entity type from the registration number prefix, which
#: is how SEBI itself distinguishes them (INZ broker, INA adviser, INH analyst).
TYPE_BY_REG_PREFIX = {
    "INZ": "stock_broker",
    "INB": "stock_broker",
    "INF": "stock_broker",
    "INE": "stock_broker",
    "INA": "investment_adviser",
    "INH": "research_analyst",
}

REG_NO_PATTERN = re.compile(r"\bIN[ZABFEH]\d{9}\b")

#: The card markup SEBI uses, in two dialects: the full listing page writes
#: double-quoted attributes, the AJAX fragment writes single-quoted ones.
#: Both must match, or a fragment silently parses to zero records.
CARD_PATTERN = re.compile(
    r"<div\s+class=['\"]title['\"]\s*>\s*<span[^>]*>(?P<title>.*?)</span>\s*</div>\s*"
    r"<div\s+class=['\"]value[^'\"]*['\"]\s*>\s*<span[^>]*>(?P<value>.*?)</span>",
    re.S | re.I,
)

#: '1 to 25 of 4993 records' -- the page tells us how many exist in total,
#: which is what lets us tell a complete import from a partial one.
RECORD_BANNER = re.compile(r"\d+\s+to\s+\d+\s+of\s+(?P<total>\d+)\s+records?", re.I)
AS_ON_DATE = re.compile(r"as on date\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})", re.I)

#: The download snippet names files by category, so a fragment with no intmId
#: can still be identified from its filename.
CATEGORY_BY_FILENAME = {
    "investment-advisers": "13",
    "research-analysts": "14",
    "stock-brokers-equity": "30",
    "stock-brokers-equity-deriv": "31",
    "stock-brokers-currency-deriv": "32",
    "stock-brokers-debt": "37",
    "stock-brokers-interest-rate": "38",
    "stock-brokers-commodity": "2",
}

#: Field labels we care about, normalised.
FIELD_ALIASES = {
    "name": "name",
    "trade name": "trade_name",
    "registration no.": "registration_number",
    "registration no": "registration_number",
    "registration number": "registration_number",
    "e-mail": "email",
    "email": "email",
    "telephone": "telephone",
    "phone": "telephone",
    "fax no.": "fax",
    "address": "address",
    "contact person": "contact_person",
    "correspondence address": "correspondence_address",
    "validity": "validity_raw",
    "exchange name": "exchange",
}

VALIDITY_SPLIT = re.compile(r"\s*(?:-|–|to)\s*", re.I)


def _clean(value: str) -> str:
    """Strip tags, decode entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html_mod.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Record
# --------------------------------------------------------------------------- #

@dataclass
class SebiRecord:
    """One intermediary as published by SEBI."""

    registration_number: str
    name: str = ""
    trade_name: str = ""
    entity_type: str = "unknown"
    category: str = ""
    email: str = ""
    telephone: str = ""
    address: str = ""
    contact_person: str = ""
    correspondence_address: str = ""
    exchange: str = ""
    validity_raw: str = ""
    valid_from: str = ""
    valid_to: str = ""          # empty means perpetual
    status: str = "unknown"     # active | expired | unknown
    source_file: str = ""
    imported_at: str = ""

    def to_dict(self) -> dict:
        return {
            "registration_number": self.registration_number,
            "name": self.name,
            "trade_name": self.trade_name,
            "entity_type": self.entity_type,
            "category": self.category,
            "email": self.email,
            "telephone": self.telephone,
            "address": self.address,
            "contact_person": self.contact_person,
            "exchange": self.exchange,
            "validity": self.validity_raw,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "status": self.status,
            "source_file": self.source_file,
            "imported_at": self.imported_at,
        }


def _parse_month_year(text: str) -> str:
    """SEBI writes dates as 'Nov 27, 2013'.  Returns ISO, or '' if unparseable."""
    text = text.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def _derive_status(valid_to_iso: str, validity_raw: str) -> str:
    """Perpetual registrations are active; dated ones depend on the end date."""
    if not validity_raw:
        return "unknown"
    if "perpetual" in validity_raw.lower():
        return "active"
    if not valid_to_iso:
        return "unknown"
    try:
        end = datetime.fromisoformat(valid_to_iso).date()
    except ValueError:
        return "unknown"
    return "active" if end >= datetime.now(timezone.utc).date() else "expired"


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def parse_sebi_html(text: str, *, category_hint: str = "") -> list[SebiRecord]:
    """Parse a SEBI listing page into records.

    Chunks on the `Name` field: every record SEBI publishes begins with one, so
    a new `Name` starts a new record.  Field sets differ between categories
    (advisers carry a contact person and fax; brokers carry a trade name and an
    exchange), so unknown labels are ignored rather than assumed.
    """
    pairs: list[tuple[str, str]] = []
    for match in CARD_PATTERN.finditer(text or ""):
        title = _clean(match.group("title")).lower().rstrip(":")
        value = _clean(match.group("value"))
        pairs.append((title, value))

    entity_type, category = ("unknown", category_hint)
    if category_hint and category_hint in CATEGORY_BY_INTM_ID:
        entity_type, category = CATEGORY_BY_INTM_ID[category_hint]

    records: list[SebiRecord] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if not current:
            return
        reg_no = (current.get("registration_number") or "").upper()
        if not reg_no:
            # A card group with no registration number is a page artefact.
            current.clear()
            return
        valid_from = valid_to = ""
        validity = current.get("validity_raw", "")
        if validity:
            parts = [p for p in VALIDITY_SPLIT.split(validity) if p.strip()]
            if len(parts) >= 2:
                valid_from = _parse_month_year(parts[0])
                if "perpetual" not in parts[1].lower():
                    valid_to = _parse_month_year(parts[1])
            elif len(parts) == 1:
                valid_from = _parse_month_year(parts[0])

        resolved_type = entity_type
        if resolved_type == "unknown":
            resolved_type = TYPE_BY_REG_PREFIX.get(reg_no[:3], "unknown")

        records.append(SebiRecord(
            registration_number=reg_no,
            name=current.get("name", ""),
            trade_name=current.get("trade_name", ""),
            entity_type=resolved_type,
            category=category,
            email=current.get("email", ""),
            telephone=current.get("telephone", ""),
            address=current.get("address", ""),
            contact_person=current.get("contact_person", ""),
            correspondence_address=current.get("correspondence_address", ""),
            exchange=current.get("exchange", ""),
            validity_raw=validity,
            valid_from=valid_from,
            valid_to=valid_to,
            status=_derive_status(valid_to, validity),
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))
        current.clear()

    for title, value in pairs:
        field_name = FIELD_ALIASES.get(title)
        if title == "name":
            # A second `Name` means the previous record ended.
            flush()
        if field_name:
            current.setdefault(field_name, value)

    flush()
    return records


def detect_category(text: str, filename: str = "") -> str:
    """Best-effort recovery of the category id from a saved listing.

    The AJAX fragment carries no intmId at all, so there are three routes, in
    order of reliability: the hidden field on a full page, the filename the
    download snippet assigns, then the registration-number prefixes present.
    """
    for intm_id in CATEGORY_BY_INTM_ID:
        if (f'intmId" value="{intm_id}"' in text
                or f"intmId='{intm_id}'" in text
                or f"intmId={intm_id}" in text):
            return intm_id

    if filename:
        stem = Path(filename).stem.lower()
        # Longest key first: 'stock-brokers-equity' is a prefix of
        # 'stock-brokers-equity-deriv', so a naive scan mislabels the latter.
        for key, intm_id in sorted(CATEGORY_BY_FILENAME.items(),
                                   key=lambda kv: -len(kv[0])):
            if key in stem:
                return intm_id

    prefixes = set(re.findall(r"\b(IN[ZABFEH])\d{9}\b", text or ""))
    if prefixes == {"INA"}:
        return "13"
    if prefixes == {"INH"}:
        return "14"
    if prefixes and prefixes <= {"INZ", "INB", "INF", "INE"}:
        return "30"
    return ""


def read_listing_meta(text: str) -> dict:
    """What the page says about itself: total record count and as-on date."""
    banner = RECORD_BANNER.search(text or "")
    as_on = AS_ON_DATE.search(text or "")
    return {
        "reported_total": int(banner.group("total")) if banner else None,
        "as_on_date": as_on.group(1) if as_on else None,
    }


# --------------------------------------------------------------------------- #
# Excel listings
# --------------------------------------------------------------------------- #

#: SEBI's Download buttons produce .xls (BIFF) or .xlsx.  The header row is not
#: the first row -- the title sits above it and some sheets carry a second
#: grouping row -- so the header is located by looking for 'Registration No.'.
HEADER_MARKER = "registration no"

#: Canonical field -> header spellings SEBI uses across categories.
EXCEL_HEADER_ALIASES = {
    "name": {"name", "trade name"},
    "registration_number": {"registration no.", "registration no", "registration number"},
    "contact_person": {"contact person"},
    "address": {"address"},
    "email": {"email-id", "email id", "e-mail", "email"},
    "telephone": {"telephone", "phone", "telephone no."},
    "fax": {"fax", "fax no."},
    "city": {"city"},
    "state": {"state"},
    "pincode": {"pincode", "pin code"},
    "exchange": {"exchange name", "exchange"},
    "valid_from": {"from", "valid from", "validity from"},
    "valid_to": {"to", "valid to", "validity to"},
}

EXCEL_AS_ON = re.compile(r"as on\s+([A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4})", re.I)


def _read_excel_grid(path: Path) -> list[list[str]]:
    """Read .xls or .xlsx into a grid of stripped strings."""
    suffix = path.suffix.lower()
    grid: list[list[str]] = []

    if suffix == ".xls":
        import xlrd  # legacy BIFF; openpyxl cannot read these

        book = xlrd.open_workbook(str(path))
        sheet = book.sheet_by_index(0)
        for r in range(sheet.nrows):
            grid.append([str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)])
    else:
        from openpyxl import load_workbook

        book = load_workbook(str(path), read_only=True, data_only=True)
        sheet = book[book.sheetnames[0]]
        for row in sheet.iter_rows(values_only=True):
            grid.append(["" if v is None else str(v).strip() for v in row])
        book.close()
    return grid


def _norm_header(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower()).rstrip(":")


def _to_iso(value: str) -> str:
    """SEBI writes 'Aug 01, 2013'.  Returns ISO, or '' when unparseable."""
    text = (value or "").strip()
    if not text or "perpetual" in text.lower():
        return ""
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_sebi_excel(path: Path) -> tuple[list[SebiRecord], dict]:
    """Parse a SEBI Download spreadsheet into records plus listing metadata.

    Returns `(records, meta)` where meta carries the as-on date and the record
    count, so the caller can tell a complete import from a partial one.
    """
    grid = _read_excel_grid(path)
    if not grid:
        return [], {}

    # Title row -> as-on date.
    as_on = ""
    for row in grid[:4]:
        for cell in row:
            match = EXCEL_AS_ON.search(cell or "")
            if match:
                as_on = match.group(1)
                break
        if as_on:
            break

    # Header row: the row carrying 'Registration No.'
    header_index = -1
    for i, row in enumerate(grid[:15]):
        if any(HEADER_MARKER in _norm_header(cell) for cell in row):
            header_index = i
            break
    if header_index < 0:
        return [], {"as_on_date": as_on or None, "reported_total": None}

    headers = [_norm_header(cell) for cell in grid[header_index]]

    # Some sheets put 'Correspondence Address' on a grouping row above the
    # headers; anything at or after that column is the correspondence block.
    corr_start = len(headers)
    if header_index > 0:
        for c, cell in enumerate(grid[header_index - 1]):
            if "correspondence" in _norm_header(cell):
                corr_start = c
                break

    # Resolve each canonical field to a column index.  Primary fields take the
    # first match; Fax/City/State/Pincode take the last, because the primary
    # block is followed by an often-empty correspondence block.
    primary: dict[str, int] = {}
    secondary: dict[str, int] = {}
    for c, header in enumerate(headers):
        for field_name, aliases in EXCEL_HEADER_ALIASES.items():
            if header in aliases:
                if c >= corr_start and field_name in {"address", "email", "telephone", "fax"}:
                    secondary[field_name] = c
                else:
                    primary.setdefault(field_name, c)

    records: list[SebiRecord] = []
    for row in grid[header_index + 1:]:
        if not any(cell for cell in row):
            continue

        def value(field_name: str, mapping: dict[str, int] = primary) -> str:
            idx = mapping.get(field_name)
            return row[idx].strip() if idx is not None and idx < len(row) else ""

        reg_no = value("registration_number").upper()
        reg_no = re.sub(r"[^A-Z0-9]", "", reg_no)
        if not REG_NO_PATTERN.fullmatch(reg_no):
            continue  # skip section headers and blank-ish rows

        valid_from = _to_iso(value("valid_from"))
        valid_to = _to_iso(value("valid_to"))
        validity_raw = ""
        if valid_from or valid_to:
            validity_raw = f"{value('valid_from') or '?'} - {value('valid_to') or '?'}"

        records.append(SebiRecord(
            registration_number=reg_no,
            name=value("name"),
            trade_name="",
            entity_type=TYPE_BY_REG_PREFIX.get(reg_no[:3], "unknown"),
            category="",
            email=value("email"),
            telephone=value("telephone"),
            address=value("address"),
            contact_person=value("contact_person"),
            correspondence_address=(row[secondary["address"]].strip()
                                    if "address" in secondary and secondary["address"] < len(row) else ""),
            exchange=value("exchange"),
            validity_raw=validity_raw,
            valid_from=valid_from,
            valid_to=valid_to,
            status="active" if (valid_to == "" and validity_raw) else (
                "expired" if valid_to and valid_to < datetime.now(timezone.utc).date().isoformat()
                else ("active" if valid_to else "unknown")),
            imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        ))

    return records, {
        "as_on_date": as_on or None,
        "reported_total": len(records),
        "title": grid[0][0] if grid and grid[0] else "",
    }


def entity_type_from_filename(filename: str) -> str:
    """Map a SEBI download filename to the entity type we care about."""
    stem = Path(filename).stem.lower()
    if "investment adviser" in stem or "investment advisor" in stem:
        return "investment_adviser"
    if "research analyst" in stem:
        return "research_analyst"
    if "stock broker" in stem or "brokers in" in stem:
        return "stock_broker"
    return ""


#: Which SEBI categories make up each entity type, and the filename fragments
#: that identify the downloads.  A stock broker registers per segment and the
#: segments overlap, so the segments must be tracked separately -- collapsing
#: them onto one key would let one file's totals overwrite another's.
SEGMENT_BY_FILENAME: list[tuple[str, str]] = [
    ("interest rate derivative", "38"),
    ("commodity derivative", "2"),
    ("currency derivative", "32"),
    ("equity derivative", "31"),
    ("debt segment", "37"),
    ("stock brokers in equity", "30"),
    ("investment adviser", "13"),
    ("investment advisor", "13"),
    ("research analyst", "14"),
]

EXPECTED_CATEGORIES: dict[str, set[str]] = {
    "investment_adviser": {"13"},
    "research_analyst": {"14"},
    "stock_broker": {"2", "30", "31", "32", "37", "38"},
}


def segment_from_filename(filename: str) -> str:
    """SEBI category id for a download, from its filename."""
    stem = Path(filename).stem.lower()
    for fragment, intm_id in SEGMENT_BY_FILENAME:
        if fragment in stem:
            return intm_id
    return ""


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

SCHEMA = """
CREATE TABLE IF NOT EXISTS intermediaries (
    registration_number TEXT PRIMARY KEY,
    name TEXT, trade_name TEXT, entity_type TEXT, category TEXT,
    email TEXT, telephone TEXT, address TEXT, contact_person TEXT,
    correspondence_address TEXT, exchange TEXT,
    validity_raw TEXT, valid_from TEXT, valid_to TEXT, status TEXT,
    source_file TEXT, imported_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_intermediaries_name ON intermediaries(name);
CREATE INDEX IF NOT EXISTS idx_intermediaries_type ON intermediaries(entity_type);

-- Which registration numbers this category's listing contained.  A separate
-- table because brokers appear in several segments at once: the same INZ
-- number is listed under equity, derivatives and debt.  A single `category`
-- column on the record cannot represent that, and counting coverage from it
-- would undercount every segment.
CREATE TABLE IF NOT EXISTS listing_membership (
    category TEXT NOT NULL,
    registration_number TEXT NOT NULL,
    PRIMARY KEY (category, registration_number)
);

-- How complete each imported category is.  SEBI paginates at 25 records, so a
-- saved page is usually a *sample*, not the whole list.  Recording what the
-- page claimed to hold is what stops the tool from treating "not in our file"
-- as "not registered" when our file holds half a percent of the register.
CREATE TABLE IF NOT EXISTS listing_coverage (
    category TEXT PRIMARY KEY,
    entity_type TEXT,
    reported_total INTEGER,
    imported_count INTEGER,
    as_on_date TEXT,
    imported_at TEXT
);
"""


class SebiRegistry:
    """SQLite-backed local registry.  Never talks to the network."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    # -- writing ----------------------------------------------------------- #

    def import_records(
        self,
        records: list[SebiRecord],
        *,
        source_file: str = "",
        category: str = "",
        entity_type: str = "",
        reported_total: int | None = None,
        as_on_date: str | None = None,
    ) -> int:
        """Upsert records, and record how complete this listing now is.

        `category` is the coverage key -- SEBI's intmId for a scraped page, or
        the entity type for a downloaded spreadsheet, which has no intmId.
        Returns how many records were written.
        """
        if not records:
            return 0
        rows = []
        for record in records:
            record.source_file = source_file or record.source_file
            rows.append((
                record.registration_number, record.name, record.trade_name,
                record.entity_type, record.category, record.email, record.telephone,
                record.address, record.contact_person, record.correspondence_address,
                record.exchange, record.validity_raw, record.valid_from, record.valid_to,
                record.status, record.source_file, record.imported_at,
            ))
        self._conn.executemany(
            """INSERT INTO intermediaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(registration_number) DO UPDATE SET
                 name=excluded.name, trade_name=excluded.trade_name,
                 entity_type=excluded.entity_type, category=excluded.category,
                 email=excluded.email, telephone=excluded.telephone,
                 address=excluded.address, contact_person=excluded.contact_person,
                 correspondence_address=excluded.correspondence_address,
                 exchange=excluded.exchange, validity_raw=excluded.validity_raw,
                 valid_from=excluded.valid_from, valid_to=excluded.valid_to,
                 status=excluded.status, source_file=excluded.source_file,
                 imported_at=excluded.imported_at""",
            rows,
        )

        # Coverage is counted from the membership table, not from the record
        # table: a broker appears in several segments at once, so the record's
        # single `category` column cannot represent how many numbers each
        # segment listed.
        if category:
            self._conn.executemany(
                "INSERT OR IGNORE INTO listing_membership "
                "(category, registration_number) VALUES (?, ?)",
                [(category, r.registration_number) for r in records],
            )
            held = self._conn.execute(
                "SELECT COUNT(*) FROM listing_membership WHERE category = ?", (category,)
            ).fetchone()[0]
            resolved_type = entity_type or CATEGORY_BY_INTM_ID.get(category, ("unknown", ""))[0]
            self._conn.execute(
                """INSERT INTO listing_coverage
                     (category, entity_type, reported_total, imported_count, as_on_date, imported_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(category) DO UPDATE SET
                     entity_type=excluded.entity_type,
                     reported_total=COALESCE(excluded.reported_total, listing_coverage.reported_total),
                     imported_count=excluded.imported_count,
                     as_on_date=COALESCE(excluded.as_on_date, listing_coverage.as_on_date),
                     imported_at=excluded.imported_at""",
                (category, resolved_type, reported_total, held, as_on_date,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

        self._conn.commit()
        return len(rows)

    # -- reading ----------------------------------------------------------- #

    def lookup(self, registration_number: str) -> dict | None:
        """Look a registration number up.  Returns None when not present."""
        token = "".join(ch for ch in (registration_number or "").upper() if ch.isalnum())
        if not token:
            return None
        row = self._conn.execute(
            "SELECT * FROM intermediaries WHERE registration_number = ?", (token,)
        ).fetchone()
        return dict(row) if row else None

    def search_by_name(self, fragment: str, limit: int = 10) -> list[dict]:
        fragment = (fragment or "").strip()
        if not fragment:
            return []
        rows = self._conn.execute(
            """SELECT * FROM intermediaries
               WHERE name LIKE ? OR trade_name LIKE ?
               ORDER BY name LIMIT ?""",
            (f"%{fragment}%", f"%{fragment}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM intermediaries").fetchone()[0]

    def count_by_type(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM intermediaries GROUP BY entity_type"
        ).fetchall()
        return {r["entity_type"]: r["n"] for r in rows}

    def last_imported(self) -> str | None:
        row = self._conn.execute("SELECT MAX(imported_at) AS t FROM intermediaries").fetchone()
        return row["t"] if row and row["t"] else None

    # -- coverage ---------------------------------------------------------- #

    def coverage(self) -> list[dict]:
        """Per-category import completeness."""
        rows = self._conn.execute(
            """SELECT category, entity_type, reported_total, imported_count,
                      as_on_date, imported_at
               FROM listing_coverage ORDER BY entity_type, category"""
        ).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            total = entry["reported_total"]
            entry["complete"] = total is None or entry["imported_count"] >= total
            entry["share"] = (
                round(entry["imported_count"] / total, 4) if total else None
            )
            out.append(entry)
        return out

    def is_complete_for(self, entity_type: str) -> bool:
        """True only when every category making up this type is whole.

        This is the guard that stops a partial import being used as evidence of
        non-registration.  A stock broker registers per segment and the
        segments overlap, so "complete" means *all six* of SEBI's broker
        listings are loaded -- five out of six would let a broker registered
        only in the missing segment be reported as unregistered.
        """
        entries = {e["category"]: e for e in self.coverage()
                   if e["entity_type"] == entity_type}
        expected = EXPECTED_CATEGORIES.get(entity_type)
        if expected is None:
            return bool(entries) and all(e["complete"] for e in entries.values())
        if set(entries) != expected:
            return False          # a whole segment is missing
        return all(e["complete"] for e in entries.values())

    def missing_categories(self, entity_type: str) -> list[str]:
        """Which of the type's expected listings have not been imported."""
        expected = EXPECTED_CATEGORIES.get(entity_type, set())
        present = {e["category"] for e in self.coverage() if e["entity_type"] == entity_type}
        return sorted(expected - present)

    def close(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------- #
# Process-wide handle
# --------------------------------------------------------------------------- #

_registry: SebiRegistry | None = None


def get_registry(db_path: Path | str | None = None) -> SebiRegistry:
    """Lazily open the local registry.

    Absence is normal -- the app runs fine without it, falling back to the
    manual hand-off -- so callers should treat an empty registry as "not
    imported yet" rather than an error.
    """
    global _registry
    if _registry is None:
        from .config import settings

        _registry = SebiRegistry(db_path or settings.registry_db)
    return _registry


def registry_status() -> dict:
    """Small summary for /health and the operator."""
    try:
        registry = get_registry()
        return {
            "loaded": True,
            "records": registry.count(),
            "by_type": registry.count_by_type(),
            "last_imported": registry.last_imported(),
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {"loaded": False, "error": type(exc).__name__}
