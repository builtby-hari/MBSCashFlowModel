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

from typing import Callable, List, Tuple

from .cashflows import CashFlow


def _pv(cfs: List[CashFlow], y: float) -> float:
    m = y / 12.0
    return sum(cf.total * (1.0 + m) ** -cf.month for cf in cfs)


def price_from_yield(cfs: List[CashFlow], y: float, face: float) -> float:
    """Clean price per 100 of current balance at nominal yield y (decimal)."""
    return 100.0 * _pv(cfs, y) / face


def yield_from_price(cfs: List[CashFlow], price: float, face: float,
                     tol: float = 1e-12) -> float:
    """Solve the yield matching a price by bisection (price falls as yield rises).

    Searches yields from -50% to 100% (decimal -0.5 to 1.0). A price outside
    the prices at those two ends raises ValueError; it is never clamped.
    """
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


def z_spread(cfs: List[CashFlow], price: float, face: float,
             spot: Callable[[int], float], tol: float = 1e-12) -> float:
    """Zero-volatility spread (decimal) over a spot curve, by bisection.

    spot(month) is the monthly-compounded nominal spot rate for that month.
    Each cash flow is discounted at spot(t) + z. This is a static spread: cash
    flows are fixed, so it is not an OAS (no rate paths, no option cost).

    Searches spreads from -50% to 100% (decimal -0.5 to 1.0) added to the
    spot curve. A price outside the prices at those two ends raises ValueError.
    """
    def px(z):
        return 100.0 * sum(cf.total * (1.0 + (spot(cf.month) + z) / 12.0) ** -cf.month
                           for cf in cfs) / face
    lo, hi = -0.5, 1.0
    if not px(hi) <= price <= px(lo):
        raise ValueError("price outside solvable spread range")
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if px(mid) > price else (lo, mid)
    return 0.5 * (lo + hi)


def wal(cfs: List[CashFlow], delay_days: float = 0.0) -> float:
    """Weighted average life in years: sum(T * principal_t) / sum(principal_t).

    T = month/12 plus optional payment delay_days/360 (default 0: pays at month end).
    """
    total = sum(cf.principal for cf in cfs)
    return sum((cf.month / 12.0 + delay_days / 360.0) * cf.principal for cf in cfs) / total


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


def _bey_times(cfs: List[CashFlow], delay_days: float) -> List[float]:
    # 30/360 time from settlement at pool issue: whole months plus payment delay.
    return [cf.month / 12.0 + delay_days / 360.0 for cf in cfs]


def bey_price(cfs: List[CashFlow], y: float, face: float, delay_days: float = 0.0) -> float:
    """Price per 100 at a semiannual bond-equivalent yield (SIFMA Standard Formulas G.1).

    Each cash flow is discounted by (1 + y/2)^(-2T), T = month/12 + delay_days/360
    (settlement on the issue date, no accrued interest; e.g. delay_days=14 for Ginnie Mae I).
    """
    ts = _bey_times(cfs, delay_days)
    return 100.0 * sum(cf.total * (1.0 + y / 2.0) ** (-2.0 * t) for cf, t in zip(cfs, ts)) / face


def bey_risk(cfs: List[CashFlow], y: float, face: float, delay_days: float = 0.0):
    """(Macaulay duration, modified duration, cash-flow convexity) in years, SIFMA G.1 formulas.

    Cash flows are held fixed (no prepayment response). Convexity is
    sum(T(T+0.5) CF (1+y/2)^(-2T-2)) / P, the closed form of the second derivative.
    """
    ts = _bey_times(cfs, delay_days)
    d = [(1.0 + y / 2.0) ** (-2.0 * t) for t in ts]
    pv = sum(cf.total * x for cf, x in zip(cfs, d))
    mac = sum(t * cf.total * x for cf, t, x in zip(cfs, ts, d)) / pv
    conv = sum(t * (t + 0.5) * cf.total * x / (1.0 + y / 2.0) ** 2
               for cf, t, x in zip(cfs, ts, d)) / pv
    return mac, mac / (1.0 + y / 2.0), conv


def mey_to_bey(y: float) -> float:
    """Monthly-compounded nominal yield -> semiannual bond-equivalent yield."""
    return 2.0 * ((1.0 + y / 12.0) ** 6 - 1.0)
