from __future__ import annotations

import queue
from dataclasses import dataclass

from .config import ConsumerConfig
from .models import OcrQueueItem


@dataclass
class OcrQueueStats:
    """
    Runtime stats for OCR queue usage.
    """

    capacity: int
    size: int


class OcrQueue:
    """
    Bounded FIFO queue with back-pressure for OCR consumption.
    """

    def __init__(self, cfg: ConsumerConfig):
        self._queue: queue.Queue[OcrQueueItem] = queue.Queue(maxsize=cfg.ocr_queue_capacity)

    def push(self, item: OcrQueueItem) -> bool:
        """
        Attempt to enqueue; return False if full (back-pressure).
        """
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            return False

    def pop(self) -> OcrQueueItem | None:
        """
        Pop next item if available; return None if empty.
        """
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    def stats(self) -> OcrQueueStats:
        return OcrQueueStats(capacity=self._queue.maxsize, size=self._queue.qsize())
