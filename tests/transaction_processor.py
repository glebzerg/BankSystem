"""Проверки процессора: python -B -m unittest tests.transaction_processor."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bank.accounts import AccountStatus, BankAccount, Currency, PremiumAccount
from bank.exceptions import InvalidOperationError
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
