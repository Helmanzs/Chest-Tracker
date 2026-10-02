"""Small helpers shared across modules."""

from __future__ import annotations


def fmt_number(value: float) -> str:
    """1234567 -> '1 234 567'."""
    return f"{value:,.0f}".replace(",", " ")


def lower_keys(mapping: dict[str, float]) -> dict[str, float]:
    return {k.lower(): v for k, v in mapping.items()}
