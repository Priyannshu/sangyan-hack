# Limitations and next steps

Written to be read before trusting any verdict this tool produces. Several of
these are not gaps that more engineering closes — they are consequences of the
decision to hold no SEBI data and to not scrape.

---

## 0. The decision that shapes everything else

**SEBI blocks automated lookups, so this tool does not perform one.**

A POST to SEBI Check's own form returns a page reading *"Unauthorized Request
Blocked — we have detected unauthorized activity"*, with a security contact for
appeals. SEBI publishes no API, and its interactive search is behind a
login-protected portal. Getting past that would mean circumventing an access
control, so the app instead **hands the user off** to SEBI's listing and uses
what they read there as the anchor.

Almost every limitation below follows from that one choice.

---

## 1. The tool cannot confirm a registration on its own

This is the headline limitation, and it is a product problem as much as a
technical one.

- **Unanchored mode cannot produce a connection verdict.** With only the
  channel details, the tool reports live domain facts, UPI and phone structure,
  and message red flags, then scores a risk level. It cannot say whether the
  channel belongs to the entity, because it has nothing authoritative to
  compare against. `reference_supplied: false` says so explicitly, and the UI
  will not show a connection verdict in that state.
- **The manual step is friction, and friction loses.** Users must open SEBI
  Check, look the number up, and transcribe the official details back. Most
  will not. A design that requires a second tab is a design most people
  abandon halfway — which is exactly when they are excited and about to pay.
- **Anything typed by hand can be typed wrong.** A mistyped official domain
  becomes a false `MISMATCH` against a legitimate firm. There is no
  verification that what the user pasted is what SEBI actually shows.

If this were ever productised, the honest options are a licensed data feed, an
official API if SEBI ever ships one, or a commercial registered-intermediary
API — all of which reintroduce the data dependence this build deliberately
avoids.

---

## 2. Live sources and their coverage gaps

**RDAP is real, but not universal and not always informative.**

- Not every TLD publishes an RDAP service. Where none exists, the lookup
  degrades to "no registration data" and the tool says so rather than guessing.
- Registrant details are frequently **redacted** for privacy (GDPR and
  equivalent). You usually get the registration date, registrar and status —
  rarely who actually owns it. So the tool can say *when* a domain was
  registered, almost never *to whom*.
- **`rdap.org` is a bootstrap service**, a convenient single entry point rather
  than a registry itself. It redirects to the authoritative server per TLD. It
  is a dependency worth knowing about, and a production build should talk to
  registries directly or use IANA's bootstrap file.
- Every lookup costs a network round trip. Latency is a second or two per
  domain, and it is the slowest part of a request.

**The reverse-link check is the strongest signal and the most fragile.**

- It fetches the entity's homepage once and searches for the candidate domain.
  If the official site is down, slow, blocking non-browser clients, or renders
  its links in JavaScript, the check returns "could not look" — reported as
  missing, never as "not there". That distinction is load-bearing: otherwise a
  failed fetch would silently become evidence against a legitimate firm.
- Only the homepage is fetched. A firm linking to a payment gateway from a
  deeper page will not be found.

**What the tool cannot see at all:** a lookalike of an official domain the user
never supplied. If SEBI's listing is silent on a website, no amount of
similarity analysis helps, because there is nothing to be similar to.

---

## 2b. UPI ownership — why this stays manual

SEBI *does* publish a UPI verification tool, at
`https://www.sebi.gov.in/upi-verification.html`. That page loads without
error, and its JavaScript is readable. It is still not something this tool can
call, for a reason worth writing down.

The page talks to `https://siportal.sebi.gov.in/intermediary/sebi-check` — the
same host that returns *"Unauthorized Request Blocked"* for automated POSTs —
and the check is **captcha-gated by design**:

```
POST /validate  (ctype=upi-check, upi=…)   → 432  Captcha required
GET  /captcha-data                          → captcha image + audio
POST /validate  (… + solved captcha)        → the answer
                                              419 expired · 433 incorrect
```

The captcha has exactly one job: establish that a human is asking. Getting past
it means OCR-ing the image, transcribing the audio, or paying a captcha-solving
service — all of which are circumvention, and all of which fail anyway, since
every request returns `432` without a solved captcha.

So the app does the honest version: a **"Verify on SEBI ↗"** button beside the
UPI field copies the handle and opens SEBI's page. A person solves the captcha
in a few seconds and gets SEBI's own answer. Free, legitimate, and it works
today.

