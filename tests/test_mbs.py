import unittest

from mbs import (Pool, project_cashflows, scheduled_payment, psa_cpr, smm_from_cpr,
                 price_from_yield, yield_from_price, wal, modified_duration,
                 static_duration_convexity)

POOL = Pool(100_000_000, 0.065, 0.06, 360, 0)


class TestPrepay(unittest.TestCase):
    def test_psa100_hits_6pct_at_month_30(self):
        self.assertAlmostEqual(psa_cpr(30, 100), 0.06)
        self.assertAlmostEqual(psa_cpr(15, 100), 0.03)
        self.assertAlmostEqual(psa_cpr(200, 100), 0.06)
        self.assertAlmostEqual(psa_cpr(30, 300), 0.18)

    def test_cashflow_cpr_at_month_30(self):
        cfs = project_cashflows(POOL, psa=100)
        self.assertAlmostEqual(cfs[29].cpr, 0.06)

    def test_smm(self):
        self.assertAlmostEqual((1 - smm_from_cpr(0.06)) ** 12, 0.94)


class TestCashflows(unittest.TestCase):
    def test_balance_amortizes_to_zero(self):
        for psa in (0, 100, 500):
            cfs = project_cashflows(POOL, psa=psa)
            self.assertAlmostEqual(cfs[-1].ending_balance, 0.0, places=4)
            self.assertAlmostEqual(sum(c.principal for c in cfs), POOL.balance, places=2)

    def test_zero_prepay_matches_mortgage_formula(self):
        pool = Pool(250_000, 0.06, 0.06, 360, 0)
        cfs = project_cashflows(pool, cpr=0.0)
        pmt = 250_000 * 0.005 / (1 - 1.005 ** -360)
        self.assertAlmostEqual(pmt, scheduled_payment(250_000, 0.06, 360))
        for c in cfs:
            self.assertAlmostEqual(c.total, pmt, places=6)
            self.assertEqual(c.prepayment, 0.0)
        self.assertAlmostEqual(cfs[0].interest, 1250.0)

    def test_net_coupon_below_wac_strips_interest(self):
        cfs = project_cashflows(POOL, psa=100)
        self.assertAlmostEqual(cfs[0].interest, POOL.balance * 0.06 / 12)

    def test_seasoned_pool(self):
        pool = Pool(50e6, 0.05, 0.045, 300, 60)
        cfs = project_cashflows(pool, psa=100)
        self.assertEqual(len(cfs), 300)
        self.assertAlmostEqual(cfs[0].cpr, 0.06)


class TestSurvivorship(unittest.TestCase):
    """With prepayments, scheduled principal must re-amortize the CURRENT balance over the
    REMAINING term. These tests fail if the payment is fixed at the original balance and term."""

    def test_scheduled_principal_reamortizes_current_balance(self):
        cfs = project_cashflows(POOL, psa=100)
        for c in cfs[:-1]:
            remaining = POOL.wam - c.month + 1
            expected = (scheduled_payment(c.beginning_balance, POOL.wac, remaining)
                        - c.beginning_balance * POOL.wac / 12)
            self.assertAlmostEqual(c.scheduled_principal, expected, places=6)

    def test_closed_form_balance_for_constant_cpr(self):
        # Balance factor = scheduled factor * (1 - SMM)^t, with the scheduled factor
        # f(t) = ((1+r)^n - (1+r)^t) / ((1+r)^n - 1)
        r, n = POOL.wac / 12, POOL.wam
        cpr = 0.12
        smm = smm_from_cpr(cpr)
        cfs = project_cashflows(POOL, cpr=cpr)
        for t in (1, 12, 60, 180, 359):
            f = ((1 + r) ** n - (1 + r) ** t) / ((1 + r) ** n - 1)
            expected = POOL.balance * f * (1 - smm) ** t
            self.assertAlmostEqual(cfs[t - 1].ending_balance, expected, delta=1e-6 * POOL.balance)

    def test_100_psa_wal_for_65_wac_30yr(self):
        # An independent value: re-amortizing the balance gives about 11.5 years at 100 PSA,
        # well above the ~7.8 years a fixed-payment shortcut would produce.
        self.assertAlmostEqual(wal(project_cashflows(POOL, psa=100)), 11.49, places=2)


class TestAnalytics(unittest.TestCase):
    def test_price_yield_round_trip(self):
        cfs = project_cashflows(POOL, psa=150)
        for y in (0.03, 0.06, 0.09):
            px = price_from_yield(cfs, y, POOL.balance)
            self.assertAlmostEqual(yield_from_price(cfs, px, POOL.balance), y, places=8)

    def test_par_when_yield_equals_coupon(self):
        cfs = project_cashflows(POOL, psa=100)
        self.assertAlmostEqual(price_from_yield(cfs, 0.06, POOL.balance), 100.0, places=6)

    def test_higher_psa_shorter_wal(self):
        wals = [wal(project_cashflows(POOL, psa=s)) for s in (50, 100, 200, 400, 600)]
        self.assertEqual(wals, sorted(wals, reverse=True))

    def test_duration_close_and_positive(self):
        cfs = project_cashflows(POOL, psa=100)
        eff, cvx = static_duration_convexity(cfs, 0.06, POOL.balance, 10)
        self.assertAlmostEqual(eff, modified_duration(cfs, 0.06), places=2)
        self.assertGreater(cvx, 0)


if __name__ == "__main__":
    unittest.main()
