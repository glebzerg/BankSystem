"""Интеграция транзакций: python -B -m unittest tests.integration.test_bank_transactions."""

import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import AuthenticationError, InvalidOperationError, TemporaryTransactionError
from bank.models.transactions import ExternalRecipient, TransactionType


class BankTransactionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.unavailable = False

        def preflight(transaction):
            if self.unavailable:
                raise TemporaryTransactionError("Нет связи")

        self.bank = Bank(
            now=lambda: self.now,
            exchange_rates={(Currency.RUB, Currency.USD): Decimal("0.01")},
            external_preflight=preflight,
        )
        self.ivan = Client("Иван", date(2000, 1, 1), {"phone": "123"})
        self.anna = Client("Анна", date(2000, 1, 1), {"phone": "456"})
        self.bank.add_client(self.ivan, "password")
        self.bank.add_client(self.anna, "password")
        self.token = self.bank.authenticate_client(self.ivan.client_id, "password")
        self.other_token = self.bank.authenticate_client(self.anna.client_id, "password")
        self.sender = self.bank.open_account(self.token, "standard", balance=Decimal("1000"))["account_id"]
        self.recipient = self.bank.open_account(self.other_token, "standard")["account_id"]

    def create(self, **kwargs):
        arguments = {
            "sender_account_id": self.sender, "recipient_account_id": self.recipient,
        }
        arguments.update(kwargs)
        return self.bank.create_transaction(
            self.token, TransactionType.INTERNAL_TRANSFER, Decimal("100"), Currency.RUB,
            **arguments,
        )

    def balance(self, token, account_id):
        return self.bank.search_accounts(token, account_id=account_id)[0]["balance"]

    def block(self, client):
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(client.client_id, "wrong")

    def test_create_process_and_read_snapshot(self):
        info = self.create()
        self.assertEqual(info["status"], "queued")
        info["amount"] = Decimal("9999")
        self.assertEqual(self.bank.process_transactions(), 1)
        actual = self.bank.get_transaction(self.token, info["transaction_id"])
        self.assertEqual(actual["amount"], Decimal("100"))
        self.assertEqual(actual["status"], "completed")
        self.assertEqual(actual["attempts_count"], 1)
        self.assertEqual(self.balance(self.token, self.sender), Decimal("900"))
        self.assertEqual(self.balance(self.other_token, self.recipient), Decimal("100"))
        self.assertEqual(self.bank.get_transactions(self.other_token), [])

    def test_cannot_use_foreign_sender_or_foreign_deposit_account(self):
        with self.assertRaises(InvalidOperationError):
            self.create(sender_account_id=self.recipient, recipient_account_id=self.sender)
        with self.assertRaises(InvalidOperationError):
            self.bank.create_transaction(
                self.token, TransactionType.DEPOSIT, Decimal("100"), Currency.RUB,
                recipient_account_id=self.recipient,
            )
        self.assertEqual(self.bank.get_transactions(self.token), [])
        self.assertEqual(self.bank.process_transactions(), 0)

    def test_cannot_read_or_cancel_foreign_transaction(self):
        info = self.create()
        for operation in (self.bank.get_transaction, self.bank.cancel_transaction):
            with self.assertRaises(InvalidOperationError):
                operation(self.other_token, info["transaction_id"])
        self.assertEqual(self.bank.get_transaction(self.token, info["transaction_id"])["status"], "queued")
        self.assertEqual(len(self.bank.get_suspicious_events(self.other_token)), 2)

    def test_cancel_keeps_transaction_in_history(self):
        info = self.create()
        self.bank.cancel_transaction(self.token, info["transaction_id"])
        self.assertEqual(self.bank.process_transactions(), 0)
        self.assertEqual(self.bank.get_transactions(self.token)[0]["status"], "cancelled")
        self.assertEqual(self.balance(self.token, self.sender), Decimal("1000"))

    def test_night_does_not_extract_pending_transaction(self):
        info = self.create()
        self.now = self.now.replace(hour=1)
        for operation in (
            self.bank.process_transactions,
            self.create,
            lambda: self.bank.cancel_transaction(self.token, info["transaction_id"]),
        ):
            with self.assertRaises(InvalidOperationError):
                operation()
        actual = self.bank.get_transaction(self.token, info["transaction_id"])
        self.assertEqual(actual["status"], "queued")
        self.assertEqual(actual["attempts_count"], 0)
        self.now = self.now.replace(hour=5)
        self.assertEqual(self.bank.process_transactions(), 0)  # Поручение назначено на 12:00.
        self.now = self.now.replace(hour=12)
        self.assertEqual(self.bank.process_transactions(), 1)

    def test_blocked_sender_or_recipient_is_rechecked(self):
        info = self.create()
        self.block(self.anna)
        self.bank.process_transactions()
        actual = self.bank.get_transaction(self.token, info["transaction_id"])
        self.assertEqual(actual["status"], "failed")
        self.assertEqual(self.balance(self.token, self.sender), Decimal("1000"))
        self.assertEqual(self.bank.get_transaction_errors(self.token)[0]["error_type"], "ClientBlockedError")

    def test_blocked_creator_is_rechecked_without_original_session(self):
        self.create()
        self.block(self.ivan)
        self.assertEqual(self.bank.process_transactions(), 1)
        self.assertEqual(self.bank._get_account(self.sender).balance, Decimal("1000"))
        self.assertEqual(self.balance(self.other_token, self.recipient), Decimal("0"))
        with self.assertRaises(AuthenticationError):
            self.bank.get_transactions(self.token)

    def test_freeze_after_scheduling_causes_failure(self):
        info = self.create(scheduled_at=self.now + timedelta(minutes=1))
        self.bank.freeze_account(self.other_token, self.recipient)
        self.now += timedelta(minutes=1)
        self.bank.process_transactions()
        actual = self.bank.get_transaction(self.token, info["transaction_id"])
        self.assertEqual(actual["status"], "failed")
        self.assertEqual(self.balance(self.token, self.sender), Decimal("1000"))

    def test_conversion_through_bank(self):
        usd = self.bank.open_account(self.other_token, "standard", currency=Currency.USD)["account_id"]
        info = self.create(recipient_account_id=usd)
        self.bank.process_transactions()
        actual = self.bank.get_transaction(self.token, info["transaction_id"])
        self.assertEqual(actual["recipient_currency"], "USD")
        self.assertEqual(self.balance(self.other_token, usd), Decimal("1"))

    def test_retries_and_error_log_are_client_scoped(self):
        self.unavailable = True
        info = self.bank.create_transaction(
            self.token, TransactionType.EXTERNAL_TRANSFER, Decimal("100"), Currency.RUB,
            sender_account_id=self.sender, external_recipient=ExternalRecipient("123", "bank"),
        )
        self.bank.process_transactions()
        errors = self.bank.get_transaction_errors(self.token)
        self.assertEqual(len(errors), 1)
        errors[0]["message"] = "changed"
        self.assertEqual(self.bank.get_transaction_errors(self.token)[0]["message"], "Нет связи")
        self.assertEqual(self.bank.get_transaction_errors(self.other_token), [])
        self.assertEqual(self.balance(self.token, self.sender), Decimal("1000"))
        self.now += timedelta(seconds=60)
        self.unavailable = False
        self.bank.process_transactions()
        actual = self.bank.get_transaction(self.token, info["transaction_id"])
        self.assertEqual(actual["status"], "completed")
        self.assertEqual(actual["attempts_count"], 2)
        self.assertEqual(self.balance(self.token, self.sender), Decimal("899"))


if __name__ == "__main__":
    unittest.main()
