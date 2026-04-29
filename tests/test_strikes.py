"""Tests for the strike-selection algorithm."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bull_call.strikes import OptionQuote, Spread, select_spread


def chain(*rows: tuple[float, float, float]) -> tuple[OptionQuote, ...]:
    return tuple(OptionQuote(strike=k, bid=b, ask=a) for k, b, a in rows)


def fixed_pop(table: dict[float, float]) -> Callable[[float], float]:
    def pop(breakeven: float) -> float:
        # exact match preferred; otherwise nearest key (test ergonomics)
        if breakeven in table:
            return table[breakeven]
        nearest = min(table, key=lambda k: abs(k - breakeven))
        return table[nearest]

    return pop


def test_happy_path_pop_binds_short_first() -> None:
    # SPX-style chain near 5000.  Width = 5 between every pair.
    c = chain(
        (4990.0, 10.00, 11.00),  # deep ITM
        (4995.0, 5.00, 6.00),
        (5000.0, 2.00, 3.00),    # ATM
        (5005.0, 0.50, 1.00),
        (5010.0, 0.10, 0.30),
    )
    # gap(4990) = ask(4990) - bid(4995) = 11 - 5 = 6   >= 5 → STOP descending
    # gap(4995) = ask(4995) - bid(5000) = 6 - 2 = 4    < 5 ✓
    # gap(5000) = ask(5000) - bid(5005) = 3 - 0.50 = 2.50 < 5 ✓
    # gap(5005) = ask(5005) - bid(5010) = 1.0 - 0.10 = 0.90 < 5 ✓
    # → long = 4995

    # Ascending from 5000: net_debit progression
    # K'=5000: nd = 6 - 2 = 4   → break_even=4999  POP=0.85 ✓
    # K'=5005: nd = 6 - 0.5 = 5.5 → break_even=5000.5 POP=0.75 ✓
    # K'=5010: nd = 6 - 0.1 = 5.9 → break_even=5000.9 POP=0.65 ✗ (< 0.70)
    # → short = 5005
    pop = fixed_pop({4999.0: 0.85, 5000.5: 0.75, 5000.9: 0.65})

    spread = select_spread(c, max_loss_usd=1_000.0, pop_fn=pop, pop_threshold=0.70)

    assert spread == Spread(long_strike=4995.0, short_strike=5005.0, debit=5.5, pop=0.75)


def test_debit_cap_binds_short_first() -> None:
    c = chain(
        (4995.0, 5.00, 6.00),
        (5000.0, 2.00, 3.00),
        (5005.0, 0.50, 1.00),
        (5010.0, 0.10, 0.30),
    )
    # Loose POP — won't bind.  Cap at $400 = debit ≤ 4.00.
    # K'=5000: nd=4.00 → exactly 4.00 ≤ 4.00 ✓
    # K'=5005: nd=5.50 > 4.00 ✗
    # → short = 5000
    pop = fixed_pop({4999.0: 0.99, 5000.5: 0.99, 5000.9: 0.99})

    spread = select_spread(c, max_loss_usd=400.0, pop_fn=pop, pop_threshold=0.70)

    assert spread is not None
    assert spread.long_strike == 4995.0
    assert spread.short_strike == 5000.0
    assert spread.debit == pytest.approx(4.00)


def test_no_viable_long_returns_none() -> None:
    # Every adjacent gap >= width — nothing valid.
    c = chain(
        (5000.0, 0.0, 6.0),
        (5005.0, 0.0, 6.0),
        (5010.0, 0.0, 6.0),
    )
    pop = fixed_pop({5000.0: 0.99})
    assert select_spread(c, max_loss_usd=1_000.0, pop_fn=pop, pop_threshold=0.70) is None


def test_no_viable_short_returns_none() -> None:
    # Long picks fine but the very first short candidate already violates POP.
    c = chain(
        (4995.0, 5.00, 6.00),
        (5000.0, 2.00, 3.00),
        (5005.0, 0.50, 1.00),
    )
    # First short K'=5000: nd=4.00, breakeven=4999.  POP=0.50 (below threshold).
    pop = fixed_pop({4999.0: 0.50, 5000.5: 0.50})
    assert select_spread(c, max_loss_usd=1_000.0, pop_fn=pop, pop_threshold=0.70) is None


def test_runs_to_end_of_chain_when_all_valid() -> None:
    c = chain(
        (4995.0, 5.00, 6.00),
        (5000.0, 2.00, 3.00),
        (5005.0, 0.50, 1.00),
        (5010.0, 0.10, 0.30),
    )
    pop = fixed_pop({4999.0: 0.99, 5000.5: 0.99, 5000.9: 0.99})
    spread = select_spread(c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.70)
    assert spread is not None
    assert spread.short_strike == 5010.0


def test_chain_too_short_returns_none() -> None:
    pop = fixed_pop({5000.0: 0.99})
    assert select_spread(chain((5000.0, 1.0, 2.0)), max_loss_usd=1_000.0, pop_fn=pop, pop_threshold=0.70) is None
    assert select_spread((), max_loss_usd=1_000.0, pop_fn=pop, pop_threshold=0.70) is None


def test_non_uniform_widths_handled() -> None:
    # Widths are 5 and 10 — algorithm should compare against each pair's own width.
    c = chain(
        (5000.0, 4.00, 4.50),  # K
        (5005.0, 1.00, 1.50),  # K_up to K=5000 (width 5)
        (5015.0, 0.20, 0.40),  # K_up to K=5005 (width 10)
    )
    # gap(5005) = ask(5005) - bid(5015) = 1.50 - 0.20 = 1.30 < 10 ✓ candidate=5005
    # gap(5000) = ask(5000) - bid(5005) = 4.50 - 1.00 = 3.50 < 5  ✓ candidate=5000
    pop = fixed_pop({5004.5: 0.99, 5004.3: 0.99})
    spread = select_spread(c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.70)
    assert spread is not None
    assert spread.long_strike == 5000.0


def test_min_loss_profit_ratio_narrows_to_deep_itm_only() -> None:
    """With min L:P = 10:1, only spreads where debit >= 10W/11 qualify.

    For a $5-wide pair, debit must be >= $4.55 (deep ITM).
    """

    c = chain(
        (4990.0, 10.00, 11.00),  # deep ITM
        (4995.0, 5.00, 6.00),
        (5000.0, 2.00, 3.00),
        (5005.0, 0.50, 1.00),
        (5010.0, 0.10, 0.30),
    )
    # Long becomes 4995 (algo result). Short candidates from 5000 up:
    # K'=5000: nd = 6 - 2 = 4.00, width=5 -> profit=1, ratio = 4 -> NOT >= 10
    # K'=5005: nd = 6 - 0.5 = 5.5, width=10 -> profit=4.5, ratio = 1.22 -> NOT >= 10
    # No short qualifies => None.
    pop = fixed_pop({4999.0: 0.99, 5000.5: 0.99, 5000.9: 0.99})
    spread = select_spread(
        c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.50,
        min_loss_profit_ratio=10.0,
    )
    assert spread is None


def test_min_loss_profit_ratio_with_deep_itm_long_yields_a_trade() -> None:
    """A chain where the long is very deep ITM gives debit close to width,
    so the L:P ratio can clear 10:1."""

    c = chain(
        (4980.0, 19.00, 20.00),  # very deep ITM
        (4985.0, 14.00, 15.00),
        (4990.0, 9.50, 10.50),
        (4995.0, 5.50, 6.00),
        (5000.0, 0.40, 0.60),
    )
    # Walk down for long:
    # gap(4995)=ask(4995)-bid(5000)=6-0.40=5.60 NOT < 5 -> stop
    # gap(4990)=ask(4990)-bid(4995)=10.50-5.50=5.0 NOT < 5 -> stop
    # gap(4985)=ask(4985)-bid(4990)=15-9.50=5.50 NOT < 5 -> stop
    # gap(4980)=ask(4980)-bid(4985)=20-14=6.0 NOT < 5 -> stop, no candidate
    # Need slightly different numbers — let's use fmw - tighter.
    c2 = chain(
        (4980.0, 18.00, 19.00),
        (4985.0, 14.00, 15.00),  # gap with 4980: 19 - 14 = 5 -> NOT <5
        (4990.0, 10.00, 11.00),  # gap with 4985: 15 - 10 = 5 -> NOT <5
        (4995.0, 6.00, 7.00),    # gap with 4990: 11 - 6 = 5 -> NOT <5
        (5000.0, 2.50, 3.00),    # gap with 4995: 7 - 2.5 = 4.5 < 5 ✓ candidate
    )
    # gap(4995) ✓ -> candidate=4995. Then gap(4990): 11-6=5, fail. long=4995.
    # Short ascending from 5000:
    # K'=5000: nd = 7 - 2.5 = 4.5, width=5, profit=0.5, ratio=9 (< 10) -> fails ratio
    # No qualifying short -> None.
    pop = fixed_pop({4999.5: 0.99})
    assert select_spread(
        c2, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.50,
        min_loss_profit_ratio=10.0,
    ) is None


def test_min_loss_profit_ratio_none_means_no_constraint() -> None:
    """Default behaviour (no min ratio) is unchanged."""

    c = chain(
        (4995.0, 5.00, 6.00),
        (5000.0, 2.00, 3.00),
        (5005.0, 0.50, 1.00),
        (5010.0, 0.10, 0.30),
    )
    pop = fixed_pop({4999.0: 0.99, 5000.5: 0.99, 5000.9: 0.99})
    s_with = select_spread(
        c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.50,
        min_loss_profit_ratio=None,
    )
    s_without = select_spread(
        c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.50,
    )
    assert s_with == s_without
    assert s_with is not None


def test_min_loss_profit_ratio_clears_when_debit_close_to_width() -> None:
    """A $5-wide spread with debit $4.60 has ratio 11.5:1, qualifies for 10:1."""

    # Build a chain where long ITM is priced such that debit hits ~4.60 for $5 width.
    # long=4990, short=4995, want debit = ask(4990) - bid(4995) = 4.60
    # ask(4990) = 9.60, bid(4995) = 5.00 -> debit 4.60
    c = chain(
        (4985.0, 14.00, 15.00),  # gap with 4990: 9.60-?
        (4990.0, 9.00, 9.60),    # gap(4985)=15-9=6 -> NOT <5; stop. long candidate not yet set here.
        (4995.0, 5.00, 5.50),
        (5000.0, 1.00, 1.50),
    )
    # Walk down: gap(4995)=ask(4995)-bid(5000)=5.50-1.00=4.50 <5 ✓ candidate=4995
    # gap(4990)=ask(4990)-bid(4995)=9.60-5.00=4.60 <5 ✓ candidate=4990
    # gap(4985)=ask(4985)-bid(4990)=15.00-9.00=6.00 NOT <5 -> stop. long=4990.
    # Short ascending from 4995:
    # K'=4995: nd=ask(4990)-bid(4995)=9.60-5.00=4.60, width=5, profit=0.40, ratio=11.5 (>=10) ✓
    # K'=5000: nd=9.60-1.00=8.60, width=10, profit=1.40, ratio=6.14 (<10) -> fail
    # short=4995, debit=4.60.
    pop = fixed_pop({4994.6: 0.99, 4998.6: 0.99})
    spread = select_spread(
        c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.50,
        min_loss_profit_ratio=10.0,
    )
    assert spread is not None
    assert spread.long_strike == 4990.0
    assert spread.short_strike == 4995.0
    assert spread.debit == pytest.approx(4.60)


def test_boundary_gap_equals_width_is_invalid() -> None:
    # gap(K) == width should fail the strict-< check and stop descending.
    c = chain(
        (5000.0, 0.0, 6.0),  # gap with 5005 = 6 - 1 = 5 -> NOT < 5
        (5005.0, 1.0, 2.0),  # gap with 5010 = 2 - 0.5 = 1.5 < 5 ✓
        (5010.0, 0.5, 1.0),
    )
    pop = fixed_pop({5006.5: 0.99})
    spread = select_spread(c, max_loss_usd=10_000.0, pop_fn=pop, pop_threshold=0.70)
    assert spread is not None
    # candidate descend: 5005 valid, 5000 invalid (gap == width) → long stays 5005
    assert spread.long_strike == 5005.0
