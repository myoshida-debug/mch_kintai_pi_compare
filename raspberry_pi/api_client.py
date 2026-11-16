from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class ApiResult:
    success: bool
    status_code: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class EnrollmentResult:
    success: bool
    session_uuid: str | None = None
    error: str | None = None


class KintaiApiClient:
    def __init__(
        self,
        base_url,
        api_key,
        timeout=5,
        session=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._api_key = api_key
        self._session = session or requests.Session()

    def send_punch(self, record):
        response = self._post(
            "/api/punch/",
            {
                "event_uuid": record.event_uuid,
                "card_uid": record.card_uid,
                "punched_at": record.punched_at,
                "terminal_id": record.terminal_id,
            },
        )
        if isinstance(response, ApiResult):
            return response

        try:
            status = response.json().get("status")
        except (ValueError, AttributeError):
            return ApiResult(False, response.status_code, "invalid_response")

        success = (
            response.status_code == 201 and status == "created"
        ) or (
            response.status_code == 200
            and status == "already_registered"
        )
        if success:
            return ApiResult(True, response.status_code)
        return ApiResult(
            False,
            response.status_code,
            f"http_{response.status_code}",
        )

    def send_heartbeat(self, terminal_id, app_version, unsent_count):
        response = self._post(
            "/api/heartbeat/",
            {
                "terminal_id": terminal_id,
                "app_version": app_version,
                "unsent_count": unsent_count,
            },
        )
        if isinstance(response, ApiResult):
            return response

        try:
            status = response.json().get("status")
        except (ValueError, AttributeError):
            return ApiResult(False, response.status_code, "invalid_response")

        if response.status_code == 200 and status == "ok":
            return ApiResult(True, response.status_code)
        return ApiResult(
            False,
            response.status_code,
            f"http_{response.status_code}",
        )

    def get_pending_enrollment(self, terminal_id):
        try:
            response = self._session.get(
                f"{self.base_url}/api/enrollment/pending/",
                headers={"X-API-Key": self._api_key, "X-Terminal-ID": terminal_id},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return EnrollmentResult(False, error="request_error")
        try:
            payload = response.json()
        except (ValueError, AttributeError):
            return EnrollmentResult(False, error="invalid_response")
        if response.status_code == 200 and payload.get("status") == "none":
            return EnrollmentResult(True)
        if response.status_code == 200 and payload.get("status") == "waiting":
            return EnrollmentResult(True, payload.get("session_uuid"))
        return EnrollmentResult(False, error=f"http_{response.status_code}")

    def capture_enrollment(self, terminal_id, session_uuid, card_uid):
        try:
            response = self._session.post(
                f"{self.base_url}/api/enrollment/capture/",
                json={"session_uuid": session_uuid, "card_uid": card_uid},
                headers={"X-API-Key": self._api_key, "X-Terminal-ID": terminal_id},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return ApiResult(False, error="request_error")
        try:
            status = response.json().get("status")
        except (ValueError, AttributeError):
            return ApiResult(False, response.status_code, "invalid_response")
        if response.status_code == 200 and status == "captured":
            return ApiResult(True, response.status_code)
        return ApiResult(False, response.status_code, f"http_{response.status_code}")

    def _post(self, endpoint, payload):
        try:
            return self._session.post(
                f"{self.base_url}{endpoint}",
                json=payload,
                headers={"X-API-Key": self._api_key},
                timeout=self.timeout,
            )
        except requests.Timeout:
            return ApiResult(False, error="request_timeout")
        except requests.ConnectionError:
            return ApiResult(False, error="connection_error")
        except requests.RequestException:
            return ApiResult(False, error="request_error")
