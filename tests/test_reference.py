"""Checks against published conventions and independent hand calculations."""

import unittest

from mbs import (Pool, project_cashflows, psa_cpr, smm_from_cpr, cpr_from_smm,
                 price_from_yield, z_spread, wal, bey_price, bey_risk)


class TestPublishedValues(unittest.TestCase):
    # PSA standard: 0.2% CPR per month of age to 6% at month 30 (SIFMA Standard Formulas).
    def test_psa_ramp_endpoints(self):
        self.assertAlmostEqual(psa_cpr(1, 100), 0.002)
        self.assertAlmostEqual(psa_cpr(30, 100), 0.06)
        self.assertAlmostEqual(psa_cpr(31, 100), 0.06)

    def test_smm_reference_values(self):
        self.assertAlmostEqual(smm_from_cpr(0.06), 0.005143, places=6)   # 6% CPR
        self.assertAlmostEqual(smm_from_cpr(0.09), 0.007828, places=6)   # 150 PSA plateau

    def test_cpr_smm_round_trip(self):
        for cpr in (0.0, 0.02, 0.2, 0.9):
            self.assertAlmostEqual(cpr_from_smm(smm_from_cpr(cpr)), cpr)
        with self.assertRaises(ValueError):
            cpr_from_smm(1.0)

    def test_first_month_hand_calc(self):
        # $400M, 7.5% WAC, 360 months, 100 PSA, worked independently of the model code.
        r = 0.075 / 12
        pmt = 400e6 * r / (1 - (1 + r) ** -360)
        sched = pmt - 400e6 * r
        smm = 1 - (1 - 0.002) ** (1 / 12)
        cf = project_cashflows(Pool(400e6, 0.075, 0.075, 360), psa=100)[0]
        self.assertAlmostEqual(cf.interest, 2_500_000.0, places=2)
        self.assertAlmostEqual(cf.scheduled_principal, sched, places=4)
        self.assertAlmostEqual(cf.prepayment, smm * (400e6 - sched), places=4)
        self.assertAlmostEqual(sched, 296_858, delta=1)   # hand-rounded sanity value


class TestSifmaWorkedExamples(unittest.TestCase):
    """Numbers printed in the SIFMA (Bond Market Association, 02/01/99) Uniform Practices
    Standard Formulas chapter SF, sections B.1, B.2 and G.1."""

    def test_b1_first_month_cash_flow_components(self):
        # 9.0% net / 9.5% gross, 360 months, 150 PSA (month-1 CPR 0.3%), per $1.00 par.
        cf = project_cashflows(Pool(1.0, 0.095, 0.09, 360), psa=150)[0]
        gross_interest = 0.095 / 12
        servicing = 0.005 / 12
        self.assertAlmostEqual(cf.scheduled_principal, 0.00049188, places=8)
        self.assertAlmostEqual(cf.prepayment, 0.00025022, places=8)
        self.assertAlmostEqual(gross_interest, 0.00791667, places=8)
        self.assertAlmostEqual(servicing, 0.00041667, places=8)
        self.assertAlmostEqual(cf.principal, 0.00074210, places=8)
        self.assertAlmostEqual(cf.interest, 0.00750000, places=8)
        self.assertAlmostEqual(cf.total, 0.00824210, places=8)

    def test_b2_prepayment_speed_example(self):
        # Pool factor 0.85150625, 344 months left, age 15 -> month 17 of the PSA ramp.
        f1, f2 = 0.85150625, 0.84732282
        cf = project_cashflows(Pool(f1, 0.095, 0.09, 344, age=16), psa=150)[0]
        self.assertAlmostEqual(cf.cpr, 0.051, places=10)
        self.assertAlmostEqual(cf.smm, 0.00435270, places=7)
        self.assertAlmostEqual(cf.scheduled_principal, 0.00047916, places=8)
        self.assertAlmostEqual(cf.prepayment, 0.00370427, places=8)
        # published pool factor after the month (F2), balance after month N
        self.assertAlmostEqual(cf.ending_balance, f2, places=8)

    def test_g1_cash_flows_and_average_life_150psa(self):
        cfs = project_cashflows(Pool(1.0, 0.095, 0.09, 360), psa=150)
        self.assertEqual(len(cfs), 360)
        for i, want in ((0, 0.8242), (1, 0.8491), (2, 0.8738), (359, 0.0562)):
            self.assertAlmostEqual(100 * cfs[i].total, want, places=4)
        # 14-day Ginnie Mae delay: published Average Life 9.77844 years
        self.assertAlmostEqual(wal(cfs, delay_days=14), 9.77844, places=5)

    def test_g1_price_yield_duration_convexity(self):
        cfs = project_cashflows(Pool(1.0, 0.095, 0.09, 360), psa=150)
        y = 0.0910675   # published bond-equivalent yield at par
        self.assertAlmostEqual(bey_price(cfs, y, 1.0, delay_days=14), 100.0, places=4)
        mac, mod, conv = bey_risk(cfs, y, 1.0, delay_days=14)
        self.assertAlmostEqual(mac, 5.73147, places=5)
        self.assertAlmostEqual(mod, 5.48186, places=5)
        self.assertAlmostEqual(conv, 54.4326, places=4)

    def test_g1_mortgage_yield_from_bey(self):
        # Mortgage (monthly) yield 8.93863% = 1200*((1+Y/200)^(1/6)-1)
        self.assertAlmostEqual(1200 * ((1 + 0.0910675 / 2) ** (1 / 6) - 1), 8.93863, places=4)


