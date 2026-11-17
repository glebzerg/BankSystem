"""Проверки очереди: python -B -m unittest tests.processing.test_transaction_queue."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from bank.models.accounts import Currency
from bank.exceptions import InvalidOperationError
from bank.processing.queue import TransactionQueue
from bank.models.transactions import Transaction, TransactionStatus, TransactionType


class TransactionQueueTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        self.queue = TransactionQueue(now=lambda: self.current)

    def make_transaction(self):
        return Transaction(
            TransactionType.DEPOSIT, Decimal("100"), Currency.RUB,
            recipient_account_id="123",
        )

    def test_priority_and_insertion_order(self):
        first, second, urgent = [self.make_transaction() for _ in range(3)]
        self.queue.add(first)
        self.queue.add(second)
        self.queue.add(urgent, priority=10)
        for expected in (urgent, first, second):
            self.assertIs(self.queue.get_next(), expected)
            self.assertIs(expected.status, TransactionStatus.PROCESSING)
        self.assertEqual(len(self.queue), 0)
        self.assertIsNone(self.queue.get_next())

    def test_future_priority_does_not_block_ready(self):
        future, ready = self.make_transaction(), self.make_transaction()
        self.queue.add(future, priority=100, scheduled_at=self.current + timedelta(hours=1))
        self.queue.add(ready)
        self.assertIs(self.queue.get_next(), ready)
        self.assertIsNone(self.queue.get_next())
        self.assertEqual(len(self.queue), 1)
        self.current += timedelta(hours=1)
        self.assertIs(self.queue.get_next(), future)

    def test_duplicate_and_invalid_status_do_not_change_queue(self):
        transaction = self.make_transaction()
        self.queue.add(transaction)
        with self.assertRaises(InvalidOperationError):
            self.queue.add(transaction)
        self.assertEqual(len(self.queue), 1)
        self.queue.get_next()
        with self.assertRaises(InvalidOperationError):
            self.queue.add(transaction)
        self.assertEqual(len(self.queue), 0)

    def test_cancel(self):
        transaction = self.make_transaction()
        self.queue.add(transaction, scheduled_at=self.current + timedelta(days=1))
        self.queue.cancel(transaction.transaction_id)
        self.assertIs(transaction.status, TransactionStatus.CANCELLED)
        self.assertEqual(len(self.queue), 0)
        self.assertIsNone(self.queue.get_next())
        with self.assertRaises(InvalidOperationError):
            self.queue.cancel(transaction.transaction_id)

    def test_direct_cancel_is_not_processed(self):
        transaction = self.make_transaction()
        self.queue.add(transaction)
        transaction.cancel()
        self.assertEqual(len(self.queue), 0)
        self.assertIsNone(self.queue.get_next())

    def test_retry_can_be_scheduled_again(self):
        transaction = self.make_transaction()
        self.queue.add(transaction)
        self.queue.get_next()
        transaction.mark_retry_pending("Временный сбой")
        self.queue.add(transaction, scheduled_at=self.current + timedelta(seconds=60))
        self.assertIsNone(self.queue.get_next())
        self.current += timedelta(seconds=60)
        self.assertIs(self.queue.get_next(), transaction)
        self.assertIsNone(transaction.failure_reason)

    def test_invalid_arguments_leave_transaction_created(self):
        for arguments in (
            {"priority": True}, {"priority": 1.5},
            {"scheduled_at": datetime(2026, 11, 16)},
            {"scheduled_at": "tomorrow"},
        ):
            with self.subTest(arguments=arguments):
                transaction = self.make_transaction()
                with self.assertRaises(InvalidOperationError):
                    self.queue.add(transaction, **arguments)
                self.assertIs(transaction.status, TransactionStatus.CREATED)
                self.assertEqual(len(self.queue), 0)

    def test_timezones_and_past_schedule(self):
        transaction = self.make_transaction()
        moscow = timezone(timedelta(hours=3))
        self.queue.add(transaction, scheduled_at=self.current.astimezone(moscow))
        self.assertIs(self.queue.get_next(), transaction)
        past = self.make_transaction()
        self.queue.add(past, scheduled_at=self.current - timedelta(days=1))
        self.assertIs(self.queue.get_next(), past)

    def test_invalid_clock_does_not_take_transaction(self):
        transaction = self.make_transaction()
        self.queue.add(transaction)
        self.current = datetime(2026, 11, 16)
        with self.assertRaises(InvalidOperationError):
            self.queue.get_next()
        self.assertEqual(len(self.queue), 1)
        self.assertIs(transaction.status, TransactionStatus.QUEUED)


if __name__ == "__main__":
    unittest.main()
