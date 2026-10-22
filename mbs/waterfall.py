"""Tranche waterfall for a senior/subordinate deal on a pool with defaults and losses.

Collateral (project_collateral), each month t:
  defaults    = MDR * beginning performing balance, MDR = 1 - (1 - CDR)^(1/12)
  performing  = beginning balance - defaults. Defaulted loans pay no interest
                and no principal; they sit in a liquidation pipeline.
  sched prin  = level-payment amortization of the performing balance (as in cashflows.py)
  prepayment  = SMM * (performing - sched prin)
  interest    = performing * net coupon / 12   (so defaults cut interest at once)
  liquidation = defaults from `lag` months ago: recovery = (1 - severity) * amount is
                cash (counted as principal), loss = severity * amount is realized.
With CDR = 0 this is exactly project_cashflows.

Deal, each month:
  1. Interest: each class is due balance * coupon / 12; a shortfall is shared pro rata
     (unpaid interest is not carried forward).
  2. Excess spread = interest collected - interest paid.
  3. Principal funds = scheduled + prepaid + recoveries. Together with excess spread
     they pay notes down toward the OC target: notes = collateral (performing plus
     pipeline) - OC target. Excess spread is captured only to restore that OC.
     Cash above what is needed goes to the residual (OC release).
  4. Principal goes pro rata by balance, or strictly sequentially (A, then M, then B)
     when principal = "sequential" or when cumulative realized loss reaches the trigger
     (the shifting-interest switch: junior classes stop receiving principal).
  5. If notes still exceed collateral after payment, the gap is written down
     bottom up (last class first). Written-down principal is never reinstated.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .analytics import yield_from_price, wal
from .cashflows import CashFlow, Pool, scheduled_payment
from .prepay import cpr_vector, smm_from_cpr

_EPS = 1e-6          # dollars; below this a balance gap is float noise


@dataclass(frozen=True)
class Tranche:
    name: str
    balance: float
    coupon: float            # decimal, fixed
    price: float = 100.0     # per 100 of original balance, used for the yield column


@dataclass(frozen=True)
class Deal:
    """tranches are senior first. OC at closing = pool balance - sum of tranche balances."""
    pool: Pool
    tranches: Tuple[Tranche, ...]
    oc_target: float = 0.0                       # dollars
    trigger_cum_loss: Optional[float] = None     # fraction of the original pool, None = off
    principal: str = "pro_rata"                  # or "sequential"

    def __post_init__(self):
        names = [t.name for t in self.tranches]
        if not names or len(set(names)) != len(names):
            raise ValueError("tranches need unique names")
        if any(t.balance <= 0 or t.coupon < 0 for t in self.tranches):
            raise ValueError("tranche balance must be > 0 and coupon >= 0")
        if sum(t.balance for t in self.tranches) > self.pool.balance + _EPS:
            raise ValueError("tranche balances exceed the pool balance")
        if self.principal not in ("pro_rata", "sequential"):
            raise ValueError("principal must be pro_rata or sequential")
        if self.oc_target < 0:
            raise ValueError("oc_target must be >= 0")


@dataclass(frozen=True)
class CollateralRow:
    month: int
    beginning_balance: float     # performing
    default: float
    scheduled_principal: float
    prepayment: float
    interest: float
    recovery: float
    loss: float
    ending_balance: float        # performing
    pipeline: float              # defaulted, not yet liquidated

    @property
    def cash(self) -> float:
        return self.interest + self.scheduled_principal + self.prepayment + self.recovery


def project_collateral(pool: Pool, cdr: float = 0.0, severity: float = 0.0, lag: int = 0,
                       psa: Optional[float] = None, cpr: Optional[float] = None) -> List[CollateralRow]:
    if not 0.0 <= cdr < 1.0:
        raise ValueError("CDR must be in [0, 1)")
    if not 0.0 <= severity <= 1.0:
        raise ValueError("severity must be in [0, 1]")
    if not (isinstance(lag, int) and lag >= 0):
        raise ValueError("lag must be a whole number of months >= 0")
    cprs = cpr_vector(pool.wam, pool.age, psa=psa, cpr=cpr)
    mdr = smm_from_cpr(cdr)
    bal, pending, rows = pool.balance, {}, []
    t = 0
    while bal > 0 or pending:
        t += 1
        remaining = pool.wam - t + 1
        d = mdr * bal if remaining >= 1 else 0.0
        perf = bal - d
        sched = prepay = 0.0
        if remaining >= 1 and perf > 0:
            sched = scheduled_payment(perf, pool.wac, remaining) - perf * pool.wac / 12.0
            if remaining == 1:
                sched = perf
            prepay = smm_from_cpr(cprs[t - 1]) * (perf - sched)
        if d > 0:
            pending[t + lag] = pending.get(t + lag, 0.0) + d
        liq = pending.pop(t, 0.0)
        end = perf - sched - prepay
        if abs(end) < 1e-9:
            end = 0.0
        rows.append(CollateralRow(t, bal, d, sched, prepay, perf * pool.net_coupon / 12.0,
                                  liq * (1.0 - severity), liq * severity, end,
                                  sum(pending.values())))
        bal = end
    return rows


@dataclass(frozen=True)
class Period:
    month: int
    interest_collected: float
    principal_collected: float       # scheduled + prepaid + recoveries
    loss: float
    interest_paid: Dict[str, float]
    principal_paid: Dict[str, float]
    writedown: Dict[str, float]
    ending: Dict[str, float]         # class balances after payment and writedown
    residual: float
    sequential: bool                 # True if principal was paid strictly in order
    collateral_end: float            # performing plus pipeline

    @property
    def paid_out(self) -> float:
        return (sum(self.interest_paid.values()) + sum(self.principal_paid.values())
                + self.residual)


@dataclass(frozen=True)
class ClassResult:
    name: str
    balance: float
    cashflows: List[CashFlow]
    wal: Optional[float]
    first_month: Optional[int]       # principal window
    last_month: Optional[int]
    writedown: float
    price: float
    yield_: Optional[float]          # monthly-compounded; None if the price is unsolvable

    @property
    def loss_pct(self) -> float:
        return 100.0 * self.writedown / self.balance

    @property
    def takes_writedown(self) -> bool:
        return self.writedown > _EPS


@dataclass(frozen=True)
class DealResult:
    collateral: List[CollateralRow]
    periods: List[Period]
    classes: List[ClassResult]
    residual: float

    def by_name(self, name: str) -> ClassResult:
        return next(c for c in self.classes if c.name == name)


def run_waterfall(deal: Deal, cdr: float = 0.0, severity: float = 0.0, lag: int = 0,
                  psa: Optional[float] = None, cpr: Optional[float] = None) -> DealResult:
    if psa is None and cpr is None:
        psa = 100.0
    coll = project_collateral(deal.pool, cdr, severity, lag, psa, cpr)
    names = [t.name for t in deal.tranches]
    bal = {t.name: t.balance for t in deal.tranches}
    coupon = {t.name: t.coupon for t in deal.tranches}
    wd_total = dict.fromkeys(names, 0.0)
    flows = {n: [] for n in names}
    periods, cum_loss, residual = [], 0.0, 0.0

    for row in coll:
        beg = dict(bal)
        n_beg = sum(beg.values())
        cum_loss += row.loss
        trig = (deal.trigger_cum_loss is not None
                and cum_loss >= deal.trigger_cum_loss * deal.pool.balance)
        sequential = deal.principal == "sequential" or trig

        due = {n: beg[n] * coupon[n] / 12.0 for n in names}
        share = min(1.0, row.interest / sum(due.values())) if sum(due.values()) > 0 else 0.0
        int_paid = {n: due[n] * share for n in names}
        excess = row.interest - sum(int_paid.values())

        coll_end = row.ending_balance + row.pipeline
        prin_funds = row.scheduled_principal + row.prepayment + row.recovery
        needed = max(0.0, n_beg - max(0.0, coll_end - deal.oc_target))
        pay = min(needed, prin_funds + excess, n_beg)
        res = prin_funds + excess - pay

        prin_paid = dict.fromkeys(names, 0.0)
        if sequential:
            left = pay
            for n in names:
                prin_paid[n] = min(beg[n], left)
                left -= prin_paid[n]
        elif n_beg > 0:
            for n in names:
                prin_paid[n] = pay * beg[n] / n_beg

        wd = dict.fromkeys(names, 0.0)
        gap = n_beg - pay - coll_end            # notes above collateral: bottom-up writedown
        for n in reversed(names):
            if gap <= _EPS:
                break
            wd[n] = min(beg[n] - prin_paid[n], gap)
            gap -= wd[n]
        for n in names:
            bal[n] = max(0.0, beg[n] - prin_paid[n] - wd[n])
            wd_total[n] += wd[n]
            flows[n].append(CashFlow(row.month, beg[n], prin_paid[n], 0.0, int_paid[n],
                                     int_paid[n] + prin_paid[n], bal[n], 0.0, 0.0))
        residual += res
        periods.append(Period(row.month, row.interest, prin_funds, row.loss, int_paid,
                              prin_paid, wd, dict(bal), res, sequential, coll_end))

    classes = []
    for t in deal.tranches:
        cfs = flows[t.name]
        paid = [c for c in cfs if c.principal > 0]
        try:
            y = yield_from_price(cfs, t.price, t.balance)
        except ValueError:
            y = None
        classes.append(ClassResult(t.name, t.balance, cfs, wal(cfs) if paid else None,
                                   paid[0].month if paid else None,
                                   paid[-1].month if paid else None,
                                   wd_total[t.name], t.price, y))
    return DealResult(coll, periods, classes, residual)


def class_losses(deal: Deal, cdr: float, severity: float, lag: int = 0,
                 psa: Optional[float] = None, cpr: Optional[float] = None) -> Dict[str, float]:
    """Loss % of original balance per class."""
    r = run_waterfall(deal, cdr, severity, lag, psa, cpr)
    return {c.name: c.loss_pct for c in r.classes}


def loss_grid(deal: Deal, cdrs, severities, lag: int = 0, psa=None, cpr=None):
    """{class: [[loss % for each severity] for each CDR]}, the classic stress grid."""
    out = {t.name: [] for t in deal.tranches}
    for c in cdrs:
        rows = [class_losses(deal, c, s, lag, psa, cpr) for s in severities]
        for n in out:
            out[n].append([r[n] for r in rows])
    return out


def breakeven_cdr(deal: Deal, name: str, severity: float, lag: int = 0, psa=None, cpr=None,
                  hi: float = 0.99, tol: float = 1e-5,
                  min_loss_pct: float = 0.01) -> Optional[float]:
    """Lowest CDR at which the class loss first exceeds min_loss_pct of its balance.

    The default threshold is 0.01% (1bp): late-life dust from the pool running off
    below the OC target is not a credit event. Found by bisection. Assumes class loss never falls as CDR rises. Returns 0.0 if it loses at CDR 0
    and None if it takes no loss even at `hi`.
    """
    def hit(c):
        return class_losses(deal, c, severity, lag, psa, cpr)[name] > min_loss_pct
    if hit(0.0):
        return 0.0
    if not hit(hi):
        return None
    lo = 0.0
    while hi - lo > tol:
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if hit(mid) else (mid, hi)
    return hi


def load_deal(path: str) -> Deal:
    """Read a deal from a TOML file (Python 3.11+). See sample_deal.toml."""
    try:
        import tomllib
    except ImportError:
        raise ValueError("reading a TOML deal needs Python 3.11+; build a Deal in code instead")
    with open(path, "rb") as f:
        d = tomllib.load(f)
    c, dl = d["collateral"], d.get("deal", {})
    pool = Pool(c["balance"], c["wac"] / 100, c["coupon"] / 100, c["wam"], c.get("age", 0))
    tr = tuple(Tranche(x["name"], pool.balance * x["balance_pct"] / 100, x["coupon"] / 100,
                       x.get("price", 100.0)) for x in d["class"])
    trig = dl.get("trigger_cum_loss_pct")
    return Deal(pool, tr, pool.balance * dl.get("oc_target_pct", 0.0) / 100,
                trig / 100 if trig is not None else None, dl.get("principal", "pro_rata"))
