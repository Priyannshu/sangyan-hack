# Borrowed Badge

A pre-payment verifier for Indian retail investors.

> **A badge can be real without the person wearing it being its owner.**

Most checks ask *"is this SEBI registration number valid?"*. Borrowed Badge asks
*"is this channel actually connected to the entity that holds this
registration?"* — because a genuine registration number is exactly what an
impersonator borrows.

---

## Read this first: what this tool can and cannot do

**It never contacts SEBI.** Automated requests to their portal are rejected —
a `GET` returns page 1, but paging and "Show All Records" both go through a
`POST` their firewall blocks (`HTTP 530 BLOCKED`), and the SEBI Check lookup
returns *"Unauthorized Request Blocked"*. Getting around that would mean
circumventing an access control, so the app doesn't try.

Instead, an **operator** downloads SEBI's own published listing files in a
normal browser and imports them (`scripts/import_sebi.py`). The app then reads
that local registry. Nothing is scraped; the data is exactly what SEBI
published, obtained by a person using the site as intended.

The tool works with or without that registry:

**Anchored — registry loaded.** A registration number resolves to the real
record: name, status, contact, address, validity. A registration number that is
absent from a *complete* listing is flagged. This is the intended mode.

**Anchored — details supplied by hand.** You can also look the registration up
on SEBI Check and type the official website / phone / payment details in. This
is still the only way to compare a **website** against an official domain,
because the download files carry no website field.

**Unanchored — neither.** The tool reports what it can genuinely determine
(live domain registration data, UPI structure, phone shape, message red flags)
and states plainly that the registration is *not confirmed*. It cannot and does
not claim whether the channel is connected.

---

## Verdicts (anchored mode)

| Verdict | Meaning |
|---|---|
| **CONNECTED** | A verified link was found between the channel and the entity in SEBI's listing. |
| **NOT CONNECTED** | We looked, and the link is genuinely not there. Only used when the listing records details for that channel. |
| **MISMATCH** | Likely impersonation — a lookalike domain, a personal payee, a different firm's name, or a lapsed registration. |
| **CANNOT VERIFY** | We could not compare — the listing was silent for that channel. **Not an accusation.** |

`NOT CONNECTED` and `CANNOT VERIFY` are kept strictly apart. Saying "not
connected" is a claim that a link should have existed and did not; it is only
made when SEBI's listing pins that channel down. Where the listing is silent
the answer is `CANNOT VERIFY`, which protects small firms with a sparse public
footprint from being wrongly accused.

Separately, every response carries a **risk level** (`none`/`low`/`medium`/
`high`) folded from all signals found. A lapsed registration or a contradicted
name scores high.

The word **"safe" never appears in any output.**

---

## What is actually live

| Signal | Source | Live? |
|---|---|---|
| Domain registration date, registrar, status | RDAP via `rdap.org` (the public registry protocol) | **Yes** |
| Whether the entity's own site links to the domain | One fetch of the official site | **Yes** |
| Lookalike / homoglyph / TLD-swap analysis | Local reasoning over the two above | Yes |
| UPI `@valid` suffix, PSP, personal-name shape | Local | Yes |
| Phone shape (1600 series vs 10-digit mobile) | Local | Yes |
| Message red flags | Local, rule-based | Yes |
| **Registration lookup, entity name, status, contact** | **SEBI's published listing files, imported by an operator** | On import |
| **Official website / UPI handle** | **You, from SEBI Check** (not in the download files) | Via hand-off |

RDAP is a public protocol designed for exactly this lookup. `rdap.org`
bootstraps to whichever registry is authoritative for the TLD, and urllib
follows that redirect. One request per domain, memoised, short timeout;
failures degrade to "no registration data" rather than raising, because an
unreachable registry is a missing fact, not a reason to accuse anyone.

### The completeness guard

SEBI publishes stock brokers **per segment**, and the segments overlap — a firm
may appear in one, several, or all. So "not in our file" only means "not
registered" when every listing for that entity type is loaded *and* complete.

Where coverage is partial, the tool says so and draws no conclusion:

> `INZ999999999 was not found. 5581 stock broker records are loaded, but 1 of
> SEBI's listings for this type have not been imported (category 30), so its
> absence means nothing either way`

Without that guard, a broker registered only in an unimported segment would be
reported as unregistered at high risk. See `docs/SEBI-DATA.md`.

---

## Quick start

Requires **Python 3.10+** (developed on 3.12, deployed on 3.14).

