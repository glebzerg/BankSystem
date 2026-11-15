"""Проверки жизненного цикла: python -B -m unittest tests.transactions."""

import unittest
from decimal import Decimal

from bank.accounts import Currency
from bank.exceptions import InvalidOperationError
from bank.transactions import Transaction, TransactionStatus, TransactionType


class TransactionLifecycleTests(unittest.TestCase):
    def make_transaction(self, status=TransactionStatus.CREATED):
        transaction = Transaction(
            TransactionType.DEPOSIT, Decimal("100"), Currency.RUB,
            recipient_account_id="123",
        )
        if status is TransactionStatus.CREATED:
            return transaction
        if status is TransactionStatus.CANCELLED:
            transaction.cancel()
            return transaction
        transaction.mark_queued()
        if status is TransactionStatus.QUEUED:
            return transaction
        transaction.mark_processing()
        if status is TransactionStatus.COMPLETED:
            transaction.mark_completed()
        elif status is TransactionStatus.FAILED:
            transaction.mark_failed("Окончательный отказ")
        elif status is TransactionStatus.RETRY_PENDING:
            transaction.mark_retry_pending("Временный сбой")
        return transaction

    def test_allowed_and_forbidden_transitions(self):
        S = TransactionStatus
        transitions = [
            ("mark_queued", (), S.QUEUED, {S.CREATED, S.RETRY_PENDING}),
            ("mark_processing", (), S.PROCESSING, {S.QUEUED}),
            ("mark_completed", (), S.COMPLETED, {S.PROCESSING}),
            ("mark_failed", ("Ошибка",), S.FAILED, {S.PROCESSING}),
            ("mark_retry_pending", ("Сбой",), S.RETRY_PENDING, {S.PROCESSING}),
            ("cancel", (), S.CANCELLED, {S.CREATED, S.QUEUED, S.RETRY_PENDING}),
        ]
        for source in S:
            for method, args, target, allowed in transitions:
                with self.subTest(source=source, method=method):
                    transaction = self.make_transaction(source)
                    before = vars(transaction).copy()
                    if source in allowed:
                        getattr(transaction, method)(*args)
                        self.assertIs(transaction.status, target)
                        self.assertEqual(transaction.created_at, before["_created_at"])
                    else:
                        with self.assertRaises(InvalidOperationError):
                            getattr(transaction, method)(*args)
                        self.assertEqual(vars(transaction), before)

    def test_retry_then_success(self):
        transaction = self.make_transaction(TransactionStatus.PROCESSING)
        transaction.mark_retry_pending("  Сбой связи  ")
        self.assertEqual(transaction.failure_reason, "Сбой связи")
        transaction.mark_queued()
        transaction.mark_processing()
        self.assertIsNone(transaction.failure_reason)
        transaction.mark_completed()
        self.assertEqual(transaction.completed_at, transaction.updated_at)
        self.assertIsNotNone(transaction.completed_at.tzinfo)

    def test_invalid_reason_does_not_change_state(self):
        for method in ("mark_failed", "mark_retry_pending"):
            for reason in (None, "", "   ", 123):
                with self.subTest(method=method, reason=reason):
                    transaction = self.make_transaction(TransactionStatus.PROCESSING)
                    before = vars(transaction).copy()
                    with self.assertRaises(InvalidOperationError):
                        getattr(transaction, method)(reason)
                    self.assertEqual(vars(transaction), before)

    def test_only_success_sets_completed_at(self):
        for status in TransactionStatus:
            with self.subTest(status=status):
                transaction = self.make_transaction(status)
                self.assertEqual(
                    transaction.completed_at is not None,
                    status is TransactionStatus.COMPLETED,
                )


if __name__ == "__main__":
    unittest.main()
