"""Input validation, error messages, and the CLI failure path."""

import os
import subprocess
import sys
import unittest

from mbs import Pool, cpr_vector, effective_risk, mey_to_bey, project_cashflows, psa_cpr

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = Pool(1e6, 0.065, 0.06, 360)


class TestPoolValidation(unittest.TestCase):
    def test_nan_fields_rejected(self):
        for kw in ({"balance": float("nan")}, {"wac": float("nan")},
                   {"net_coupon": float("nan")}, {"balance": float("inf")}):
            args = {"balance": 1e6, "wac": 0.065, "net_coupon": 0.06, "wam": 360, **kw}
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                Pool(**args)

    def test_non_integer_term_rejected(self):
        with self.assertRaises(ValueError):
            Pool(1e6, 0.065, 0.06, 360.5)
        with self.assertRaises(ValueError):
            Pool(1e6, 0.065, 0.06, 360, age=12.0)


class TestCprVector(unittest.TestCase):
    def test_psa_1700_reports_speed_too_high(self):
        with self.assertRaisesRegex(ValueError, "PSA speed too high"):
            project_cashflows(POOL, psa=1700)

    def test_exactly_one_of_psa_or_cpr(self):
        with self.assertRaises(ValueError):
            cpr_vector(3, psa=100, cpr=0.1)
        with self.assertRaises(ValueError):
            cpr_vector(3)

    def test_start_age_offset(self):
        self.assertEqual(cpr_vector(3, start_age=10, psa=100),
                         [psa_cpr(11, 100), psa_cpr(12, 100), psa_cpr(13, 100)])

    def test_zero_cpr_means_no_prepayment(self):
        self.assertEqual(cpr_vector(3, cpr=0.0), [0.0, 0.0, 0.0])
        cfs = project_cashflows(POOL, cpr=0.0)
        self.assertTrue(all(c.prepayment == 0.0 for c in cfs))

    def test_cpr_of_one_rejected(self):
        with self.assertRaises(ValueError):
            cpr_vector(3, cpr=1.0)


class TestBurnoutAndShock(unittest.TestCase):
    def test_negative_burnout_rejected(self):
        with self.assertRaises(ValueError):
            project_cashflows(POOL, psa=100, burnout=-1)

    def test_zero_shock_rejected(self):
        with self.assertRaises(ValueError):
            effective_risk(POOL, 0.074, 0.055, shock_bp=0)


class TestMeyToBey(unittest.TestCase):
    def test_monthly_to_semiannual(self):
        # Hand value: 2 * ((1 + 0.055/12)^6 - 1) = 5.5634%
        self.assertAlmostEqual(mey_to_bey(0.055), 0.055634, places=5)


class TestCliFailures(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(REPO, "cli.py"), *args],
                              capture_output=True, text=True, cwd=REPO, timeout=120, check=False)

    def assertCleanFailure(self, *args):
        result = self.run_cli(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_net_coupon_above_wac(self):
        self.assertCleanFailure("--wac", "5", "--coupon", "6")

    def test_cpr_of_100(self):
        self.assertCleanFailure("--cpr", "100")

    def test_psa_too_high(self):
        self.assertCleanFailure("--psa", "1700")

    def test_zero_shock(self):
        self.assertCleanFailure("--shock", "0")


if __name__ == "__main__":
    unittest.main()
