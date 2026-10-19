"""Analyses complémentaires : clustering hiérarchique et ruptures de corrélation.

Ces calculs ne sont pas nécessaires au calcul des corrélations (fait en SQL),
mais ils ajoutent deux lectures utiles :

  * un ordre de matrice par similarité, pour faire apparaître les groupes
    (tech, défensif, taux, or, crypto) au lieu d'un ordre alphabétique ;
  * des dates de rupture sur les corrélations glissantes, pour distinguer un
    changement durable d'un simple bruit de fenêtre.

Le clustering est implémenté en Python pur (average linkage) : avec ~20 actifs,
inutile d'ajouter scipy/numpy sur le Raspberry Pi. La détection de ruptures
utilise `ruptures` si le paquet optionnel est installé, sinon une segmentation
binaire interne.
"""

from __future__ import annotations

import argparse
import logging
import math
from datetime import date, timedelta

from . import config, db

logger = logging.getLogger(__name__)


# ───────────────────────────── Clustering ─────────────────────────────
def _distance(correlation: float | None) -> float:
    """Distance de corrélation : 0 = identique, 2 = opposé."""
    if correlation is None:
        return 1.0
    return 1.0 - max(-1.0, min(1.0, float(correlation)))


def cluster_assets(
    tickers: list[str],
    correlations: dict[frozenset[str], float],
    k: int = 5,
) -> list[list[str]]:
    """Clustering hiérarchique ascendant, average linkage.

    Retourne les clusters dans l'ordre des feuilles du dendrogramme. Cet ordre
    est utilisé pour réordonner la matrice Grafana : les actifs proches se
    suivent visuellement.
    """
    tickers = list(tickers)
    n = len(tickers)
    if n == 0:
        return []
    k = max(1, min(k, n))

    index = {ticker: i for i, ticker in enumerate(tickers)}
    nodes: dict[int, dict] = {
        i: {"members": [ticker], "left": None, "right": None, "order": i}
        for i, ticker in enumerate(tickers)
    }
    active: set[int] = set(nodes)
    distances: dict[frozenset[int], float] = {}

    for i in range(n):
        for j in range(i + 1, n):
            corr = correlations.get(frozenset((tickers[i], tickers[j])))
            distances[frozenset((i, j))] = _distance(corr)

    next_id = n
    while len(active) > k:
        best_pair = None
        best_distance = math.inf
        for i in sorted(active):
            for j in sorted(active):
                if i >= j:
                    continue
                distance = distances[frozenset((i, j))]
                if distance < best_distance - 1e-12 or (
                    abs(distance - best_distance) <= 1e-12
                    and (best_pair is None or (i, j) < best_pair)
                ):
                    best_distance = distance
                    best_pair = (i, j)
        if best_pair is None:
            break

        i, j = best_pair
        members_i = nodes[i]["members"]
        members_j = nodes[j]["members"]
        size_i, size_j = len(members_i), len(members_j)
        new_id = next_id
        next_id += 1
        nodes[new_id] = {
            "members": members_i + members_j,
            "left": i,
            "right": j,
            "order": min(nodes[i]["order"], nodes[j]["order"]),
        }

        for other in active:
            if other in (i, j):
                continue
            d_i = distances[frozenset((i, other))]
            d_j = distances[frozenset((j, other))]
            distances[frozenset((new_id, other))] = (
                size_i * d_i + size_j * d_j
            ) / (size_i + size_j)

        active.remove(i)
        active.remove(j)
        active.add(new_id)

    def leaves(node_id: int) -> list[str]:
        node = nodes[node_id]
        if node["left"] is None:
            return list(node["members"])
        return leaves(node["left"]) + leaves(node["right"])

    clusters = [leaves(node_id) for node_id in active]
    clusters.sort(key=lambda members: index[members[0]])
    return clusters


def compute_clusters(
    conn,
    start: date,
    end: date,
    basis: str = "EUR",
    min_obs: int = 60,
    k: int = 5,
) -> list[dict]:
    """Calcule et persiste l'ordre des actifs dans finance.assets."""
    assets = db.fetch_assets(conn)
    tickers = [asset["ticker"] for asset in assets]
    pairs = db.fetch_pairs(conn, start, end, basis, min_obs)
    correlations = {
        frozenset((row["asset_a"], row["asset_b"])): float(row["correlation"])
        for row in pairs
        if row["correlation"] is not None
    }
    if not correlations:
        logger.warning("Clustering ignoré : aucune corrélation disponible.")
        return []

    clusters = cluster_assets(tickers, correlations, k=k)
    assignments: list[dict] = []
    order = 10
    for cluster_id, members in enumerate(clusters, start=1):
        for ticker in members:
            assignments.append(
                {
                    "ticker": ticker,
                    "cluster_id": cluster_id,
                    "cluster_label": f"Cluster {cluster_id}",
                    "display_order": order,
                }
            )
            order += 10

    with conn:
        db.update_clusters(conn, assignments)
    logger.info("Clustering : %s clusters, %s actifs ordonnés.", len(clusters), len(assignments))
    return assignments


