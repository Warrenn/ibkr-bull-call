"""Fetch the 0DTE option chain for an underlying via CPAPI.

End-to-end flow (matches ibind's reference example for SPX combos):
  1. ``search_contract_by_symbol`` → underlying conid + months string (e.g. "APR26")
  2. ``search_strikes_by_conid`` → list of strike floats for that month
  3. Filter strikes to a window around spot
  4. For each strike, ``search_secdef_info_by_conid`` → list of weekly contracts;
     pick the one whose maturity == today (the 0DTE entry)
  5. ``live_marketdata_snapshot`` for all selected option conids → bid/ask + IV
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any

from ibind import IbkrClient

from bull_call.chain import ChainSnapshot, OptionContract
from bull_call.strikes import OptionQuote

log = logging.getLogger(__name__)


# CPAPI snapshot field IDs (verified in ibind/client/ibkr_definitions.py)
_FIELD_BID_PRICE = "84"
_FIELD_ASK_PRICE = "86"
_FIELD_LAST_PRICE = "31"
_FIELD_IMPLIED_VOL_OPTION = "7633"   # per-strike IV
_FIELD_IMPLIED_VOL_UNDERLYING = "7283"  # ATM IV from option model
_FIELD_MARKET_DATA_AVAILABILITY = "6509"

_DEFAULT_FIELDS = ",".join([
    _FIELD_BID_PRICE,
    _FIELD_ASK_PRICE,
    _FIELD_LAST_PRICE,
    _FIELD_IMPLIED_VOL_OPTION,
    _FIELD_MARKET_DATA_AVAILABILITY,
])


def _month_token(date: dt.date) -> str:
    """Format date as IBKR's secdef month token, e.g. 'APR26'."""

    return date.strftime("%b%y").upper()


def _expiry_yyyymmdd(date: dt.date) -> str:
    return date.strftime("%Y%m%d")


def _is_realtime(availability: str | None) -> bool:
    """First char R == real-time; D/Z/Y/N indicate delayed/frozen/none."""

    return availability is not None and len(availability) > 0 and availability[0] == "R"


def _safe_float(value: object) -> float:
    if value is None or value == "":
        return math.nan
    if isinstance(value, str):
        cleaned = value.replace(",", "").rstrip("CHK%")
        try:
            return float(cleaned)
        except ValueError:
            return math.nan
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan


