"""Valuation and risk analytics on a projected cash flow stream.

Yield convention: nominal annual yield compounded monthly (mortgage yield),
i.e. monthly rate = y/12 and the month-t cash flow is discounted by
(1 + y/12)^-t. Settlement is assumed at the start of month 1 (no accrued
interest, no payment delay). Price is quoted per 100 of current balance.
mey_to_bey() restates a monthly-compounded yield as a semiannual
bond-equivalent yield, the convention used to compare MBS to Treasuries.

static_duration_convexity() holds the cash flows fixed when yields shift, so it
ignores the prepayment response to rates. For measures that re-project
prepayments under each rate shock, see ratelinked.effective_risk(). Neither is
OAS-based.
"""

from typing import List, Tuple

from .cashflows import CashFlow


def _pv(cfs: List[CashFlow], y: float) -> float:
    m = y / 12.0
    return sum(cf.total * (1.0 + m) ** -cf.month for cf in cfs)


def price_from_yield(cfs: List[CashFlow], y: float, face: float) -> float:
    """Clean price per 100 of current balance at nominal yield y (decimal)."""
    return 100.0 * _pv(cfs, y) / face


def yield_from_price(cfs: List[CashFlow], price: float, face: float,
                     tol: float = 1e-12) -> float:
    """Solve the yield matching a price by bisection (price falls as yield rises)."""
    lo, hi = -0.5, 1.0
    if not price_from_yield(cfs, hi, face) <= price <= price_from_yield(cfs, lo, face):
        raise ValueError("price outside solvable yield range")
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if price_from_yield(cfs, mid, face) > price:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return 0.5 * (lo + hi)


def wal(cfs: List[CashFlow]) -> float:
    """Weighted average life in years: sum(t/12 * principal_t) / sum(principal_t)."""
    total = sum(cf.principal for cf in cfs)
    return sum(cf.month / 12.0 * cf.principal for cf in cfs) / total


def macaulay_duration(cfs: List[CashFlow], y: float) -> float:
    """PV-weighted average time of cash flows, in years."""
    m = y / 12.0
    pv = [cf.total * (1.0 + m) ** -cf.month for cf in cfs]
    return sum(cf.month / 12.0 * p for cf, p in zip(cfs, pv)) / sum(pv)


def modified_duration(cfs: List[CashFlow], y: float) -> float:
    """Macaulay / (1 + y/12) for monthly compounding."""
    return macaulay_duration(cfs, y) / (1.0 + y / 12.0)


def static_duration_convexity(cfs: List[CashFlow], y: float, face: float,
                              shock_bp: float = 25.0) -> Tuple[float, float]:
    """Finite-difference duration and convexity from +/- shock_bp, cash flows held fixed.

    Duration = (P- - P+) / (2 P0 dy);  Convexity = (P- + P+ - 2 P0) / (P0 dy^2).
    Cash flows are NOT re-projected, so prepayments do not respond to the shock.
    """
    dy = shock_bp / 10000.0
    p0 = price_from_yield(cfs, y, face)
    up = price_from_yield(cfs, y + dy, face)
    dn = price_from_yield(cfs, y - dy, face)
    return (dn - up) / (2.0 * p0 * dy), (dn + up - 2.0 * p0) / (p0 * dy * dy)


def mey_to_bey(y: float) -> float:
    """Monthly-compounded nominal yield -> semiannual bond-equivalent yield."""
    return 2.0 * ((1.0 + y / 12.0) ** 6 - 1.0)
