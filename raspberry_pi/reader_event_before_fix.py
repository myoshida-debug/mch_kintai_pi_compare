import logging
import queue

from smartcard.CardMonitoring import CardMonitor, CardObserver

from .reader import PcscCardReader, ReaderError

logger = logging.getLogger(__name__)


class CardEventObserver(CardObserver):
    def __init__(self, event_queue):
        super().__init__()
        self.event_queue = event_queue

    def update(self, observable, actions):
        addedcards, removedcards = actions

        for card in addedcards:
            self.event_queue.put(("inserted", card))

        for card in removedcards:
            self.event_queue.put(("removed", card))


class EventPcscCardReader(PcscCardReader):
    def iter_uids(self, stop_event):
        event_queue = queue.Queue()
        observer = CardEventObserver(event_queue)
        monitor = CardMonitor()

        monitor.addObserver(observer)

        try:
            while not stop_event.is_set():
                try:
                    event_type, card = event_queue.get(timeout=0.5)
                except queue.Empty:
                    continue

                if event_type == "inserted":
                    connection = None
                    try:
                        connection = card.createConnection()
                        connection.connect()

                        uid = self.read_uid(connection)

                        logger.info("Card UID read successfully.")
                        yield uid

                    except Exception:
                        logger.exception("Could not read card UID.")

                    finally:
                        if connection is not None:
                            try:
                                connection.disconnect()
                            except Exception:
                                logger.warning(
                                    "PC/SC disconnect failed.",
                                    exc_info=True,
                                )

                elif event_type == "removed":
                    logger.info("Card removed from PC/SC reader.")

        finally:
            monitor.deleteObserver(observer)
