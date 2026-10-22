"""Prepayment conventions: CPR, SMM, and the PSA standard ramp."""


def smm_from_cpr(cpr: float) -> float:
    """Single monthly mortality from an annualised CPR (both decimals).

    SMM = 1 - (1 - CPR) ** (1/12)
    """
    if not 0.0 <= cpr < 1.0:
        raise ValueError("CPR must be in [0, 1)")
    return 1.0 - (1.0 - cpr) ** (1.0 / 12.0)


def cpr_from_smm(smm: float) -> float:
    """Annualised CPR from a single monthly mortality: CPR = 1 - (1 - SMM) ** 12."""
    if not 0.0 <= smm < 1.0:
        raise ValueError("SMM must be in [0, 1)")
    return 1.0 - (1.0 - smm) ** 12


def psa_cpr(age: int, speed: float = 100.0) -> float:
    """CPR (decimal) at a given loan age in months under the PSA benchmark.

    100 PSA ramps 0.2% CPR per month of age up to 6% CPR at month 30, then
    stays flat. Other speeds scale the whole curve: CPR = speed/100 * base.
    """
    if speed < 0:
        raise ValueError("PSA speed must be non-negative")
    base = 0.06 * min(age, 30) / 30.0
    return base * speed / 100.0


def cpr_vector(n_months: int, start_age: int = 0, psa=None, cpr=None):
    """CPR for projection months 1..n_months.

    Month t of the projection has loan age start_age + t. Provide either a
    PSA speed or a constant CPR (decimal), not both.
    """
    if (psa is None) == (cpr is None):
        raise ValueError("Specify exactly one of psa or cpr")
    if psa is not None:
        cprs = [psa_cpr(start_age + t, psa) for t in range(1, n_months + 1)]
        if max(cprs) >= 1.0:
            raise ValueError("PSA speed too high: CPR would reach 100%")
        return cprs
    if hasattr(cpr, "__len__"):          # month-by-month CPRs, used by the OAS paths
        if len(cpr) < n_months or max(cpr) >= 1.0:
            raise ValueError("need one CPR below 100% per month")
        return list(cpr[:n_months])
    if cpr >= 1.0:
        raise ValueError("CPR must be below 100%")
    return [cpr] * n_months
