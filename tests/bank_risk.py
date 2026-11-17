"""Блокировка опасных поручений до списания и повторная оценка риска."""

import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from bank.accounts import Currency
from bank.audit import AuditLog
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuditWriteError, InvalidOperationError, TemporaryTransactionError
from bank.risk import RiskAnalyzer
from bank.transactions import ExternalRecipient, TransactionType


class BankRiskTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.make_bank()

    def make_bank(self, analyzer=None):
        self.log = AuditLog()
        self.calls = []
        self.unavailable = False

        def preflight(transaction):
            self.calls.append(transaction.transaction_id)
            if self.unavailable:
                raise TemporaryTransactionError("Нет связи")

        self.bank = Bank(now=lambda: self.now, audit_log=self.log,
                         risk_analyzer=analyzer, external_preflight=preflight)
        client = Client("Иван", date(2000, 1, 1), {"phone": "123"})
        self.bank.add_client(client, "password")
        self.token = self.bank.authenticate_client(client.client_id, "password")
        self.sender = self.bank.open_account(self.token, "standard", balance=Decimal("1000000"))["account_id"]
        self.recipient = self.bank.open_account(self.token, "standard")["account_id"]

    def create(self, kind=TransactionType.INTERNAL_TRANSFER, amount="100", **kwargs):
        participants = {
            TransactionType.INTERNAL_TRANSFER: dict(sender_account_id=self.sender,
                                                     recipient_account_id=self.recipient),
            TransactionType.EXTERNAL_TRANSFER: dict(sender_account_id=self.sender,
                                                     external_recipient=ExternalRecipient("123", "bank")),
            TransactionType.DEPOSIT: dict(recipient_account_id=self.sender),
            TransactionType.WITHDRAWAL: dict(sender_account_id=self.sender),
        }[kind]
        return self.bank.create_transaction(self.token, kind, Decimal(amount), Currency.RUB,
                                             **participants, **kwargs)["transaction_id"]

    def info(self, identifier):
        return self.bank.get_transaction(self.token, identifier)

    def balances(self):
        return tuple(self.bank._get_account(identifier).balance for identifier in (self.sender, self.recipient))

    def test_low_and_medium_risk_execute_and_success_warms_recipient_immediately(self):
        deposit = self.create(TransactionType.DEPOSIT)
        first = self.create()
        second = self.create()
        self.assertIsNone(self.info(first)["risk"])
        self.assertEqual(self.bank.process_transactions(), 3)
        self.assertEqual(self.info(deposit)["risk"]["level"], "low")
        self.assertEqual(self.info(first)["risk"], {
            "level": "medium", "score": 1, "reasons": ("new_recipient",),
        })
        self.assertEqual(self.info(second)["risk"]["level"], "low")
        self.assertTrue(all(self.info(identifier)["status"] == "completed"
                            for identifier in (deposit, first, second)))
        self.assertEqual(self.balances(), (Decimal("999900"), Decimal("200")))
        snapshot = self.info(first)
        snapshot["risk"]["level"] = "changed"
        self.assertEqual(self.info(first)["risk"]["level"], "medium")

    def test_large_transfer_to_new_recipient_is_final_failure_before_any_money_moves(self):
        identifier = self.create(amount="100000")
        before = self.balances()
        self.assertEqual(self.bank.process_transactions(), 1)
        actual = self.info(identifier)
        self.assertEqual(actual["status"], "failed")
        self.assertEqual(actual["risk"]["reasons"], ("large_amount", "new_recipient"))
        self.assertIn("высокого риска", actual["failure_reason"])
        self.assertEqual(actual["attempts_count"], 1)
        self.assertIsNone(actual["fee"])
        self.assertEqual(self.balances(), before)
        errors = self.bank.get_transaction_errors(self.token)
        self.assertEqual(errors[0]["error_type"], "HighRiskTransactionError")
        self.assertIsNone(errors[0]["retry_at"])
        events = self.log.filter(transaction_id=identifier)
        self.assertEqual([event.event_type for event in events], ["risk_blocked", "transaction_failed"])
        self.assertEqual(events[0].event_type, "risk_blocked")
        self.assertEqual(events[0].level.value, "error")
        self.now += timedelta(minutes=10)
        self.assertEqual(self.bank.process_transactions(), 0)
        self.assertEqual(self.balances(), before)

    def test_external_high_risk_never_calls_preflight_or_charges_fee(self):
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER, "100000")
        self.bank.process_transactions()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.info(identifier)["status"], "failed")
        self.assertIsNone(self.info(identifier)["fee"])
        self.assertEqual(self.balances()[0], Decimal("1000000"))

    def test_frequency_blocks_fifth_new_recipient_transfer(self):
        for _ in range(4):
            self.create(TransactionType.DEPOSIT, "1")
        identifier = self.create()
        self.assertEqual(self.bank.process_transactions(), 5)
        self.assertEqual(self.info(identifier)["risk"]["reasons"], ("frequent_operations", "new_recipient"))
        self.assertEqual(self.info(identifier)["status"], "failed")
        self.assertEqual(self.balances(), (Decimal("1000004"), Decimal("0")))

    def test_failed_transfer_does_not_make_recipient_familiar(self):
        self.bank.freeze_account(self.token, self.recipient)
        first = self.create()
        self.bank.process_transactions()
        self.assertEqual(self.info(first)["status"], "failed")
        self.bank.unfreeze_account(self.token, self.recipient)
        second = self.create()
        self.bank.process_transactions()
        self.assertEqual(self.info(second)["risk"]["reasons"], ("new_recipient",))
        self.assertEqual(self.info(second)["status"], "completed")

    def test_scheduled_transfer_is_assessed_at_execution_with_current_history(self):
        identifier = self.create(scheduled_at=self.now + timedelta(seconds=30))
        for _ in range(4):
            self.create(TransactionType.DEPOSIT, "1")
        self.assertEqual(self.bank.process_transactions(), 4)
        self.assertIsNone(self.info(identifier)["risk"])
        self.now += timedelta(seconds=30)
        self.assertEqual(self.bank.process_transactions(), 1)
        self.assertEqual(self.info(identifier)["status"], "failed")
        self.assertEqual(self.info(identifier)["risk"]["level"], "high")

    def test_retry_is_reassessed_and_can_be_blocked_before_second_external_call(self):
        self.make_bank(RiskAnalyzer(frequency_limit=2, frequency_window=timedelta(minutes=5)))
        self.unavailable = True
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER)
        self.bank.process_transactions()
        self.assertEqual(self.info(identifier)["status"], "queued")
        self.assertEqual(self.info(identifier)["risk"]["level"], "medium")
        self.create(TransactionType.DEPOSIT, "1")
        self.bank.process_transactions()
        self.now += timedelta(minutes=1)
        self.unavailable = False
        self.bank.process_transactions()
        self.assertEqual(self.info(identifier)["status"], "failed")
        self.assertEqual(self.info(identifier)["attempts_count"], 2)
        self.assertEqual(self.info(identifier)["risk"]["level"], "high")
        self.assertEqual(self.calls, [identifier])
        self.assertEqual(self.balances()[0], Decimal("1000001"))
        errors = self.bank.get_transaction_errors(self.token)
        self.assertEqual([error["error_type"] for error in errors],
                         ["TemporaryTransactionError", "HighRiskTransactionError"])
        self.assertIsNone(errors[-1]["retry_at"])

    def test_retry_alone_is_not_counted_twice_and_success_remembers_external_recipient(self):
        self.make_bank(RiskAnalyzer(frequency_limit=2, frequency_window=timedelta(minutes=5)))
        self.unavailable = True
        identifier = self.create(TransactionType.EXTERNAL_TRANSFER)
        self.bank.process_transactions()
        self.now += timedelta(minutes=1)
        self.unavailable = False
        self.bank.process_transactions()
        self.assertEqual(self.info(identifier)["status"], "completed")
        self.assertEqual(self.info(identifier)["risk"]["reasons"], ("new_recipient",))
        next_id = self.create(TransactionType.EXTERNAL_TRANSFER)
        self.bank.process_transactions()
        self.assertEqual(self.info(next_id)["risk"]["reasons"], ("frequent_operations",))
        self.assertEqual(self.info(next_id)["status"], "completed")
        self.assertEqual(self.balances()[0], Decimal("999798"))

    def test_cancelled_operations_do_not_count_as_attempts(self):
        for _ in range(5):
            identifier = self.create()
            self.bank.cancel_transaction(self.token, identifier)
        identifier = self.create()
        self.bank.process_transactions()
        self.assertEqual(self.info(identifier)["risk"]["reasons"], ("new_recipient",))
        self.assertEqual(self.info(identifier)["status"], "completed")

    def test_audit_failure_stops_operation_before_balance_changes(self):
        identifier = self.create()
        before = self.balances()
        with patch.object(self.log, "add", side_effect=AuditWriteError("Нет доступа")):
            self.bank.process_transactions()
        self.assertEqual(self.info(identifier)["status"], "failed")
        self.assertEqual(self.balances(), before)
        self.assertEqual(self.bank.get_transaction_errors(self.token)[0]["error_type"], "AuditWriteError")

    def test_invalid_analyzer_is_rejected(self):
        with self.assertRaises(InvalidOperationError):
            Bank(risk_analyzer={})


if __name__ == "__main__":
    unittest.main()
