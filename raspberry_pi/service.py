import logging
import uuid
import threading
from datetime import datetime, timedelta, timezone


TOKYO = timezone(timedelta(hours=9), name="Asia/Tokyo")
logger = logging.getLogger(__name__)


class AttendanceService:
    def __init__(
        self,
        database,
        api_client,
        terminal_id,
        app_version,
        retry_interval=10,
        heartbeat_interval=300,
        retry_batch_size=100,
        now=None,
        enrollment_poll_interval=1,
    ):
        self.database = database
        self.api_client = api_client
        self.terminal_id = terminal_id
        self.app_version = app_version
        self.retry_interval = retry_interval
        self.heartbeat_interval = heartbeat_interval
        self.retry_batch_size = retry_batch_size
        self._now = now or (lambda: datetime.now(TOKYO))
        self.enrollment_poll_interval = enrollment_poll_interval
        self._enrollment_session_uuid = None
        self._enrollment_lock = threading.Lock()
        self._send_lock = threading.Lock()

    def handle_uid(self, uid):
        normalized_uid = str(uid).strip().upper()
        if not normalized_uid:
            return None

        with self._enrollment_lock:
            enrollment_uuid = self._enrollment_session_uuid
        if enrollment_uuid:
            result = self.api_client.capture_enrollment(
                self.terminal_id, enrollment_uuid, normalized_uid
            )
            if result.success:
                with self._enrollment_lock:
                    if self._enrollment_session_uuid == enrollment_uuid:
                        self._enrollment_session_uuid = None
                logger.info("Card enrollment captured.")
            else:
                logger.warning("Card enrollment capture failed: %s", result.error)
            return result

        event_uuid = str(uuid.uuid4())
        punched_at = self._now().astimezone(TOKYO).isoformat()

        # The local write must complete before any network operation starts.
        record = self.database.save_punch(
            event_uuid=event_uuid,
            card_uid=normalized_uid,
            punched_at=punched_at,
            terminal_id=self.terminal_id,
        )
        logger.info("Punch stored locally: event_uuid=%s", event_uuid)
        with self._send_lock:
            self._send_record(record, retry=False)
        return record

    def resend_pending(self):
        with self._send_lock:
            records = self.database.get_unsent(limit=self.retry_batch_size)
            for record in records:
                self._send_record(record, retry=True)
            return len(records)

    def send_heartbeat(self):
        unsent_count = self.database.unsent_count()
        result = self.api_client.send_heartbeat(
            terminal_id=self.terminal_id,
            app_version=self.app_version,
            unsent_count=unsent_count,
        )
        if result.success:
            logger.debug("Heartbeat sent successfully.")
        else:
            logger.warning("Heartbeat failed: %s", result.error)
        return result

    def run_retry_loop(self, stop_event):
        while not stop_event.is_set():
            try:
                self.resend_pending()
            except Exception:
                logger.exception("Unexpected error in retry worker.")
            stop_event.wait(self.retry_interval)

    def run_heartbeat_loop(self, stop_event):
        while not stop_event.is_set():
            try:
                self.send_heartbeat()
            except Exception:
                logger.exception("Unexpected error in heartbeat worker.")
            stop_event.wait(self.heartbeat_interval)

    def poll_enrollment(self):
        result = self.api_client.get_pending_enrollment(self.terminal_id)
        if result.success:
            with self._enrollment_lock:
                self._enrollment_session_uuid = result.session_uuid
        return result

    def run_enrollment_loop(self, stop_event):
        while not stop_event.is_set():
            try:
                self.poll_enrollment()
            except Exception:
                logger.exception("Unexpected error in enrollment worker.")
            stop_event.wait(self.enrollment_poll_interval)

    def _send_record(self, record, retry):
        result = self.api_client.send_punch(record)
        if result.success:
            self.database.mark_sent(record.id)
            message = "Punch resend succeeded." if retry else "Punch sent."
            logger.info("%s event_uuid=%s", message, record.event_uuid)
            return True

        self.database.mark_failed(record.id, result.error or "send_failed")
        logger.warning(
            "Punch send failed: event_uuid=%s error=%s",
            record.event_uuid,
            result.error or "send_failed",
        )
        return False
