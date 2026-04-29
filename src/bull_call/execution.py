"""Execution dataclasses (library-neutral)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FillReport:
    """Outcome of a combo order submission."""

    filled: bool
    avg_fill_price: float
    order_id: str | None = None
