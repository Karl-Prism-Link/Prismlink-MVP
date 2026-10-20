# PRISM LINK — 20-Second Promo (Draft v1)

**Format:** 16:9 master 1920×1080 · 9:16 (1080×1920) and 1:1 (1080×1080) reframes
**Lengths:** 20.0s master · 15.0s paid-social cut · 6.0s bumper (all three in the animatic)

*1:1 is a centre-crop of the 9:16 layout — same stacking order, logo moved to the top third.*
**Audience:** single-location salon owners (1–8 chairs) losing calls during peak hours
**Core promise:** Every call answered, every appointment booked, nothing forgotten.
**Tone:** calm, premium, tactile — a good receptionist, not a robot.

---

## 1. Script (VO + supers)

| # | Time | VO | On-screen text | Visual |
|---|------|----|----------------|--------|
| 1 | 0.0–3.2 | *(no VO — room tone, phone ring)* | `3:47 PM` · `Every chair is full.` | Close-up, warm salon light. Handheld phone on the counter, ringing. A `MISSED CALL` badge stamps down and the ring stops. |
| 2 | 3.2–6.4 | "Your busiest hour shouldn't cost you bookings." | `PRISM LINK` · `Answers in one ring.` | Cut to navy. Prism Link logo resolves out of a prism light streak; teal audio waveform breathes under it. |
| 3 | 6.4–12.0 | "Prism Link answers every call, checks the calendar, and books the client — by voice." | Bubbles: *"Hi — any openings Thursday?"* / *"Thursday 2:30 with Amara. Shall I book it?"* / *"Yes please."* → `BOOKED · Thu 2:30 PM` | Split screen: caller audio waveform left, live transcript right. Booking card slides in and locks with a soft click. |
| 4 | 12.0–16.0 | "Every call logged. Every client remembered." | `Call summary` · `Transcript` · `Message inbox` | Dashboard push-in. The Thursday 2:30 chip lands on the calendar grid; a summary card fills in; an after-hours message slides into the inbox. |
| 5 | 16.0–18.6 | "It's your AI receptionist — answering, booking, and remembering, 24/7." | `24/7 answering` · `Books real appointments` · `Every call logged` | Three claims stack fast, one per beat, thin teal rule between them. |
| 6 | 18.6–20.0 | "Prism Link. Every call, captured." | `PRISM LINK` · `Every call, captured.` · `14-day free trial · no card` | End card on navy with prism streak; URL under the lockup. |

**Word count:** 38 words ≈ 16s at 145 wpm. Room for two natural pauses (after beat 2 and before the tagline).

---

## 2. Delivery notes

- **VO:** warm, mid-register, conversational; land hard on *"bookings"*, *"booked"*, *"captured"*. Do not smile-voice the CTA.
- **Music:** sparse lo-fi piano + soft sub pulse, 84 BPM so each shot boundary falls on a beat. Duck −6 dB under VO. Final hit at 18.6s, tail out by 20.0s.
- **SFX:** one phone ring (beat 1), UI click on booking lock (beat 3), soft whoosh on dashboard push (beat 4), single low boom on logo (beat 6). Nothing else.
- **Type:** supers in the dashboard's UI font, all-caps labels at 0.08em tracking. Never more than 7 words on screen at once.
- **Captions:** burned-in, 90% safe area, because ~80% of feed views are muted. The VO is written to read as captions on their own.

---

## 3. Brand kit (from the live dashboard)

| Token | Hex | Use |
|-------|-----|-----|
| Ink navy | `#09283E` | Backgrounds, end card |
| Deep teal | `#087F98` | Primary brand, waveform |
| Cyan | `#00BACC` | Accent, prism streak, progress |
| Mint | `#168565` | Confirmation / success |
| Mist | `#EAF9FB` | Card fills |
| Paper | `#F7FAFC` | Light scenes |

Logo: wordmark `PRISM` (600) + `LINK` (300), 0.22em tracking, prism glyph to the left that catches a single cyan streak at 18.6s.

---

## 4. Export checklist

- [ ] Master 1920×1080 H.264, 12 Mbps, 24 fps, −14 LUFS.
- [ ] 9:16 and 1:1 reframes: keep the transcript bubbles centred, move the logo to top third.
- [ ] Silent version (music + captions only) for autoplay placements.
- [ ] End card held 2s longer in the paid cut; trim beat 4 by 0.5s to make room.
- [ ] Legal: "14-day free trial · no card required" must stay on screen ≥ 1.2s.
- [ ] Claims match shipped behaviour — do not say "answers 100% of calls" until Aurora end-to-end calling is signed off (see `docs/MVP_STATUS.md`).

---

## 5. Cutdowns

### 15s (paid social / YouTube pre-roll)

Same six beats, retimed; each shot's internal animation scale is applied automatically in the animatic (`C` key).

| Time | VO | Beat |
|---|---|---|
| 0.0–2.2 | "Your busiest hour shouldn't cost you bookings." *(starts 0.6s in, over the ring)* | Missed call |
| 2.2–4.6 | "Prism Link answers." | Logo + waveform |
| 4.6–9.4 | "Checks the calendar, books the client, confirms — by voice." | Booking call |
| 9.4–12.4 | "Every call logged. Every client remembered." | Dashboard |
| 12.4–14.0 | "Answering, booking, remembering — 24/7." | Claims |
| 14.0–15.0 | "Prism Link. Every call, captured." | End card |

36 words / 15s ≈ 144 wpm. Music: cut at 15.0 on the downbeat, no tail.

### 6s (bumper / in-feed)

Three beats only — hook, proof, brand. Scenes 2, 4 and 5 are dropped.

| Time | VO | Beat |
|---|---|---|
| 0.0–1.4 | "Missed calls are lost bookings." | Ring → **MISSED CALL** stamp |
| 1.4–4.4 | "Prism Link answers, books, and remembers." | Transcript → **Booked · Thu 2:30 PM** |
| 4.4–6.0 | "Every call, captured." | End card (URL only, no trial line) |

13 words / 6s ≈ 130 wpm — deliberately slower than the 20s cut, because bumpers are watched once.

---

## 6. 9:16 reframe (1080×1920)

Shot-by-shot for Reels / TikTok / Shorts:

1. **Missed call** — "3:47 PM" lockup at the top third, phone centred but 340px wide, `MISSED CALL` stamp across the screen centre. The ring stays inside the top 60% so the caption bar never covers it.
2. **Logo** — lockup centred, waveform below it; no text in the bottom 22% (UI chrome).
3. **Booking call** — panel stacks: caller card on top (640px), transcript below. Bubbles at max 90% width, 24px type so *"Thursday, 2:30 with Amara"* stays on two lines.
4. **Dashboard** — left nav is dropped; calendar becomes the full frame with taller day columns, cards stack beneath.
5. **Claims** — 40px, one claim per line, stacked with a rule between each.
6. **End card** — logo top third, tagline centre, CTA and URL inside the middle 60%.

Safe areas: keep all type 96px from the top and 190px from the bottom; nothing in the right 120px (DM/share rail).

---

## 7. Animatic controls

`docs/marketing/promo-20s.html`

| Key | Action |
|---|---|
| `C` | cycle cut: 20s → 15s → 6s |
| `V` | toggle 16:9 / 9:16 stage |
| `R` or click the stage | replay |
| `Space` | pause / resume |
| `H` | hide the HUD (clean frame for screen recording) |
| click the progress bar | scrub |


