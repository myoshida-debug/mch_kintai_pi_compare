import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from raspberry_pi.database import PunchDatabase


class PunchDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_directory.name) / "kintai.db"
        self.database = PunchDatabase(self.db_path)
        self.database.initialize()

    def tearDown(self):
        self.temp_directory.cleanup()

    def save_punch(self, event_uuid=None):
        return self.database.save_punch(
            event_uuid=event_uuid or str(uuid.uuid4()),
            card_uid="04A23F9182",
            punched_at="2026-10-01T08:27:15+09:00",
            terminal_id="TIME-01",
        )

    def test_database_initialization_creates_schema(self):
        self.assertTrue(self.db_path.exists())
        with sqlite3.connect(self.db_path) as connection:
            table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'punches'
                """
            ).fetchone()
        self.assertEqual(table[0], "punches")

    def test_save_punch(self):
        record = self.save_punch()

        self.assertEqual(record.card_uid, "04A23F9182")
        self.assertFalse(record.sent)

    def test_event_uuid_is_unique(self):
        event_uuid = str(uuid.uuid4())
        self.save_punch(event_uuid)

        with self.assertRaises(sqlite3.IntegrityError):
            self.save_punch(event_uuid)

    def test_unsent_count(self):
        self.save_punch()
        self.save_punch()

        self.assertEqual(self.database.unsent_count(), 2)

    def test_get_unsent_returns_oldest_first(self):
        first = self.save_punch()
        second = self.save_punch()

        records = self.database.get_unsent()

        self.assertEqual([record.id for record in records], [first.id, second.id])

    def test_mark_sent(self):
        record = self.save_punch()

        self.database.mark_sent(record.id)
        updated = self.database.get(record.id)

        self.assertTrue(updated.sent)
        self.assertIsNotNone(updated.sent_at)
        self.assertIsNone(updated.last_error)

    def test_mark_failed_increments_retry_count(self):
        record = self.save_punch()

        self.database.mark_failed(record.id, "connection_error")
        updated = self.database.get(record.id)

        self.assertFalse(updated.sent)
        self.assertEqual(updated.retry_count, 1)
        self.assertEqual(updated.last_error, "connection_error")

    def test_data_survives_database_reopen(self):
        record = self.save_punch()

        reopened = PunchDatabase(self.db_path)
        reopened.initialize()

        self.assertEqual(reopened.get(record.id).event_uuid, record.event_uuid)