# ───────────────────────────── Ruptures ─────────────────────────────
def _binary_segmentation(
    values: list[float],
    max_breaks: int = 3,
    min_size: int = 60,
    min_improvement: float = 0.05,
    min_effect: float = 0.10,
) -> list[int]:
    """Segmentation binaire d'une série 1D par minimisation de la SSE.

    Utilisée seulement si `ruptures` n'est pas installé. Ce n'est pas PELT,
    mais c'est déterministe, sans dépendance, et suffisant pour des séries de
    corrélations de quelques centaines de points.
    """
    n = len(values)
    if n < 2 * min_size:
        return []

    def best_split(start: int, end: int):
        length = end - start
        if length < 2 * min_size:
            return None
        total = sum(values[start:end])
        total_sq = sum(v * v for v in values[start:end])
        sse_total = total_sq - total * total / length
        if sse_total <= 0:
            return None
        best = None
        for split in range(start + min_size, end - min_size + 1):
            left = values[start:split]
            right = values[split:end]
            left_sum = sum(left)
            right_sum = sum(right)
            sse_left = sum(v * v for v in left) - left_sum * left_sum / len(left)
            sse_right = sum(v * v for v in right) - right_sum * right_sum / len(right)
            improvement = (sse_total - (sse_left + sse_right)) / sse_total
            effect = abs(right_sum / len(right) - left_sum / len(left))
            if effect < min_effect:
                continue
            if best is None or improvement > best[0]:
                best = (improvement, split)
        if best is None or best[0] < min_improvement:
            return None
        return best

    segments = [(0, n)]
    breaks: list[int] = []
    while len(breaks) < max_breaks:
        candidate = None
        for start, end in segments:
            split = best_split(start, end)
            if split is None:
                continue
            if candidate is None or split[0] > candidate[0]:
                candidate = (split[0], split[1], start, end)
        if candidate is None:
            break
        _, split, start, end = candidate
        breaks.append(split)
        segments.remove((start, end))
        segments.extend([(start, split), (split, end)])
    return sorted(breaks)


def _break_score(values: list[float], index: int, min_size: int) -> float:
    left = values[max(0, index - min_size):index]
    right = values[index:min(index + min_size, len(values))]
    if not left or not right:
        return 0.0
    return abs(sum(right) / len(right) - sum(left) / len(left))


def detect_breaks(
    values: list[float],
    dates: list[date],
    max_breaks: int = 3,
    min_size: int = 252,
) -> tuple[str, list[dict]]:
    """Détecte des ruptures de moyenne dans une série de corrélations.

    Retourne (méthode, liste de dict {break_date, score}).
    """
    if len(values) != len(dates) or len(values) < 2 * min_size:
        return "none", []

    method = "binary_segmentation"
    indices: list[int] = []
    try:
        import numpy as np  # type: ignore
        import ruptures as rpt  # type: ignore

        signal = np.asarray(values, dtype=float).reshape(-1, 1)
        n_bkps = min(max_breaks, max(0, len(values) // min_size - 1))
        if n_bkps > 0:
            algorithm = rpt.Binseg(model="l2", min_size=min_size).fit(signal)
            raw = algorithm.predict(n_bkps=n_bkps)
            indices = [int(b - 1) for b in raw[:-1]]
            method = "ruptures_binseg"
    except Exception:  # ImportError, erreur numérique, version incompatible…
        indices = _binary_segmentation(values, max_breaks=max_breaks, min_size=min_size)

    if method != "ruptures_binseg":
        indices = _binary_segmentation(values, max_breaks=max_breaks, min_size=min_size)

    # Espacement minimal et déduplication.
    filtered: list[int] = []
    for index in sorted(set(indices)):
        if index <= 0 or index >= len(values):
            continue
        if filtered and index - filtered[-1] < min_size:
            continue
        filtered.append(index)
    filtered = filtered[:max_breaks]

    breaks = [
        {
            "break_date": dates[index],
            "score": round(_break_score(values, index, min_size), 6),
        }
        for index in filtered
    ]
    return (method if breaks else "none"), breaks


def compute_breaks(
    conn,
    start: date,
    end: date,
    basis: str = "EUR",
    window: int = 126,
    max_breaks: int = 3,
    min_size: int = 252,
) -> int:
    """Calcule les ruptures de chaque actif contre URTH et les persiste."""
    assets = [asset for asset in db.fetch_assets(conn) if asset["ticker"] != "URTH"]
    total = 0
    with conn:
        for asset in assets:
            rows = db.fetch_rolling_corr(conn, asset["ticker"], "URTH", window, start, end, basis)
            values = [float(row["correlation"]) for row in rows if row["correlation"] is not None]
            dates = [row["trade_date"] for row in rows if row["correlation"] is not None]
            method, breaks = detect_breaks(values, dates, max_breaks=max_breaks, min_size=min_size)
            db.replace_breaks(conn, asset["asset_id"], window, method, breaks)
            total += len(breaks)
            if breaks:
                logger.info("%s : %s rupture(s) (%s)", asset["ticker"], len(breaks), method)
    logger.info("Ruptures : %s date(s) enregistrée(s).", total)
    return total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clustering et ruptures de corrélation.")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--basis", choices=("EUR", "LOCAL"), default=config.BASE_CURRENCY)
    parser.add_argument("--min-obs", type=int, default=config.MIN_OBS)
    parser.add_argument("--cluster-k", type=int, default=config.CLUSTER_K)
    parser.add_argument("--window", type=int, default=config.BREAK_WINDOW)
    parser.add_argument("--skip-clusters", action="store_true")
    parser.add_argument("--skip-breaks", action="store_true")
    parser.add_argument("--log-level", default=config.LOG_LEVEL)
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level, format="%(asctime)s [%(levelname)s] %(message)s")
    end = args.end
    start = args.start or (end - timedelta(days=365 * config.CLUSTER_LOOKBACK_YEARS))

    conn = db.connect()
    try:
        db.init_schema(conn)
        if not args.skip_clusters:
            compute_clusters(conn, start, end, args.basis, args.min_obs, args.cluster_k)
        if not args.skip_breaks:
            compute_breaks(conn, start, end, args.basis, args.window, config.BREAK_MAX, config.BREAK_MIN_SIZE)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
