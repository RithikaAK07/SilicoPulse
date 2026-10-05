"""Small, dependency-light statistics helpers used by the evidence layer.

Everything here is deterministic and computed from counts, so any number shown in the UI or
quoted by the Copilot can be traced back to (k failures, n runs) of a concrete subgroup.
"""
from __future__ import annotations

import math

import numpy as np


def _norm_sf(z: float) -> float:
    """Survival function of the standard normal (one-sided upper tail)."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def two_sided_p(z: float) -> float:
    return float(min(1.0, 2 * _norm_sf(abs(z))))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (robust for small n / extreme p)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """Pooled two-proportion z-test: group 1 vs group 2. Returns (z, two-sided p)."""
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(max(pooled * (1 - pooled) * (1 / n1 + 1 / n2), 1e-12))
    z = (p1 - p2) / se
    return z, two_sided_p(z)


def binom_z(k: int, n: int, expected_p: float) -> tuple[float, float]:
    """z-score of observing k/n against an expected rate (normal approximation)."""
    if n <= 0:
        return 0.0, 1.0
    se = math.sqrt(max(expected_p * (1 - expected_p) / n, 1e-12))
    z = (k / n - expected_p) / se
    return z, two_sided_p(z)


def odds_ratio(k1: int, n1: int, k2: int, n2: int) -> float:
    """Odds ratio of failure, group 1 vs group 2, with Haldane-Anscombe 0.5 correction."""
    a, b = k1 + 0.5, (n1 - k1) + 0.5
    c, d = k2 + 0.5, (n2 - k2) + 0.5
    return (a / b) / (c / d)


def chi2_independence(table: np.ndarray) -> tuple[float, float, float]:
    """Chi-square test of independence for an r x 2 (value x pass/fail) table.
    Returns (chi2, p_value, cramers_v)."""
    from scipy.stats import chi2_contingency

    table = table[table.sum(axis=1) > 0]
    if table.shape[0] < 2 or (table.sum(axis=0) == 0).any():
        return 0.0, 1.0, 0.0
    chi2, p, _, _ = chi2_contingency(table, correction=False)
    n = table.sum()
    v = math.sqrt(chi2 / (n * (min(table.shape) - 1))) if n else 0.0
    return float(chi2), float(p), float(v)


def cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or len(b) < 2:
        return 0.0
    sa, sb = a.var(ddof=1), b.var(ddof=1)
    pooled = math.sqrt(((len(a) - 1) * sa + (len(b) - 1) * sb) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else 0.0


def evidence_strength(p: float, n: int, min_n: int, tests: int = 1, alpha: float = 0.01) -> str:
    """Qualitative evidence label. Bonferroni-adjusts p for the number of hypotheses tested."""
    if n < min_n:
        return "insufficient"
    p_adj = min(1.0, p * max(1, tests))
    if p_adj < alpha / 10:
        return "strong"
    if p_adj < alpha:
        return "moderate"
    if p_adj < 0.05:
        return "weak"
    return "not significant"


def severity(fail_rate: float, lift: float, strength: str) -> str:
    if strength in ("insufficient", "not significant"):
        return "low"
    if fail_rate >= 0.6 and lift >= 2.0:
        return "critical"
    if lift >= 2.0 or fail_rate >= 0.5:
        return "high"
    if lift >= 1.4:
        return "medium"
    return "low"