def fetch_0dte_call_chain(
    client: IbkrClient,
    *,
    symbol: str,
    today_et: dt.date,
    window: int = 40,
    require_realtime: bool = True,
) -> ChainSnapshot | None:
    """Fetch and snapshot the 0DTE call chain for ``symbol`` (e.g. SPX).

    Returns ``None`` if the chain or required market data are unavailable.
    Hard-fails on delayed quotes when ``require_realtime`` is True (default).
    """

    # 1) Underlying contract — for indices we pass secType='IND'.
    sec_type = "IND" if symbol.upper() in {"SPX", "VIX", "NDX", "RUT", "XSP"} else "STK"
    search = client.search_contract_by_symbol(symbol=symbol.upper(), sec_type=sec_type)
    matches = search.data
    if not matches:
        log.error("no contract match for %s", symbol)
        return None
    underlying = matches[0]
    underlying_conid = underlying["conid"]

    # Find the OPT section for this underlying — has the months string.
    opt_section = next(
        (s for s in underlying.get("sections", []) if s.get("secType") == "OPT"),
        None,
    )
    if opt_section is None:
        log.error("no OPT section under %s", symbol)
        return None
    month = _month_token(today_et)
    if month not in opt_section.get("months", "").split(";"):
        log.error("month %s not listed for %s; available: %s",
                  month, symbol, opt_section.get("months"))
        return None

    # Underlying spot snapshot.
    spot_resp = client.live_marketdata_snapshot(
        conids=str(underlying_conid),
        fields=f"{_FIELD_LAST_PRICE},{_FIELD_BID_PRICE},{_FIELD_ASK_PRICE},{_FIELD_IMPLIED_VOL_UNDERLYING},{_FIELD_MARKET_DATA_AVAILABILITY}",
    )
    spot_row = (spot_resp.data or [{}])[0]
    spot = _spot_from_row(spot_row)
    if spot is None:
        log.error("could not read spot for %s", symbol)
        return None
    if require_realtime and not _is_realtime(spot_row.get(_FIELD_MARKET_DATA_AVAILABILITY)):
        log.error("delayed market data for %s; refusing to trade", symbol)
        return None

    atm_iv_underlying = _safe_float(spot_row.get(_FIELD_IMPLIED_VOL_UNDERLYING))

    # 2) Strikes for this month.
    strikes_resp = client.search_strikes_by_conid(
        conid=str(underlying_conid), sec_type="OPT", month=month,
    )
    call_strikes = sorted(strikes_resp.data.get("call", []))
    if not call_strikes:
        log.error("no call strikes returned")
        return None

    # 3) Window around spot.
    spot_idx = min(range(len(call_strikes)), key=lambda i: abs(call_strikes[i] - spot))
    lo = max(0, spot_idx - window)
    hi = min(len(call_strikes), spot_idx + window + 1)
    selected_strikes = call_strikes[lo:hi]
    target_atm_strike = call_strikes[spot_idx]

    # 4) For each strike, find the contract whose maturity == today (the 0DTE leg).
    expiry = _expiry_yyyymmdd(today_et)
    contracts: dict[float, OptionContract] = {}
    for strike in selected_strikes:
        info = client.search_secdef_info_by_conid(
            conid=str(underlying_conid),
            sec_type="OPT",
            month=month,
            strike=strike,
            right="C",
        )
        for entry in info.data or []:
            if entry.get("maturityDate") == expiry:
                contracts[strike] = OptionContract(
                    strike=strike,
                    conid=int(entry["conid"]),
                    right="C",
                    expiry=expiry,
                )
                break

    if not contracts:
        log.error("no 0DTE call contracts found for %s on %s", symbol, expiry)
        return None

    # 5) Bulk snapshot all selected option conids.
    conids_csv = ",".join(str(c.conid) for c in contracts.values())
    snapshot = client.live_marketdata_snapshot(conids=conids_csv, fields=_DEFAULT_FIELDS)
    rows_by_conid = {int(r["conid"]): r for r in (snapshot.data or []) if "conid" in r}

    quotes: list[OptionQuote] = []
    atm_iv_per_strike = math.nan
    for strike, contract in sorted(contracts.items(), key=lambda kv: kv[0]):
        row = rows_by_conid.get(contract.conid)
        if row is None:
            continue
        if require_realtime and not _is_realtime(row.get(_FIELD_MARKET_DATA_AVAILABILITY)):
            continue
        bid = _safe_float(row.get(_FIELD_BID_PRICE))
        ask = _safe_float(row.get(_FIELD_ASK_PRICE))
        if not (math.isfinite(bid) and math.isfinite(ask) and bid > 0 and ask > 0):
            continue
        quotes.append(OptionQuote(strike=strike, bid=bid, ask=ask))
        if math.isnan(atm_iv_per_strike) and strike == target_atm_strike:
            atm_iv_per_strike = _safe_float(row.get(_FIELD_IMPLIED_VOL_OPTION))

    if not quotes:
        log.error("no live quotes parsed from snapshot for %s", symbol)
        return None

    # Prefer per-strike IV at ATM; fall back to underlying-level ATM IV.
    atm_iv = atm_iv_per_strike if math.isfinite(atm_iv_per_strike) else atm_iv_underlying
    if not math.isfinite(atm_iv) or atm_iv <= 0:
        log.error("no usable ATM IV; cannot compute POP")
        return None

    return ChainSnapshot(
        symbol=symbol.upper(),
        expiry=expiry,
        spot=spot,
        atm_iv=atm_iv,
        quotes=tuple(quotes),
        contracts=contracts,
    )


def _spot_from_row(row: dict[str, Any]) -> float | None:
    for field in (_FIELD_LAST_PRICE, _FIELD_BID_PRICE, _FIELD_ASK_PRICE):
        v = _safe_float(row.get(field))
        if math.isfinite(v) and v > 0:
            return v
    return None


def fetch_spot(client: IbkrClient, *, conid: int) -> float | None:
    """One-shot spot read for an arbitrary contract conid."""

    resp = client.live_marketdata_snapshot(
        conids=str(conid),
        fields=f"{_FIELD_LAST_PRICE},{_FIELD_BID_PRICE},{_FIELD_ASK_PRICE}",
    )
    rows = resp.data or []
    if not rows:
        return None
    return _spot_from_row(rows[0])


def estimate_close_credit(
    client: IbkrClient, *, long_leg: OptionContract, short_leg: OptionContract,
) -> float | None:
    """Conservative estimate of the credit a SELL combo MKT would receive.

    Uses ``bid(long) - ask(short)`` — the worst likely fill when crossing the
    spread.  Returns ``None`` if quotes are unavailable.
    """

    conids_csv = f"{long_leg.conid},{short_leg.conid}"
    resp = client.live_marketdata_snapshot(
        conids=conids_csv, fields=f"{_FIELD_BID_PRICE},{_FIELD_ASK_PRICE}",
    )
    rows = {int(r["conid"]): r for r in (resp.data or []) if "conid" in r}
    long_row = rows.get(long_leg.conid)
    short_row = rows.get(short_leg.conid)
    if not (long_row and short_row):
        return None
    bid_long = _safe_float(long_row.get(_FIELD_BID_PRICE))
    ask_short = _safe_float(short_row.get(_FIELD_ASK_PRICE))
    if not (math.isfinite(bid_long) and math.isfinite(ask_short)):
        return None
    return bid_long - ask_short
