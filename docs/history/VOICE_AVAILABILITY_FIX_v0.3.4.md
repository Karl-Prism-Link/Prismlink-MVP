# PRISM LINK v0.3.4 — Voice availability reason fix

This patch separates business-hours status from calendar availability in the live voice tool response.

`check_availability` now returns:

- `available`
- `availability_reason`: `available`, `calendar_conflict`, or `outside_business_hours`
- `calendar_available`
- `within_business_hours`
- `business_hours_for_day`

The system prompt now tells the LLM to treat `availability_reason` as authoritative and never describe a calendar conflict as the salon being closed.

No environment-variable, database, OAuth, or dependency changes are required.
