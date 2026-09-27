# PRISM LINK v0.3.5 — Voice date/time guard

This patch hardens live voice date/time handling after a test exposed inconsistent weekday/date and AM/PM interpretation.

Changes:
- passes the caller's original date/time phrase into availability and booking tools;
- validates named weekdays, today/tomorrow, and explicit AM/PM against the LLM-generated ISO datetime;
- blocks availability/booking on a mismatch and requires clarification;
- returns canonical weekday/date/time/spoken_start fields from tools;
- instructs the LLM to repeat the tool-returned datetime rather than calculate calendar labels itself.

No database migration or new dependency is required.
