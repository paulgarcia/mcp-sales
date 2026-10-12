"""The privacy layer. It runs on every tool result, after the tool and before
the result leaves the server.

It lives here and not inside each tool on purpose. If every tool had to
remember to redact, the next tool written in a hurry would leak, and nobody
would notice until it was too late. Here a new tool is safe by default: to let
a field through you would have to change this file on purpose.

Rules:

  * Emails and phones leave only as SHA-256 hashes, normalized the way Meta's
    Conversions API expects. Same input, same hash, so whoever consumes the data
    can match it against ad audiences without the store exposing anyone.
  * Addresses, coordinates and national ids never leave. Not masked: dropped.
    City is fine; it is aggregable and marketing needs it to plan coverage.
  * Names leave as initials, enough to tell two rows apart and not enough to
    identify anyone.
  * Counts of PEOPLE below 5 leave as "< 5". "1 customer in a small town who
    bought X" is a name in another format.

The k-anonymity rule applies to dicts under a key that starts with `by_`. That
convention is for counts of people only. Breakdowns that count THINGS (units,
codes, ads) must be lists of objects, or "7 units" would silently become "< 5".
"""

import hashlib
import re
from typing import Any

K_MIN = 5

# Keys that never leave, whatever their value.
DROPPED = frozenset(
    {
        "address",
        "address_text",
        "delivery_address",
        "delivery_number",
        "delivery_reference",
        "reference",
        "lat",
        "lng",
        "latitude",
        "longitude",
        "delivery_lat",
        "delivery_lng",
        "postal_code",
        "national_id",
        "cedula",
        "dni",
        "password",
        "token",
        "access_token",
    }
)

# Keys whose value leaves as a hash, and the kind of normalization it needs.
HASHED = {
    "email": "email",
    "customer_email": "email",
    "phone": "phone",
    "customer_phone": "phone",
}

# Keys whose value leaves as initials.
INITIALS = frozenset({"full_name", "customer_name"})

PEOPLE_COUNTS_PREFIX = "by_"


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    """Digits only, country code included, no leading zeros (Meta's format)."""
    return re.sub(r"\D", "", value).lstrip("0")


def sha256_hex(value: str | None, kind: str) -> str | None:
    """The hash Meta's Conversions API uses. None stays None."""
    if not value or not value.strip():
        return None
    normalized = normalize_email(value) if kind == "email" else normalize_phone(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def initials(name: str | None) -> str:
    """'Ana María Pérez' -> 'A. M. P.'"""
    parts = [p for p in (name or "").split() if p]
    return " ".join(f"{p[0].upper()}." for p in parts[:3])


def k_anon(n: int | None) -> int | str | None:
    """A count of people, or '< 5' when it is small enough to identify someone."""
    if n is None:
        return None
    return n if n >= K_MIN else f"< {K_MIN}"


def redact(value: Any) -> Any:
    """Apply the rules at any depth."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in DROPPED:
                continue
            if lowered in HASHED:
                # Also when the value is None: the key becomes `hash_email`
                # either way, so every row has the same shape and a consumer
                # never has to wonder whether a plain `email` slipped through.
                kind = HASHED[lowered]
                out[f"hash_{kind}"] = sha256_hex(item, kind) if isinstance(item, str) else None
                continue
            if lowered in INITIALS and isinstance(item, str):
                out[key] = initials(item)
                continue
            if lowered.startswith(PEOPLE_COUNTS_PREFIX) and isinstance(item, dict):
                out[key] = {
                    k: k_anon(v) if isinstance(v, int) and not isinstance(v, bool) else redact(v)
                    for k, v in item.items()
                }
                continue
            out[key] = redact(item)
        return out
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value
