"""Аудит результатов и общая защита прямых и очередных операций."""

import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from bank.accounts import Currency
from bank.audit import AuditLog
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuditWriteError, HighRiskTransactionError, InsufficientFundsError, TemporaryTransactionError
from bank.transactions import ExternalRecipient, TransactionType


class OperationAuditTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.log = AuditLog()
        self.unavailable = False

        def preflight(transaction):
            if self.unavailable:
                raise TemporaryTransactionError("Нет связи")

        self.bank = Bank(now=lambda: self.now, audit_log=self.log, external_preflight=preflight)
        self.client = Client("Иван", date(2000, 1, 1), {"phone": "123"})
        self.bank.add_client(self.client, "password")
        self.token = self.bank.authenticate_client(self.client.client_id, "password")
        self.account_id = self.bank.open_account(self.token, "standard", balance=Decimal("1000000"))["account_id"]

    def create(self, kind=TransactionType.DEPOSIT, amount="1"):
        participants = (
            {"recipient_account_id": self.account_id} if kind is TransactionType.DEPOSIT
            else {"sender_account_id": self.account_id, "external_recipient": ExternalRecipient("123", "bank")}
        )
        return self.bank.create_transaction(self.token, kind, Decimal(amount), Currency.RUB,
                                             **participants)["transaction_id"]

    def events(self, identifier):
        return [record.event_type for record in self.log.filter(transaction_id=identifier)]

    def test_transaction_success_and_cancellation_are_logged_once(self):
        completed = self.create()
        cancelled = self.create()
        self.bank.cancel_transaction(self.token, cancelled)
        self.bank.process_transactions()
        self.bank.process_transactions()
        self.assertEqual(self.events(completed), ["risk_assessed", "transaction_completed"])
        self.assertEqual(self.events(cancelled), ["transaction_cancelled"])
        self.assertTrue(all(record.client_id == self.client.client_id for record in self.log.records))

    def test_retry_then_success_has_one_outcome_per_attempt(self):
        self.unavailable = True
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER, "100")
        self.bank.process_transactions()
        self.now += timedelta(minutes=1)
        self.unavailable = False
        self.bank.process_transactions()
        self.assertEqual(self.events(identifier), [
            "risk_assessed", "transaction_retry_scheduled", "risk_assessed", "transaction_completed",
        ])
        retry = self.log.filter(transaction_id=identifier)[1]
        self.assertIn("TemporaryTransactionError", retry.description)
        self.assertIn("повтор:", retry.description)
        self.assertEqual(len(self.bank.get_transaction_errors(self.token)), 1)

    def test_terminal_error_and_exhausted_retries_are_logged(self):
        failed = self.create(TransactionType.EXTERNAL_TRANSFER, "2000000")
        self.bank.process_transactions()
        self.assertEqual(self.events(failed), ["risk_blocked", "transaction_failed"])
        self.unavailable = True
        retrying = self.create(TransactionType.EXTERNAL_TRANSFER, "100")
        for _ in range(3):
            self.bank.process_transactions()
            self.now += timedelta(minutes=1)
        self.assertEqual(self.events(retrying).count("transaction_retry_scheduled"), 2)
        self.assertEqual(self.events(retrying).count("transaction_failed"), 1)
        self.assertEqual(self.bank.get_transaction(self.token, retrying)["attempts_count"], 3)

    def test_direct_success_and_failure_have_separate_operation_ids(self):
        self.bank.deposit(self.token, self.account_id, Decimal("100"))
        self.bank.withdraw(self.token, self.account_id, Decimal("50"))
        empty = self.bank.open_account(self.token, "standard")["account_id"]
        with self.assertRaises(InsufficientFundsError):
            self.bank.withdraw(self.token, empty, Decimal("1"))
        started = [record for record in self.log.records if record.event_type == "operation_started"]
        self.assertEqual(len({record.transaction_id for record in started}), 3)
        for record in started:
            self.assertTrue(record.transaction_id.startswith("direct:"))
        self.assertEqual(self.events(started[0].transaction_id),
                         ["operation_started", "risk_assessed", "operation_completed"])
        self.assertEqual(self.events(started[2].transaction_id),
                         ["operation_started", "risk_assessed", "operation_failed"])
        self.assertIn("InsufficientFundsError", self.log.records[-1].description)

    def test_direct_activity_can_block_queued_transfer(self):
        for _ in range(4):
            self.bank.deposit(self.token, self.account_id, Decimal("1"))
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER, "100")
        self.bank.process_transactions()
        info = self.bank.get_transaction(self.token, identifier)
        self.assertEqual(info["status"], "failed")
        self.assertEqual(info["risk"]["reasons"], ("frequent_operations", "new_recipient"))

    def test_queued_activity_can_block_direct_deposit_without_balance_change(self):
        for _ in range(4):
            self.create()
        self.bank.process_transactions()
        account = self.bank._get_account(self.account_id)
        before = account.balance
        with self.assertRaises(HighRiskTransactionError):
            self.bank.deposit(self.token, self.account_id, Decimal("100000"))
        self.assertEqual(account.balance, before)
        self.assertEqual([record.event_type for record in self.log.records[-3:]],
                         ["operation_started", "risk_blocked", "operation_failed"])

    def test_direct_withdrawal_and_investment_are_checked_before_mutation(self):
        premium = self.bank.open_account(self.token, "premium", balance=Decimal("1000000"))["account_id"]
        investment = self.bank.open_account(self.token, "investment", balance=Decimal("1000000"))["account_id"]
        portfolio_before = self.bank._get_account(investment).portfolio
        for _ in range(4):
            self.bank.deposit(self.token, self.account_id, Decimal("1"))
        for account_id, action in (
            (premium, lambda: self.bank.withdraw(self.token, premium, Decimal("100000"))),
            (investment, lambda: self.bank.invest(self.token, investment, "stocks", Decimal("100000"))),
        ):
            with self.subTest(account_id=account_id):
                with self.assertRaises(HighRiskTransactionError):
                    action()
                self.assertEqual(self.bank._get_account(account_id).balance, Decimal("1000000"))
        self.assertEqual(self.bank._get_account(investment).portfolio, portfolio_before)

    def test_interest_is_audited_but_does_not_increase_client_operation_frequency(self):
        savings = self.bank.open_account(self.token, "savings", balance=Decimal("100"),
                                         monthly_interest_rate=Decimal("0.01"))["account_id"]
        for _ in range(5):
            self.assertGreater(self.bank.apply_monthly_interest(self.token, savings), 0)
        self.assertEqual(len(self.log.records), 10)
        self.assertTrue(all(record.event_type in ("operation_started", "operation_completed")
                            for record in self.log.records))
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER, "100")
        self.bank.process_transactions()
        self.assertEqual(self.bank.get_transaction(self.token, identifier)["risk"]["reasons"], ("new_recipient",))

    def test_direct_audit_failure_before_execution_preserves_balance(self):
        before = self.bank._get_account(self.account_id).balance
        with patch.object(self.log, "add", side_effect=AuditWriteError("Нет доступа")):
            with self.assertRaises(AuditWriteError):
                self.bank.deposit(self.token, self.account_id, Decimal("100"))
        self.assertEqual(self.bank._get_account(self.account_id).balance, before)

    def fail_only_completed_event(self, event_type):
        original_add = self.log.add

        def add(level, event, description, **kwargs):
            if event == event_type:
                raise AuditWriteError("Ошибка записи результата")
            return original_add(level, event, description, **kwargs)

        return patch.object(self.log, "add", side_effect=add)

    def test_direct_audit_failure_after_success_does_not_report_operation_as_failed(self):
        with self.fail_only_completed_event("operation_completed"):
            with self.assertRaises(AuditWriteError):
                self.bank.deposit(self.token, self.account_id, Decimal("100"))
        self.assertEqual(self.bank._get_account(self.account_id).balance, Decimal("1000100"))
        self.assertNotIn("operation_failed", [record.event_type for record in self.log.records])

    def test_transaction_audit_failure_after_success_does_not_retry_or_reverse_payment(self):
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER, "100")
        with self.fail_only_completed_event("transaction_completed"):
            with self.assertRaises(AuditWriteError):
                self.bank.process_transactions()
        self.assertEqual(self.bank.get_transaction(self.token, identifier)["status"], "completed")
        self.now += timedelta(minutes=1)
        self.assertEqual(self.bank.process_transactions(), 0)
        self.assertEqual(self.bank._get_account(self.account_id).balance, Decimal("999899"))
        self.assertEqual(self.bank.get_transaction_errors(self.token), [])


if __name__ == "__main__":
    unittest.main()
