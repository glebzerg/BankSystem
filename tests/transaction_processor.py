"""Проверки процессора: python -B -m unittest tests.transaction_processor."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bank.accounts import AccountStatus, BankAccount, Currency, PremiumAccount
from bank.exceptions import InvalidOperationError, TemporaryTransactionError
from bank.transaction_processor import TransactionProcessor
from bank.transaction_queue import TransactionQueue
from bank.transactions import ExternalRecipient, Transaction, TransactionStatus, TransactionType


class TransactionProcessorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
        self.queue = TransactionQueue(now=lambda: self.now)
        self.sender = BankAccount("Иван", balance=Decimal("2000"))
        self.recipient = BankAccount("Анна", balance=Decimal("100"))
        self.accounts = {
            account.account_id: account for account in (self.sender, self.recipient)
        }
        self.processor = TransactionProcessor(
            self.queue, self.accounts,
            exchange_rates={(Currency.RUB, Currency.USD): Decimal("0.012345")},
        )

    def operation(self, kind=TransactionType.INTERNAL_TRANSFER, amount="100", **kwargs):
        participants = {
            TransactionType.DEPOSIT: {"recipient_account_id": self.recipient.account_id},
            TransactionType.WITHDRAWAL: {"sender_account_id": self.sender.account_id},
            TransactionType.INTERNAL_TRANSFER: {
                "sender_account_id": self.sender.account_id,
                "recipient_account_id": self.recipient.account_id,
            },
            TransactionType.EXTERNAL_TRANSFER: {
                "sender_account_id": self.sender.account_id,
                "external_recipient": ExternalRecipient("123", "other-bank"),
            },
        }[kind]
        participants.update(kwargs)
        transaction = Transaction(kind, Decimal(amount), Currency.RUB, **participants)
        self.queue.add(transaction)
        return transaction

    def test_all_operation_types(self):
        operations = [self.operation(kind) for kind in TransactionType]
        self.assertEqual(self.processor.process_all(), operations)
        self.assertTrue(all(t.status is TransactionStatus.COMPLETED for t in operations))
        self.assertEqual(self.sender.balance, Decimal("1699"))
        self.assertEqual(self.recipient.balance, Decimal("300"))
        self.assertEqual(operations[-1].fee, Decimal("1"))
        self.assertIsNone(self.processor.process_next())
        self.assertEqual(self.sender.balance, Decimal("1699"))

    def test_conversion_and_recorded_calculation(self):
        usd = BankAccount("Анна", currency=Currency.USD)
        self.accounts[usd.account_id] = usd
        transaction = self.operation(amount="1000", recipient_account_id=usd.account_id)
        self.processor.process_all()
        self.assertIs(transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(usd.balance, Decimal("12.35"))
        self.assertEqual(self.sender.balance, Decimal("1000"))
        self.assertEqual(transaction.exchange_rate, Decimal("0.012345"))
        self.assertEqual(transaction.recipient_amount, Decimal("12.35"))
        self.assertIs(transaction.recipient_currency, Currency.USD)

    def test_failure_does_not_stop_following_operation(self):
        failed = self.operation(amount="3000")
        completed = self.operation()
        self.processor.process_all()
        self.assertIs(failed.status, TransactionStatus.FAILED)
        self.assertTrue(failed.failure_reason)
        self.assertIsNone(failed.completed_at)
        self.assertIs(completed.status, TransactionStatus.COMPLETED)
        self.assertEqual(self.sender.balance, Decimal("1900"))
        self.assertEqual(self.recipient.balance, Decimal("200"))

    def test_frozen_recipient_preserves_both_balances(self):
        frozen = BankAccount("Анна", status=AccountStatus.FROZEN)
        self.accounts[frozen.account_id] = frozen
        transaction = self.operation(recipient_account_id=frozen.account_id)
        self.processor.process_all()
        self.assertIs(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(self.sender.balance, Decimal("2000"))
        self.assertEqual(frozen.balance, Decimal("0"))

    def test_missing_account_rate_and_wrong_currency(self):
        eur = BankAccount("Анна", currency=Currency.EUR)
        self.accounts[eur.account_id] = eur
        operations = [
            self.operation(recipient_account_id="missing"),
            self.operation(recipient_account_id=eur.account_id),
            self.operation(TransactionType.DEPOSIT, recipient_account_id=eur.account_id),
        ]
        self.processor.process_all()
        self.assertTrue(all(t.status is TransactionStatus.FAILED for t in operations))
        self.assertEqual(self.sender.balance, Decimal("2000"))
        self.assertEqual(eur.balance, Decimal("0"))

    def test_premium_fees_depend_on_operation(self):
        premium = PremiumAccount("Иван", balance=Decimal("1000"))
        self.accounts[premium.account_id] = premium
        withdrawal = self.operation(TransactionType.WITHDRAWAL, sender_account_id=premium.account_id)
        external = self.operation(TransactionType.EXTERNAL_TRANSFER, sender_account_id=premium.account_id)
        internal = self.operation(sender_account_id=premium.account_id)
        self.processor.process_all()
        self.assertEqual((withdrawal.fee, external.fee, internal.fee),
                         (Decimal("50"), Decimal("1"), Decimal("0")))
        self.assertEqual(premium.balance, Decimal("649"))

    def test_external_fee_rounding_and_insufficient_funds(self):
        success = self.operation(TransactionType.EXTERNAL_TRANSFER, amount="1.50")
        failure = self.operation(TransactionType.EXTERNAL_TRANSFER, amount="1998.48")
        self.processor.process_all()
        self.assertEqual(success.fee, Decimal("0.02"))
        self.assertIs(failure.status, TransactionStatus.FAILED)
        self.assertEqual(self.sender.balance, Decimal("1998.48"))

    def test_tiny_converted_amount_and_fractional_cent_are_rejected(self):
        usd = BankAccount("Анна", currency=Currency.USD)
        self.accounts[usd.account_id] = usd
        tiny = self.operation(amount="0.01", recipient_account_id=usd.account_id)
        fractional = self.operation(amount="1.001")
        self.processor.process_all()
        self.assertIs(tiny.status, TransactionStatus.FAILED)
        self.assertIs(fractional.status, TransactionStatus.FAILED)
        self.assertEqual(self.sender.balance, Decimal("2000"))
        self.assertEqual(usd.balance, Decimal("0"))

    def test_future_operation_remains_queued(self):
        future = Transaction(TransactionType.DEPOSIT, Decimal("100"), Currency.RUB,
                             recipient_account_id=self.recipient.account_id)
        self.queue.add(future, scheduled_at=self.now + timedelta(hours=1))
        self.assertEqual(self.processor.process_all(), [])
        self.assertIs(future.status, TransactionStatus.QUEUED)

    def test_invalid_rates_are_rejected_at_configuration(self):
        for rates in (
            {(Currency.RUB, Currency.USD): Decimal("0")},
            {(Currency.RUB, Currency.USD): Decimal("NaN")},
            {(Currency.RUB, Currency.USD): 0.01},
            {("RUB", "USD"): Decimal("0.01")},
            {(Currency.RUB, Currency.RUB): Decimal("2")},
        ):
            with self.subTest(rates=rates):
                with self.assertRaises(InvalidOperationError):
                    TransactionProcessor(self.queue, self.accounts, exchange_rates=rates)

    def test_temporary_failure_then_success_without_double_debit(self):
        calls = []

        def preflight(transaction):
            calls.append(transaction.transaction_id)
            if len(calls) == 1:
                raise TemporaryTransactionError("Банк временно недоступен")

        processor = TransactionProcessor(self.queue, self.accounts, external_preflight=preflight)
        transaction = self.operation(TransactionType.EXTERNAL_TRANSFER)
        processor.process_all()
        self.assertIs(transaction.status, TransactionStatus.QUEUED)
        self.assertEqual(transaction.attempts_count, 1)
        self.assertEqual(self.sender.balance, Decimal("2000"))
        self.assertEqual(processor.errors[0].retry_at, self.now + timedelta(seconds=60))
        self.assertEqual(processor.process_all(), [])
        self.now += timedelta(seconds=59)
        self.assertIsNone(processor.process_next())
        self.now += timedelta(seconds=1)
        processor.process_all()
        self.assertIs(transaction.status, TransactionStatus.COMPLETED)
        self.assertEqual(transaction.attempts_count, 2)
        self.assertIsNone(transaction.failure_reason)
        self.assertEqual(self.sender.balance, Decimal("1899"))
        self.assertEqual(len(processor.errors), 1)
        self.assertEqual(processor.process_all(), [])
        self.assertEqual(self.sender.balance, Decimal("1899"))

    def test_retry_limit_is_three_attempts_total(self):
        def unavailable(transaction):
            raise TemporaryTransactionError("Нет связи")

        processor = TransactionProcessor(self.queue, self.accounts, external_preflight=unavailable)
        transaction = self.operation(TransactionType.EXTERNAL_TRANSFER)
        for _ in range(3):
            processor.process_all()
            self.now += timedelta(seconds=60)
        self.assertIs(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(transaction.attempts_count, 3)
        self.assertEqual([record.attempt for record in processor.errors], [1, 2, 3])
        self.assertIsNone(processor.errors[-1].retry_at)
        self.assertEqual(self.sender.balance, Decimal("2000"))
        self.assertEqual(len(self.queue), 0)

    def test_permanent_failure_is_logged_without_retry(self):
        transaction = self.operation(amount="3000")
        self.processor.process_all()
        self.assertIs(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(transaction.attempts_count, 1)
        record = self.processor.errors[0]
        self.assertEqual(record.transaction_id, transaction.transaction_id)
        self.assertEqual(record.error_type, "InsufficientFundsError")
        self.assertEqual(record.occurred_at, self.now)
        self.assertIsNone(record.retry_at)
        self.assertEqual(len(self.queue), 0)

    def test_retry_can_be_cancelled(self):
        def unavailable(transaction):
            raise TemporaryTransactionError("Нет связи")

        processor = TransactionProcessor(self.queue, self.accounts, external_preflight=unavailable)
        transaction = self.operation(TransactionType.EXTERNAL_TRANSFER)
        processor.process_all()
        self.queue.cancel(transaction.transaction_id)
        self.now += timedelta(seconds=60)
        self.assertEqual(processor.process_all(), [])
        self.assertEqual(transaction.attempts_count, 1)
        self.assertEqual(self.sender.balance, Decimal("2000"))

    def test_log_snapshot_and_records_cannot_be_changed(self):
        snapshot = self.processor.errors
        self.operation(amount="3000")
        self.processor.process_all()
        self.assertEqual(snapshot, ())
        self.assertEqual(len(self.processor.errors), 1)
        with self.assertRaises(AttributeError):
            self.processor.errors[0].message = "changed"

    def test_retry_settings_are_validated(self):
        for kwargs in (
            {"max_attempts": 0}, {"max_attempts": True},
            {"retry_delay": timedelta(0)}, {"retry_delay": 60},
            {"external_preflight": "not callable"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(InvalidOperationError):
                    TransactionProcessor(self.queue, self.accounts, **kwargs)

    def test_no_retry_when_max_attempts_is_one(self):
        def unavailable(transaction):
            raise TemporaryTransactionError()

        processor = TransactionProcessor(
            self.queue, self.accounts, max_attempts=1, external_preflight=unavailable,
        )
        transaction = self.operation(TransactionType.EXTERNAL_TRANSFER)
        processor.process_all()
        self.assertIs(transaction.status, TransactionStatus.FAILED)
        self.assertEqual(transaction.failure_reason, "TemporaryTransactionError")
        self.assertIsNone(processor.errors[0].retry_at)

    def test_calculation_is_validated_before_changing_transaction(self):
        transaction = self.operation()
        with self.assertRaises(InvalidOperationError):
            transaction.set_calculation(Decimal("0"))
        self.queue.get_next()
        before = vars(transaction).copy()
        with self.assertRaises(InvalidOperationError):
            transaction.set_calculation(
                Decimal("0"), exchange_rate=Decimal("1"),
                recipient_amount=Decimal("0"), recipient_currency=Currency.RUB,
            )
        self.assertEqual(vars(transaction), before)


if __name__ == "__main__":
    unittest.main()
