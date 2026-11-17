"""Проверки переводов: python -B -m unittest tests.processing.test_transfers."""

import unittest
from decimal import Decimal

from bank.models.accounts import (
    AccountStatus, BankAccount, Currency, InvestmentAccount,
    PremiumAccount, SavingsAccount,
)
from bank.exceptions import (
    AccountClosedError, AccountFrozenError, InsufficientFundsError,
    InvalidOperationError,
)


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.sender = BankAccount("Иван", balance=Decimal("1000"))
        self.recipient = BankAccount("Анна", balance=Decimal("200"))

    def assert_rejected(self, error, sender, recipient, amount, **kwargs):
        before = sender.balance, recipient.balance
        with self.assertRaises(error):
            sender.transfer_to(recipient, amount, **kwargs)
        self.assertEqual((sender.balance, recipient.balance), before)

    def test_transfer_and_fee(self):
        self.sender.transfer_to(self.recipient, Decimal("300"), fee=Decimal("3"))
        self.assertEqual(self.sender.balance, Decimal("697"))
        self.assertEqual(self.recipient.balance, Decimal("500"))
        self.assertEqual(self.sender.balance + self.recipient.balance, Decimal("1197"))

    def test_commission_must_also_be_available(self):
        self.assert_rejected(
            InsufficientFundsError, self.sender, self.recipient,
            Decimal("1000"), fee=Decimal("10"),
        )
        self.sender.transfer_to(self.recipient, Decimal("1000"))
        self.assertEqual(self.sender.balance, Decimal("0"))

    def test_frozen_and_closed_participants(self):
        for status, error in (
            (AccountStatus.FROZEN, AccountFrozenError),
            (AccountStatus.CLOSED, AccountClosedError),
        ):
            for blocked_sender in (True, False):
                with self.subTest(status=status, blocked_sender=blocked_sender):
                    blocked = BankAccount("Заблокированный", balance=Decimal("500"), status=status)
                    sender = blocked if blocked_sender else self.sender
                    recipient = self.recipient if blocked_sender else blocked
                    self.assert_rejected(error, sender, recipient, Decimal("100"))

    def test_premium_overdraft_without_withdrawal_fee(self):
        premium = PremiumAccount("Иван", balance=Decimal("100"), overdraft_limit=Decimal("500"))
        premium.transfer_to(self.recipient, Decimal("590"), fee=Decimal("10"))
        self.assertEqual(premium.balance, Decimal("-500"))
        self.assertEqual(self.recipient.balance, Decimal("790"))
        self.assert_rejected(InsufficientFundsError, premium, self.recipient, Decimal("0.01"))

    def test_savings_minimum_includes_fee(self):
        savings = SavingsAccount("Иван", balance=Decimal("1000"), min_balance=Decimal("500"))
        self.assert_rejected(
            InsufficientFundsError, savings, self.recipient,
            Decimal("500"), fee=Decimal("1"),
        )
        savings.transfer_to(self.recipient, Decimal("490"), fee=Decimal("10"))
        self.assertEqual(savings.balance, Decimal("500"))

    def test_investments_are_not_spent(self):
        account = InvestmentAccount("Иван", balance=Decimal("1000"))
        account.invest("stocks", Decimal("900"))
        portfolio = account.portfolio
        self.assert_rejected(InsufficientFundsError, account, self.recipient, Decimal("101"))
        account.transfer_to(self.recipient, Decimal("100"))
        self.assertEqual(account.portfolio, portfolio)

    def test_invalid_amounts_and_fees(self):
        for amount in (1.0, Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(amount=amount):
                self.assert_rejected(InvalidOperationError, self.sender, self.recipient, amount)
        for fee in (1.0, Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(fee=fee):
                self.assert_rejected(
                    InvalidOperationError, self.sender, self.recipient,
                    Decimal("1"), fee=fee,
                )

    def test_self_transfer_and_currency_mismatch(self):
        same_id = BankAccount("Копия", account_id=self.sender.account_id)
        foreign_currency = BankAccount("Анна", currency=Currency.USD)
        for recipient in (self.sender, same_id, foreign_currency):
            with self.subTest(recipient=recipient):
                self.assert_rejected(InvalidOperationError, self.sender, recipient, Decimal("1"))

    def test_transfer_is_not_cash_withdrawal(self):
        account = BankAccount("Иван", balance=Decimal("100000"))
        account.transfer_to(self.recipient, Decimal("60000"))
        self.assertEqual(account.balance, Decimal("40000"))


if __name__ == "__main__":
    unittest.main()
