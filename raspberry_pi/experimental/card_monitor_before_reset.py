"""Experimental PC/SC card state monitor.

Not used by the production attendance service.
"""

import logging
import threading

from smartcard.CardRequest import CardRequest
from smartcard.Exceptions import (
    CardRequestTimeoutException,
    SmartcardException,
)

logger = logging.getLogger(__name__)


class RecoveringCardMonitor:
    """Monitor card state changes with a bounded wait."""

    def __init__(
        self,
        timeout=1.0,
        reconnect_interval=2.0,
        request_factory=CardRequest,
    ):
        self.timeout = timeout
        self.reconnect_interval = reconnect_interval
        self.request_factory = request_factory

    @staticmethod
    def card_key(card):
        reader = str(getattr(card, "reader", ""))
        atr = bytes(getattr(card, "atr", []) or [])
        return reader, atr

    def iter_events(self, stop_event):
        previous_cards = {}

        while not stop_event.is_set():
            request = None
            try:
                request = self.request_factory(timeout=self.timeout)

                while not stop_event.is_set():
                    try:
                        cards = request.waitforcardevent()
                    except CardRequestTimeoutException:
                        continue

                    current_cards = {
                        self.card_key(card): card
                        for card in cards
                    }

                    for key, card in previous_cards.items():
                        if key not in current_cards:
                            yield "removed", card

                    for key, card in current_cards.items():
                        if key not in previous_cards:
                            yield "inserted", card

                    previous_cards = current_cards

            except SmartcardException:
                logger.exception(
                    "PC/SC monitoring failed; retrying."
                )

                previous_cards = {}

                if stop_event.wait(self.reconnect_interval):
                    break

            finally:
                if request is not None:
                    pcsc_request = getattr(
                        request, "pcsccardrequest", None
                    )
                    if pcsc_request is not None:
                        try:
                            pcsc_request.release()
                        except Exception:
                            logger.exception(
                                "Failed to release PC/SC context."
                            )
