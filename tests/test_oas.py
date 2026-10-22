import unittest

from mbs import Pool, project_rate_linked, price_from_yield, z_spread
from mbs.oas import (default_spot, simulate_paths, path_totals, oas_price, solve_oas,
                     oas_analysis)

PREMIUM = Pool(100_000_000, 0.065, 0.06, 360)
DISCOUNT = Pool(100_000_000, 0.045, 0.04, 360)
MKT = 0.074
N = 100


def px(pool):
    return price_from_yield(project_rate_linked(pool, MKT), 0.055, pool.balance)


class TestOAS(unittest.TestCase):
    def test_zero_vol_flat_prepay_equals_z_spread(self):
        flat = {"steepness": 0.0}               # CPR no longer depends on the rate
        cfs = project_rate_linked(PREMIUM, MKT, **flat)
        price = price_from_yield(cfs, 0.055, PREMIUM.balance)
        z = z_spread(cfs, price, PREMIUM.balance, default_spot)
        r = oas_analysis(PREMIUM, price, MKT, vol=0.0, n_paths=4, **flat)
        self.assertAlmostEqual(r.oas, z, places=8)
        self.assertAlmostEqual(r.option_cost, 0.0, places=8)

    def test_reprices_zero_coupon_to_curve(self):
        paths = simulate_paths(default_spot, 120, MKT, vol=0.015, n_paths=40, seed=3)
        totals = [[0.0] * 59 + [100.0]] * 40
        want = 100.0 * (1.0 + default_spot(60) / 12.0) ** -60
        self.assertAlmostEqual(oas_price(totals, paths, default_spot, 0.0, 100.0), want, places=9)
        self.assertAlmostEqual(solve_oas(totals, paths, default_spot, want, 100.0), 0.0, places=8)

    def test_option_cost_positive_and_rises_with_vol(self):
        costs = [oas_analysis(PREMIUM, px(PREMIUM), MKT, vol=v, n_paths=N).option_cost
                 for v in (0.005, 0.01, 0.015)]
        self.assertGreater(costs[0], 0.0)
        self.assertEqual(costs, sorted(costs))

    def test_seed_is_deterministic(self):
        a = oas_analysis(PREMIUM, px(PREMIUM), MKT, n_paths=N, seed=7)
        b = oas_analysis(PREMIUM, px(PREMIUM), MKT, n_paths=N, seed=7)
        self.assertEqual(a, b)
        self.assertNotEqual(a.oas, oas_analysis(PREMIUM, px(PREMIUM), MKT, n_paths=N, seed=8).oas)

    def test_antithetic_pairs_mirror_each_other(self):
        p = simulate_paths(default_spot, 24, MKT, vol=0.01, n_paths=4, seed=1)
        m = p.mortgage                          # long rate is linear in the shock, so mirrors average out
        for i in (0, 11, 23):
            self.assertAlmostEqual(m[0][i] + m[1][i], m[2][i] + m[3][i], places=12)
        self.assertNotEqual(m[0][23], m[1][23])

    def test_premium_duration_below_discount(self):
        prem = oas_analysis(PREMIUM, px(PREMIUM), MKT, n_paths=N)
        disc = oas_analysis(DISCOUNT, px(DISCOUNT), MKT, n_paths=N)
        self.assertLess(prem.oas_duration, disc.oas_duration)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            simulate_paths(default_spot, 12, MKT, n_paths=3)
        with self.assertRaises(ValueError):
            oas_analysis(PREMIUM, -5.0, MKT, n_paths=4)


if __name__ == "__main__":
    unittest.main()
