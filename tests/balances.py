"""Проверки сводного баланса банка по валютам."""
import unittest
from datetime import date, datetime
from decimal import Decimal

from bank.accounts import Currency
from bank.bank import Bank
from bank.clients import Client


class BalancesTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.current)

    def register(self, name):
        client = Client(name, date(2000, 1, 1), {"email": "test@example.com"})
        self.bank.add_client(client, "password")
        return self.bank.authenticate_client(client.client_id, "password")

    def test_empty_bank(self):
        self.assertEqual(self.bank.get_total_balance(), {
            currency.value: Decimal("0") for currency in Currency
        })

    def test_currencies_portfolio_debt_and_frozen_accounts(self):
        first = self.register("Иван")
        second = self.register("Анна")
        standard = self.bank.open_account(first, "standard", balance=Decimal("1000"))
        self.bank.freeze_account(first, standard["account_id"])
        self.bank.open_account(second, "savings", balance=Decimal("200"))
        investment = self.bank.open_account(second, "investment", balance=Decimal("500"))
        before = self.bank.get_total_balance()
        self.bank.invest(second, investment["account_id"], "stocks", Decimal("300"))
        self.assertEqual(self.bank.get_total_balance(), before)
        premium = self.bank.open_account(first, "premium")
        self.bank.withdraw(first, premium["account_id"], Decimal("100"))
        self.bank.open_account(second, "standard", currency=Currency.USD, balance=Decimal("20"))
        closed = self.bank.open_account(first, "standard")
        self.bank.close_account(first, closed["account_id"])
        expected = {currency.value: Decimal("0") for currency in Currency}
        expected.update(RUB=Decimal("1550"), USD=Decimal("20"))
        accounts_before = {key: value.get_account_info() for key, value in self.bank._accounts.items()}
        self.current = datetime(2026, 11, 13, 1, tzinfo=Bank.BANK_TIMEZONE)
        result = self.bank.get_total_balance()
        self.assertEqual(result, expected)
        result["RUB"] = Decimal("999999")
        self.assertEqual(self.bank.get_total_balance(), expected)
        self.assertEqual(
            {key: value.get_account_info() for key, value in self.bank._accounts.items()},
            accounts_before,
        )

    def test_negative_total_is_preserved(self):
        token = self.register("Иван")
        account = self.bank.open_account(token, "premium")
        self.bank.withdraw(token, account["account_id"], Decimal("100"))
        self.assertEqual(self.bank.get_total_balance()["RUB"], Decimal("-150"))


if __name__ == "__main__":
    unittest.main()
