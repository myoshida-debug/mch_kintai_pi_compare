import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from raspberry_pi.api_client import ApiResult, EnrollmentResult
from raspberry_pi.database import PunchDatabase, PunchRecord
from raspberry_pi.service import AttendanceService


TOKYO = timezone(timedelta(hours=9), name="Asia/Tokyo")


class FakeApiClient:
    def __init__(self, punch_result=None, heartbeat_result=None):
        self.punch_result = punch_result or ApiResult(True, 201)
        self.heartbeat_result = heartbeat_result or ApiResult(True, 200)
        self.punches = []
        self.heartbeats = []
        self.enrollment_result = EnrollmentResult(True)
        self.captures = []

    def send_punch(self, record):
        self.punches.append(record)
        return self.punch_result

    def send_heartbeat(self, terminal_id, app_version, unsent_count):
        self.heartbeats.append((terminal_id, app_version, unsent_count))
        return self.heartbeat_result

    def get_pending_enrollment(self, terminal_id):
        return self.enrollment_result

    def capture_enrollment(self, terminal_id, session_uuid, card_uid):
        self.captures.append((terminal_id, session_uuid, card_uid))
        return ApiResult(True, 200)


class AttendanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        path = Path(self.temp_directory.name) / "kintai.db"
        self.database = PunchDatabase(path)
        self.database.initialize()
        self.api_client = FakeApiClient()
        self.service = AttendanceService(
            database=self.database,
            api_client=self.api_client,
            terminal_id="TIME-01",
            app_version="0.1.0",
            now=lambda: datetime(2026, 10, 1, 8, 27, 15, tzinfo=TOKYO),
        )

    def tearDown(self):
        self.temp_directory.cleanup()

    def save_unsent(self):
        return self.database.save_punch(
            event_uuid="25a8ee88-f179-43fb-8fb4-fb665e32e991",
            card_uid="04A23F9182",
            punched_at="2026-10-01T08:27:15+09:00",
            terminal_id="TIME-01",
        )

    def test_uid_is_saved_before_network_send(self):
        database = TimelineDatabase()
        api_client = TimelineApiClient(database.timeline)
        service = AttendanceService(
            database=database,
            api_client=api_client,
            terminal_id="TIME-01",
            app_version="0.1.0",
            now=lambda: datetime(2026, 10, 1, 8, 27, 15, tzinfo=TOKYO),
        )

        service.handle_uid("04a23f9182")

        self.assertEqual(database.timeline[:2], ["save", "send"])

    def test_api_send_receives_saved_record(self):
        record = self.service.handle_uid("04A23F9182")

        self.assertEqual(len(self.api_client.punches), 1)
        self.assertEqual(self.api_client.punches[0].id, record.id)

    def test_database_failure_prevents_network_send(self):
        database = Mock()
        database.save_punch.side_effect = OSError("simulated database error")
        api_client = FakeApiClient()
        service = AttendanceService(
            database=database,
            api_client=api_client,
            terminal_id="TIME-01",
            app_version="0.1.0",
            now=lambda: datetime(2026, 10, 1, 8, 27, 15, tzinfo=TOKYO),
        )

        with self.assertRaises(OSError):
            service.handle_uid("04A23F9182")

        self.assertEqual(api_client.punches, [])

    def test_api_failure_keeps_record_unsent(self):
        self.api_client.punch_result = ApiResult(False, error="connection_error")

        record = self.service.handle_uid("04A23F9182")
        stored = self.database.get(record.id)

        self.assertFalse(stored.sent)
        self.assertEqual(stored.retry_count, 1)

    def test_api_success_marks_record_sent(self):
        record = self.service.handle_uid("04A23F9182")

        self.assertTrue(self.database.get(record.id).sent)

    def test_pending_record_resend_success(self):
        record = self.save_unsent()

        self.service.resend_pending()

        self.assertTrue(self.database.get(record.id).sent)

    def test_pending_record_resend_failure(self):
        record = self.save_unsent()
        self.api_client.punch_result = ApiResult(False, error="http_500")

        self.service.resend_pending()
        stored = self.database.get(record.id)

        self.assertFalse(stored.sent)
        self.assertEqual(stored.retry_count, 1)
        self.assertEqual(stored.last_error, "http_500")

    def test_heartbeat_contains_current_unsent_count(self):
        self.save_unsent()

        result = self.service.send_heartbeat()

        self.assertTrue(result.success)
        self.assertEqual(
            self.api_client.heartbeats,
            [("TIME-01", "0.1.0", 1)],
        )

    def test_no_enrollment_uses_normal_punch(self):
        self.service.poll_enrollment()
        self.service.handle_uid("AB12")
        self.assertEqual(len(self.api_client.punches), 1)
        self.assertEqual(self.api_client.captures, [])

    def test_enrollment_captures_without_sqlite_or_punch_api(self):
        self.api_client.enrollment_result = EnrollmentResult(True, "session-1")
        self.service.poll_enrollment()
        before = self.database.unsent_count()
        self.service.handle_uid(" ab12 ")
        self.assertEqual(self.database.unsent_count(), before)
        self.assertEqual(self.api_client.punches, [])
        self.assertEqual(self.api_client.captures, [("TIME-01", "session-1", "AB12")])

    def test_capture_failure_does_not_create_punch(self):
        self.api_client.enrollment_result = EnrollmentResult(True, "session-1")
        self.api_client.capture_enrollment = Mock(return_value=ApiResult(False, error="http_500"))
        self.service.poll_enrollment()
        result = self.service.handle_uid("AB12")
        self.assertFalse(result.success)
        self.assertEqual(self.database.unsent_count(), 0)
        self.assertEqual(self.api_client.punches, [])

    def test_expired_enrollment_poll_returns_to_normal_punch(self):
        self.api_client.enrollment_result = EnrollmentResult(True, "session-1")
        self.service.poll_enrollment()
        self.api_client.enrollment_result = EnrollmentResult(True)
        self.service.poll_enrollment()
        self.service.handle_uid("AB12")
        self.assertEqual(len(self.api_client.punches), 1)


class TimelineDatabase:
    def __init__(self):
        self.timeline = []

    def save_punch(self, event_uuid, card_uid, punched_at, terminal_id):
        self.timeline.append("save")
        return PunchRecord(
            id=1,
            event_uuid=event_uuid,
            card_uid=card_uid,
            punched_at=punched_at,
            terminal_id=terminal_id,
            sent=False,
            created_at=punched_at,
            sent_at=None,
            retry_count=0,
            last_error=None,
        )

    def mark_sent(self, record_id):
        self.timeline.append("mark_sent")


class TimelineApiClient:
    def __init__(self, timeline):
        self.timeline = timeline

    def send_punch(self, record):
        self.timeline.append("send")
        return ApiResult(True, 201)
