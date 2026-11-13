"""Проверки рейтинга клиентов по выбранной валюте."""
import unittest
from datetime import date, datetime
from decimal import Decimal

from bank.accounts import Currency
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuthenticationError, InvalidOperationError


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.current)

    def register(self, number):
        client = Client(
            "Иван", date(2000, 1, 1), {"email": "test@example.com"},
            client_id=f"{number:012d}",
        )
        self.bank.add_client(client, "password")
        token = self.bank.authenticate_client(client.client_id, "password")
        return client, token

    def test_empty_bank_and_invalid_currency(self):
        self.assertEqual(self.bank.get_clients_ranking(), [])
        for currency in ("RUB", None, []):
            with self.subTest(currency=currency), self.assertRaises(InvalidOperationError):
                self.bank.get_clients_ranking(currency)

    def test_ranking_uses_all_accounts_portfolio_debt_and_currency(self):
        first, first_token = self.register(1)
        second, second_token = self.register(2)
        third, third_token = self.register(3)
        self.bank.open_account(first_token, "standard", balance=Decimal("100"))
        savings = self.bank.open_account(first_token, "savings", balance=Decimal("200"))
        self.bank.freeze_account(first_token, savings["account_id"])
        investment = self.bank.open_account(second_token, "investment", balance=Decimal("500"))
        self.bank.invest(second_token, investment["account_id"], "stocks", Decimal("400"))
        debt = self.bank.open_account(third_token, "premium")
        self.bank.withdraw(third_token, debt["account_id"], Decimal("100"))
        self.bank.open_account(third_token, "standard", currency=Currency.USD, balance=Decimal("999"))
        closed = self.bank.open_account(second_token, "standard")
        self.bank.close_account(second_token, closed["account_id"])
        # Блокировка не должна исключать средства из банковской аналитики.
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(second.client_id, "wrong")
        before = {key: value.get_account_info() for key, value in self.bank._accounts.items()}
        self.current = datetime(2026, 11, 13, 1, tzinfo=Bank.BANK_TIMEZONE)
        ranking = self.bank.get_clients_ranking()
        self.assertEqual([row["client_id"] for row in ranking], [second.client_id, first.client_id, third.client_id])
        self.assertEqual([row["total_balance"] for row in ranking], [Decimal("500"), Decimal("300"), Decimal("-150")])
        usd = self.bank.get_clients_ranking(Currency.USD)
        self.assertEqual([row["client_id"] for row in usd], [third.client_id, first.client_id, second.client_id])
        self.assertEqual([row["total_balance"] for row in usd], [Decimal("999"), Decimal("0"), Decimal("0")])
        ranking[0]["total_balance"] = Decimal("999999")
        self.assertEqual(self.bank.get_clients_ranking()[0]["total_balance"], Decimal("500"))
        self.assertEqual({key: value.get_account_info() for key, value in self.bank._accounts.items()}, before)

    def test_equal_totals_are_sorted_by_id(self):
        second, _ = self.register(2)
        first, _ = self.register(1)
        self.assertEqual(
            [row["client_id"] for row in self.bank.get_clients_ranking()],
            [first.client_id, second.client_id],
        )


if __name__ == "__main__":
    unittest.main()
