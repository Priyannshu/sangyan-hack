# Borrowed Badge — Video Pitch Script

**Target length:** 2:45 (a 60-second cut is at the end)
**Live URL:** https://sangyan.getn.space
**Repo:** https://github.com/Priyannshu/sangyan-hack

Everything below is verified working. Type the demo inputs exactly as written —
each has been tested against the live deployment.

---

## Before you hit record

- [ ] Open https://sangyan.getn.space in a clean browser window, full screen, **no bookmarks bar**
- [ ] Zoom to ~110% so form text is legible on video
- [ ] Have the repo open in a second tab for the architecture beat
- [ ] Do one full dry run of all three demo inputs — RDAP lookups take a second or two
- [ ] Close Slack / notifications
- [ ] If reading this cold, rehearse the opening line 5× — it carries the whole pitch

---

## The script

### 1 — Hook | 0:00–0:18

| | |
|---|---|
| **On screen** | Your face, or a plain slide with just the project name. Nothing else. |
| **Say** | *"You get a WhatsApp message about an investment. It includes a SEBI registration number. So you look it up — and it's real. It belongs to a genuine, registered firm. So you pay.*<br><br>*And that's the last you see of your money.*<br><br>*Because the number was real. The person holding it wasn't."* |

> **Delivery:** slow down on the last two lines. That contrast *is* the product.

---

### 2 — The insight | 0:18–0:40

| | |
|---|---|
| **On screen** | Cut to a slide: **"Is this registration number valid?"** → struck through → **"Is this channel connected to the entity that holds it?"** |
| **Say** | *"Every existing check asks the first question. SEBI's own portal answers it. And the answer is yes — the number is real.*<br><br>*But a registration number is public. It's printed on websites, on letterheads, in annual reports. It costs an impersonator nothing to copy.*<br><br>*We built Borrowed Badge to ask the second question. Is the website, the UPI ID, the phone number you were given — actually attached to the firm that holds that registration?*<br><br>*A badge can be real without the person wearing it being its owner."* |

---

### 3 — Live demo: the borrowed badge | 0:40–1:20

| | |
|---|---|
| **On screen** | Switch to the live app. Type into **SEBI registration number**: `INA000000037` |
| **Say** | *"Let's use a real one. This is a genuine SEBI registration number for an investment adviser."* |
| **Do** | Click **Check these details**. Result card appears. |
| **Say** | *"And there it is — resolved against SEBI's published listings. Kavitha Menon, an investment adviser, status active since 2013. That part checks out.*<br><br>*Now watch what happens when the number is real but the firm name isn't."* |
| **Do** | Type into **Firm name you were shown**: `Totally Different Holdings Pvt Ltd`. Click Check. |
| **Say** | *"**Mismatch. Likely impersonation.** High risk.*<br><br>*The registration is genuine. The firm displaying it is not the firm that owns it. That's the borrowed badge — and a registration-validity check would have passed this straight through."* |

> **If the demo stalls:** keep talking. The RDAP lookup takes a beat; the narration covers it.

---

### 4 — Live demo: the fake number | 1:20–1:45

| | |
|---|---|
| **Do** | Clear the name field. Change the number to `INA999999999`. Check. |
| **Say** | *"Now a number that doesn't exist.*<br><br>*High risk — it's not in SEBI's listing, and we can say that because we hold the **complete** set of 1,046 registered investment advisers.*" |
| **Do** | Scroll to the evidence panel. Expand it. |
| **Say** | *"And every verdict shows its work. What we checked, what we found, where it came from. No black box — this is a tool people use before sending money, so it has to be inspectable."* |

---

### 5 — Under the hood | 1:45–2:15

