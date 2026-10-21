import io
import unittest
from contextlib import redirect_stdout

from mbs import (Pool, project_cashflows, project_rate_linked, refi_cpr, effective_risk,
                 wal, smm_from_cpr)
import cli
from mbs.ratelinked import FLOOR_CPR, CAP_CPR, MIDPOINT

POOL = Pool(100_000_000, 0.065, 0.06, 360, 0)
Y = 0.055


class TestRefiCurve(unittest.TestCase):
    def test_monotone_and_bounded(self):
        vals = [refi_cpr(i / 1000) for i in range(-40, 81)]
        self.assertEqual(vals, sorted(vals))
        self.assertTrue(all(FLOOR_CPR <= v <= CAP_CPR for v in vals))

    def test_midpoint_is_halfway(self):
        self.assertAlmostEqual(refi_cpr(MIDPOINT), (FLOOR_CPR + CAP_CPR) / 2)

    def test_limits(self):
        self.assertAlmostEqual(refi_cpr(-0.10), FLOOR_CPR, places=4)
        self.assertAlmostEqual(refi_cpr(0.15), CAP_CPR, places=4)

    def test_bad_parameters(self):
        with self.assertRaises(ValueError):
            refi_cpr(0.0, floor=0.5, cap=0.4)

    def test_rate_linked_matches_constant_cpr(self):
        a = project_rate_linked(POOL, 0.06)
        b = project_cashflows(POOL, cpr=refi_cpr(0.005))
        self.assertEqual([c.total for c in a], [c.total for c in b])


class TestEffectiveRisk(unittest.TestCase):
    def test_negative_effective_convexity_near_the_money(self):
        r = effective_risk(POOL, 0.065, Y)
        self.assertLess(r.effective_convexity, 0)
        self.assertGreater(r.static_convexity, 0)

    def test_effective_duration_shortens_as_rates_fall(self):
        durs = [effective_risk(POOL, m, Y).effective_duration for m in (0.075, 0.065, 0.055, 0.045)]
        self.assertEqual(durs, sorted(durs, reverse=True))

    def test_deep_in_the_money_convexity_less_negative(self):
        near = effective_risk(POOL, 0.065, Y).effective_convexity
        deep = effective_risk(POOL, 0.045, Y).effective_convexity
        self.assertLess(near, deep)

    def test_wal_shortens_as_rates_fall(self):
        wals = [wal(project_rate_linked(POOL, m)) for m in (0.075, 0.065, 0.055, 0.045)]
        self.assertEqual(wals, sorted(wals, reverse=True))

    def test_static_matches_cashflows_at_base_rate(self):
        r = effective_risk(POOL, 0.06, Y)
        from mbs import static_duration_convexity
        d, c = static_duration_convexity(project_rate_linked(POOL, 0.06), Y, POOL.balance)
        self.assertAlmostEqual(r.static_duration, d, places=12)
        self.assertAlmostEqual(r.static_convexity, c, places=9)


class TestBurnout(unittest.TestCase):
    def test_zero_burnout_is_identical(self):
        a = project_cashflows(POOL, cpr=0.2)
        b = project_cashflows(POOL, cpr=0.2, burnout=0.0)
        self.assertEqual([c.total for c in a], [c.total for c in b])

    def test_principal_conserved(self):
        cfs = project_cashflows(POOL, cpr=0.3, burnout=2.0)
        self.assertAlmostEqual(sum(c.principal for c in cfs), POOL.balance, places=2)
        self.assertAlmostEqual(cfs[-1].ending_balance, 0.0, places=4)

    def test_burnout_lengthens_wal(self):
        self.assertGreater(wal(project_cashflows(POOL, cpr=0.3, burnout=2.0)),
                           wal(project_cashflows(POOL, cpr=0.3)))

    def test_effective_cpr_declines_and_starts_at_base(self):
        cfs = project_cashflows(POOL, cpr=0.3, burnout=2.0)
        self.assertAlmostEqual(cfs[0].cpr, 0.3)
        self.assertLess(cfs[36].cpr, cfs[12].cpr)
        self.assertLess(cfs[12].cpr, cfs[0].cpr)

    def test_recorded_smm_matches_effective_cpr(self):
        for c in project_cashflows(POOL, cpr=0.3, burnout=1.5)[:24]:
            self.assertAlmostEqual(c.smm, smm_from_cpr(c.cpr), places=12)

    def test_negative_burnout_rejected(self):
        with self.assertRaises(ValueError):
            project_cashflows(POOL, cpr=0.2, burnout=-1.0)


class TestCli(unittest.TestCase):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main(list(args))
        return buf.getvalue()

    def test_market_rate_reports_effective_risk(self):
        out = self.run_cli("--market-rate", "7.4", "--show", "0")
        self.assertIn("Effective duration", out)
        self.assertIn("Rate sensitivity", out)
        self.assertIn("Static duration", out)

    def test_default_run_labels_static_measures(self):
        out = self.run_cli("--show", "0")
        self.assertIn("Static duration", out)
        self.assertNotIn("Effective duration", out)
        self.assertIn("WAL 11.49", out)

    def test_burnout_flag(self):
        out = self.run_cli("--cpr", "20", "--burnout", "2", "--show", "0")
        self.assertIn("burnout 2", out)


if __name__ == "__main__":
    unittest.main()
