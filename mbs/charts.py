"""Hand-written SVG charts for the README (no plotting library).

Every chart is written twice: a light file (docs/<name>.svg) and a dark file
(docs/<name>-dark.svg) for GitHub's dark theme. The sample-output panel renders
the real text printed by cli.py. Regenerate everything with:

    python3 -m mbs.charts

Data helpers at the top are shared with plots_mpl.py so both renderers plot the
same numbers.
"""

import contextlib
import io
import math
import os
import sys
from xml.sax.saxutils import escape

from .cashflows import Pool, project_cashflows
from .analytics import wal, price_from_yield
from .ratelinked import effective_risk, project_rate_linked

W, H = 760, 440
L, R, T, B = 80, 30, 55, 65           # plot margins
FONT = 'font-family="Helvetica, Arial, sans-serif"'
MONO = 'font-family="Menlo, Consolas, Monaco, monospace"'

POOL = Pool(100_000_000, 0.065, 0.06, 360, 0)   # $100mm, 6.5% WAC, 6.0% coupon, 360 months
YIELD = 0.055                                    # discount yield used for the charts

PROFILE_PSAS = (50, 100, 300, 500)
PRICE_YIELDS = (4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5)   # percent
PRICE_PSAS = (50, 100, 150, 200, 300, 500)
CONVEXITY_RATES = tuple(round(5.4 + 0.5 * i, 2) for i in range(7))   # percent, 5.40 to 8.40

THEMES = {
    "light": {
        "navy": "#1f3a5f", "teal": "#14b8a6", "light_teal": "#5eead4", "slate": "#94a3b8",
        "grid": "#cbd5e1", "text": "#334155", "bg": "#ffffff",
        "heat_lo": "#ccfbf1", "heat_hi": "#1f3a5f",
    },
    "dark": {
        "navy": "#60a5fa", "teal": "#2dd4bf", "light_teal": "#5eead4", "slate": "#94a3b8",
        "grid": "#475569", "text": "#e2e8f0", "bg": None,
        "heat_lo": "#1e293b", "heat_hi": "#60a5fa",
    },
}


# ---------------------------------------------------------------- data

def wal_duration_data(yld=YIELD):
    """Mortgage rates (%), WAL (years) and effective duration at each rate."""
    rates = [4.0 + 0.25 * i for i in range(19)]
    wals = [wal(project_rate_linked(POOL, m / 100)) for m in rates]
    durs = [effective_risk(POOL, m / 100, yld).effective_duration for m in rates]
    return rates, wals, durs


def convexity_data(yld=YIELD):
    """Mortgage rates (%), effective convexity and static convexity at each rate."""
    eff, stat = [], []
    for m in CONVEXITY_RATES:
        er = effective_risk(POOL, m / 100, yld)
        eff.append(er.effective_convexity)
        stat.append(er.static_convexity)
    return list(CONVEXITY_RATES), eff, stat


def profile_data():
    """{PSA speed: monthly total cash flow to investor in $ millions}."""
    return {psa: [c.total / 1e6 for c in project_cashflows(POOL, psa=psa)]
            for psa in PROFILE_PSAS}


def components_data(psa=100):
    """Annual interest, scheduled principal and prepayments ($ millions) at one PSA speed."""
    cfs = project_cashflows(POOL, psa=psa)
    years = (cfs[-1].month - 1) // 12 + 1
    interest, sched, prepay = [0.0] * years, [0.0] * years, [0.0] * years
    for c in cfs:
        i = (c.month - 1) // 12
        interest[i] += c.interest / 1e6
        sched[i] += c.scheduled_principal / 1e6
        prepay[i] += c.prepayment / 1e6
    return list(range(1, years + 1)), interest, sched, prepay


def price_grid():
    """Price per 100 for each discount yield (rows) and PSA speed (columns)."""
    curves = {psa: project_cashflows(POOL, psa=psa) for psa in PRICE_PSAS}
    return [[price_from_yield(curves[psa], y / 100, POOL.balance) for psa in PRICE_PSAS]
            for y in PRICE_YIELDS]


