"""Bull-call-spread strike selection.

User-specified algorithm (verbatim intent):

  Long leg — descending walk:
    For each adjacent pair (K, K_up) in DESCENDING order:
      gap = ask(K) - bid(K_up)
      width = K_up - K
      while gap < width: this K is the candidate long; keep descending.
    The lowest K still satisfying gap < width is the long.

  Short leg — ascending walk from the strike just above the long:
    For each candidate K':
      net_debit = ask(long) - bid(K')
      breakeven = long_strike + net_debit
      pop       = pop_fn(breakeven)
      while net_debit * 100 <= max_loss_usd AND pop >= pop_threshold:
        this K' is the candidate short; keep ascending.
    The highest K' still satisfying both constraints is the short.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptionQuote:
    """Snapshot of one option strike at a point in time."""

    strike: float
    bid: float
    ask: float


@dataclass(frozen=True, slots=True)
class Spread:
    """A chosen bull call spread."""

    long_strike: float
    short_strike: float
    debit: float
    pop: float


def _find_long_index(chain: Sequence[OptionQuote]) -> int | None:
    candidate: int | None = None
    # Walk descending: i is the index of K, i+1 is K_up.
    for i in range(len(chain) - 2, -1, -1):
        width = chain[i + 1].strike - chain[i].strike
        gap = chain[i].ask - chain[i + 1].bid
        if gap < width:
            candidate = i
        else:
            break
    return candidate


def _find_short_index(
    chain: Sequence[OptionQuote],
    long_idx: int,
    *,
    max_loss_usd: float,
    pop_fn: Callable[[float], float],
    pop_threshold: float,
) -> tuple[int, float, float] | None:
    """Return (index, net_debit, pop) for the chosen short, or None if none valid.

    Selection constraints (all must hold):
      - net_debit * 100 <= max_loss_usd
      - pop >= pop_threshold
      - max_profit > 0 (no guaranteed loss)

    The profit-to-loss ratio is **not** a selection constraint — it's
    enforced at the limit-order level (see ``cpapi.execution._safe_debit_max``).
    Selection picks the widest viable spread under the dollar/POP caps; the
    order layer caps the price you'll actually pay.
    """

    long = chain[long_idx]
    candidate: tuple[int, float, float] | None = None
    for j in range(long_idx + 1, len(chain)):
        net_debit = long.ask - chain[j].bid
        width = chain[j].strike - long.strike
        max_profit_per_share = width - net_debit
        breakeven = long.strike + net_debit
        pop = pop_fn(breakeven)
        debit_ok = net_debit * 100.0 <= max_loss_usd
        pop_ok = pop >= pop_threshold
        viable = max_profit_per_share > 0
        if debit_ok and pop_ok and viable:
            candidate = (j, net_debit, pop)
        else:
            break
    return candidate


def select_spread(
    chain: Sequence[OptionQuote],
    *,
    max_loss_usd: float,
    pop_fn: Callable[[float], float],
    pop_threshold: float = 0.70,
) -> Spread | None:
    """Choose the bull call spread per the descending-then-ascending algorithm.

    ``chain`` must be sorted ascending by strike.

    The profit-to-loss ratio is enforced at the limit-order level, not here.
    """

    if len(chain) < 2:
        return None

    long_idx = _find_long_index(chain)
    if long_idx is None:
        return None

    short = _find_short_index(
        chain,
        long_idx,
        max_loss_usd=max_loss_usd,
        pop_fn=pop_fn,
        pop_threshold=pop_threshold,
    )
    if short is None:
        return None
    short_idx, debit, pop = short

    return Spread(
        long_strike=chain[long_idx].strike,
        short_strike=chain[short_idx].strike,
        debit=debit,
        pop=pop,
    )