The other route to automated UPI ownership is a commercial VPA lookup —
Razorpay (`registered_name` plus a `name_match_score`), Eko (~₹1.44/lookup) or
Juspay — all of which query the NPCI network and require a KYC'd account. See
`backend/upi_verifier.py`, which is the integration point and is off unless
credentials are supplied.

Watch for one trap if you go that route: some methods are a **penny drop** — an
actual ₹1 transfer to the handle being checked. For fraud verification that is
backwards: you would be paying the suspected fraudster. Ask for a *penniless*
name-resolve.

---

## 3. Cat-and-mouse evasion

Every rule here is public, which means every rule here is evadable by anyone
who reads this repository.

| Signal | How it gets defeated |
|---|---|
| Domain age | Park the domain for two years before use. Age is a weak signal by design. |
| Reverse links | Host the fake site on a subdomain of a compromised legitimate site. |
| Lookalike detection | Register a name with no string resemblance at all and rely on the victim recognising the *brand*, not the domain. |
| Payee name matching | Use a firm-sounding payee name that token-matches the real one. Route through an aggregator. |
| `@valid` handles | Obtain one for an unrelated registered entity. **This is the borrowed badge applied to the payment rail itself.** |
| Message flags | Write a polite message. Trivial, which is why flags never move the verdict. |
| The whole tool | Target entities whose SEBI listing is sparse — the cases where the honest answer is `CANNOT VERIFY`. **The tool is weakest exactly where fraud is easiest.** |

The structural problem: the tool is reactive and its rules are published. A
determined adversary adapts within days.

---

## 4. The liability of a wrong verdict

The hardest problem, and not a technical one.

**A false `MISMATCH` accuses a legitimate business of fraud.** The design spends
most of its effort avoiding that: `CANNOT VERIFY` where the listing is silent,
a high payee-mismatch bar, `CANNOT VERIFY` confidence capped, behaviour flags
structurally prevented from changing a connection verdict. Despite all that:

- A legitimate firm whose bank payee is the **sole proprietor's personal name**
  — very common among small Indian investment advisers — reads as a payee
  mismatch.
- A firm that **recently migrated domains** can be flagged as a young-domain
  lookalike if the new name resembles the old.
- Because the anchor is **typed by the user**, a transcription error becomes a
  false accusation the tool has no way to detect.

**A false `CONNECTED`** does not defame anyone; it fails to protect someone.
The `@valid`-handle path is the weakest link: it returns `CONNECTED` at Medium
confidence on the strength of a suffix an attacker can obtain. A production
version should probably never say `CONNECTED` without either a listing-supplied
identifier or a reverse link.

Before this could be shown to real users: a human-review path for every
`MISMATCH`, a settled legal position on liability for a false accusation, and
final wording owned by a lawyer. In India that means defamation exposure and
SEBI's own rules on who may characterise an entity as unregistered.

---

## 5. Adoption

- **Who runs it?** A consumer will not paste UPI details into an unknown site.
  The plausible homes — a bank or broker's app, a regulator's portal, a
  consumer body — each have their own incentive problem.
- **Where does it sit in the flow?** Verifying is a step people skip when
  excited. It has to be *in the way* — attached to the payment confirmation
  screen — not a site you must remember to visit.
- **The two-tab problem.** As above: the flow requires leaving for SEBI and
  coming back. That is the single biggest threat to the tool being used at all.
- **Trust in a negative answer.** The most common honest output is
  `CANNOT VERIFY` or a risk assessment without confirmation. A tool that
  usually says "I can't confirm" must avoid teaching users that this means
  "fine".

---

## 6. Deliberately out of scope

APK analysis, social media group inspection, QR decoding, a browser extension,
regulators other than SEBI, real payments, and user accounts. There is no ML
model — every verdict is rule-based and explainable, which is the right call
for a tool whose output may be used to accuse someone.

---

## 7. Where to take it next

Ordered by what would reduce harm most:

1. **Get an authoritative data source.** A licensed feed, a commercial
   registered-intermediary API, or SEBI's published bulk downloads. Everything
   in section 1 dissolves if the anchor stops being typed by hand.
2. **Make the hand-off seamless.** Prefill what SEBI's form allows, deep-link
   precisely, or guide a paste-back with parsing so the user does not
   transcribe.
3. **Strengthen the reverse-link crawl** — a few internal pages, not just the
   homepage — and treat its failure modes explicitly.
4. **Add a human-review and appeal path for `MISMATCH`** before any user sees a
   verdict about a real firm.
5. **Instrument the field false-positive rate** by asking users to correct
   verdicts. Only production traffic measures against reality.
6. **Get the wording and liability model reviewed by a lawyer** before any
   public deployment.
