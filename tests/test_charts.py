import importlib.util
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

from mbs import charts


class TestSvgCharts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = charts.render_all(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_chart_written_in_light_and_dark(self):
        names = {os.path.basename(p) for p in self.paths}
        expected = {f"{name}{suffix}.svg" for name, _ in charts.CHARTS for suffix in ("", "-dark")}
        self.assertEqual(names, expected)

    def test_every_svg_is_valid_xml(self):
        for path in self.paths:
            root = ET.parse(path).getroot()
            self.assertTrue(root.tag.endswith("}svg"), path)

    def test_dark_variant_has_no_white_background(self):
        with open(os.path.join(self.tmp.name, "wal_duration_vs_rate-dark.svg"), encoding="utf-8") as f:
            self.assertNotIn('fill="#ffffff"', f.read())
        with open(os.path.join(self.tmp.name, "wal_duration_vs_rate.svg"), encoding="utf-8") as f:
            self.assertIn('fill="#ffffff"', f.read())

    def test_banner_carries_the_tagline(self):
        with open(os.path.join(self.tmp.name, "banner.svg"), encoding="utf-8") as f:
            self.assertIn("Agency MBS Cash Flows, Prepayment and Negative Convexity", f.read())

    def test_sample_output_panel_holds_real_cli_text(self):
        text = charts.cli_output()
        self.assertIn("Effective convexity -282.1", text)
        with open(os.path.join(self.tmp.name, "sample-output.svg"), encoding="utf-8") as f:
            self.assertIn("Effective convexity -282.1", f.read())


class TestChartData(unittest.TestCase):
    def test_effective_convexity_negative_and_static_positive(self):
        _, eff, stat = charts.convexity_data()
        self.assertTrue(all(e < 0 for e in eff))
        self.assertTrue(all(s > 0 for s in stat))

    def test_price_is_par_when_yield_equals_coupon(self):
        grid = charts.price_grid()
        row = charts.PRICE_YIELDS.index(6.0)
        for value in grid[row]:
            self.assertAlmostEqual(value, 100.0, places=2)

    def test_price_falls_as_yield_rises(self):
        grid = charts.price_grid()
        for col in range(len(charts.PRICE_PSAS)):
            column = [row[col] for row in grid]
            self.assertTrue(all(a > b for a, b in zip(column, column[1:])))

    def test_components_sum_to_balance(self):
        _, interest, sched, prepay = charts.components_data(100)
        self.assertAlmostEqual(sum(sched) + sum(prepay), charts.POOL.balance / 1e6, places=3)


@unittest.skipUnless(importlib.util.find_spec("matplotlib"),
                     "matplotlib not installed (optional extra)")
class TestMatplotlibPlots(unittest.TestCase):
    def test_pngs_written(self):
        import plots_mpl
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(plots_mpl.main(d), 0)
            for name in plots_mpl.FILES:
                with open(os.path.join(d, name), "rb") as f:
                    self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n", name)


if __name__ == "__main__":
    unittest.main()
