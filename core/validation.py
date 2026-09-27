from __future__ import annotations

import re


class CustomerInputError(ValueError):
    """Raised when customer data is unsuitable for a booking record."""


_PHONE_SEPARATORS = re.compile(r"[\s().-]+")
_NZ_LOCAL_PHONE = re.compile(r"^0(?:[2-9]\d{7,10}|800\d{6,8}|900\d{6,8})$")


def normalise_nz_phone(value: str) -> str:
    """Return a digits-only NZ number, accepting local and +64 formats."""
    raw = value.strip()
    compact = _PHONE_SEPARATORS.sub("", raw)
    if compact.startswith("+64"):
        compact = "0" + compact[3:]
    elif compact.startswith("64") and len(compact) > 9:
        compact = "0" + compact[2:]
    if not _NZ_LOCAL_PHONE.fullmatch(compact):
        raise CustomerInputError("Enter a valid New Zealand phone number.")
    return compact


def normalise_customer_name(value: str) -> str:
    """Permit human names while excluding control characters and numeric data."""
    name = " ".join(value.split())
    if not 2 <= len(name) <= 160:
        raise CustomerInputError("Customer name must be between 2 and 160 characters.")
    if not all(character.isalpha() or character in " -'." for character in name):
        raise CustomerInputError(
            "Customer name may contain letters, spaces, apostrophes, hyphens, and periods only."
        )
    return name
