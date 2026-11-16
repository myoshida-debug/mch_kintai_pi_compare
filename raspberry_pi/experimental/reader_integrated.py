"""Experimental event-driven UID reader. Not for production."""

import logging

from raspberry_pi.reader_event import EventPcscCardReader
from .card_monitor import RecoveringCardMonitor

logger = logging.getLogger(__name__)


class IntegratedEventReader(EventPcscCardReader):
    """Read a UID once per card presentation."""

    def __init__(self, *args, event_monitor_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event_monitor_factory = (
            event_monitor_factory or RecoveringCardMonitor
        )

    def iter_uids(self, stop_event):
        monitor = self.event_monitor_factory()
        active_readers = set()
        recovery_pending = False

        for event_type, card in monitor.iter_events(stop_event):
            if stop_event.is_set():
                break

            if event_type == "reset":
                recovery_pending = True
                logger.warning(
                    "PC/SC monitoring reset; waiting for card absence."
                )
                continue

            if event_type == "absent":
                if recovery_pending:
                    active_readers.clear()
                    recovery_pending = False
                continue

            if recovery_pending:
                continue

            if not self._matches_reader(card):
                continue

            reader_key = self._card_key(card)

            if event_type == "removed":
                active_readers.discard(reader_key)
                continue

            if event_type != "inserted":
                continue

            if reader_key in active_readers:
                continue

            active_readers.add(reader_key)

            uid = self._read_with_retry(card, stop_event)

            if uid is not None:
                yield uid
