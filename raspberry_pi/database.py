import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


TOKYO = timezone(timedelta(hours=9), name="Asia/Tokyo")


@dataclass(frozen=True)
class PunchRecord:
    id: int
    event_uuid: str
    card_uid: str
    punched_at: str
    terminal_id: str
    sent: bool
    created_at: str
    sent_at: str | None
    retry_count: int
    last_error: str | None


class PunchDatabase:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS punches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_uuid TEXT NOT NULL UNIQUE,
                    card_uid TEXT NOT NULL,
                    punched_at TEXT NOT NULL,
                    terminal_id TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0 CHECK (sent IN (0, 1)),
                    created_at TEXT NOT NULL,
                    sent_at TEXT,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS punches_unsent_order_idx
                ON punches (sent, id)
                """
            )

    def save_punch(self, event_uuid, card_uid, punched_at, terminal_id):
        created_at = _now_iso()
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                INSERT INTO punches (
                    event_uuid, card_uid, punched_at, terminal_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_uuid,
                    card_uid,
                    punched_at,
                    terminal_id,
                    created_at,
                ),
            )
            record_id = cursor.lastrowid
        return self.get(record_id)

    def get(self, record_id):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM punches WHERE id = ?", (record_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def unsent_count(self):
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM punches WHERE sent = 0"
            ).fetchone()
        return row["count"]

    def get_unsent(self, limit=100):
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM punches
                WHERE sent = 0
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def mark_sent(self, record_id):
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE punches
                SET sent = 1, sent_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (_now_iso(), record_id),
            )

    def mark_failed(self, record_id, error):
        safe_error = str(error)[:1000]
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE punches
                SET sent = 0,
                    retry_count = retry_count + 1,
                    last_error = ?
                WHERE id = ?
                """,
                (safe_error, record_id),
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection


def _row_to_record(row):
    return PunchRecord(
        id=row["id"],
        event_uuid=row["event_uuid"],
        card_uid=row["card_uid"],
        punched_at=row["punched_at"],
        terminal_id=row["terminal_id"],
        sent=bool(row["sent"]),
        created_at=row["created_at"],
        sent_at=row["sent_at"],
        retry_count=row["retry_count"],
        last_error=row["last_error"],
    )


def _now_iso():
    return datetime.now(TOKYO).isoformat()
