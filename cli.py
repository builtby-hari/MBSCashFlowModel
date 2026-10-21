#!/usr/bin/env python3
"""Command-line interface for the MBS cash flow model."""

import argparse
import csv

from mbs import (Pool, project_cashflows, price_from_yield, yield_from_price,
                 wal, macaulay_duration, modified_duration,
                 static_duration_convexity, mey_to_bey,
                 refi_cpr, project_rate_linked, effective_risk)


def build_parser():
    p = argparse.ArgumentParser(description="Agency MBS pass-through cash flow model")
    p.add_argument("--balance", type=float, default=100_000_000, help="current balance ($)")
    p.add_argument("--wac", type=float, default=6.5, help="gross WAC, percent")
    p.add_argument("--coupon", type=float, default=6.0, help="net pass-through coupon, percent")
    p.add_argument("--wam", type=int, default=360, help="remaining term, months")
    p.add_argument("--age", type=int, default=0, help="loan age (WALA), months")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--psa", type=float, help="PSA speed (default 100 if no --cpr)")
    g.add_argument("--cpr", type=float, help="constant CPR, percent")
    g.add_argument("--market-rate", type=float, dest="market_rate",
                   help="current mortgage rate, percent: prepayments follow a refinancing S-curve "
                        "driven by WAC minus this rate, and effective duration/convexity are reported")
    p.add_argument("--burnout", type=float, default=0.0,
                   help="burnout k >= 0: CPR is scaled by exp(-k * cumulative prepaid fraction) (default 0, off)")
    h = p.add_mutually_exclusive_group()
    h.add_argument("--yield", dest="yld", type=float, help="discount yield, percent (default 5.5)")
    h.add_argument("--price", type=float, help="price per 100; solves for yield")
    p.add_argument("--shock", type=float, default=25.0, help="bp shock for effective duration")
    p.add_argument("--scenarios", type=float, nargs="*", default=[50, 100, 150, 200, 300, 500],
                   help="PSA speeds for the scenario table")
    p.add_argument("--show", type=int, default=6, help="months of cash flow table to print")
    p.add_argument("--csv", help="write the full monthly cash flow table to this CSV path")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    pool = Pool(a.balance, a.wac / 100, a.coupon / 100, a.wam, a.age)
    psa, cpr = a.psa, (a.cpr / 100 if a.cpr is not None else None)
    mkt = a.market_rate / 100 if a.market_rate is not None else None
    if psa is None and cpr is None and mkt is None:
        psa = 100.0
    if mkt is not None:
        cfs = project_rate_linked(pool, mkt, a.burnout)
        speed = f"rate-linked {refi_cpr(pool.wac - mkt) * 100:.1f} CPR at {a.market_rate:g}% market rate"
    else:
        cfs = project_cashflows(pool, psa=psa, cpr=cpr, burnout=a.burnout)
        speed = f"{psa:g} PSA" if psa is not None else f"{a.cpr:g} CPR"
    if a.burnout:
        speed += f", burnout {a.burnout:g}"

    if a.price is not None:
        y = yield_from_price(cfs, a.price, pool.balance)
        px = a.price
    else:
        y = (a.yld if a.yld is not None else 5.5) / 100
        px = price_from_yield(cfs, y, pool.balance)

    eff, cvx = static_duration_convexity(cfs, y, pool.balance, a.shock)
    print(f"Pool: ${pool.balance:,.0f}  WAC {a.wac:.3f}%  coupon {a.coupon:.3f}%  "
          f"WAM {pool.wam}  age {pool.age}  prepay {speed}")
    print(f"Price {px:.4f}   Yield {y*100:.4f}% (monthly comp.)   BEY {mey_to_bey(y)*100:.4f}%")
    print(f"WAL {wal(cfs):.2f} yrs   Macaulay {macaulay_duration(cfs, y):.2f}   "
          f"Modified {modified_duration(cfs, y):.2f}")
    print(f"Static duration {eff:.2f}   Static convexity {cvx:.1f}  "
          f"(+/-{a.shock:g}bp, prepayments held fixed)")
    if mkt is not None:
        er = effective_risk(pool, mkt, y, a.shock, a.burnout)
        print(f"Effective duration {er.effective_duration:.2f}   Effective convexity "
              f"{er.effective_convexity:.1f}  (+/-{a.shock:g}bp, prepayments re-projected; "
              f"market rate and yield move together)")

    if a.show > 0:
        print(f"\nFirst {a.show} months of cash flow:")
        print(f"{'Mo':>3} {'Begin Bal':>15} {'Sched Prin':>12} {'Prepay':>12} "
              f"{'Interest':>12} {'Total CF':>13} {'End Bal':>15}")
        for c in cfs[:a.show]:
            print(f"{c.month:>3} {c.beginning_balance:>15,.0f} {c.scheduled_principal:>12,.0f} "
                  f"{c.prepayment:>12,.0f} {c.interest:>12,.0f} {c.total:>13,.0f} "
                  f"{c.ending_balance:>15,.0f}")

    if mkt is not None:
        print(f"\nRate sensitivity at a fixed {y*100:.2f}% yield (prepayments re-projected at each market rate):")
        print(f"{'Mkt rate':>9} {'Incentive':>10} {'CPR':>6} {'WAL':>7} {'EffDur':>8} {'EffConv':>9} {'StatConv':>9}")
        for step in (1.0, 0.5, 0.0, -0.5, -1.0, -1.5, -2.0):
            m = mkt + step / 100
            er = effective_risk(pool, m, y, a.shock, a.burnout)
            w = wal(project_rate_linked(pool, m, a.burnout))
            print(f"{m*100:>8.2f}% {(pool.wac - m)*100:>+9.2f}% {er.cpr*100:>5.1f}% {w:>7.2f} "
                  f"{er.effective_duration:>8.2f} {er.effective_convexity:>9.1f} {er.static_convexity:>9.1f}")

    print(f"\nScenarios at a fixed {y*100:.2f}% yield (cash flows re-projected per speed):")
    print(f"{'PSA':>5} {'WAL':>7} {'Price':>9} {'ModDur':>8}")
    for s in a.scenarios:
        sc = project_cashflows(pool, psa=s, burnout=a.burnout)
        print(f"{s:>5g} {wal(sc):>7.2f} {price_from_yield(sc, y, pool.balance):>9.3f} "
              f"{modified_duration(sc, y):>8.2f}")

    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["month", "beginning_balance", "scheduled_principal", "prepayment",
                        "interest", "total_cash_flow", "ending_balance", "cpr", "smm"])
            for c in cfs:
                w.writerow([c.month, f"{c.beginning_balance:.2f}", f"{c.scheduled_principal:.2f}",
                            f"{c.prepayment:.2f}", f"{c.interest:.2f}", f"{c.total:.2f}",
                            f"{c.ending_balance:.2f}", f"{c.cpr:.6f}", f"{c.smm:.8f}"])
        print(f"\nWrote {len(cfs)} rows to {a.csv}")


if __name__ == "__main__":
    main()