```bash
cd borrowed-badge
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>. API docs at `/docs`.

There is **no data-generation step** — the app ships with no dataset.

---

## API

### `POST /verify`

```bash
curl -s http://127.0.0.1:8000/verify -H "Content-Type: application/json" -d '{
  "registration_number": "INZ000031633",
  "website": "examp1e.com",
  "sebi_entity_name": "Example Securities Pvt Ltd",
  "sebi_status": "active",
  "sebi_website": "example.com"
}'
```

Two groups of fields:

**What you were given** — `website`, `upi_id`, `bank_account`, `ifsc`,
`payee_name`, `phone`, `message_text`, `registration_number`, `entity_name`.

**What SEBI's listing shows** (the anchor) — `sebi_entity_name`,
`sebi_entity_type`, `sebi_status`, `sebi_website`, `sebi_upi`, `sebi_phone`,
`sebi_payee_name`.

You must supply at least one detail to check. Omit the `sebi_*` group and you
get a risk assessment with `reference_supplied: false`.

Response:

```json
{
  "overall_verdict": "MISMATCH",
  "verdict_label": "Mismatch / likely impersonation",
  "confidence": "Medium",
  "headline": "These details do not match the entity in SEBI's listing...",
  "reference_supplied": true,
  "risk_level": "high",
  "risk_flags": ["LOOKALIKE_DOMAIN", "RECENTLY_REGISTERED_DOMAIN"],
  "channels": [ { "channel": "website", "verdict": "MISMATCH", "evidence": [ ... ] } ],
  "behaviour": { "risk_level": "high", "flags": [ ... ] },
  "rules_fired": ["DOMAIN_LOOKALIKE", "DOMAIN_YOUNG"],
  "reasons": ["..."],
  "recommended_next_steps": ["..."],
  "sebi_check_url": "https://siportal.sebi.gov.in/intermediary/sebi-check"
}
```

### `GET /health`

Liveness plus what external sources the service uses.

### `GET /`

The single-page frontend.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BB_HTTP_TIMEOUT` | `6.0` | timeout for RDAP and homepage fetches, seconds |
| `BB_RDAP_BASE` | `https://rdap.org/domain/` | RDAP bootstrap endpoint |
| `BB_MAX_HOMEPAGE_BYTES` | `300000` | cap on the official-site fetch |
| `BB_PAYEE_THRESHOLD` | `0.82` | payee name must clear this to count as the entity |
| `BB_PAYEE_MISMATCH` | `0.55` | below this, the payee is an unrelated party |
| `BB_LOOKALIKE_THRESHOLD` | `0.80` | domain similarity to treat as an imitation |
| `BB_YOUNG_DOMAIN_DAYS` | `90` | domain age worth calling out |
| `BB_PERSIST_INPUTS` | `false` | store user inputs (off — see below) |

---

## Privacy

Nothing from a `/verify` request is written to disk or to a log. The payload is
held in memory for the duration of the call and discarded. Pasted messages, UPI
IDs and phone numbers are used for matching and then dropped; only matched
patterns are returned, never the whole message.

---

## Safety and ethics

- **No scraping of SEBI.** Their portal blocks automated access and says so; the
  app respects that and hands the user off instead.
- Domain lookups go to **RDAP**, a public protocol designed for the purpose.
  One request per domain, descriptive User-Agent, short timeout, failures
  degrade quietly.
- No site impersonating a real firm was created for this project, and there is
  no scam content or tooling to create fake credentials anywhere in it.
- This is a defensive consumer-protection tool. **SEBI's portal is the
  authoritative source** and is linked on every result.

---

## Project structure

```
backend/
  main.py             FastAPI app
  schemas.py          request/response models
  config.py           thresholds, all env-overridable
  types.py            the four verdicts, evidence containers
  textmatch.py        fuzzy matching (difflib, upgrades to rapidfuzz)
  entity_reference.py Module A — the anchor, from SEBI's listing
  domain_analyzer.py  Module B — live RDAP + live reverse-link + lookalike
  payment_analyzer.py Module C
  phone_analyzer.py   Module D
  behaviour_flags.py  Module E
  verdict_engine.py   Module F
frontend/index.html   single-page UI, no build step
docs/LIMITATIONS.md   read before trusting any verdict
```

---

## Limitations

Read **[docs/LIMITATIONS.md](docs/LIMITATIONS.md)**. It covers what changed when
the tool stopped holding data, why the unanchored mode cannot give a
connection verdict, cat-and-mouse evasion, and the liability of a wrong verdict.
