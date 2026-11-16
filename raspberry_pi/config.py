import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    api_key: str
    terminal_id: str
    app_version: str
    reader_device: str
    sqlite_path: Path
    retry_interval: float
    heartbeat_interval: float
    request_timeout: float
    reader_backend: str = "pcsc"
    pcsc_reader_name: str = ""
    enrollment_poll_interval: float = 1.0

    @classmethod
    def from_environment(cls, env_file=None):
        load_dotenv(env_file)
        return cls(
            api_base_url=_required("KINTAI_API_BASE_URL").rstrip("/"),
            api_key=_required("KINTAI_API_KEY"),
            terminal_id=_required("TERMINAL_ID"),
            app_version=os.getenv("APP_VERSION", "0.1.0"),
            reader_backend=os.getenv("READER_BACKEND", "pcsc").strip().lower(),
            pcsc_reader_name=os.getenv("PCSC_READER_NAME", "").strip(),
            reader_device=os.getenv("READER_DEVICE", "/dev/input/event3"),
            sqlite_path=Path(
                os.getenv(
                    "SQLITE_PATH",
                    "/var/lib/mch-kintai/kintai.db",
                )
            ),
            retry_interval=_positive_float("RETRY_INTERVAL", "10"),
            heartbeat_interval=_positive_float(
                "HEARTBEAT_INTERVAL", "300"
            ),
            request_timeout=_positive_float("REQUEST_TIMEOUT", "5"),
            enrollment_poll_interval=_positive_float(
                "ENROLLMENT_POLL_INTERVAL", "1"
            ),
        )


def _required(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"必須環境変数が設定されていません: {name}")
    return value


def _positive_float(name, default):
    try:
        value = float(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name}は数値で指定してください。") from exc
    if value <= 0:
        raise ValueError(f"{name}は0より大きい値を指定してください。")
    return value
