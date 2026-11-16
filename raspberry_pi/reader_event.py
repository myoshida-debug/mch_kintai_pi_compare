"""Event-driven PC/SC card reader (experimental)."""

import logging
import queue
import threading
from time import monotonic

from smartcard.CardMonitoring import CardMonitor, CardObserver

from .reader import PcscCardReader

logger = logging.getLogger(__name__)


class CardEventObserver(CardObserver):
    def __init__(self, event_queue):
        super().__init__()
        self.event_queue = event_queue

    def update(self, observable, actions):
        addedcards, removedcards = actions

        for card in removedcards:
            self.event_queue.put(("removed", card))

        for card in addedcards:
            logger.info("TIMING card inserted event: %.6f", monotonic())
            self.event_queue.put(("inserted", card, monotonic()))


class EventPcscCardReader(PcscCardReader):
    """Read a card UID once per presentation."""

    def __init__(
        self,
        *args,
        uid_retries=3,
        retry_delay=0.15,
        monitor_factory=CardMonitor,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.uid_retries = max(1, int(uid_retries))
        self.retry_delay = max(0.0, float(retry_delay))
        self.monitor_factory = monitor_factory

    @staticmethod
    def _card_key(card):
        return str(getattr(card, "reader", card))

    def _matches_reader(self, card):
        configured = getattr(self, "reader_name", None)

        if not configured:
            return True

        actual = self._card_key(card)

        return (
            configured.lower() == actual.lower()
            or configured.lower() in actual.lower()
        )

    def _read_with_retry(self, card, stop_event):
        for attempt in range(self.uid_retries):
            if stop_event.is_set():
                return None

            connection = None

            try:
                connection = card.createConnection()
                connect_start = monotonic()
                logger.info("TIMING connection start: %.6f", connect_start)
                try:
                    connection.connect()
                finally:
                    logger.info(
                        "TIMING connection elapsed: %.3f ms",
                        (monotonic() - connect_start) * 1000,
                    )

                uid_start = monotonic()
                try:
                    uid = self.read_uid(connection)
                except Exception as exc:
                    if "UID APDU failed with status 63 00" not in str(exc):
                        raise

                    logger.warning(
                        "UID status 63 00: retrying on same connection."
                    )
                    uid = self.read_uid(connection)

                logger.info(
                    "TIMING UID read elapsed: %.3f ms",
                    (monotonic() - uid_start) * 1000,
                )

                logger.info("Card UID read successfully.")
                return uid

            except Exception:
                logger.warning(
                    "Card UID read failed (%s/%s).",
                    attempt + 1,
                    self.uid_retries,
                    exc_info=True,
                )

            finally:
                if connection is not None:
                    try:
                        connection.disconnect()
                    except Exception:
                        logger.warning(
                            "PC/SC disconnect failed.",
                            exc_info=True,
                        )

            if attempt + 1 < self.uid_retries:
                if stop_event.wait(self.retry_delay):
                    return None

        return None

    def iter_uids(self, stop_event):
        event_queue = queue.Queue()
        observer = CardEventObserver(event_queue)

        # Prevent duplicate UID notifications while a card is held.
        active_cards = set()

        monitor = self.monitor_factory()
        observer_registered = False

        try:
            monitor.addObserver(observer)
            observer_registered = True

            while not stop_event.is_set():
                try:
                    event = event_queue.get(timeout=0.5)
                    event_type, card = event[:2]
                    queued_at = event[2] if len(event) > 2 else None
                except queue.Empty:
                    continue

                if not self._matches_reader(card):
                    continue

                card_key = self._card_key(card)

                if event_type == "removed":
                    active_cards.discard(card_key)
                    logger.info(
                        "Card removed from PC/SC reader: %s",
                        card_key,
                    )
                    continue

                if event_type != "inserted":
                    continue

                if card_key in active_cards:
                    continue

                active_cards.add(card_key)

                if queued_at is not None:
                    logger.info(
                        "TIMING event queue elapsed: %.3f ms",
                        (monotonic() - queued_at) * 1000,
                    )

                uid = self._read_with_retry(card, stop_event)

                if uid is not None:
                    yield uid

        finally:
            if observer_registered:
                logger.info("Removing PC/SC card observer.")
                monitor.deleteObserver(observer)
