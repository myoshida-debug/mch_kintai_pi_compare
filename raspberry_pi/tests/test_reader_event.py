import threading
import unittest

from raspberry_pi.reader_event import (
    EventPcscCardReader,
)


UID_RESULT = ([0x04, 0xAB, 0x0C], 0x90, 0x00)
STATUS_63_00 = ([], 0x63, 0x00)


class FakeConnection:
    def __init__(self, result):
        self.result = result
        self.disconnected = False

    def connect(self):
        if isinstance(self.result, Exception):
            raise self.result

    def transmit(self, apdu):
        return self.result

    def disconnect(self):
        self.disconnected = True


class FakeCard:
    def __init__(self, outcomes, reader="Contactless"):
        self.reader = reader
        self.outcomes = list(outcomes)
        self.connections = []

    def createConnection(self):
        result = self.outcomes.pop(0)
        connection = FakeConnection(result)
        self.connections.append(connection)
        return connection


class FakeMonitor:
    def __init__(self, events):
        self.events = events
        self.observer = None
        self.deleted = False

    def addObserver(self, observer):
        self.observer = observer
        for event_type, card in self.events:
            if event_type == "inserted":
                observer.update(self, ([card], []))
            elif event_type == "removed":
                observer.update(self, ([], [card]))

    def deleteObserver(self, observer):
        self.deleted = True


class StopAfterUidEvent:
    def __init__(self, count):
        self.limit = count
        self.calls = 0

    def is_set(self):
        return self.calls >= self.limit

    def wait(self, timeout):
        return False

    def uid_received(self):
        self.calls += 1


class EventReaderTests(unittest.TestCase):
    def make_reader(self, events, **kwargs):
        monitor = FakeMonitor(events)
        reader = EventPcscCardReader(
            retry_delay=0,
            monitor_factory=lambda: monitor,
            **kwargs,
        )
        return reader, monitor

    def test_normal_uid(self):
        card = FakeCard([UID_RESULT])
        reader, monitor = self.make_reader([
            ("inserted", card),
        ])
        stop = threading.Event()

        generator = reader.iter_uids(stop)

        self.assertEqual(next(generator), "04AB0C")

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)
        self.assertTrue(card.connections[0].disconnected)

    def test_transient_63_00_retry(self):
        card = FakeCard([STATUS_63_00, UID_RESULT])
        reader, monitor = self.make_reader([
            ("inserted", card),
        ])
        stop = threading.Event()

        generator = reader.iter_uids(stop)

        self.assertEqual(next(generator), "04AB0C")
        self.assertEqual(len(card.connections), 2)
        self.assertTrue(
            all(c.disconnected for c in card.connections)
        )

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)

    def test_uid_retry_on_same_connection(self):
        class TransientConnection(FakeConnection):
            def __init__(self):
                super().__init__(UID_RESULT)
                self.transmit_count = 0

            def transmit(self, apdu):
                self.transmit_count += 1
                if self.transmit_count == 1:
                    return STATUS_63_00
                return UID_RESULT

        class TransientCard:
            reader = "Contactless"

            def __init__(self):
                self.connections = []

            def createConnection(self):
                connection = TransientConnection()
                self.connections.append(connection)
                return connection

        card = TransientCard()
        reader, monitor = self.make_reader([
            ("inserted", card),
        ])
        stop = threading.Event()
        generator = reader.iter_uids(stop)

        result = []
        errors = []

        def read_once():
            try:
                result.append(next(generator))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=read_once, daemon=True)
        worker.start()
        worker.join(timeout=2.0)

        self.assertFalse(worker.is_alive(), "UID取得が2秒以内に完了しません")
        self.assertFalse(errors, f"UID取得エラー: {errors}")
        self.assertEqual(result, ["04AB0C"])
        self.assertEqual(len(card.connections), 1)
        self.assertEqual(card.connections[0].transmit_count, 2)
        self.assertTrue(card.connections[0].disconnected)

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)

    def test_connection_error_retry(self):
        card = FakeCard([
            RuntimeError("Card is unpowered. (0x80100067)"),
            UID_RESULT,
        ])
        reader, monitor = self.make_reader([
            ("inserted", card),
        ])
        stop = threading.Event()
        generator = reader.iter_uids(stop)

        self.assertEqual(next(generator), "04AB0C")
        self.assertEqual(len(card.connections), 2)
        self.assertTrue(
            all(c.disconnected for c in card.connections)
        )

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)

    def test_same_card_after_removal(self):
        card = FakeCard([UID_RESULT, UID_RESULT])
        reader, monitor = self.make_reader([
            ("inserted", card),
            ("inserted", card),
            ("removed", card),
            ("inserted", card),
        ])
        stop = threading.Event()

        generator = reader.iter_uids(stop)

        self.assertEqual(next(generator), "04AB0C")
        self.assertEqual(next(generator), "04AB0C")
        self.assertEqual(len(card.connections), 2)

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)

    def test_failed_short_tap_then_next_tap(self):
        failed_card = FakeCard([
            RuntimeError("Card is unpowered"),
            RuntimeError("Card is unpowered"),
            RuntimeError("Card is unpowered"),
        ])
        next_card = FakeCard([UID_RESULT])

        reader, monitor = self.make_reader([
            ("inserted", failed_card),
            ("removed", failed_card),
            ("inserted", next_card),
        ])

        stop = threading.Event()
        generator = reader.iter_uids(stop)

        self.assertEqual(next(generator), "04AB0C")
        self.assertEqual(len(failed_card.connections), 3)
        self.assertEqual(len(next_card.connections), 1)

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)

    def test_other_reader_is_ignored(self):
        wrong_card = FakeCard(
            [UID_RESULT],
            reader="Other Reader",
        )
        right_card = FakeCard(
            [UID_RESULT],
            reader="AK9567 Contactless",
        )

        reader, monitor = self.make_reader(
            [
                ("inserted", wrong_card),
                ("inserted", right_card),
            ],
            reader_name="AK9567",
        )

        stop = threading.Event()
        generator = reader.iter_uids(stop)

        self.assertEqual(next(generator), "04AB0C")
        self.assertEqual(len(wrong_card.connections), 0)

        stop.set()
        with self.assertRaises(StopIteration):
            next(generator)

        self.assertTrue(monitor.deleted)


if __name__ == "__main__":
    unittest.main()
