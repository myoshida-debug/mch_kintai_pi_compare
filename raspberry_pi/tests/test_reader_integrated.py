import threading
import unittest

from raspberry_pi.experimental.reader_integrated import IntegratedEventReader


class FakeCard:
    reader = "AK9567 Contactless"
    atr = [1, 2, 3]


class FakeMonitor:
    def __init__(self, events):
        self.events = events

    def iter_events(self, stop_event):
        for event in self.events:
            if stop_event.is_set():
                return
            yield event


class IntegratedReaderTests(unittest.TestCase):
    def test_insert_remove_reinsert(self):
        card = FakeCard()
        events = [
            ("inserted", card),
            ("inserted", card),
            ("removed", card),
            ("inserted", card),
        ]

        reader = IntegratedEventReader(
            event_monitor_factory=lambda: FakeMonitor(events),
        )

        calls = []
        reader._read_with_retry = lambda card, stop: (
            calls.append(card) or "04AB0C"
        )

        result = list(reader.iter_uids(threading.Event()))

        self.assertEqual(result, ["04AB0C", "04AB0C"])
        self.assertEqual(len(calls), 2)

    def test_other_reader_ignored(self):
        card = FakeCard()
        reader = IntegratedEventReader(
            reader_name="Other Reader",
            event_monitor_factory=lambda: FakeMonitor([
                ("inserted", card),
            ]),
        )

        reader._read_with_retry = lambda card, stop: "04AB0C"

        result = list(reader.iter_uids(threading.Event()))

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
