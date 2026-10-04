"""Import SEBI intermediary listings into the local registry.

This is an OPERATOR tool, not part of the running app.  It never contacts SEBI.
You download the files from SEBI yourself; this parses what you saved.

Two input kinds are supported:

  * **Spreadsheets** -- SEBI's own "Download" buttons on the Recognised
    Intermediaries pages.  These are the complete lists and are strongly
    preferred: `.xls` or `.xlsx`.
  * **Saved listing pages** -- the HTML you get from the browser snippet in
    docs/SEBI-DATA.md.  These hold only the first page (25 records), so they
    import as PARTIAL and the app will not treat a missing registration number
    as evidence.

Usage
-----
    python scripts/import_sebi.py ~/Downloads/sebi/*.xls
    python scripts/import_sebi.py --db data/sebi_registry.db saved/*.xls saved/*.html
    python scripts/import_sebi.py --status

Where to get the files: see docs/SEBI-DATA.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.sebi_registry import (  # noqa: E402
    CATEGORY_BY_INTM_ID,
    SebiRegistry,
    detect_category,
    entity_type_from_filename,
    parse_sebi_excel,
    parse_sebi_html,
    read_listing_meta,
    segment_from_filename,
)

#: Entity types this tool reasons about.  Everything else SEBI publishes
#: (FPIs, AIFs, mutual funds, banks) is skipped -- it is not needed and its
#: registration numbers do not follow the same format.
RELEVANT_TYPES = {"stock_broker", "investment_adviser", "research_analyst"}

SPREADSHEET_SUFFIXES = {".xls", ".xlsx", ".xlsm"}


def import_spreadsheet(registry: SebiRegistry, path: Path) -> tuple[int, str]:
    records, meta = parse_sebi_excel(path)
    entity_type = entity_type_from_filename(path.name)
    segment = segment_from_filename(path.name)
    if not segment:
        # Fall back to whatever the numbers say.
        types = {r.entity_type for r in records}
        entity_type = entity_type or (types.pop() if len(types) == 1 else "")
        segment = entity_type

    kept = [r for r in records if r.entity_type in RELEVANT_TYPES]
    if not kept:
        return 0, "no records for the tracked intermediary types"

    for record in kept:
        record.category = segment
    # A SEBI "Download" file is the complete list for its segment, so the
    # denominator is the number of *distinct* registrations it holds. SEBI's
    # advisers file carries 1,049 rows for 1,046 registrations -- a handful of
    # duplicates. Comparing unique-against-rows would leave a complete import
    # looking permanently partial and silently disable the absence finding.
    distinct = len({r.registration_number for r in kept})
    written = registry.import_records(
        kept,
        source_file=path.name,
        category=segment,
        entity_type=entity_type,
        reported_total=distinct,
        as_on_date=meta.get("as_on_date"),
    )
    label = CATEGORY_BY_INTM_ID.get(segment, ("", segment))[1]
    detail = (
        f"{written} rows -> {distinct} registrations | {label} | "
        f"as on {meta.get('as_on_date') or 'unknown'}"
    )
    return written, detail


def import_html(registry: SebiRegistry, path: Path) -> tuple[int, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    intm_id = detect_category(text, path.name)
    meta = read_listing_meta(text)
    records = parse_sebi_html(text, category_hint=intm_id)
    if not records:
        return 0, "no SEBI records found"

    written = registry.import_records(
        records,
        source_file=path.name,
        category=intm_id or (records[0].entity_type if records else ""),
        entity_type=CATEGORY_BY_INTM_ID.get(intm_id, ("", ""))[0],
        reported_total=meta.get("reported_total"),
        as_on_date=meta.get("as_on_date"),
    )
    claimed = meta.get("reported_total")
    detail = f"{written} records"
    if claimed:
        detail += f" of {claimed} ({written / claimed:.1%}) -- PARTIAL"
    return written, detail


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--status", action="store_true",
                        help="report what is loaded and exit")
    args = parser.parse_args()

    if args.db is None:
        from backend.config import settings
        args.db = settings.registry_db

    registry = SebiRegistry(args.db)

    if args.status:
        print(f"Registry: {args.db}")
        if not registry.count():
            print("  empty -- nothing imported yet")
            return 0
        print(f"  records       : {registry.count()}")
        for entity_type, count in sorted(registry.count_by_type().items()):
            print(f"  {entity_type.replace('_', ' '):<20} {count}")
        print(f"  last imported : {registry.last_imported()}")
        print("\nCoverage by listing:")
        for entry in registry.coverage():
            verdict = "complete" if entry["complete"] else "PARTIAL"
            share = f"{entry['share']:.1%}" if entry["share"] is not None else "?"
            print(f"  {entry['category'][:34]:<36} {entry['imported_count']:>6} "
                  f"{share:>8}  {verdict}")
        print("\nAbsence is treated as evidence for:",
              ", ".join(t for t in RELEVANT_TYPES if registry.is_complete_for(t)) or "nothing yet")
        return 0

    if not args.files:
        parser.print_help()
        return 2

    total = 0
    for path in args.files:
        if not path.exists():
            print(f"  SKIP {path.name} -- not found")
            continue
        try:
            if path.suffix.lower() in SPREADSHEET_SUFFIXES:
                written, detail = import_spreadsheet(registry, path)
            else:
                written, detail = import_html(registry, path)
        except Exception as exc:
            print(f"  FAIL {path.name} -- {type(exc).__name__}: {exc}")
            continue

        total += written
        marker = "ok  " if written else "skip"
        print(f"  {marker} {path.name[:58]:<60} {detail}")

    print()
    print(f"Imported {total} rows. Registry now holds {registry.count()} distinct registrations.")
    print()
    print("Coverage:")
    for entry in registry.coverage():
        verdict = "complete" if entry["complete"] else "PARTIAL"
        share = f"{entry['share']:.1%}" if entry["share"] is not None else "?"
        print(f"  {entry['entity_type']:<20} {entry['category'][:32]:<34} "
              f"{entry['imported_count']:>6}  {share:>8}  {verdict}")

    complete_types = [t for t in RELEVANT_TYPES if registry.is_complete_for(t)]
    print()
    if complete_types:
        print("Absence of a registration number counts as evidence for: "
              + ", ".join(sorted(complete_types)))
    else:
        print("No type has a complete listing yet, so absence of a registration "
              "number will NOT be reported as a finding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
