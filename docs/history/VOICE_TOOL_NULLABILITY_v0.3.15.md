# PRISM LINK Voice Tool Nullability — v0.3.15

Groq validates generated tool arguments against the advertised JSON schema before Pipecat invokes the local handler. Some models explicitly emit `null` for optional fields such as `staff_name`, `starts_at_local`, or `customer_name`.

v0.3.15 makes optional string properties explicitly nullable with JSON Schema `anyOf` (`string` or `null`) while keeping consequential and required fields strict. The handlers already treat missing/null optional values as no preference/unknown.

This prevents otherwise valid calls such as `check_availability(..., staff_name=null)` from being rejected before they reach PRISM LINK's deterministic validation layer.
