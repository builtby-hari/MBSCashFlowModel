import os
import unittest

from mbs import Pool, project_cashflows
from mbs.waterfall import (Deal, Tranche, project_collateral, run_waterfall, loss_grid,
                           breakeven_cdr, class_losses)

POOL = Pool(100_000_000, 0.07, 0.065, 360)
TRANCHES = (Tranche("A", 80e6, 0.0525), Tranche("M", 12e6, 0.06), Tranche("B", 6e6, 0.07))


def deal(**kw):
    kw.setdefault("oc_target", 2e6)
    return Deal(POOL, TRANCHES, **kw)


class TestCollateral(unittest.TestCase):
    def test_zero_default_matches_pass_through(self):
        want = project_cashflows(POOL, psa=150)
        got = project_collateral(POOL, 0.0, 0.0, 6, psa=150)
        self.assertEqual(len(got), len(want))
        for g, w in zip(got, want):
            self.assertAlmostEqual(g.interest, w.interest, places=6)
            self.assertAlmostEqual(g.scheduled_principal + g.prepayment, w.principal, places=6)
            self.assertAlmostEqual(g.ending_balance, w.ending_balance, places=6)

    def test_defaults_cut_balance_and_interest_then_lag_loss(self):
        base = project_collateral(POOL, 0.0, 0.0, 0, psa=100)
        rows = project_collateral(POOL, 0.05, 0.4, 3, psa=100)
        self.assertLess(rows[0].interest, base[0].interest)
        self.assertGreater(rows[0].default, 0)
        self.assertEqual(sum(r.loss for r in rows[:3]), 0.0)       # nothing liquidates before the lag
        self.assertAlmostEqual(rows[3].loss, 0.4 * rows[0].default)
        self.assertAlmostEqual(rows[3].recovery, 0.6 * rows[0].default)
        self.assertAlmostEqual(sum(r.loss + r.recovery for r in rows),
                               sum(r.default for r in rows))


class TestWaterfall(unittest.TestCase):
    def test_zero_default_pays_in_full(self):
        r = run_waterfall(deal(), 0.0, 0.0, 0, psa=100)
        for c in r.classes:
            self.assertFalse(c.takes_writedown)
            self.assertAlmostEqual(sum(f.principal for f in c.cashflows), c.balance, places=4)

    def test_cash_conservation_every_period(self):
        for kw in ({}, {"principal": "sequential"}, {"trigger_cum_loss": 0.01}):
            r = run_waterfall(deal(**kw), 0.06, 0.6, 6, psa=100)
            for p, row in zip(r.periods, r.collateral):
                self.assertAlmostEqual(row.cash, p.paid_out, places=4)

    def test_losses_hit_juniors_first(self):
        r = run_waterfall(deal(), 0.10, 1.0, 6, psa=100)
        order = ["A", "M", "B"]
        self.assertTrue(r.by_name("A").takes_writedown)             # extreme case reaches A
        for p in r.periods:
            for i, n in enumerate(order):
                if p.writedown[n] > 1e-6:                           # all juniors already wiped out
                    for j in order[i + 1:]:
                        self.assertAlmostEqual(p.ending[j], 0.0, places=4)
        mid = run_waterfall(deal(), 0.035, 0.4, 6, psa=100)
        self.assertTrue(mid.by_name("B").takes_writedown)
        self.assertLess(mid.by_name("A").loss_pct, 0.01)

    def test_trigger_switches_to_sequential(self):
        d = deal(trigger_cum_loss=0.01)
        r = run_waterfall(d, 0.06, 0.6, 6, psa=100)
        trip = [p.month for p in r.periods if p.sequential]
        self.assertTrue(trip)
        first = trip[0]
        self.assertGreater(first, 1)
        before = r.periods[first - 2]
        self.assertGreater(before.principal_paid["M"], 0)           # pro rata: M paid with A
        for p in r.periods[first - 1:]:
            if p.ending["A"] > 1e-6 and sum(p.principal_paid.values()) > 0:
                self.assertAlmostEqual(p.principal_paid["B"], 0.0, places=6)
                self.assertAlmostEqual(p.principal_paid["M"], 0.0, places=6)
        self.assertFalse(any(p.sequential for p in run_waterfall(deal(), 0.06, 0.6, 6, psa=100).periods))

    def test_oc_captures_excess_spread(self):
        lo = run_waterfall(deal(oc_target=0.0), 0.04, 0.4, 6, psa=100)
        hi = run_waterfall(deal(oc_target=6e6), 0.04, 0.4, 6, psa=100)
        self.assertLessEqual(hi.by_name("B").writedown, lo.by_name("B").writedown)
        self.assertLess(hi.residual, lo.residual)


class TestBreakEven(unittest.TestCase):
    def test_breakeven_is_first_loss_and_ordered(self):
        d = deal()
        be = {n: breakeven_cdr(d, n, 0.4, 6, psa=100) for n in "ABM"}
        self.assertLess(be["B"] if "B" in be else 0, be["M"])
        self.assertLess(be["M"], be["A"])
        for n, c in be.items():
            self.assertEqual(class_losses(d, c - 1e-3, 0.4, 6, psa=100)[n] > 0.01, False)
            self.assertTrue(class_losses(d, c + 1e-3, 0.4, 6, psa=100)[n] > 0.01)

    def test_loss_monotone_in_cdr_and_severity(self):
        g = loss_grid(deal(), [i / 100 for i in range(0, 13)], [0.2, 0.4, 0.6, 0.8, 1.0], 6, psa=100)
        for rows in g.values():
            for col in range(5):
                v = [r[col] for r in rows]
                self.assertEqual(v, sorted(v))
            for r in rows:
                self.assertEqual(r, sorted(r))

    def test_no_loss_cases(self):
        self.assertIsNone(breakeven_cdr(deal(), "A", 0.0, 6, psa=100))


class TestSampleFile(unittest.TestCase):
    def test_sample_deal_loads(self):
        from mbs.waterfall import load_deal
        path = os.path.join(os.path.dirname(__file__), "..", "sample_deal.toml")
        try:
            d = load_deal(path)
        except ValueError:                                           # Python before 3.11
            self.skipTest("tomllib needs Python 3.11+")
        self.assertEqual([t.name for t in d.tranches], ["A", "M", "B"])


if __name__ == "__main__":
    unittest.main()
