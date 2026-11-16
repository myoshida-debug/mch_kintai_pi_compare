import threading
import unittest

from raspberry_pi.experimental.reader_integrated import IntegratedEventReader


class FakeCard:
    reader = "AK9567 Contactless"
    atr = [1, 2, 3]


class FakeMonitor:
    def iter_events(self, stop_event):
        card = FakeCard()
        yield "inserted", card
        yield "inserted", card
        yield "reset", None
        yield "inserted", card


class ResetTests(unittest.TestCase):
    def test_recovery_after_reset(self):
        reader = IntegratedEventReader(
            event_monitor_factory=FakeMonitor
        )

        calls = []

        def fake_read(card, stop_event):
            calls.append(card)
            return "04AB0C"

        reader._read_with_retry = fake_read

        result = list(reader.iter_uids(threading.Event()))

        self.assertEqual(result, ["04AB0C"])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
