# Getting the SEBI data

Borrowed Badge never contacts SEBI. Automated requests to their portal are
rejected — a `GET` returns page 1, but paging and "Show All Records" both go
through a `POST` that their firewall blocks (`HTTP 530 BLOCKED`), and the SEBI
Check lookup returns *"Unauthorized Request Blocked"*.

So **you** download the files in your own browser, and the importer reads them.
Nothing in this project talks to sebi.gov.in.

This is an operator task. It is not exposed in the UI and there is no endpoint
that serves the raw registry.

---

## 1. Where the data lives

```
https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognised=yes
```

That page lists every intermediary category. **Each row has a "Download"
link** — that is SEBI's intended bulk route, and it gives you the *complete*
list as a spreadsheet, not a 25-record page.

### Files this tool uses

| What to download | Entity type | Records (Oct 2026) |
|---|---|---|
| Investment Adviser | investment adviser | 1,046 |
| Research Analyst | research analyst | 2,262 |
| Registered Stock Brokers in **equity** segment | stock broker | — |
| Registered Stock Brokers in Equity Derivative Segment | stock broker | 1,778 |
| Registered Stock Brokers in Currency Derivative Segment | stock broker | 1,641 |
| Registered Stock Brokers in Commodity Derivative Segment | stock broker | 840 |
| Registered Stock Brokers in Debt Segment | stock broker | 455 |
| Registered Stock Brokers in Interest Rate Derivative Segment | stock broker | 867 |

Anything else on that page (FPIs, AIFs, mutual funds, banks, depositories,
merchant bankers) is **skipped** — not needed, and their registration numbers
do not follow the same format.

> **All six stock-broker segments are required.** A broker registers per
> segment, and a firm registered only in one segment appears only in that
> segment's file. With five of six loaded, a genuine broker from the missing
> segment would look unregistered. The importer detects this and refuses to
> draw that conclusion until the set is complete.

---

## 2. Import them

```bash
python scripts/import_sebi.py "C:/sebi/"*.xls
```

```
  ok   Investment Adviser as on Oct 04 2026.xls          1049 rows -> 1046 registrations | Investment Adviser | as on Oct 04, 2026
  ok   Research Analyst as on Oct 04 2026.xls            2271 rows -> 2262 registrations | Research Analyst | as on Oct 04, 2026
  ...
Imported 14147 rows. Registry now holds 6163 distinct registrations.

Coverage:
  investment_adviser   13     1046   100.0%  complete
  research_analyst     14     2262   100.0%  complete
  stock_broker         2       840   100.0%  complete
  stock_broker         31     1778   100.0%  complete
  stock_broker         32     1641   100.0%  complete
  stock_broker         37      455   100.0%  complete
  stock_broker         38      867   100.0%  complete

No type has a complete listing yet, so absence of a registration number will NOT be reported as a finding.
```

Rows are upserted on registration number, so re-running after a refresh updates
in place and never duplicates.

Check what is loaded at any time:

```bash
python scripts/import_sebi.py --status
```

### The safety rule this enforces

SEBI reports a *count* on each listing page, and the importer records what it
actually got. **Absence of a registration number is only treated as a finding
when the listing is complete.** In the run above, the equity segment is missing,
so the tool says:

> `INZ999999999 was not found. 5581 stock broker records are loaded, but 1 of
> SEBI's listings for this type have not been imported (category 30), so its
> absence means nothing either way`

That guard is the difference between a useful tool and a false-accusation
generator. Without it, a broker from an unimported segment would be reported as
unregistered at **high risk**.

---

## 3. What the app does with it

A registration number now resolves automatically:

```
verdict : CANNOT_VERIFY
headline: Registration found: AXIS SECURITIES LIMITED holds INZ000161633 (status active).
entity  : AXIS SECURITIES LIMITED | active | validity May 23, 2000 - Perpetual
```

| Situation | Result |
|---|---|
| Found, active | The record anchors the comparison. Name, status and contact come from SEBI, not from the user. |
| Found, expired/suspended | `MISMATCH` — a real number with a dead credential. |
| Not found, listing complete | high risk, `REG_NO_NOT_IN_SEBI_LISTING` |
| Not found, listing complete but this type partly loaded | reported as *not checked* — never as fake |
| Not found, category not loaded | reported as *not checked* |
| Registry absent | app still works; falls back to the manual hand-off |

User-supplied fields still win where present — someone reading the live page is
fresher than any saved file.

---

## 4. Keeping it current

SEBI reissues these listings regularly; each file carries an "as on" date.
There is no automation, by design — refreshing is a deliberate act:

```bash
# download fresh files from SEBI, then:
python scripts/import_sebi.py "C:/sebi/"*.xls
python scripts/import_sebi.py --status
```

Every record carries `imported_at` and the listing's `as_on` date, and lookups
surface them, so a verdict never silently leans on stale data.

On the server, after importing locally:

```bash
gzip -c data/sebi_registry.db | ssh <server> \
  'gunzip > /tmp/r.db && mv /tmp/r.db /opt/borrowed-badge/data/sebi_registry.db'
ssh <server> 'sudo systemctl restart borrowed-badge.service'
```

**Suggested cadence:** monthly. Registration statuses change slowly.

---

## 5. What this data does *not* contain

These spreadsheets give **name, registration number, contact person, address,
email, telephone and validity dates** — for every registered firm.

They do **not** contain an official **website** or **UPI handle**. Those live in
SEBI Check, which is a separate tool with no API and its own bot protection. So:

- The registry confirms *who* an entity is, whether it is active, and its
  registered phone and address.
- Comparing a **website** against an official domain still needs SEBI Check, or
  the user typing the official website into the "From SEBI's listing" fields.

That gap is real and this data source cannot close it.

---

## Appendix: the HTML fallback

If a file is not available as a download, the listing pages can be saved from
the browser. These hold **only page 1 — 25 records** — so they import as
`PARTIAL` and the app will not treat a missing registration number as evidence.

Open a category page, click **Show All Records**, then in the console:

```js
copy(document.documentElement.outerHTML)
```

Paste into a file and import it. Ctrl+S does **not** work — it saves the
original source, not the rows loaded afterwards.
