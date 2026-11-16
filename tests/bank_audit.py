"""Проверки подключения аудита к событиям безопасности банка."""

import json
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bank.audit import AuditLevel, AuditLog
from bank.bank import Bank
from bank.clients import Client, ClientStatus
from bank.exceptions import (
    AuditWriteError, AuthenticationError, ClientBlockedError, InvalidOperationError,
)


class BankAuditTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        # Проверяем аудит, а не стойкость хеширования: ускоряем подготовку клиентов.
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.log = AuditLog()
        self.bank = Bank(now=lambda: self.current, audit_log=self.log)
        self.client = Client("Иван", date(2000, 1, 1), {"phone": "+79991234567"})
        self.bank.add_client(self.client, "correct-password")
        self.token = self.bank.authenticate_client(self.client.client_id, "correct-password")

    def test_failed_logins_and_single_block_event_without_credentials(self):
        self.assertEqual(self.log.records, ())
        for _ in range(2):
            with self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(self.client.client_id, "secret-wrong-password")
        self.assertEqual(self.bank.get_suspicious_events(self.token), [])
        with self.assertRaises(ClientBlockedError):
            self.bank.authenticate_client(self.client.client_id, "secret-wrong-password")
        with self.assertRaises(ClientBlockedError):
            self.bank.authenticate_client(self.client.client_id, "correct-password")
        self.assertEqual([record.event_type for record in self.log.records], [
            "login_failed", "login_failed", "login_failed", "login_blocked", "login_failed",
        ])
        self.assertEqual(len(self.bank._suspicious_events), 1)
        for record in self.log.records:
            self.assertEqual(record.client_id, self.client.client_id)
            self.assertEqual(record.occurred_at, self.current)
            self.assertIs(record.level, AuditLevel.ERROR if record.event_type == "login_blocked"
                          else AuditLevel.WARNING)
        text = repr(self.log.records)
        for secret in ("correct-password", "secret-wrong-password", self.token):
            self.assertNotIn(secret, text)
        with self.assertRaises(AuthenticationError):
            self.bank.get_suspicious_events(self.token)

    def test_invalid_and_unknown_credentials_do_not_store_unverified_ids(self):
        for identifier, password in (([], "password"), ("", "password"),
                                     ("unverified-id", "password"),
                                     (self.client.client_id, None)):
            with self.subTest(identifier=identifier):
                with self.assertRaises(AuthenticationError):
                    self.bank.authenticate_client(identifier, password)
        self.assertEqual(len(self.log.records), 4)
        self.assertTrue(all(record.client_id is None for record in self.log.records))
        self.assertNotIn("unverified-id", repr(self.log.records))
        self.assertIs(self.client.status, ClientStatus.ACTIVE)

    def test_foreign_access_and_night_attempt_preserve_legacy_events(self):
        account = self.bank.open_account(self.token, "standard")
        other = Client("Анна", date(2000, 1, 1), {"phone": "+79991234568"})
        self.bank.add_client(other, "other-password")
        other_token = self.bank.authenticate_client(other.client_id, "other-password")
        with self.assertRaises(InvalidOperationError):
            self.bank.freeze_account(other_token, account["account_id"])
        self.assertEqual(self.log.records[-1].event_type, "foreign_account_access")
        self.assertEqual(self.log.records[-1].client_id, other.client_id)
        self.assertEqual(self.bank.get_suspicious_events(self.token), [])
        self.current = datetime(2026, 11, 17, 1, tzinfo=Bank.BANK_TIMEZONE)
        with self.assertRaises(InvalidOperationError):
            self.bank.deposit(self.token, account["account_id"], Decimal("10"))
        record = self.log.records[-1]
        legacy = self.bank.get_suspicious_events(self.token)[0]
        self.assertEqual(record.event_type, "night_operation")
        self.assertIs(record.level, AuditLevel.WARNING)
        self.assertEqual(record.occurred_at, legacy["time"])
        self.assertEqual(record.description, legacy["description"])
        self.assertEqual(record.client_id, legacy["client_id"])
        self.assertEqual(self.bank._get_account(account["account_id"]).balance, Decimal("0"))
        legacy["description"] = "Изменено"
        self.assertNotEqual(record.description, "Изменено")

    def test_anonymous_night_attempt_is_logged(self):
        self.current = datetime(2026, 11, 17, 1, tzinfo=Bank.BANK_TIMEZONE)
        with self.assertRaises(InvalidOperationError):
            self.bank.add_client(self.client, "correct-password")
        self.assertIsNone(self.log.records[-1].client_id)
        self.assertEqual(self.log.records[-1].event_type, "night_operation")

    def test_injected_file_log_uses_bank_time(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            # Источник времени журнала не должен заменять время события банка.
            def unexpected_clock():
                self.fail("Банк должен передавать время события явно")

            log = AuditLog(file_path=path, now=unexpected_clock)
            bank = Bank(now=lambda: self.current, audit_log=log)
            with self.assertRaises(AuthenticationError):
                bank.authenticate_client("unknown", "private-password")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["occurred_at"], self.current.isoformat())
            self.assertEqual(saved["event_type"], "login_failed")
            self.assertNotIn("private-password", path.read_text(encoding="utf-8"))

    def test_file_failure_does_not_prevent_blocking_or_session_revocation(self):
        with TemporaryDirectory() as directory:
            bank = Bank(now=lambda: self.current,
                        audit_log=AuditLog(file_path=Path(directory) / "audit.jsonl"))
            client = Client("Олег", date(2000, 1, 1), {"phone": "+79991234569"})
            bank.add_client(client, "password")
            token = bank.authenticate_client(client.client_id, "password")
            for _ in range(2):
                with self.assertRaises(AuthenticationError):
                    bank.authenticate_client(client.client_id, "wrong")
            with patch.object(Path, "open", side_effect=PermissionError("Нет доступа")):
                with self.assertRaises(AuditWriteError):
                    bank.authenticate_client(client.client_id, "wrong")
            self.assertIs(client.status, ClientStatus.BLOCKED)
            self.assertNotIn(token, bank._sessions)

    def test_default_log_and_invalid_injection(self):
        bank = Bank(now=lambda: self.current)
        with self.assertRaises(AuthenticationError):
            bank.authenticate_client("unknown", "password")
        self.assertEqual(len(bank._audit_log.records), 1)
        self.assertEqual(bank._audit_log.records[0].occurred_at, self.current)
        with self.assertRaises(InvalidOperationError):
            Bank(audit_log=[])


if __name__ == "__main__":
    unittest.main()
