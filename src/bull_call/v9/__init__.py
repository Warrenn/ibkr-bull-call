"""v9 sector momentum paper-trading module.

Cross-sectional momentum (Jegadeesh-Titman 12-1) on 11 SPDR sector ETFs.
Hold top-3 equal-weighted, monthly rebalance. Long-only. Frozen spec at
``research/specs/strategy-spec-v9-sector-momentum.yaml``; paper-trading
plan at ``docs/STRATEGY-v9-paper-trading.md``.
"""

from __future__ import annotations
