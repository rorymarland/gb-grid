"""API view: /api/sandbox, the starting figures for the "what if" sandbox.

The sandbox itself runs in the browser. The server supplies what the browser
can't easily work out: an estimate of the grid's inertia from its current
mix (fitted to NESO's published inertia where there's enough overlap), and
the response NESO held at the chosen moment.
"""

import time

from .frequency import response_at

# Synchronous generation: spinning machines locked to the grid's frequency
SYNC_LH = 'COALESCE(gas, 0) + COALESCE(coal, 0) + COALESCE(nuclear, 0) + COALESCE(biomass, 0) + COALESCE(hydro, 0) + COALESCE(storage, 0)'
# Without a fit: GVA·s per GW of synchronous output, plus a floor for demand-side
# motors, synchronous compensators and smaller plant
DEFAULT_FIT = (6.5, 30.0)


def fit_line(points):
    """Least squares y = a x + b, with R squared. points: [(x, y)]."""
    n = len(points)
    if n < 50:
        return None
    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0] * p[0] for p in points)
    sxy = sum(p[0] * p[1] for p in points)
    den = n * sxx - sx * sx
    if den <= 0:
        return None
    a = (n * sxy - sx * sy) / den
    b = (sy - a * sx) / n
    mean = sy / n
    total = sum((p[1] - mean) ** 2 for p in points) or 1.0
    resid = sum((p[1] - (a * p[0] + b)) ** 2 for p in points)
    return a, b, 1 - resid / total


class SandboxViews:
    """Mixed into Grid."""

    def inertia_fit(self):
        version = (self.store.scalar("SELECT MAX(t) FROM hh WHERE k = 'inertia'"), self.store.scalar('SELECT MAX(t) FROM lh'))
        return self.cached(('inertia-fit',), version, self._build_inertia_fit)

    def _build_inertia_fit(self):
        rows = self.store.query("SELECT lh.t, (%s) / 1000.0, hh.v FROM lh JOIN hh ON hh.k = 'inertia' AND hh.t = lh.t" % SYNC_LH)
        points = [(r[1], r[2]) for r in rows if r[1] is not None and r[2] is not None and r[2] > 0]
        fit = fit_line(points)
        # Only trust a fit that is physically sensible
        if fit and 2.0 <= fit[0] <= 15.0 and -20 <= fit[1] <= 120 and fit[2] >= 0.3:
            return {'a': round(fit[0], 2), 'b': round(fit[1], 1), 'r2': round(fit[2], 2), 'points': len(points), 'fitted': True}
        return {'a': DEFAULT_FIT[0], 'b': DEFAULT_FIT[1], 'r2': None, 'points': len(points), 'fitted': False}

    def sandbox_payload(self, at=None):
        now = time.time()
        t = at if at and now - 8 * 86400 < at <= now + 60 else now
        held = response_at(self.store, t)
        if not held:   # fall back to the latest block we know of
            last = self.store.scalar('SELECT MAX(start) FROM resp WHERE start <= ?', (t,))
            held = response_at(self.store, last) if last else {}
        latest = self.store.query("SELECT t, v FROM hh WHERE k = 'inertia' ORDER BY t DESC LIMIT 1")
        return {
            't': int(t), 'held': held, 'fit': self.inertia_fit(),
            'published_inertia': {'t': latest[0][0], 'gvas': round(latest[0][1])} if latest else None,
            'generated': int(now),
        }
