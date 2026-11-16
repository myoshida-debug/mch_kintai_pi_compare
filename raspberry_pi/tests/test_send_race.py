import tempfile
import threading
import unittest
from pathlib import Path

from raspberry_pi.api_client import ApiResult
from raspberry_pi.database import PunchDatabase
from raspberry_pi.service import AttendanceService


class BlockingApiClient:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.sent = []
        self.lock = threading.Lock()

    def send_punch(self, record):
        with self.lock:
            self.sent.append(record.event_uuid)
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("Test API was not released")
        return ApiResult(True, 201)


class SendRaceTests(unittest.TestCase):
    def test_normal_send_and_retry_do_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = PunchDatabase(Path(tmp) / "test.db")
            database.initialize()
            api = BlockingApiClient()

            service = AttendanceService(
                database=database,
                api_client=api,
                terminal_id="TIME-01",
                app_version="test",
            )

            errors = []

            def punch():
                try:
                    service.handle_uid("04A23F9182")
                except Exception as exc:
                    errors.append(exc)

            def retry():
                try:
                    service.resend_pending()
                except Exception as exc:
                    errors.append(exc)

            t1 = threading.Thread(target=punch)
            t1.start()

            self.assertTrue(
                api.started.wait(timeout=5),
                "Normal send did not start",
            )

            t2 = threading.Thread(target=retry)
            t2.start()

            try:
                api.release.set()
                t1.join(timeout=5)
                t2.join(timeout=5)

                self.assertFalse(t1.is_alive())
                self.assertFalse(t2.is_alive())
                self.assertEqual(errors, [])
                self.assertEqual(len(api.sent), 1)
                self.assertEqual(database.unsent_count(), 0)
            finally:
                api.release.set()
                t1.join(timeout=5)
                t2.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
