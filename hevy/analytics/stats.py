"""Small, dependency-free statistics."""
from __future__ import annotations

import statistics


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def theil_sen(points: list[tuple[float, float]]) -> float | None:
    """Median of the slopes between every pair of points (x distinct). Robust to a single odd point."""
    slopes = []
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            (x1, y1), (x2, y2) = points[i], points[j]
            if x2 != x1:
                slopes.append((y2 - y1) / (x2 - x1))
    return statistics.median(slopes) if slopes else None
