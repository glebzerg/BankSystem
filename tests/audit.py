"""Проверки журнала аудита: python -B -m unittest tests.audit."""

import json
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bank.audit import AuditLevel, AuditLog, AuditRecord
from bank.exceptions import AuditWriteError, InvalidOperationError


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

    def test_explicit_event_time_overrides_clock_and_is_validated(self):
        event_time = self.current - timedelta(hours=1)
        record = self.log.add(AuditLevel.INFO, "event", "Событие", occurred_at=event_time)
        self.assertEqual(record.occurred_at, event_time)
        with self.assertRaises(InvalidOperationError):
            self.log.add(AuditLevel.INFO, "event", "Событие", occurred_at=datetime(2026, 11, 17))
        self.assertEqual(self.log.records, (record,))

    def test_filters_work_separately_and_together(self):
        first = self.log.add(AuditLevel.INFO, "completed", "Успех", client_id="c1",
                             transaction_id="t1")
        self.current += timedelta(hours=1)
        second = self.log.add(AuditLevel.ERROR, "failed", "Отказ", client_id="c2",
                              transaction_id="t2")
        self.current += timedelta(hours=1)
        third = self.log.add(AuditLevel.ERROR, "failed", "Отказ", client_id="c1",
                             transaction_id="t3")
        self.assertEqual(self.log.filter(), (first, second, third))
        self.assertEqual(self.log.filter(level=AuditLevel.ERROR), (second, third))
        self.assertEqual(self.log.filter(client_id="c1"), (first, third))
        self.assertEqual(self.log.filter(transaction_id="t2"), (second,))
        self.assertEqual(self.log.filter(level=AuditLevel.ERROR, client_id="c1",
                                         transaction_id="t3"), (third,))
        self.assertEqual(self.log.filter(level=AuditLevel.INFO, client_id="c2"), ())
        snapshot = self.log.filter()
        self.log.add(AuditLevel.INFO, "event", "Событие")
        self.assertEqual(snapshot, (first, second, third))

    def test_period_is_inclusive_and_compares_timezones(self):
        first = self.log.add(AuditLevel.INFO, "event", "Первое")
        self.current += timedelta(hours=1)
        second = self.log.add(AuditLevel.INFO, "event", "Второе")
        moscow = timezone(timedelta(hours=3))
        boundary = first.occurred_at.astimezone(moscow)
        self.assertEqual(self.log.filter(start_at=boundary, end_at=boundary), (first,))
        self.assertEqual(self.log.filter(start_at=second.occurred_at), (second,))
        self.assertEqual(self.log.filter(end_at=boundary), (first,))

    def test_invalid_filters_are_rejected_even_for_empty_log(self):
        for kwargs in (
            {"level": "error"}, {"start_at": datetime(2026, 11, 17)},
            {"end_at": "today"}, {"client_id": " "}, {"transaction_id": 1},
            {"start_at": self.current, "end_at": self.current - timedelta(seconds=1)},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(InvalidOperationError):
                    self.log.filter(**kwargs)

    def test_jsonl_appends_without_duplicates_or_overwriting(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            log = AuditLog(now=lambda: self.current, file_path=path)
            self.assertFalse(path.exists())
            first = log.add(AuditLevel.WARNING, "event", "Первая строка\nВторая",
                            client_id="c1", transaction_id="t1")
            log.add(AuditLevel.ERROR, "failed", "Ошибка")
            # Чтение и фильтрация не должны повторно сохранять события.
            log.records
            log.filter(level=AuditLevel.ERROR)
            # Новый экземпляр дописывает файл, но не загружает старые записи.
            other = AuditLog(now=lambda: self.current, file_path=str(path))
            self.assertEqual(other.records, ())
            other.add(AuditLevel.INFO, "started", "Новый запуск")
            content = path.read_text(encoding="utf-8")
            lines = content.splitlines()
            self.assertEqual(len(lines), 3)
            self.assertIn("Первая", content)
            self.assertEqual(json.loads(lines[0]), {
                "occurred_at": first.occurred_at.isoformat(),
                "level": "warning", "event_type": "event",
                "description": "Первая строка\nВторая",
                "client_id": "c1", "transaction_id": "t1",
            })
            self.assertIsNone(json.loads(lines[1])["client_id"])
            self.assertEqual(json.loads(lines[2])["event_type"], "started")

    def test_file_error_keeps_memory_unchanged_and_can_be_retried(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            log = AuditLog(file_path=path)
            first = log.add(AuditLevel.INFO, "event", "Первое")
            with patch.object(Path, "open", side_effect=PermissionError("Нет доступа")):
                with self.assertRaises(AuditWriteError) as caught:
                    log.add(AuditLevel.ERROR, "failed", "Ошибка")
            self.assertIsInstance(caught.exception.__cause__, PermissionError)
            self.assertEqual(log.records, (first,))
            log.add(AuditLevel.ERROR, "failed", "Ошибка")
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 2)

    def test_missing_parent_raises_audit_error(self):
        with TemporaryDirectory() as directory:
            log = AuditLog(file_path=Path(directory) / "missing" / "audit.jsonl")
            with self.assertRaises(AuditWriteError):
                log.add(AuditLevel.INFO, "event", "Событие")
            self.assertEqual(log.records, ())

    def test_invalid_file_paths_are_rejected(self):
        for value in ("", "  ", 123):
            with self.subTest(value=value):
                with self.assertRaises(InvalidOperationError):
                    AuditLog(file_path=value)


if __name__ == "__main__":
    unittest.main()
