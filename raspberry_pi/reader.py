import logging
import select
import time
from abc import ABC, abstractmethod
from pathlib import Path


logger = logging.getLogger(__name__)


class ReaderError(Exception):
    """Base error for card reader failures."""


class ReaderUnavailableError(ReaderError):
    """Raised when the configured reader cannot be opened."""


class CardReader(ABC):
    @abstractmethod
    def iter_uids(self, stop_event):
        """Yield complete card UIDs until stop_event is set."""


def normalize_uid(uid):
    """Return the canonical UID representation, or None for an empty UID."""
    normalized = str(uid).strip().upper()
    return normalized or None


class PcscCardReader(CardReader):
    GET_UID_APDU = [0xFF, 0xCA, 0x00, 0x00, 0x00]

    def __init__(
        self,
        reader_name=None,
        poll_interval=0.05,
        reconnect_interval=5.0,
        removal_miss_threshold=3,
        readers_provider=None,
    ):
        self.reader_name = str(reader_name).strip() if reader_name else None
        self.poll_interval = poll_interval
        self.reconnect_interval = reconnect_interval
        self.removal_miss_threshold = removal_miss_threshold
        self._readers_provider = readers_provider

    def _get_readers(self):
        if self._readers_provider is not None:
            return list(self._readers_provider())
        try:
            # Keep pyscard optional for development and test environments.
            from smartcard.System import readers
        except ImportError as exc:
            raise ReaderUnavailableError(
                "pyscard is required to use the PC/SC card reader."
            ) from exc
        return list(readers())

    def select_reader(self):
        available = self._get_readers()
        if not available:
            raise ReaderUnavailableError("No PC/SC reader found.")

        if self.reader_name:
            requested = self.reader_name.casefold()
            exact = [r for r in available if str(r).casefold() == requested]
            partial = [r for r in available if requested in str(r).casefold()]
            matches = exact or partial
            if not matches:
                raise ReaderUnavailableError(
                    f"Configured PC/SC reader not found: {self.reader_name}"
                )
            return matches[0]

        for reader in available:
            if "contactless" in str(reader).casefold():
                return reader
        return available[0]

    @classmethod
    def read_uid(cls, connection):
        data, sw1, sw2 = connection.transmit(cls.GET_UID_APDU)
        if (sw1, sw2) != (0x90, 0x00):
            raise ReaderError(
                f"UID APDU failed with status {sw1:02X} {sw2:02X}."
            )
        uid = normalize_uid("".join(f"{byte:02X}" for byte in data))
        if not uid:
            raise ReaderError("Reader returned an empty UID.")
        return uid

    def iter_uids(self, stop_event):
        selected = None
        card_present = False
        uid_yielded = False
        connection_misses = 0
        connection_error_count = 0
        last_connection_warning = time.monotonic()
        while not stop_event.is_set():
            if selected is None:
                try:
                    selected = self.select_reader()
                    logger.info("Using PC/SC reader: %s", selected)
                except ReaderError as exc:
                    logger.warning("PC/SC reader unavailable: %s", exc)
                    stop_event.wait(self.reconnect_interval)
                    continue

            connection = None
            connected = False
            uid_to_yield = None
            try:
                connection = selected.createConnection()
                connection.connect()
                connected = True
                card_present = True
                connection_misses = 0
                if not uid_yielded:
                    logger.info("Card detected. Reading UID.")
                    uid = self.read_uid(connection)
                    uid_yielded = True
                    logger.info("Card UID read successfully.")
                    uid_to_yield = uid
            except ReaderError as exc:
                # An APDU status error (including AK9567's transient 63 00)
                # does not mean that the card was removed. Retry next poll.
                logger.warning("Could not read card UID: %s", exc)
            except Exception as exc:
                from smartcard.Exceptions import NoCardException

                if not isinstance(exc, NoCardException):
                    connection_error_count += 1

                now = time.monotonic()

                if connection_error_count and now - last_connection_warning >= 60:
                    logger.warning(
                        "PC/SC connection failures in last interval: "
                        "count=%d, last_error=%s: %s",
                        connection_error_count,
                        type(exc).__name__,
                        exc,
                    )
                    connection_error_count = 0
                    last_connection_warning = now

                try:
                    current_names = {str(reader) for reader in self._get_readers()}
                except ReaderError:
                    current_names = set()
                if str(selected) not in current_names:
                    logger.warning("PC/SC reader disconnected: %s", exc)
                    selected = None
                    card_present = False
                    uid_yielded = False
                    connection_misses = 0
                    stop_event.wait(self.reconnect_interval)
                    continue
                if card_present:
                    connection_misses += 1
                    if connection_misses >= self.removal_miss_threshold:
                        logger.info("Card removed from PC/SC reader.")
                        card_present = False
                        uid_yielded = False
                        connection_misses = 0
            finally:
                if connected and connection is not None:
                    try:
                        connection.disconnect()
                    except Exception as exc:
                        logger.warning("PC/SC disconnect failed: %s", exc)
            if uid_to_yield is not None:
                yield uid_to_yield
            stop_event.wait(self.poll_interval)


class UIDAssembler:
    def __init__(self):
        self._characters = []

    def feed(self, value):
        if value in {"\n", "\r", "ENTER"}:
            uid = normalize_uid("".join(self._characters))
            self._characters.clear()
            return uid or None

        if isinstance(value, str) and len(value) == 1:
            character = value.upper()
            if character in "0123456789ABCDEF":
                self._characters.append(character)
        return None


class EvdevCardReader(CardReader):
    KEY_MAP = {
        **{f"KEY_{value}": value for value in "0123456789ABCDEF"},
        **{f"KEY_KP{value}": value for value in "0123456789"},
        "KEY_ENTER": "ENTER",
        "KEY_KPENTER": "ENTER",
    }

    def __init__(self, device_path):
        self.device_path = Path(device_path)

    def iter_uids(self, stop_event):
        if not self.device_path.exists():
            raise ReaderUnavailableError(
                f"Reader device not found: {self.device_path}"
            )

        try:
            from evdev import InputDevice, ecodes
        except ImportError as exc:
            raise ReaderUnavailableError(
                "evdev is required to use the Linux card reader."
            ) from exc

        try:
            device = InputDevice(str(self.device_path))
        except OSError as exc:
            raise ReaderUnavailableError(
                f"Reader device could not be opened: {self.device_path}"
            ) from exc

        assembler = UIDAssembler()
        try:
            while not stop_event.is_set():
                readable, _, _ = select.select([device.fd], [], [], 1.0)
                if not readable:
                    continue
                for event in device.read():
                    if event.type != ecodes.EV_KEY or event.value != 1:
                        continue
                    key_name = ecodes.KEY.get(event.code)
                    value = self.KEY_MAP.get(key_name)
                    if value is None:
                        continue
                    uid = assembler.feed(value)
                    if uid:
                        yield uid
        except OSError as exc:
            raise ReaderError("Reader disconnected while reading.") from exc
        finally:
            device.close()


def main():
    """Run a standalone PC/SC reader check on the Raspberry Pi."""
    import argparse
    import threading

    parser = argparse.ArgumentParser(description="Print PC/SC card UIDs.")
    parser.add_argument(
        "--reader",
        default=None,
        help="PC/SC reader name (exact or partial match)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop_event = threading.Event()
    reader = PcscCardReader(reader_name=args.reader)
    try:
        for uid in reader.iter_uids(stop_event):
            print(uid, flush=True)
    except KeyboardInterrupt:
        stop_event.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