| | |
|---|---|
| **On screen** | Switch to the repo README, or a simple architecture slide. |
| **Say** | *"Two data sources, both real.*<br><br>*Domain facts come live from **RDAP** — the public registry protocol — registration date, registrar, status. A domain offered as a registered broker's portal but registered nine days ago is a fact you can act on.*<br><br>*Entity records come from **SEBI's own published listing files**. Right now we hold 6,163 real registrations: 1,046 investment advisers, 2,262 research analysts, and 2,855 stock brokers.*<br><br>*Everything else is local reasoning on top of those two."* |

---

### 6 — The honest constraint | 2:15–2:40

| | |
|---|---|
| **On screen** | Back to the app, or a slide reading **"HTTP 530 BLOCKED"**. |
| **Say** | *"One thing we want to be straight about.*<br><br>*We tried to fetch SEBI data automatically. Their firewall returned **530 BLOCKED** — and a page saying they'd detected unauthorised activity.*<br><br>*We could have spoofed a browser and worked around it. We didn't. Getting past an access control is not a feature.*<br><br>*So we do the honest version: an operator downloads SEBI's own published files, and the app imports them. Nothing is scraped. And where we can't check something, the tool says **'can't verify'** — it never guesses, and it never says the word 'safe'."* |

> **This section wins points.** Most hackathon pitches hide the constraint. Naming it — and explaining the ethical line — reads as engineering judgement.

---

### 7 — Close | 2:40–2:55

| | |
|---|---|
| **On screen** | The verdict card, or the app's home screen. |
| **Say** | *"Borrowed Badge. It doesn't tell you whether a registration is valid — SEBI already does that.*<br><br>*It tells you whether the channel in front of you actually belongs to the firm behind the badge.*<br><br>*Live now, open source, and built to say 'I don't know' when it doesn't know."* |

---

## The 60-second cut

For a short slot, use **beats 1, 3, 6** only.

| Time | Beat |
|---|---|
| 0:00–0:12 | **Hook.** The WhatsApp message with a real registration number. |
| 0:12–0:25 | **Insight.** "A registration number is public. It costs nothing to copy." |
| 0:25–0:42 | **Demo.** `INA000000037` → real firm, found and active. Then add `Totally Different Holdings Pvt Ltd` → **MISMATCH**. |
| 0:42–0:55 | **Honesty.** "SEBI blocks automated access. We didn't work around it — an operator imports their published files." |
| 0:55–1:00 | **Close.** "It tells you whether the channel belongs to the firm behind the badge." |

---

## Delivery notes

**Do**
- Lead with the human scenario, not the architecture. Judges see a dozen feature lists; they remember a story.
- Pause after "Mismatch. Likely impersonation." Let the red card sit on screen.
- Say "can't verify" out loud. It's a differentiator, not a weakness.

**Don't**
- Don't claim the tool *stops* fraud. It informs a decision before payment. Overclaiming is the fastest way to lose a technical judge.
- Don't say "safe" — the app is built never to, and it would undercut the point you're making.
- Don't read the evidence panel line by line on camera. Show it, summarise it, move on.

**Likely questions**

| Question | Answer |
|---|---|
| *"What about the data being stale?"* | Every record carries an import date and SEBI's own "as on" date, and lookups surface them. Refresh is a one-command operator task. |
| *"What if a firm isn't in your data?"* | Then it says "not checked" — never "fake". Absence only counts as a finding when the listing is *complete* for that type. SEBI publishes brokers per segment, so we require all six before drawing that conclusion. |
| *"Can't an impersonator just use a valid `@valid` UPI handle?"* | Yes — and the tool returns only *medium* confidence for that path, never high, precisely because of that. We'd rather under-claim. |
| *"Why not just use SEBI Check?"* | It answers a different question — whether the registration is real. Ours answers whether the *channel* is theirs. They're complementary. |
| *"What's the false-positive story?"* | A false "mismatch" accuses an honest business of fraud. It's the error we spent the most effort avoiding: thin records return "can't verify", the payee threshold is deliberately high, and confidence on "can't verify" is capped so a finding of absence never reads as a confident result. |