def cli_output():
    """The exact text printed by `python3 cli.py --market-rate 7.40 --yield 5.5`."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["--market-rate", "7.40", "--yield", "5.5"])
    return buf.getvalue()


# ---------------------------------------------------------------- helpers

def _ticks(lo, hi, n=5):
    """Evenly spaced 'nice' tick values (1, 2, 2.5, 5 x power of ten) covering lo..hi."""
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    t, out = math.ceil(lo / step - 1e-9) * step, []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


def _svg_open(th, title):
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
             f'role="img" aria-label="{escape(title, {chr(34): "&quot;"})}">']
    if th["bg"]:
        parts.append(f'<rect width="{W}" height="{H}" fill="{th["bg"]}"/>')
    parts.append(f'<text x="{W / 2}" y="28" text-anchor="middle" font-size="16" font-weight="bold" '
                 f'fill="{th["text"]}" {FONT}>{escape(title)}</text>')
    return parts


def _rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _mix(c1, c2, t):
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


class Chart:
    """Cartesian plot with optional negative y range and category-style x ticks."""

    def __init__(self, th, title, xlabel, ylabel, xlim, ylim, yfmt="{:g}",
                 xticks=None, xlabels=None, xfmt="{:g}"):
        self.th = th
        self.xmin, self.xmax = xlim
        self.ymin, self.ymax = ylim
        self.p = _svg_open(th, title)
        for v in _ticks(self.ymin, self.ymax):
            y = self.y(v)
            self.p.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" stroke="{th["grid"]}"/>')
            self.text(L - 8, y + 4, yfmt.format(v), size=11, anchor="end")
        xs = xticks if xticks is not None else _ticks(self.xmin, self.xmax)
        labels = xlabels if xlabels is not None else [xfmt.format(v) for v in xs]
        for v, lab in zip(xs, labels):
            x = self.x(v)
            self.p.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{H - B}" y2="{H - B + 5}" stroke="{th["text"]}"/>')
            self.text(x, H - B + 19, lab, size=11)
        if self.ymin < 0 < self.ymax:
            y0 = self.y(0)
            self.p.append(f'<line x1="{L}" x2="{W - R}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="{th["text"]}" stroke-width="1"/>')
        self.p.append(f'<line x1="{L}" x2="{L}" y1="{T}" y2="{H - B}" stroke="{th["text"]}"/>')
        self.p.append(f'<line x1="{L}" x2="{W - R}" y1="{H - B}" y2="{H - B}" stroke="{th["text"]}"/>')
        self.text((L + W - R) / 2, H - 18, xlabel, size=12)
        self.p.append(f'<text transform="translate(18 {(T + H - B) / 2}) rotate(-90)" text-anchor="middle" '
                      f'font-size="12" fill="{th["text"]}" {FONT}>{escape(ylabel)}</text>')

    def x(self, v):
        return L + (v - self.xmin) / (self.xmax - self.xmin) * (W - L - R)

    def y(self, v):
        return H - B - (v - self.ymin) / (self.ymax - self.ymin) * (H - T - B)

    def text(self, x, y, s, size=12, anchor="middle"):
        self.p.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" '
                      f'fill="{self.th["text"]}" {FONT}>{escape(str(s))}</text>')

    def line(self, xs, ys, color, width=2.2):
        pts = " ".join(f"{self.x(a):.1f},{self.y(b):.1f}" for a, b in zip(xs, ys))
        self.p.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                      f'stroke-width="{width}" stroke-linejoin="round"/>')

    def bar(self, center, halfwidth, v_lo, v_hi, color):
        x0, x1 = self.x(center - halfwidth), self.x(center + halfwidth)
        ya, yb = self.y(v_lo), self.y(v_hi)
        self.p.append(f'<rect x="{min(x0, x1):.1f}" y="{min(ya, yb):.1f}" width="{abs(x1 - x0):.1f}" '
                      f'height="{abs(yb - ya):.1f}" fill="{color}"/>')

    def legend(self, items, x, y=T + 8):
        for i, (label, color) in enumerate(items):
            yy = y + i * 18
            self.p.append(f'<rect x="{x}" y="{yy}" width="14" height="4" fill="{color}" transform="translate(0 4)"/>')
            self.text(x + 20, yy + 11, label, size=12, anchor="start")

    def svg(self):
        return "\n".join(self.p + ["</svg>"]) + "\n"


# ---------------------------------------------------------------- charts

def banner_svg(th):
    w, h = 1200, 240
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
             f'role="img" aria-label="MBS Cash Flow Model Banner">']
    if th["bg"]:
        parts.append(f'<rect width="{w}" height="{h}" fill="{th["bg"]}"/>')
    parts.append(f'<rect x="90" y="70" width="72" height="5" fill="{th["teal"]}"/>')
    parts.append(f'<text x="90" y="150" font-size="60" font-weight="bold" fill="{th["navy"]}" {FONT}>'
                 f'MBS Cash Flow Model</text>')
    parts.append(f'<text x="90" y="192" font-size="24" fill="{th["text"]}" {FONT}>'
                 f'Agency MBS Cash Flows, Prepayment and Negative Convexity</text>')
    parts.append(f'<line x1="0" x2="{w}" y1="222" y2="222" stroke="{th["teal"]}" stroke-width="1.5"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def convexity_svg(th):
    rates, eff, stat = convexity_data()
    n = len(rates)
    ch = Chart(th, "Convexity by Mortgage Rate: Effective vs Static", "Current Mortgage Rate (%)",
               "Convexity", (0, n), (min(eff) * 1.15, max(stat) * 1.45), yfmt="{:.0f}",
               xticks=[i + 0.5 for i in range(n)], xlabels=[f"{r:.2f}" for r in rates])
    hw = 0.19
    for i, (e, s) in enumerate(zip(eff, stat)):
        xc = i + 0.5
        ch.bar(xc - hw, hw, 0, e, th["navy"])
        ch.bar(xc + hw, hw, 0, s, th["teal"])
        ch.text(ch.x(xc - hw), ch.y(e) + (14 if e < 0 else -5), f"{e:.0f}", size=10)
        ch.text(ch.x(xc + hw), ch.y(s) + (14 if s < 0 else -5), f"{s:.0f}", size=10)
    ch.legend([("Effective (Prepayments Re-Projected)", th["navy"]),
               ("Static (Prepayments Held Fixed)", th["teal"])], x=L + 14)
    return ch.svg()


def rate_svg(th):
    rates, wals, durs = wal_duration_data()
    ch = Chart(th, "WAL and Effective Duration vs Mortgage Rate", "Current Mortgage Rate (%)",
               "Years", (4.0, 8.5), (0, max(wals) * 1.12), xticks=[4, 5, 6, 7, 8])
    ch.line(rates, wals, th["navy"])
    ch.line(rates, durs, th["teal"])
    ch.legend([("WAL", th["navy"]), ("Effective Duration", th["teal"])], x=L + 14)
    return ch.svg()


def profile_svg(th):
    series = profile_data()
    colors = {50: th["slate"], 100: th["navy"], 300: th["teal"], 500: th["light_teal"]}
    ymax = max(max(v) for v in series.values()) * 1.1
    ch = Chart(th, "Monthly Cash Flow to Investor by PSA Speed", "Month",
               "Cash Flow ($ millions)", (0, 360), (0, ymax), yfmt="{:.1f}",
               xticks=[0, 60, 120, 180, 240, 300, 360])
    for psa, ys in series.items():
        ch.line(list(range(1, len(ys) + 1)), ys, colors[psa])
    ch.legend([(f"{psa} PSA", colors[psa]) for psa in series], x=W - R - 130)
    return ch.svg()


def components_svg(th):
    years, interest, sched, prepay = components_data(100)
    n = len(years)
    totals = [a + b + c for a, b, c in zip(interest, sched, prepay)]
    tick_years = [y for y in (5, 10, 15, 20, 25, 30) if y <= n]
    ch = Chart(th, "Annual Cash Flow Split at 100 PSA: Interest, Principal, Prepayments",
               "Year", "$ millions per year", (0, n), (0, max(totals) * 1.1), yfmt="{:.0f}",
               xticks=[y - 0.5 for y in tick_years], xlabels=[str(y) for y in tick_years])
    for i in range(n):
        xc, hw = i + 0.5, 0.36
        base = 0.0
        for amount, color in ((interest[i], th["navy"]), (sched[i], th["slate"]), (prepay[i], th["teal"])):
            ch.bar(xc, hw, base, base + amount, color)
            base += amount
    ch.legend([("Interest", th["navy"]), ("Scheduled Principal", th["slate"]),
               ("Prepayments", th["teal"])], x=W - R - 170)
    return ch.svg()


def heatmap_svg(th):
    grid = price_grid()
    flat = [v for row in grid for v in row]
    vmin, vmax = min(flat), max(flat)
    rows, cols = len(PRICE_YIELDS), len(PRICE_PSAS)
    x0, x1 = L + 10, W - R - 70
    y0, y1 = T, H - B
    cw, rh = (x1 - x0) / cols, (y1 - y0) / rows
    parts = _svg_open(th, "Price per 100 by Discount Yield and PSA Speed")
    parts.append(f'<defs><linearGradient id="heatbar" x1="0" y1="0" x2="0" y2="1">'
                 f'<stop offset="0" stop-color="{th["heat_hi"]}"/>'
                 f'<stop offset="1" stop-color="{th["heat_lo"]}"/></linearGradient></defs>')
    for r in range(rows):
        yi = rows - 1 - r                        # highest discount yield at the top
        y_top = y0 + r * rh
        parts.append(f'<text x="{L - 8}" y="{y_top + rh / 2 + 4:.1f}" text-anchor="end" font-size="11" '
                     f'fill="{th["text"]}" {FONT}>{PRICE_YIELDS[yi]:.1f}%</text>')
        for c in range(cols):
            v = grid[yi][c]
            fill = _mix(th["heat_lo"], th["heat_hi"], (v - vmin) / (vmax - vmin))
            lum = sum(w * ch for w, ch in zip((0.299, 0.587, 0.114), _rgb(fill)))
            ink = "#ffffff" if lum < 150 else "#0f172a"
            x = x0 + c * cw
            parts.append(f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{cw:.1f}" height="{rh:.1f}" '
                         f'fill="{fill}" stroke="{th["bg"] or "#0f172a"}" stroke-width="1"/>')
            parts.append(f'<text x="{x + cw / 2:.1f}" y="{y_top + rh / 2 + 4:.1f}" text-anchor="middle" '
                         f'font-size="12" fill="{ink}" {FONT}>{v:.2f}</text>')
    for c, psa in enumerate(PRICE_PSAS):
        parts.append(f'<text x="{x0 + (c + 0.5) * cw:.1f}" y="{H - B + 19}" text-anchor="middle" font-size="11" '
                     f'fill="{th["text"]}" {FONT}>{psa}</text>')
    parts.append(f'<text x="{(x0 + x1) / 2}" y="{H - 18}" text-anchor="middle" font-size="12" '
                 f'fill="{th["text"]}" {FONT}>PSA Speed</text>')
    parts.append(f'<text transform="translate(18 {(y0 + y1) / 2}) rotate(-90)" text-anchor="middle" '
                 f'font-size="12" fill="{th["text"]}" {FONT}>Discount Yield</text>')
    bx = W - R - 44
    parts.append(f'<rect x="{bx}" y="{y0}" width="14" height="{y1 - y0:.1f}" fill="url(#heatbar)"/>')
    parts.append(f'<text x="{bx + 20}" y="{y0 + 10}" font-size="11" fill="{th["text"]}" {FONT}>{vmax:.2f}</text>')
    parts.append(f'<text x="{bx + 20}" y="{y1}" font-size="11" fill="{th["text"]}" {FONT}>{vmin:.2f}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def sample_output_svg(th):
    lines = cli_output().rstrip("\n").split("\n")
    w = 1040
    h = 64 + 20 * len(lines) + 24
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
             f'role="img" aria-label="Sample Terminal Output of cli.py">']
    parts.append(f'<rect x="4" y="4" width="{w - 8}" height="{h - 8}" rx="12" fill="#0f172a" '
                 f'stroke="{th["grid"]}" stroke-width="1.5"/>')
    parts.append(f'<path d="M4 16 a12 12 0 0 1 12 -12 H{w - 16} a12 12 0 0 1 12 12 V48 H4 Z" fill="#1e293b"/>')
    for i, color in enumerate(("#f87171", "#fbbf24", "#4ade80")):
        parts.append(f'<circle cx="{28 + i * 20}" cy="28" r="6" fill="{color}"/>')
    parts.append('<line x1="4" x2="%d" y1="48" y2="48" stroke="#334155"/>' % (w - 4))
    for i, line in enumerate(lines):
        y = 74 + 20 * i
        parts.append(f'<text x="28" y="{y}" font-size="13" fill="#e2e8f0" xml:space="preserve" {MONO}>'
                     f'{escape(line)}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


CHARTS = (
    ("banner", banner_svg),
    ("convexity_bars", convexity_svg),
    ("wal_duration_vs_rate", rate_svg),
    ("cashflow_profile_by_psa", profile_svg),
    ("cashflow_components_100psa", components_svg),
    ("price_heatmap", heatmap_svg),
    ("sample-output", sample_output_svg),
)


def render_all(out="docs"):
    """Write every chart in light and dark. Returns the list of paths written."""
    os.makedirs(out, exist_ok=True)
    written = []
    for name, build in CHARTS:
        for variant, suffix in (("light", ""), ("dark", "-dark")):
            path = os.path.join(out, f"{name}{suffix}.svg")
            with open(path, "w", encoding="utf-8") as f:
                f.write(build(THEMES[variant]))
            written.append(path)
    return written


def main(out="docs"):
    for path in render_all(out):
        print(path)


if __name__ == "__main__":
    main()
