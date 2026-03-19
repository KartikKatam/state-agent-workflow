from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from producer.buffer import IdBufferManager
from producer.models import BinSnapshot, TrackId

from .config import ConsumerConfig


@dataclass
class IdPoolState:
    """
    Tracks last-consumed snapshot versions per TrackId.
    """

    last_versions: dict[TrackId, int]


def fetch_updated_snapshots(
    buffer_mgr: IdBufferManager,
    state: IdPoolState,
) -> list[BinSnapshot]:
    """
    Pull all bins that have newer versions than the last consumed snapshot.

    Args:
        buffer_mgr: Producer buffer manager
        state: Consumer-side state tracking last versions

    Returns:
        List of updated BinSnapshot objects
    """
    return buffer_mgr.get_all_updated_bins(state.last_versions)


def choose_highest_priority_bin(
    snapshots: Iterable[BinSnapshot],
) -> BinSnapshot | None:
    """
    Choose the highest-priority snapshot from the updated bins.

    Current policy: pick the most recently updated bin (highest last_update_frame_idx).
    Tie-breaker: higher version.
    """
    snapshots_list = list(snapshots)
    if not snapshots_list:
        return None

    return max(
        snapshots_list,
        key=lambda s: (s.last_update_frame_idx, s.version),
    )


def get_next_snapshot(
    buffer_mgr: IdBufferManager,
    state: IdPoolState,
    cfg: ConsumerConfig,
) -> BinSnapshot | None:
    """
    Fetch the next snapshot for rich analysis.

    Args:
        buffer_mgr: Producer buffer manager
        state: Consumer-side state tracking last versions
        cfg: Consumer configuration (reserved for future policies)

    Returns:
        Selected BinSnapshot or None if no updates are available
    """
    _ = cfg
    updated = fetch_updated_snapshots(buffer_mgr, state)
    selected = choose_highest_priority_bin(updated)

    if selected is None:
        return None

    state.last_versions[selected.track_id] = selected.version
    return selected
