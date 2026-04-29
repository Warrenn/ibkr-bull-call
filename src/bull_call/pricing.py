"""Black-Scholes probability-of-profit and time helpers."""

from __future__ import annotations

import datetime as dt
import math

from scipy.stats import norm

_SECONDS_PER_YEAR = 365.25 * 86400.0


def pop_bs(
    *,
    spot: float,
    breakeven: float,
    iv: float,
    time_years: float,
    r: float,
) -> float:
    """Probability that S_T > breakeven under risk-neutral lognormal dynamics.

    For a bull call spread, ``breakeven = long_strike + net_debit``.
    """

    if iv < 0:
        raise ValueError(f"iv must be >= 0; got {iv}")
    if time_years < 0:
        raise ValueError(f"time_years must be >= 0; got {time_years}")

    if time_years == 0.0 or iv == 0.0:
        forward = spot * math.exp(r * time_years)
        return 1.0 if forward > breakeven else 0.0

    sigma_root_t = iv * math.sqrt(time_years)
    d2 = (math.log(spot / breakeven) + (r - 0.5 * iv * iv) * time_years) / sigma_root_t
    return float(norm.cdf(d2))


def seconds_to_session_close(now_utc: dt.datetime, close_utc: dt.datetime) -> float:
    """Seconds from ``now_utc`` to ``close_utc``, clamped at 0."""

    delta = (close_utc - now_utc).total_seconds()
    return max(delta, 0.0)


def years_to_session_close(now_utc: dt.datetime, close_utc: dt.datetime) -> float:
    """Convert remaining session seconds to a fraction of a calendar year."""

    return seconds_to_session_close(now_utc, close_utc) / _SECONDS_PER_YEAR
