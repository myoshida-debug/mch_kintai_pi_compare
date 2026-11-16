import secrets
import unittest
from unittest.mock import Mock

import requests

from raspberry_pi.api_client import KintaiApiClient
from raspberry_pi.database import PunchRecord


class KintaiApiClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = KintaiApiClient(
            base_url="https://kintai.example.test",
            api_key=secrets.token_urlsafe(48),
            timeout=5,
            session=self.session,
        )
        self.record = PunchRecord(
            id=1,
            event_uuid="25a8ee88-f179-43fb-8fb4-fb665e32e991",
            card_uid="04A23F9182",
            punched_at="2026-10-01T08:27:15+09:00",
            terminal_id="TIME-01",
            sent=False,
            created_at="2026-10-01T08:27:15+09:00",
            sent_at=None,
            retry_count=0,
            last_error=None,
        )

    def response(self, status_code, payload):
        response = Mock(status_code=status_code)
        response.json.return_value = payload
        return response

    def test_punch_201_is_success(self):
        self.session.post.return_value = self.response(
            201, {"status": "created"}
        )

        result = self.client.send_punch(self.record)

        self.assertTrue(result.success)

    def test_punch_200_already_registered_is_success(self):
        self.session.post.return_value = self.response(
            200, {"status": "already_registered"}
        )

        result = self.client.send_punch(self.record)

        self.assertTrue(result.success)

    def test_punch_401_is_failure(self):
        self.session.post.return_value = self.response(
            401, {"error": "authentication_failed"}
        )

        result = self.client.send_punch(self.record)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "http_401")

    def test_punch_500_is_failure(self):
        self.session.post.return_value = self.response(
            500, {"error": "internal_error"}
        )

        result = self.client.send_punch(self.record)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "http_500")

    def test_timeout_is_failure(self):
        self.session.post.side_effect = requests.Timeout()

        result = self.client.send_punch(self.record)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "request_timeout")

    def test_heartbeat_200_is_success(self):
        self.session.post.return_value = self.response(200, {"status": "ok"})

        result = self.client.send_heartbeat("TIME-01", "0.1.0", 3)

        self.assertTrue(result.success)

    def test_heartbeat_failure_is_returned_without_raising(self):
        self.session.post.side_effect = requests.ConnectionError()

        result = self.client.send_heartbeat("TIME-01", "0.1.0", 3)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "connection_error")

    def test_pending_enrollment_returns_session(self):
        self.session.get.return_value = self.response(
            200, {"status": "waiting", "session_uuid": "session-1"}
        )

        result = self.client.get_pending_enrollment("TIME-01")

        self.assertTrue(result.success)
        self.assertEqual(result.session_uuid, "session-1")
        headers = self.session.get.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Terminal-ID"], "TIME-01")

    def test_no_pending_enrollment_returns_none(self):
        self.session.get.return_value = self.response(200, {"status": "none"})

        result = self.client.get_pending_enrollment("TIME-01")

        self.assertTrue(result.success)
        self.assertIsNone(result.session_uuid)

    def test_capture_enrollment_success(self):
        self.session.post.return_value = self.response(200, {"status": "captured"})

        result = self.client.capture_enrollment("TIME-01", "session-1", "AB12")

        self.assertTrue(result.success)
        call = self.session.post.call_args
        self.assertEqual(call.kwargs["json"]["card_uid"], "AB12")
        self.assertEqual(call.kwargs["headers"]["X-Terminal-ID"], "TIME-01")

    def test_capture_enrollment_failure(self):
        self.session.post.return_value = self.response(409, {"error": "session_expired"})

        result = self.client.capture_enrollment("TIME-01", "session-1", "AB12")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "http_409")
