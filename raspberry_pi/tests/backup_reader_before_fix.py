import unittest
from unittest.mock import patch

from raspberry_pi.reader import (
    PcscCardReader,
    ReaderError,
    ReaderUnavailableError,
    UIDAssembler,
)


class UIDAssemblerTests(unittest.TestCase):
    def test_uid_characters_are_assembled(self):
        assembler = UIDAssembler()
        for character in "04A23F9182":
            result = assembler.feed(character)

        self.assertIsNone(result)
        self.assertEqual(assembler.feed("ENTER"), "04A23F9182")

    def test_lowercase_characters_are_uppercased(self):
        assembler = UIDAssembler()
        for character in "04a23f":
            assembler.feed(character)

        self.assertEqual(assembler.feed("ENTER"), "04A23F")

    def test_enter_confirms_and_resets_uid(self):
        assembler = UIDAssembler()
        for character in "AB12":
            assembler.feed(character)

        self.assertEqual(assembler.feed("\n"), "AB12")
        self.assertIsNone(assembler.feed("ENTER"))

    def test_empty_uid_is_ignored(self):
        assembler = UIDAssembler()

        self.assertIsNone(assembler.feed("ENTER"))


class PcscCardReaderTests(unittest.TestCase):
    def test_contactless_reader_is_preferred(self):
        contact = FakeReader("Alcor Link [Contactless Card Reader]")
        reader = PcscCardReader(
            readers_provider=lambda: [FakeReader("SAM Reader"), contact]
        )

        self.assertIs(reader.select_reader(), contact)

    def test_configured_reader_supports_exact_and_partial_match(self):
        alcor = FakeReader("Alcor Link AK9567 [Contactless Card Reader] 01 00")
        available = [FakeReader("Other Contactless"), alcor]

        exact = PcscCardReader(
            "Alcor Link AK9567 [Contactless Card Reader] 01 00",
            readers_provider=lambda: available,
        )
        partial = PcscCardReader(
            "AK9567 [Contactless Card Reader]",
            readers_provider=lambda: available,
        )

        self.assertIs(exact.select_reader(), alcor)
        self.assertIs(partial.select_reader(), alcor)

    def test_missing_reader_raises_unavailable(self):
        reader = PcscCardReader(readers_provider=lambda: [])

        with self.assertRaises(ReaderUnavailableError):
            reader.select_reader()

    def test_uid_apdu_success_is_uppercase(self):
        connection = FakeConnection(result=([0x04, 0xAB, 0x0C], 0x90, 0x00))

        self.assertEqual(PcscCardReader.read_uid(connection), "04AB0C")
        self.assertEqual(connection.apdu, [0xFF, 0xCA, 0x00, 0x00, 0x00])

    def test_uid_apdu_rejects_failure_status(self):
        connection = FakeConnection(result=([0x04], 0x6A, 0x82))

        with self.assertRaises(ReaderError):
            PcscCardReader.read_uid(connection)

    def test_uid_apdu_rejects_empty_uid(self):
        connection = FakeConnection(result=([], 0x90, 0x00))

        with self.assertRaises(ReaderError):
            PcscCardReader.read_uid(connection)

    def test_held_card_is_not_repeated_and_can_be_read_after_removal(self):
        physical_reader = FakeReader(
            "Contactless",
            outcomes=[
                UID_RESULT,
                UID_RESULT,
                OSError("no card"),
                OSError("no card"),
                OSError("no card"),
                UID_RESULT,
            ],
        )
        reader = PcscCardReader(
            poll_interval=0,
            readers_provider=lambda: [physical_reader],
        )
        uids = reader.iter_uids(NeverStopEvent())

        self.assertEqual(next(uids), "04AB0C")
        self.assertEqual(next(uids), "04AB0C")
        self.assertEqual(physical_reader.create_count, 6)

    def test_transient_63_00_is_retried_and_uid_is_yielded_once(self):
        physical_reader = FakeReader(
            "Contactless",
            outcomes=[STATUS_63_00, UID_RESULT, UID_RESULT, UID_RESULT],
        )
        reader = PcscCardReader(
            poll_interval=0,
            readers_provider=lambda: [physical_reader],
        )

        self.assertEqual(
            list(reader.iter_uids(StopAfterWaitEvent(4))),
            ["04AB0C"],
        )

    def test_single_connection_failure_does_not_clear_present_card(self):
        physical_reader = FakeReader(
            "Contactless",
            outcomes=[UID_RESULT, OSError("transient"), UID_RESULT],
        )
        reader = PcscCardReader(
            poll_interval=0,
            readers_provider=lambda: [physical_reader],
        )

        self.assertEqual(
            list(reader.iter_uids(StopAfterWaitEvent(3))),
            ["04AB0C"],
        )

    def test_removal_then_same_uid_is_a_new_card_presentation(self):
        physical_reader = FakeReader(
            "Contactless",
            outcomes=[
                UID_RESULT,
                OSError("no card"),
                OSError("no card"),
                OSError("no card"),
                UID_RESULT,
            ],
        )
        reader = PcscCardReader(
            poll_interval=0,
            readers_provider=lambda: [physical_reader],
        )

        self.assertEqual(
            list(reader.iter_uids(StopAfterWaitEvent(5))),
            ["04AB0C", "04AB0C"],
        )

    def test_disconnected_reader_is_searched_again(self):
        old = FakeReader("Contactless old", outcomes=[OSError("removed")])
        new = FakeReader("Contactless new", outcomes=[UID_RESULT])
        available = iter([[old], [], [new]])
        reader = PcscCardReader(
            poll_interval=0,
            reconnect_interval=0,
            readers_provider=lambda: next(available),
        )

        self.assertEqual(next(reader.iter_uids(NeverStopEvent())), "04AB0C")
        self.assertEqual(new.create_count, 1)

    def test_pyscard_is_imported_lazily(self):
        real_import = __import__

        def reject_smartcard(name, *args, **kwargs):
            if name.startswith("smartcard"):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=reject_smartcard):
            reader = PcscCardReader()
            with self.assertRaises(ReaderUnavailableError):
                reader.select_reader()


UID_RESULT = ([0x04, 0xAB, 0x0C], 0x90, 0x00)
STATUS_63_00 = ([], 0x63, 0x00)


class NeverStopEvent:
    def is_set(self):
        return False

    def wait(self, timeout):
        return False


class StopAfterWaitEvent:
    def __init__(self, wait_limit):
        self.wait_limit = wait_limit
        self.wait_count = 0

    def is_set(self):
        return self.wait_count >= self.wait_limit

    def wait(self, timeout):
        self.wait_count += 1
        return self.is_set()


class FakeConnection:
    def __init__(self, result=None, connect_error=None):
        self.result = result
        self.connect_error = connect_error
        self.apdu = None

    def connect(self):
        if self.connect_error:
            raise self.connect_error

    def transmit(self, apdu):
        self.apdu = apdu
        return self.result


class FakeReader:
    def __init__(self, name, outcomes=None):
        self.name = name
        self.outcomes = list(outcomes or [])
        self.create_count = 0

    def __str__(self):
        return self.name

    def createConnection(self):
        self.create_count += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            return FakeConnection(connect_error=outcome)
        return FakeConnection(result=outcome)
