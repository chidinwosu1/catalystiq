"""Cross-sectional ranking metrics (Model 4).

Precision@k, hit-rate among the top-k, NDCG@k, Spearman rank correlation,
average/median realized net return of the top-ranked candidates, turnover
between consecutive ranking dates, and sector concentration of the top-k.

A ranking is a list of items ordered best-first; each carries a realized
forward outcome (net return) and a binary "good" label used for precision/hit
metrics.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class RankedItem:
    symbol: str
    predicted_score: float
    realized_net_return: float
    is_good: bool
    sector: str | None = None


def _by_score(items: Sequence[RankedItem]) -> list[RankedItem]:
    return sorted(items, key=lambda it: it.predicted_score, reverse=True)


def precision_at_k(items: Sequence[RankedItem], k: int) -> float:
    top = _by_score(items)[:k]
    if not top:
        return float("nan")
    return sum(1 for it in top if it.is_good) / len(top)


def hit_rate_top_k(items: Sequence[RankedItem], k: int) -> float:
    """1 if any of the top-k is good, else 0 (per ranking date)."""
    top = _by_score(items)[:k]
    if not top:
        return float("nan")
    return 1.0 if any(it.is_good for it in top) else 0.0


def ndcg_at_k(items: Sequence[RankedItem], k: int) -> float:
    """NDCG@k using realized_net_return as graded relevance (shifted to be
    non-negative so negative returns don't invert the gain)."""
    ranked = _by_score(items)[:k]
    if not ranked:
        return float("nan")
    rels = np.array([it.realized_net_return for it in items], dtype=float)
    shift = -min(0.0, float(rels.min()))
    def gain(x: float) -> float:
        return (x + shift)
    dcg = sum(gain(it.realized_net_return) / math.log2(i + 2) for i, it in enumerate(ranked))
    ideal = sorted(items, key=lambda it: it.realized_net_return, reverse=True)[:k]
    idcg = sum(gain(it.realized_net_return) / math.log2(i + 2) for i, it in enumerate(ideal))
    if idcg == 0:
        return float("nan")
    return float(dcg / idcg)


def spearman_rank_correlation(items: Sequence[RankedItem]) -> float:
    """Spearman correlation between predicted score and realized return."""
    if len(items) < 3:
        return float("nan")
    pred = np.array([it.predicted_score for it in items], dtype=float)
    real = np.array([it.realized_net_return for it in items], dtype=float)
    rp = _rankdata(pred)
    rr = _rankdata(real)
    rp -= rp.mean()
    rr -= rr.mean()
    denom = math.sqrt(float((rp**2).sum()) * float((rr**2).sum()))
    if denom == 0:
        return float("nan")
    return float((rp * rr).sum() / denom)


def _rankdata(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    sorted_a = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def top_k_returns(items: Sequence[RankedItem], k: int) -> tuple[float, float]:
    top = _by_score(items)[:k]
    if not top:
        return (float("nan"), float("nan"))
    rets = np.array([it.realized_net_return for it in top], dtype=float)
    return (float(rets.mean()), float(np.median(rets)))


def sector_concentration_top_k(items: Sequence[RankedItem], k: int) -> float:
    """Largest single-sector share among the top-k (0..1)."""
    top = _by_score(items)[:k]
    if not top:
        return float("nan")
    counts: dict[str, int] = {}
    for it in top:
        s = it.sector or "unknown"
        counts[s] = counts.get(s, 0) + 1
    return max(counts.values()) / len(top)


def turnover(prev_top: Sequence[str], curr_top: Sequence[str]) -> float:
    """Fraction of the current top-k that was not in the previous top-k."""
    if not curr_top:
        return float("nan")
    prev = set(prev_top)
    changed = sum(1 for s in curr_top if s not in prev)
    return changed / len(curr_top)


def ranking_metrics(items: Sequence[RankedItem], *, k_values: Sequence[int] = (1, 4, 10)) -> dict:
    out: dict = {}
    for k in k_values:
        out[f"precision_at_{k}"] = precision_at_k(items, k)
        out[f"hit_rate_top_{k}"] = hit_rate_top_k(items, k)
        out[f"ndcg_at_{k}"] = ndcg_at_k(items, k)
        mean_r, med_r = top_k_returns(items, k)
        out[f"mean_net_return_top_{k}"] = mean_r
        out[f"median_net_return_top_{k}"] = med_r
        out[f"sector_concentration_top_{k}"] = sector_concentration_top_k(items, k)
    out["spearman"] = spearman_rank_correlation(items)
    out["n"] = float(len(items))
    return out
