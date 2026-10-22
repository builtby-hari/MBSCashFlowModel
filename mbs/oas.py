"""Option-adjusted spread (OAS) by Monte Carlo on a Ho-Lee short rate.

Method, in order:
1. Short rate. Continuous Ho-Lee (Ho and Lee 1986; Hull, Options, Futures and Other
   Derivatives, Ho-Lee / Hull-White with a = 0): r(t) = f(0,t) + vol^2 t^2 / 2 + vol W(t),
   stepped monthly. f(0,t) is the instantaneous forward implied by the input spot curve and
   vol is an absolute (normal) volatility in decimal per year, e.g. 0.01 = 100bp.
2. Calibration. Each month the simulated discount factors are scaled by one number so their
   path average equals the curve discount factor (an empirical martingale correction). The
   zero-spread model therefore reprices any fixed cash flow to the curve exactly, whatever
   the path count. Paths are antithetic (each draw is paired with its mirror) and seeded.
3. Mortgage rate. Simulated long rate (Ho-Lee zero yield, `long_years` tenor, default 10)
   plus a constant spread. The spread is set so the path starts at the current mortgage rate.
   It is held fixed when the curve is shifted, and has no volatility of its own.
4. Prepayments. Each month's CPR comes from the refi S-curve at that path's mortgage rate,
   then optional burnout, through the same projection code as the rest of the package.
5. OAS. The constant spread s, applied as discount factor x [(1+(spot+s)/12)/(1+spot/12)]^-t,
   that makes the average present value equal the price. At zero volatility this is the
   Z-spread. Option cost = Z-spread minus OAS.

The curve, vol, spread tenor and S-curve defaults are illustrative, not market data.
"""

import math
import random
from dataclasses import dataclass
from typing import Callable, List, Optional

from .analytics import z_spread
from .cashflows import Pool, project_cashflows
from .ratelinked import project_rate_linked, refi_cpr

DEFAULT_VOL = 0.01      # 100bp per year normal vol, illustrative


def default_spot(month: int) -> float:
    """Illustrative upward-sloping spot curve (monthly compounding): 4.0% rising toward 5.0%."""
    return 0.04 + 0.01 * (1.0 - math.exp(-month / 60.0))


@dataclass(frozen=True)
class RatePaths:
    discount: List[List[float]]   # [path][month-1] calibrated discount factor
    mortgage: List[List[float]]   # [path][month-1] mortgage rate at the start of that month
    spread: float                 # mortgage rate minus simulated long rate


def _ln_p0(spot, m):
    return -m * math.log1p(spot(m) / 12.0) if m else 0.0


