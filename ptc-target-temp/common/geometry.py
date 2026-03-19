from __future__ import annotations

import numpy as np


def order_keypoints_by_angle(
    keypoints: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """
    Order 4 keypoints as [TL, TR, BR, BL] using centroid angle sort.

    Rotation-invariant ordering by sorting points by angle from centroid,
    then identifying TL as the point with smallest x+y.

    Args:
        keypoints: List of 4 (x, y) tuples (any order)

    Returns:
        Ordered list [TL, TR, BR, BL]
    """
    if len(keypoints) != 4:
        raise ValueError("Expected exactly 4 keypoints")

    # Compute centroid
    cx = sum(x for x, y in keypoints) / 4.0
    cy = sum(y for x, y in keypoints) / 4.0

    # Sort by angle from centroid
    def angle_from_centroid(pt: tuple[float, float]) -> float:
        return float(np.arctan2(pt[1] - cy, pt[0] - cx))

    sorted_kp = sorted(keypoints, key=angle_from_centroid)

    # Identify TL as point with smallest x + y
    tl_idx = min(range(4), key=lambda i: sorted_kp[i][0] + sorted_kp[i][1])

    # Rotate list so TL is first
    ordered = sorted_kp[tl_idx:] + sorted_kp[:tl_idx]

    return ordered
