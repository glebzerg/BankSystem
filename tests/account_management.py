"""Проверки заморозки, разморозки и закрытия счетов."""
import unittest
from datetime import date
from decimal import Decimal

from bank.accounts import AccountStatus
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import (
    AccountClosedError, AccountFrozenError, AuthenticationError, InvalidOperationError,
)


class AccountManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bank = Bank()
        self.client = Client("Иван", date(2000, 1, 1), {"phone": "+79991234567"})
        self.bank.add_client(self.client, "password")
        self.token = self.bank.authenticate_client(self.client.client_id, "password")

    def open(self, kind="standard", balance=Decimal("0")):
        info = self.bank.open_account(self.token, kind, balance=balance)
        return self.bank._get_account(info["account_id"])

    def test_freeze_unfreeze_and_closed_account_restrictions(self):
        account = self.open()
        with self.assertRaises(InvalidOperationError):
            self.bank.unfreeze_account(self.token, account.account_id)
        self.bank.freeze_account(self.token, account.account_id)
        self.assertIs(account.status, AccountStatus.FROZEN)
        with self.assertRaises(AccountFrozenError):
            account.deposit(Decimal("1"))
        with self.assertRaises(InvalidOperationError):
            self.bank.freeze_account(self.token, account.account_id)
        self.bank.unfreeze_account(self.token, account.account_id)
        self.assertIs(account.status, AccountStatus.ACTIVE)
        self.bank.close_account(self.token, account.account_id)
        self.assertIs(account.status, AccountStatus.CLOSED)
        for operation in (self.bank.freeze_account, self.bank.unfreeze_account,
                          self.bank.close_account):
            with self.assertRaises(InvalidOperationError):
                operation(self.token, account.account_id)
        with self.assertRaises(AccountClosedError):
            account.deposit(Decimal("1"))
        self.assertIn(account.account_id, self.client.account_numbers)
        self.assertIs(self.bank._get_account(account.account_id), account)

    def test_frozen_empty_account_can_close(self):
        account = self.open()
        self.bank.freeze_account(self.token, account.account_id)
        self.bank.close_account(self.token, account.account_id)
        self.assertIs(account.status, AccountStatus.CLOSED)

    def test_money_debt_and_portfolio_prevent_closure(self):
        positive = self.open(balance=Decimal("100"))
        debt = self.open("premium")
        debt.withdraw(Decimal("100"))
        investment = self.open("investment", Decimal("100"))
        investment.invest("stocks", Decimal("100"))
        for account in (positive, debt, investment):
            with self.subTest(kind=type(account).__name__), self.assertRaises(InvalidOperationError):
                self.bank.close_account(self.token, account.account_id)
            self.assertIs(account.status, AccountStatus.ACTIVE)
        positive.withdraw(Decimal("100"))
        debt.deposit(Decimal("150"))
        for account in (positive, debt):
            self.bank.close_account(self.token, account.account_id)
            self.assertIs(account.status, AccountStatus.CLOSED)

    def test_invalid_session_and_foreign_account_cannot_change_status(self):
        account = self.open()
        other = Client("Анна", date(1990, 1, 1), {"phone": "+79991234568"})
        self.bank.add_client(other, "password")
        other_token = self.bank.authenticate_client(other.client_id, "password")
        for operation in (self.bank.freeze_account, self.bank.unfreeze_account,
                          self.bank.close_account):
            with self.assertRaises(AuthenticationError):
                operation("invalid", account.account_id)
            with self.assertRaises(InvalidOperationError):
                operation(other_token, account.account_id)
            with self.assertRaises(InvalidOperationError):
                operation(self.token, "unknown")
            self.assertIs(account.status, AccountStatus.ACTIVE)


if __name__ == "__main__":
    unittest.main()