def simulate_paths(spot: Callable[[int], float], months: int, market_rate: float,
                   vol: float = DEFAULT_VOL, n_paths: int = 200, seed: int = 1,
                   long_years: float = 10.0, spread: Optional[float] = None) -> RatePaths:
    """Antithetic Ho-Lee paths calibrated to `spot`. Pass `spread` to hold a base spread fixed."""
    if n_paths < 2 or n_paths % 2:
        raise ValueError("n_paths must be an even number >= 2")
    if vol < 0 or long_years <= 0 or months < 1:
        raise ValueError("need vol >= 0, long_years > 0, months >= 1")
    lnp0 = [_ln_p0(spot, m) for m in range(months + 1)]
    fwd = [0.0] + [12.0 * (lnp0[i - 1] - lnp0[i]) for i in range(1, months + 1)]
    ln_long = [_ln_p0(spot, m + round(long_years * 12)) for m in range(months)]
    tau, step = long_years, math.sqrt(1.0 / 12.0)
    rng = random.Random(seed)

    def one(z_path, sign):
        w, cum, disc, longs = 0.0, 0.0, [], []
        for i in range(1, months + 1):
            t = (i - 1) / 12.0
            r = fwd[i] + 0.5 * vol * vol * t * t + vol * w
            cum += r / 12.0
            disc.append(math.exp(-cum))
            ln_p = ln_long[i - 1] - lnp0[i - 1] + tau * fwd[i] - 0.5 * vol * vol * t * tau * tau - tau * r
            longs.append(-ln_p / tau)
            w += sign * z_path[i - 1] * step
        return disc, longs

    disc, longs = [], []
    for _ in range(n_paths // 2):
        z = [rng.gauss(0.0, 1.0) for _ in range(months)]
        for sign in (1.0, -1.0):
            d, lg = one(z, sign)
            disc.append(d)
            longs.append(lg)
    for i in range(months):
        c = (1.0 + spot(i + 1) / 12.0) ** -(i + 1) / (sum(d[i] for d in disc) / n_paths)
        for d in disc:
            d[i] *= c
    if spread is None:
        spread = market_rate - longs[0][0]
    return RatePaths(disc, [[lg + spread for lg in p] for p in longs], spread)


def path_totals(pool: Pool, paths: RatePaths, burnout: float = 0.0, **curve) -> List[List[float]]:
    """Total investor cash flow by month for each path, prepayments driven by that path's mortgage rate."""
    return [[cf.total for cf in project_cashflows(
        pool, cpr=[refi_cpr(pool.wac - m, **curve) for m in p], burnout=burnout)]
        for p in paths.mortgage]


def _mean_pv(totals, paths):
    n = len(totals)
    months = max(len(t) for t in totals)
    return [sum(t[i] * d[i] for t, d in zip(totals, paths.discount) if i < len(t)) / n
            for i in range(months)]


def _price(mean_pv, spot, s, face):
    return 100.0 * sum(a * ((1.0 + (spot(i + 1) + s) / 12.0) / (1.0 + spot(i + 1) / 12.0)) ** -(i + 1)
                       for i, a in enumerate(mean_pv)) / face


def oas_price(totals: List[List[float]], paths: RatePaths, spot, spread: float, face: float) -> float:
    """Average present value per 100 of balance at a constant spread over the paths."""
    return _price(_mean_pv(totals, paths), spot, spread, face)


def solve_oas(totals, paths, spot, price: float, face: float, tol: float = 1e-12) -> float:
    """Spread matching `price`, by bisection over -50% to 100% (ValueError if outside)."""
    a = _mean_pv(totals, paths)
    lo, hi = -0.5, 1.0
    if not _price(a, spot, hi, face) <= price <= _price(a, spot, lo, face):
        raise ValueError("price outside solvable spread range")
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if _price(a, spot, mid, face) > price else (lo, mid)
    return 0.5 * (lo + hi)


@dataclass(frozen=True)
class OASResult:
    price: float
    oas: float
    z_spread: float
    option_cost: float
    oas_duration: float
    oas_convexity: float
    mortgage_spread: float


def oas_analysis(pool: Pool, price: float, market_rate: float, spot: Callable[[int], float] = default_spot,
                 vol: float = DEFAULT_VOL, n_paths: int = 200, seed: int = 1, burnout: float = 0.0,
                 shock_bp: float = 25.0, long_years: float = 10.0, **curve) -> OASResult:
    """OAS, Z-spread, option cost and OAS-based duration/convexity for a price per 100.

    The Z-spread uses the static rate-linked cash flows at `market_rate`. Duration and
    convexity shift the spot curve in parallel by +/- shock_bp, re-simulate with the same
    seed and base mortgage spread, and reprice at the base OAS.
    """
    if not shock_bp > 0:
        raise ValueError("shock_bp must be > 0")
    dy = shock_bp / 10_000.0

    def sim(shift, spread=None):
        def sp(m):
            return spot(m) + shift
        paths = simulate_paths(sp, pool.wam, market_rate,
                               vol, n_paths, seed, long_years, spread)
        return sp, paths, path_totals(pool, paths, burnout, **curve)

    sp0, p0, t0 = sim(0.0)
    oas = solve_oas(t0, p0, sp0, price, pool.balance)
    z = z_spread(project_rate_linked(pool, market_rate, burnout, **curve), price, pool.balance, spot)

    def value(shift):
        sp, p, t = sim(shift, p0.spread)
        return oas_price(t, p, sp, oas, pool.balance)

    up, dn = value(dy), value(-dy)
    return OASResult(price, oas, z, z - oas,
                     (dn - up) / (2.0 * price * dy),
                     (dn + up - 2.0 * price) / (price * dy * dy), p0.spread)
