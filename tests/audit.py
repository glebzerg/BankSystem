"""Проверки журнала аудита: python -B -m unittest tests.audit."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

from bank.audit import AuditLevel, AuditLog, AuditRecord
from bank.exceptions import InvalidOperationError


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.log = AuditLog(now=lambda: self.current)

    def test_add_preserves_event_data_and_order(self):
        first = self.log.add(AuditLevel.INFO, "transaction_completed", "Перевод выполнен",
                             client_id="client-1", transaction_id="tx-1")
        self.current = datetime(2026, 11, 17, 13, tzinfo=timezone.utc)
        second = self.log.add(AuditLevel.WARNING, "night_operation", "Ночная операция")
        self.assertEqual(self.log.records, (first, second))
        self.assertEqual(first.occurred_at.hour, 12)
        self.assertEqual(second.occurred_at, self.current)
        self.assertIs(first.level, AuditLevel.INFO)
        self.assertEqual(first.event_type, "transaction_completed")
        self.assertEqual(first.description, "Перевод выполнен")
        self.assertEqual(first.client_id, "client-1")
        self.assertEqual(first.transaction_id, "tx-1")
        self.assertIsNone(second.client_id)
        self.assertIsNone(second.transaction_id)

    def test_snapshot_and_record_cannot_change_log(self):
        record = self.log.add(AuditLevel.ERROR, "transfer_failed", "Ошибка перевода")
        snapshot = self.log.records
        with self.assertRaises(FrozenInstanceError):
            record.description = "Изменено"
        self.log.add(AuditLevel.CRITICAL, "security_failure", "Критическая ошибка")
        self.assertIsInstance(snapshot, tuple)
        self.assertEqual(snapshot, (record,))
        self.assertEqual(len(self.log.records), 2)

    def test_invalid_data_does_not_add_record(self):
        valid = dict(level=AuditLevel.INFO, event_type="event", description="Событие")
        for field, values in (
            ("level", ("info", None, 1)),
            ("event_type", ("", "  ", None, 1)),
            ("description", ("", "  ", None, 1)),
            ("client_id", ("", "  ", 1)),
            ("transaction_id", ("", "  ", 1)),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(InvalidOperationError):
                        self.log.add(**(valid | {field: value}))
                    self.assertEqual(self.log.records, ())

    def test_record_validates_time_even_when_created_directly(self):
        for value in (None, "2026-11-17", datetime(2026, 11, 17)):
            with self.subTest(value=value):
                with self.assertRaises(InvalidOperationError):
                    AuditRecord(value, AuditLevel.INFO, "event", "Событие")

    def test_invalid_clock_result_does_not_add_record(self):
        log = AuditLog(now=lambda: datetime(2026, 11, 17))
        with self.assertRaises(InvalidOperationError):
            log.add(AuditLevel.INFO, "event", "Событие")
        self.assertEqual(log.records, ())

    def test_default_clock_uses_aware_utc_time(self):
        log = AuditLog()
        before = datetime.now(timezone.utc)
        record = log.add(AuditLevel.INFO, "event", "Событие")
        after = datetime.now(timezone.utc)
        self.assertLessEqual(before, record.occurred_at)
        self.assertLessEqual(record.occurred_at, after)
        self.assertEqual(record.occurred_at.tzinfo, timezone.utc)

    def test_logs_have_independent_storage(self):
        self.log.add(AuditLevel.INFO, "event", "Событие")
        self.assertEqual(AuditLog().records, ())

    def test_invalid_clock_is_rejected(self):
        with self.assertRaises(InvalidOperationError):
            AuditLog(now=self.current)


if __name__ == "__main__":
    unittest.main()
