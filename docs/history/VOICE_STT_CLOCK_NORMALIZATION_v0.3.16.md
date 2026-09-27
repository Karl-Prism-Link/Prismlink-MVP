# PRISM LINK v0.3.16 — Spoken Clock Normalization

Voice ASR providers may normalize a caller saying "two o'clock" or "two" as `02:00` even when no AM/PM was spoken. The previous parser treated any `HH:MM` transcript as a literal 24-hour time, causing `02:00` to become 2 AM and fail a 09:00–17:00 business-hours check.

v0.3.16 treats numeric transcript times from `01:00` through `12:59` without an explicit AM/PM marker as 12-hour ambiguous voice times. PRISM LINK then uses configured business hours to infer AM/PM only when exactly one interpretation fits. `00:xx` and `13:xx`–`23:xx` remain explicit 24-hour times.

Examples for a salon open 09:00–17:00:

- `Monday, 02:00` -> Monday 2 PM (only PM fits)
- `Monday, 09:30` -> Monday 9:30 AM (only AM fits)
- `Monday, 13:00` -> Monday 1 PM (explicit 24-hour time)
- explicit `2 AM` / `2 PM` always wins

The inference remains deterministic and does not ask the LLM to choose AM/PM.
