import logging
import signal
import threading

from .api_client import KintaiApiClient
from .config import Settings
from .database import PunchDatabase
from .reader import EvdevCardReader, PcscCardReader, ReaderError
from .reader_event import EventPcscCardReader
from .service import AttendanceService


logger = logging.getLogger(__name__)


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main():
    configure_logging()
    try:
        settings = Settings.from_environment()
    except ValueError as exc:
        logger.error("Configuration error: %s", exc)
        return 2

    database = PunchDatabase(settings.sqlite_path)
    database.initialize()
    api_client = KintaiApiClient(
        base_url=settings.api_base_url,
        api_key=settings.api_key,
        timeout=settings.request_timeout,
    )
    service = AttendanceService(
        database=database,
        api_client=api_client,
        terminal_id=settings.terminal_id,
        app_version=settings.app_version,
        retry_interval=settings.retry_interval,
        heartbeat_interval=settings.heartbeat_interval,
        enrollment_poll_interval=settings.enrollment_poll_interval,
    )
    if settings.reader_backend == "pcsc":
        reader = PcscCardReader(
            reader_name=settings.pcsc_reader_name or None,
            reconnect_interval=settings.retry_interval,
        )
        reader_description = settings.pcsc_reader_name or "Contactless (auto)"
    elif settings.reader_backend == "pcsc_event":
        reader = EventPcscCardReader(
            reader_name=settings.pcsc_reader_name or None,
            reconnect_interval=settings.retry_interval,
        )
        reader_description = "PC/SC Event"
    elif settings.reader_backend == "evdev":
        reader = EvdevCardReader(settings.reader_device)
        reader_description = settings.reader_device
    else:
        logger.error(
            "Configuration error: READER_BACKEND must be pcsc, pcsc_event or evdev."
        )
        return 2
    stop_event = threading.Event()

    def request_stop(signum, frame):
        logger.info("Shutdown requested.")
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    workers = [
        threading.Thread(
            target=service.run_retry_loop,
            args=(stop_event,),
            name="punch-retry",
            daemon=True,
        ),
        threading.Thread(
            target=service.run_enrollment_loop,
            args=(stop_event,),
            name="enrollment-poll",
            daemon=True,
        ),
        threading.Thread(
            target=service.run_heartbeat_loop,
            args=(stop_event,),
            name="heartbeat",
            daemon=True,
        ),
    ]
    for worker in workers:
        worker.start()

    logger.info("Attendance client started for terminal %s.", settings.terminal_id)
    try:
        while not stop_event.is_set():
            try:
                logger.info("Connecting to reader %s.", reader_description)
                for uid in reader.iter_uids(stop_event):
                    service.handle_uid(uid)
            except ReaderError as exc:
                logger.error("Reader error: %s", exc)
                stop_event.wait(settings.retry_interval)
            except Exception:
                logger.exception("Unexpected card processing error.")
                stop_event.wait(settings.retry_interval)
    finally:
        stop_event.set()
        for worker in workers:
            worker.join(timeout=5)
        logger.info("Attendance client stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
