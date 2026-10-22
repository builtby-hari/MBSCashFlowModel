#!/usr/bin/env python3
"""Command-line interface for the MBS cash flow model."""

import argparse
import csv

from mbs import (
    Pool,
    breakeven_cdr,
    effective_risk,
    load_deal,
    loss_grid,
    macaulay_duration,
    mey_to_bey,
    modified_duration,
    oas_analysis,
    price_from_yield,
    project_cashflows,
    project_rate_linked,
    refi_cpr,
    run_waterfall,
    static_duration_convexity,
    wal,
    yield_from_price,
)


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
    p.add_argument("--oas", action="store_true",
                   help="also report OAS, Z-spread, option cost and OAS duration/convexity "
                        "(needs --market-rate; Monte Carlo on an illustrative curve)")
    p.add_argument("--paths", type=int, default=500, help="OAS Monte Carlo paths, even number (default 500)")
    p.add_argument("--vol", type=float, default=100.0, help="OAS short-rate volatility, bp per year (default 100)")
    p.add_argument("--seed", type=int, default=1, help="OAS random seed (default 1)")
    p.add_argument("--shock", type=float, default=25.0, help="bp shock for effective duration")
    p.add_argument("--scenarios", type=float, nargs="*", default=[50, 100, 150, 200, 300, 500],
                   help="PSA speeds for the scenario table")
    p.add_argument("--show", type=int, default=6, help="months of cash flow table to print")
    p.add_argument("--waterfall", metavar="FILE",
                   help="tranche waterfall: deal TOML file (see sample_deal.toml); uses --psa or --cpr "
                        "for prepayments and ignores the pool flags")
    p.add_argument("--cdr", type=float, default=2.0, help="waterfall constant default rate, percent (default 2)")
    p.add_argument("--severity", type=float, default=40.0, help="waterfall loss severity, percent (default 40)")
    p.add_argument("--lag", type=int, default=6, help="waterfall recovery lag, months (default 6)")
    p.add_argument("--grid", action="store_true", help="also print the CDR x severity class loss grid")
    p.add_argument("--csv", help="write the full monthly cash flow table to this CSV path")
    return p


def main(argv=None):
    parser = build_parser()
    a = parser.parse_args(argv)
    if not a.shock > 0:
        parser.error("--shock must be > 0")
    try:
        _waterfall(a) if a.waterfall else _run(a)
    except (ValueError, OSError) as e:
        parser.error(str(e))
    except KeyError as e:
        parser.error(f"deal file is missing required key {e}")


def _waterfall(a):
    deal = load_deal(a.waterfall)
    cpr = a.cpr / 100 if a.cpr is not None else None
    psa = a.psa if a.psa is not None else (100.0 if cpr is None else None)
    cdr, sev = a.cdr / 100, a.severity / 100
    r = run_waterfall(deal, cdr, sev, a.lag, psa, cpr)
    speed = f"{psa:g} PSA" if psa is not None else f"{a.cpr:g} CPR"
    pool = deal.pool
    print(f"Deal: ${pool.balance:,.0f} pool  WAC {pool.wac*100:.3f}%  net {pool.net_coupon*100:.3f}%  "
          f"WAM {pool.wam}  prepay {speed}")
    print(f"Stress: CDR {a.cdr:g}%  severity {a.severity:g}%  lag {a.lag} months  "
          f"OC target ${deal.oc_target:,.0f}  trigger "
          + (f"{deal.trigger_cum_loss*100:g}% cumulative loss" if deal.trigger_cum_loss is not None else "off"))
    trip = next((p.month for p in r.periods if p.sequential and deal.principal == "pro_rata"), None)
    print("Principal rule: " + (deal.principal if trip is None else f"pro rata, sequential from month {trip}"))
    print(f"\n{'Class':>5} {'Balance':>14} {'Cpn':>6} {'WAL':>6} {'Window':>10} {'Loss %':>7} "
          f"{'Writedown':>10} {'Price':>7} {'Yield':>7}")
    for c, t in zip(r.classes, deal.tranches):
        w = f"{c.first_month}-{c.last_month}" if c.first_month else "none"
        y = f"{c.yield_*100:.3f}%" if c.yield_ is not None else "n/a"
        print(f"{c.name:>5} {c.balance:>14,.0f} {t.coupon*100:>5.2f}% "
              f"{(f'{c.wal:.2f}' if c.wal else 'n/a'):>6} {w:>10} {c.loss_pct:>7.2f} "
              f"{'yes' if c.takes_writedown else 'no':>10} {c.price:>7.2f} {y:>7}")
    print(f"Residual / OC cash: ${r.residual:,.0f}")
    print(f"\nBreak-even CDR at {a.severity:g}% severity (class loss first exceeds 0.01%):")
    for t in deal.tranches:
        b = breakeven_cdr(deal, t.name, sev, a.lag, psa, cpr)
        print(f"  {t.name}: " + (f"{b*100:.2f}%" if b is not None else "no loss at 99% CDR"))
    if a.grid:
        cdrs, sevs = [0.01, 0.02, 0.04, 0.06, 0.08, 0.10], [0.2, 0.4, 0.6, 0.8, 1.0]
        g = loss_grid(deal, cdrs, sevs, a.lag, psa, cpr)
        for t in deal.tranches:
            print(f"\nClass {t.name} loss % of original balance (rows CDR, columns severity):")
            print(f"{'CDR':>6} " + " ".join(f"{s*100:>6g}%" for s in sevs))
            for c, row in zip(cdrs, g[t.name]):
                print(f"{c*100:>5g}% " + " ".join(f"{v:>7.2f}" for v in row))


def _run(a):
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

    if a.oas:
        if mkt is None:
            raise ValueError("--oas needs --market-rate")
        o = oas_analysis(pool, px, mkt, vol=a.vol / 10_000.0, n_paths=a.paths, seed=a.seed,
                         burnout=a.burnout, shock_bp=a.shock)
        print(f"\nOAS ({a.paths} antithetic paths, vol {a.vol:g}bp, seed {a.seed}, illustrative curve)")
        print(f"OAS {o.oas*1e4:.1f}bp   Z-spread {o.z_spread*1e4:.1f}bp   "
              f"Option cost {o.option_cost*1e4:.1f}bp")
        print(f"OAS duration {o.oas_duration:.2f}   OAS convexity {o.oas_convexity:.1f}  "
              f"(+/-{a.shock:g}bp curve shift, OAS held fixed)")

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