class TestEdgeCases(unittest.TestCase):
    def test_zero_coupon_pool_amortizes_straight_line(self):
        cfs = project_cashflows(Pool(1200.0, 0.0, 0.0, 12), cpr=0.0)
        self.assertTrue(all(abs(c.scheduled_principal - 100.0) < 1e-9 for c in cfs))

    def test_one_month_pool(self):
        cfs = project_cashflows(Pool(1000.0, 0.06, 0.05, 1), psa=100)
        self.assertEqual(len(cfs), 1)
        self.assertAlmostEqual(cfs[0].ending_balance, 0.0)

    def test_negative_coupons_rejected(self):
        with self.assertRaises(ValueError):
            Pool(1000.0, 0.06, -0.01, 12)
        with self.assertRaises(ValueError):
            Pool(1000.0, -0.01, -0.02, 12)


class TestOriginalTerm(unittest.TestCase):
    def test_default_and_valid(self):
        self.assertEqual(Pool(100.0, 0.06, 0.05, 300, age=60).original_term, 360)
        Pool(100.0, 0.06, 0.05, 180, age=0, original_term=180)

    def test_age_plus_wam_over_term_rejected(self):
        with self.assertRaises(ValueError):
            Pool(100.0, 0.06, 0.05, 300, age=61)
        with self.assertRaises(ValueError):
            Pool(100.0, 0.06, 0.05, 180, age=1, original_term=180)
        with self.assertRaises(ValueError):
            Pool(100.0, 0.06, 0.05, 100, original_term=0)


class TestZSpread(unittest.TestCase):
    def setUp(self):
        self.pool = Pool(1e6, 0.06, 0.055, 120)
        self.cfs = project_cashflows(self.pool, psa=150)
        self.curve = lambda m: 0.03 + 0.0001 * m   # upward-sloping spot curve

    def test_price_at_solved_spread_round_trips(self):
        z = z_spread(self.cfs, 99.0, 1e6, self.curve)
        px = 100 * sum(c.total * (1 + (self.curve(c.month) + z) / 12) ** -c.month
                       for c in self.cfs) / 1e6
        self.assertAlmostEqual(px, 99.0, places=6)

    def test_flat_curve_matches_yield(self):
        y = 0.05
        price = price_from_yield(self.cfs, y, 1e6)
        self.assertAlmostEqual(z_spread(self.cfs, price, 1e6, lambda m: 0.04), 0.01, places=8)

    def test_cheaper_price_means_wider_spread(self):
        self.assertGreater(z_spread(self.cfs, 97, 1e6, self.curve),
                           z_spread(self.cfs, 101, 1e6, self.curve))


if __name__ == "__main__":
    unittest.main()
