"""История фактических балансов при операциях через Bank."""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from bank.accounts import Currency
from bank.audit import AuditLog
from bank.balance_history import BalancePoint
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuditWriteError, AuthenticationError, HighRiskTransactionError, InvalidOperationError, TemporaryTransactionError
from bank.transactions import ExternalRecipient, TransactionType


class BalanceHistoryTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.unavailable = False

        def preflight(transaction):
            if self.unavailable:
                raise TemporaryTransactionError("Нет связи")

        self.log = AuditLog()
        self.bank = Bank(now=lambda: self.now, audit_log=self.log, external_preflight=preflight,
                         exchange_rates={(Currency.RUB, Currency.USD): Decimal("0.01")})
        self.tokens = []
        for name in ("Иван", "Анна"):
            client = Client(name, date(1990, 1, 1), {"phone": name})
            self.bank.add_client(client, "password")
            self.tokens.append(self.bank.authenticate_client(client.client_id, "password"))
        self.sender = self.open(balance=Decimal("1000"))
        self.recipient = self.open(owner=1)

    def open(self, kind="standard", owner=0, **kwargs):
        return self.bank.open_account(self.tokens[owner], kind, **kwargs)["account_id"]

    def history(self, account_id=None, owner=0):
        return self.bank.get_balance_history(self.tokens[owner], account_id or self.sender)

    def create(self, kind, amount, **participants):
        return self.bank.create_transaction(self.tokens[0], kind, Decimal(amount), Currency.RUB,
                                             **participants)["transaction_id"]

    def test_opening_point_and_immutable_snapshot_with_exact_decimal_amount(self):
        snapshot = self.history()
        self.assertIsInstance(snapshot, tuple)
        self.assertEqual(len(snapshot), 1)
        self.assertEqual(snapshot[0], BalancePoint(self.sender, self.now, Decimal("1000"),
                                                  Currency.RUB, "account_opened"))
        self.now += timedelta(seconds=1)
        self.bank.deposit(self.tokens[0], self.sender, Decimal("10.123"))
        self.assertEqual(len(snapshot), 1)
        latest = self.history()[-1]
        self.assertEqual(latest.balance, Decimal("1010.123"))
        self.assertEqual(latest.occurred_at, self.now)
        self.assertEqual(latest.operation_type, "deposit")
        self.assertTrue(latest.operation_id.startswith("direct:"))
        with self.assertRaises(FrozenInstanceError):
            latest.balance = Decimal("0")

    def test_direct_withdrawal_includes_premium_fee_and_overdraft(self):
        premium = self.open("premium", balance=Decimal("100"))
        self.bank.withdraw(self.tokens[0], premium, Decimal("200"))
        self.assertEqual([point.balance for point in self.history(premium)],
                         [Decimal("100"), Decimal("-150")])

    def test_internal_transfer_records_both_accounts_at_same_time(self):
        self.now += timedelta(minutes=1)
        identifier = self.create(TransactionType.INTERNAL_TRANSFER, "100",
                                 sender_account_id=self.sender, recipient_account_id=self.recipient)
        self.bank.process_transactions()
        sender = self.history()[-1]
        recipient = self.history(self.recipient, owner=1)[-1]
        self.assertEqual((sender.balance, recipient.balance), (Decimal("900"), Decimal("100")))
        self.assertEqual(sender.occurred_at, recipient.occurred_at)
        self.assertEqual(sender.occurred_at, self.now)
        self.assertEqual(sender.operation_id, identifier)
        self.assertEqual(recipient.operation_id, identifier)

    def test_conversion_keeps_actual_balance_and_currency_for_each_account(self):
        usd = self.open(owner=1, currency=Currency.USD)
        self.create(TransactionType.INTERNAL_TRANSFER, "100",
                    sender_account_id=self.sender, recipient_account_id=usd)
        self.bank.process_transactions()
        self.assertEqual(self.history()[-1].balance, Decimal("900"))
        point = self.history(usd, owner=1)[-1]
        self.assertEqual(point.balance, Decimal("1.00"))
        self.assertIs(point.currency, Currency.USD)

    def test_queue_deposit_and_withdrawal_are_recorded_once_each(self):
        self.create(TransactionType.DEPOSIT, "100", recipient_account_id=self.sender)
        self.create(TransactionType.WITHDRAWAL, "50", sender_account_id=self.sender)
        self.bank.process_transactions()
        self.assertEqual([point.balance for point in self.history()],
                         [Decimal("1000"), Decimal("1100"), Decimal("1050")])
        self.assertEqual([point.operation_type for point in self.history()],
                         ["account_opened", "deposit", "withdrawal"])
        self.assertEqual(self.bank.process_transactions(), 0)
        self.assertEqual(len(self.history()), 3)

    def test_external_retry_changes_history_only_on_success_with_fee(self):
        self.unavailable = True
        self.create(TransactionType.EXTERNAL_TRANSFER, "100", sender_account_id=self.sender,
                    external_recipient=ExternalRecipient("123", "bank"))
        self.bank.process_transactions()
        self.assertEqual(len(self.history()), 1)
        self.unavailable = False
        self.now += timedelta(minutes=1)
        self.bank.process_transactions()
        self.assertEqual(len(self.history()), 2)
        self.assertEqual(self.history()[-1].balance, Decimal("899"))
        self.assertEqual(self.history()[-1].occurred_at, self.now)

    def test_rejected_failed_and_cancelled_transactions_do_not_add_points(self):
        self.bank.freeze_account(self.tokens[1], self.recipient)
        self.create(TransactionType.INTERNAL_TRANSFER, "100000",
                    sender_account_id=self.sender, recipient_account_id=self.recipient)
        self.create(TransactionType.WITHDRAWAL, "2000", sender_account_id=self.sender)
        self.create(TransactionType.INTERNAL_TRANSFER, "100",
                    sender_account_id=self.sender, recipient_account_id=self.recipient)
        cancelled = self.create(TransactionType.DEPOSIT, "1", recipient_account_id=self.sender)
        self.bank.cancel_transaction(self.tokens[0], cancelled)
        self.bank.process_transactions()
        self.assertEqual(len(self.history()), 1)
        self.assertEqual(len(self.history(self.recipient, owner=1)), 1)

    def test_direct_risk_rejection_does_not_add_balance_point(self):
        for _ in range(4):
            self.bank.deposit(self.tokens[0], self.sender, Decimal("1"))
        snapshot = self.history()
        with self.assertRaises(HighRiskTransactionError):
            self.bank.deposit(self.tokens[0], self.sender, Decimal("100000"))
        self.assertEqual(self.history(), snapshot)

    def test_interest_and_investment_record_free_balance(self):
        savings = self.open("savings", balance=Decimal("1000"))
        investment = self.open("investment", balance=Decimal("1000"))
        self.bank.apply_monthly_interest(self.tokens[0], savings)
        self.bank.invest(self.tokens[0], investment, "stocks", Decimal("250"))
        self.assertEqual(self.history(savings)[-1].balance, Decimal("1010"))
        self.assertEqual(self.history(savings)[-1].operation_type, "monthly_interest")
        self.assertEqual(self.history(investment)[-1].balance, Decimal("750"))
        self.assertEqual(self.history(investment)[-1].operation_type, "investment")
        zero_rate = self.open("savings", balance=Decimal("1000"), monthly_interest_rate=Decimal("0"))
        self.bank.apply_monthly_interest(self.tokens[0], zero_rate)
        self.assertEqual(len(self.history(zero_rate)), 1)

    def test_history_survives_audit_failure_after_success(self):
        original_add = self.log.add

        def add(level, event_type, description, **kwargs):
            if event_type in ("operation_completed", "transaction_completed"):
                raise AuditWriteError("Не удалось записать результат")
            return original_add(level, event_type, description, **kwargs)

        with patch.object(self.log, "add", side_effect=add):
            with self.assertRaises(AuditWriteError):
                self.bank.deposit(self.tokens[0], self.sender, Decimal("100"))
            self.create(TransactionType.WITHDRAWAL, "50", sender_account_id=self.sender)
            with self.assertRaises(AuditWriteError):
                self.bank.process_transactions()
        self.assertEqual([point.balance for point in self.history()],
                         [Decimal("1000"), Decimal("1100"), Decimal("1050")])
        self.assertEqual(self.bank.process_transactions(), 0)

    def test_access_requires_account_owner_and_valid_session(self):
        with self.assertRaises(InvalidOperationError):
            self.bank.get_balance_history(self.tokens[1], self.sender)
        with self.assertRaises(AuthenticationError):
            self.bank.get_balance_history("unknown", self.sender)
        with self.assertRaises(InvalidOperationError):
            self.bank.get_balance_history(self.tokens[0], "missing")

    def test_invalid_point_data_is_rejected(self):
        valid = dict(account_id="account", occurred_at=self.now, balance=Decimal("1"),
                     currency=Currency.RUB, operation_type="deposit")
        for key, value in (("occurred_at", datetime(2026, 11, 17)), ("balance", Decimal("NaN")),
                           ("balance", "1"), ("currency", "RUB"), ("account_id", ""),
                           ("operation_type", " "), ("operation_id", 123)):
            with self.subTest(key=key, value=value), self.assertRaises(InvalidOperationError):
                BalancePoint(**(valid | {key: value}))


if __name__ == "__main__":
    unittest.main()
