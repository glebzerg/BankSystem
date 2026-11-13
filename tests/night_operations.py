"""Проверки ночного запрета и денежных операций через банк."""
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from bank.accounts import AccountStatus
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AccountFrozenError, InvalidOperationError


class NightOperationsTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.current)
        self.client = Client("Иван", date(2000, 1, 1), {"phone": "+79991234567"})
        self.bank.add_client(self.client, "password")
        self.token = self.bank.authenticate_client(self.client.client_id, "password")

    def open(self, kind="standard", balance=Decimal("0")):
        return self.bank.open_account(self.token, kind, balance=balance)["account_id"]

    def test_boundaries_and_timezone_conversion(self):
        account_id = self.open()
        for hour, minute, second, allowed in [
            (0, 0, 0, False), (4, 59, 59, False),
            (5, 0, 0, True), (23, 59, 59, True),
        ]:
            self.current = datetime(2026, 11, 13, hour, minute, second,
                                    tzinfo=Bank.BANK_TIMEZONE)
            before = self.bank._get_account(account_id).balance
            with self.subTest(hour=hour):
                if allowed:
                    self.bank.deposit(self.token, account_id, Decimal("1"))
                    self.assertEqual(self.bank._get_account(account_id).balance, before + 1)
                else:
                    with self.assertRaises(InvalidOperationError):
                        self.bank.deposit(self.token, account_id, Decimal("1"))
                    self.assertEqual(self.bank._get_account(account_id).balance, before)
        # 21:00 UTC — это полночь следующего дня в Москве.
        self.current = datetime(2026, 11, 13, 21, tzinfo=timezone.utc)
        with self.assertRaises(InvalidOperationError):
            self.bank.deposit(self.token, account_id, Decimal("1"))

    def test_all_mutations_blocked_but_login_allowed(self):
        standard = self.open(balance=Decimal("100"))
        empty = self.open()
        frozen = self.open()
        self.bank.freeze_account(self.token, frozen)
        savings = self.open("savings", Decimal("100"))
        investment = self.open("investment", Decimal("100"))
        accounts_before = {key: value.get_account_info() for key, value in self.bank._accounts.items()}
        numbers_before = self.client.account_numbers
        other = Client("Анна", date(2000, 1, 1), {"phone": "+79991234568"})
        self.current = datetime(2026, 11, 13, 1, tzinfo=Bank.BANK_TIMEZONE)
        operations = [
            lambda: self.bank.add_client(other, "password"),
            lambda: self.bank.open_account(self.token, "standard"),
            lambda: self.bank.deposit(self.token, standard, Decimal("1")),
            lambda: self.bank.withdraw(self.token, standard, Decimal("1")),
            lambda: self.bank.invest(self.token, investment, "stocks", Decimal("1")),
            lambda: self.bank.apply_monthly_interest(self.token, savings),
            lambda: self.bank.freeze_account(self.token, standard),
            lambda: self.bank.unfreeze_account(self.token, frozen),
            lambda: self.bank.close_account(self.token, empty),
        ]
        for index, operation in enumerate(operations):
            with self.subTest(operation=index), self.assertRaises(InvalidOperationError):
                operation()
        self.assertNotIn(other.client_id, self.bank._clients)
        self.assertEqual(self.client.account_numbers, numbers_before)
        self.assertEqual(
            {key: value.get_account_info() for key, value in self.bank._accounts.items()},
            accounts_before,
        )
        token = self.bank.authenticate_client(self.client.client_id, "password")
        self.assertIs(self.bank._get_authenticated_client(token), self.client)

    def test_daytime_operations_and_account_rules(self):
        standard = self.open()
        self.bank.deposit(self.token, standard, Decimal("100"))
        self.bank.withdraw(self.token, standard, Decimal("20"))
        self.assertEqual(self.bank._get_account(standard).balance, Decimal("80"))
        for operation in (
            lambda: self.bank.invest(self.token, standard, "stocks", Decimal("1")),
            lambda: self.bank.apply_monthly_interest(self.token, standard),
        ):
            with self.assertRaises(InvalidOperationError):
                operation()
        savings = self.open("savings", Decimal("100"))
        self.assertEqual(self.bank.apply_monthly_interest(self.token, savings), Decimal("1"))
        investment = self.open("investment", Decimal("100"))
        self.bank.invest(self.token, investment, "stocks", Decimal("25"))
        self.assertEqual(self.bank._get_account(investment).portfolio["stocks"], Decimal("25"))
        self.bank.freeze_account(self.token, standard)
        with self.assertRaises(AccountFrozenError):
            self.bank.withdraw(self.token, standard, Decimal("1"))
        self.assertIs(self.bank._get_account(standard).status, AccountStatus.FROZEN)

    def test_invalid_clock(self):
        with self.assertRaises(InvalidOperationError):
            Bank(now=123)
        for value in (None, "12:00", datetime(2026, 11, 13, 12)):
            self.current = value
            with self.subTest(value=value), self.assertRaises(InvalidOperationError):
                self.bank.open_account(self.token, "standard")


if __name__ == "__main__":
    unittest.main()
