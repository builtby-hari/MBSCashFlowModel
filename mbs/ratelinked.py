"""Rate-linked prepayments and effective duration/convexity.

Prepayment speed is a function of the refinancing incentive (pool WAC minus the current
market mortgage rate) through an S-curve:

    CPR = floor + (cap - floor) / (1 + exp(-steepness * (incentive - midpoint)))

Effective duration and convexity then shock the market mortgage rate and the discount yield
together and RE-PROJECT the cash flows at each shocked rate, so the prepayment response is
included. That response is what produces negative convexity.

The S-curve parameters are illustrative assumptions chosen for shape. They are not calibrated
to agency prepayment data, so treat the outputs as a demonstration of the mechanism.
The refinancing CPR is constant over the life of the pool (no seasoning in the rate response).
"""

import math
from dataclasses import dataclass

from .analytics import price_from_yield, static_duration_convexity
from .cashflows import Pool, project_cashflows

FLOOR_CPR = 0.04        # turnover that happens regardless of rates
CAP_CPR = 0.45          # maximum CPR when the incentive is large
MIDPOINT = 0.01         # incentive at the middle of the S-curve (100bp)
STEEPNESS = 100.0       # S-curve steepness per unit of incentive


def refi_cpr(incentive: float, floor: float = FLOOR_CPR, cap: float = CAP_CPR,
             midpoint: float = MIDPOINT, steepness: float = STEEPNESS) -> float:
    """Constant CPR (decimal) implied by a refinancing incentive (decimal)."""
    if not 0.0 <= floor < cap < 1.0:
        raise ValueError("need 0 <= floor < cap < 1")
    return floor + (cap - floor) / (1.0 + math.exp(-steepness * (incentive - midpoint)))


def project_rate_linked(pool: Pool, market_rate: float, burnout: float = 0.0, **curve):
    """Cash flows when prepayments follow the S-curve at `market_rate` (decimal)."""
    return project_cashflows(pool, cpr=refi_cpr(pool.wac - market_rate, **curve), burnout=burnout)


@dataclass(frozen=True)
class EffectiveRisk:
    price: float
    effective_duration: float
    effective_convexity: float
    static_duration: float
    static_convexity: float
    cpr: float


def effective_risk(pool: Pool, market_rate: float, y: float, shock_bp: float = 25.0,
                   burnout: float = 0.0, **curve) -> EffectiveRisk:
    """Effective duration/convexity with cash flows re-projected under each rate shock.

    market_rate and y are decimals. Each shock moves both by +/- shock_bp. Static measures are
    computed on the base-rate cash flows for comparison.
    """
    if not shock_bp > 0:
        raise ValueError("shock_bp must be > 0")
    dy = shock_bp / 10_000.0

    def value(shift):
        cfs = project_rate_linked(pool, market_rate + shift, burnout, **curve)
        return price_from_yield(cfs, y + shift, pool.balance)

    p0, up, dn = value(0.0), value(dy), value(-dy)
    base = project_rate_linked(pool, market_rate, burnout, **curve)
    s_dur, s_cvx = static_duration_convexity(base, y, pool.balance, shock_bp)
    return EffectiveRisk(
        price=p0,
        effective_duration=(dn - up) / (2.0 * p0 * dy),
        effective_convexity=(dn + up - 2.0 * p0) / (p0 * dy * dy),
        static_duration=s_dur,
        static_convexity=s_cvx,
        cpr=refi_cpr(pool.wac - market_rate, **curve),
    )
