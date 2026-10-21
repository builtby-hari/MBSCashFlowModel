"""Pass-through cash flow projection."""

import math
from dataclasses import dataclass
from typing import List, Optional

from .prepay import cpr_vector, smm_from_cpr


@dataclass(frozen=True)
class Pool:
    """A single-WAC agency pass-through pool.

    balance:     current outstanding principal balance
    wac:         gross weighted-average coupon (decimal, e.g. 0.065)
    net_coupon:  pass-through rate paid to investors = WAC - servicing - g-fee
    wam:         weighted-average remaining maturity at settlement, in months
    age:         weighted-average loan age (WALA) in months; drives the PSA ramp
    """
    balance: float
    wac: float
    net_coupon: float
    wam: int
    age: int = 0

    def __post_init__(self):
        if self.balance <= 0 or self.wam < 1 or self.age < 0:
            raise ValueError("balance must be > 0, wam >= 1, age >= 0")
        if self.net_coupon > self.wac:
            raise ValueError("net coupon cannot exceed gross WAC")


@dataclass(frozen=True)
class CashFlow:
    month: int
    beginning_balance: float
    scheduled_principal: float
    prepayment: float
    interest: float          # interest passed through to the investor (net coupon)
    total: float             # interest + scheduled principal + prepayment
    ending_balance: float
    cpr: float
    smm: float

    @property
    def principal(self) -> float:
        return self.scheduled_principal + self.prepayment


def scheduled_payment(balance: float, rate: float, n_months: int) -> float:
    """Level monthly mortgage payment: B * r / (1 - (1+r)^-n), r = annual rate / 12."""
    r = rate / 12.0
    if r == 0:
        return balance / n_months
    return balance * r / (1.0 - (1.0 + r) ** -n_months)


def project_cashflows(pool: Pool, psa: Optional[float] = None,
                      cpr: Optional[float] = None, burnout: float = 0.0) -> List[CashFlow]:
    """Project monthly pass-through cash flows to the investor.

    Each month:
      payment    = level payment on current balance over remaining term (gross WAC)
      sched prin = payment - balance * WAC/12
      prepay     = SMM * (balance - sched prin)
      interest   = balance * net_coupon/12      (servicing + g-fee strip stays out)

    burnout k >= 0 (default 0, off) scales each month's CPR by
    exp(-k * cumulative prepayments / original balance): the borrowers most
    sensitive to rates leave first, so the remaining pool prepays more slowly.
    The CPR recorded on each CashFlow is the effective (post-burnout) CPR.
    """
    if burnout < 0:
        raise ValueError("burnout must be >= 0")
    cprs = cpr_vector(pool.wam, pool.age, psa=psa, cpr=cpr)
    bal = pool.balance
    prepaid_total = 0.0
    rows: List[CashFlow] = []
    for t in range(1, pool.wam + 1):
        remaining = pool.wam - t + 1
        pmt = scheduled_payment(bal, pool.wac, remaining)
        sched = pmt - bal * pool.wac / 12.0
        if remaining == 1:
            sched = bal                      # clear rounding dust in the final month
        cpr_t = cprs[t - 1] * math.exp(-burnout * prepaid_total / pool.balance) if burnout else cprs[t - 1]
        smm = smm_from_cpr(cpr_t)
        prepay = smm * (bal - sched)
        prepaid_total += prepay
        interest = bal * pool.net_coupon / 12.0
        end = bal - sched - prepay
        if abs(end) < 1e-9:
            end = 0.0
        rows.append(CashFlow(t, bal, sched, prepay, interest,
                             interest + sched + prepay, end, cpr_t, smm))
        bal = end
        if bal <= 0:
            break
    return rows
