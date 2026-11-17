"""Статистика ошибок: повторы, окончательные отказы и блокировки."""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bank.accounts import Currency
from bank.audit import AuditLevel, AuditLog
from bank.audit_reports import AuditReports
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuthenticationError, InsufficientFundsError, InvalidOperationError, TemporaryTransactionError
from bank.transactions import ExternalRecipient, TransactionType


class ErrorStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.log = AuditLog(now=lambda: self.now)
        self.reports = AuditReports(self.log)

    def add(self, event, identifier="tx", error_type=None, client_id="c1"):
        self.log.add(AuditLevel.ERROR, event, "Описание", transaction_id=identifier,
                     client_id=client_id, error_type=error_type)

    def test_empty_statistics(self):
        self.assertEqual(self.reports.error_statistics("c1"), {
            "failed_attempts": 0, "failed_operations": 0,
            "risk_blocked_operations": 0, "by_error_type": {},
        })

    def test_retry_followed_by_success_or_cancellation_is_not_final_failure(self):
        for identifier, outcome in (("success", "transaction_completed"), ("cancelled", "transaction_cancelled")):
            self.add("transaction_retry_scheduled", identifier, "TemporaryTransactionError")
            self.add(outcome, identifier)
        stats = self.reports.error_statistics("c1")
        self.assertEqual(stats["failed_attempts"], 2)
        self.assertEqual(stats["failed_operations"], 0)
        self.assertEqual(stats["by_error_type"], {"TemporaryTransactionError": 2})

    def test_exhausted_retries_count_three_attempts_and_one_failed_operation(self):
        self.add("transaction_retry_scheduled", error_type="TemporaryTransactionError")
        self.add("transaction_retry_scheduled", error_type="TemporaryTransactionError")
        self.add("transaction_failed", error_type="TemporaryTransactionError")
        stats = self.reports.error_statistics("c1")
        self.assertEqual(stats["failed_attempts"], 3)
        self.assertEqual(stats["failed_operations"], 1)

    def test_risk_block_is_not_counted_as_second_error(self):
        self.add("risk_blocked")
        self.add("transaction_failed", error_type="HighRiskTransactionError")
        self.add("operation_failed", "direct:1", "InsufficientFundsError")
        self.add("transaction_failed", "foreign", "AccountFrozenError", client_id="c2")
        self.add("login_failed", identifier=None, error_type="AuthenticationError")
        self.assertEqual(self.reports.error_statistics("c1"), {
            "failed_attempts": 2, "failed_operations": 2, "risk_blocked_operations": 1,
            "by_error_type": {"HighRiskTransactionError": 1, "InsufficientFundsError": 1},
        })

    def test_period_filters_error_time_inclusively(self):
        boundary = self.now
        self.add("transaction_retry_scheduled", error_type="TemporaryTransactionError")
        self.now += timedelta(minutes=1)
        self.add("transaction_failed", error_type="TemporaryTransactionError")
        earlier = self.reports.error_statistics("c1", start_at=boundary, end_at=boundary)
        self.assertEqual(earlier["failed_attempts"], 1)
        self.assertEqual(earlier["failed_operations"], 0)
        later = self.reports.error_statistics("c1", start_at=self.now)
        self.assertEqual(later["failed_attempts"], 1)
        self.assertEqual(later["failed_operations"], 1)

    def test_validation_and_result_isolation(self):
        for client_id in (None, "", []):
            with self.subTest(client_id=client_id), self.assertRaises(InvalidOperationError):
                self.reports.error_statistics(client_id)
        with self.assertRaises(InvalidOperationError):
            self.reports.error_statistics("c1", start_at=datetime(2026, 11, 17))
        self.add("transaction_failed", error_type="InsufficientFundsError")
        stats = self.reports.error_statistics("c1")
        stats["by_error_type"].clear()
        self.assertEqual(self.reports.error_statistics("c1")["by_error_type"], {"InsufficientFundsError": 1})

    def test_error_type_validation_and_json(self):
        for invalid in ("", " ", 123):
            with self.subTest(invalid=invalid), self.assertRaises(InvalidOperationError):
                self.add("operation_failed", error_type=invalid)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "audit.jsonl"
            log = AuditLog(file_path=path)
            log.add(AuditLevel.ERROR, "operation_failed", "Ошибка", error_type="InsufficientFundsError")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["error_type"], "InsufficientFundsError")


class BankErrorStatisticsTests(unittest.TestCase):
    def test_real_retry_direct_error_risk_block_and_client_isolation(self):
        now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        unavailable = True

        def preflight(transaction):
            if unavailable:
                raise TemporaryTransactionError("Нет связи")

        with patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000):
            bank = Bank(now=lambda: now, external_preflight=preflight)
            tokens = []
            for name in ("Иван", "Анна"):
                client = Client(name, date(2000, 1, 1), {"phone": name})
                bank.add_client(client, "password")
                tokens.append(bank.authenticate_client(client.client_id, "password"))
            token, other_token = tokens
            account = bank.open_account(token, "standard", balance=Decimal("1000"))["account_id"]
            for amount in ("100", "100000"):
                bank.create_transaction(token, TransactionType.EXTERNAL_TRANSFER, Decimal(amount), Currency.RUB,
                                        sender_account_id=account, external_recipient=ExternalRecipient("123", "bank"))
            bank.process_transactions()
            empty = bank.open_account(token, "standard")["account_id"]
            with self.assertRaises(InsufficientFundsError):
                bank.withdraw(token, empty, Decimal("1"))
            now += timedelta(minutes=1)
            unavailable = False
            bank.process_transactions()
            self.assertEqual(bank.get_error_statistics(token), {
                "failed_attempts": 3, "failed_operations": 2, "risk_blocked_operations": 1,
                "by_error_type": {"HighRiskTransactionError": 1, "InsufficientFundsError": 1,
                                  "TemporaryTransactionError": 1},
            })
            self.assertEqual(bank.get_error_statistics(other_token)["failed_attempts"], 0)
            self.assertEqual(bank.get_error_statistics(token, start_at=now)["failed_attempts"], 0)
            for invalid_token in (None, [], "unknown"):
                with self.assertRaises(AuthenticationError):
                    bank.get_error_statistics(invalid_token)
