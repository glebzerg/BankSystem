"""Проверки поиска собственных счетов."""
import unittest
from datetime import date, datetime
from decimal import Decimal

from bank.accounts import AccountStatus, Currency
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuthenticationError, InvalidOperationError


class SearchingTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.current)
        client = Client("Иван", date(2000, 1, 1), {"phone": "+79991234567"})
        self.bank.add_client(client, "password")
        self.token = self.bank.authenticate_client(client.client_id, "password")

    def test_filters_and_client_isolation(self):
        standard = self.bank.open_account(self.token, "standard")
        savings = self.bank.open_account(self.token, "savings")
        premium = self.bank.open_account(self.token, "premium", currency=Currency.USD)
        investment = self.bank.open_account(self.token, "investment")
        self.bank.freeze_account(self.token, premium["account_id"])
        self.bank.close_account(self.token, standard["account_id"])
        other = Client("Анна", date(2000, 1, 1), {"phone": "+79991234568"})
        self.bank.add_client(other, "password")
        other_token = self.bank.authenticate_client(other.client_id, "password")
        self.assertEqual(self.bank.search_accounts(other_token), [])
        foreign = self.bank.open_account(other_token, "standard")

        self.assertEqual(len(self.bank.search_accounts(self.token)), 4)
        for kind, info in [("standard", standard), ("savings", savings),
                           ("premium", premium), ("investment", investment)]:
            found = self.bank.search_accounts(self.token, account_type=kind)
            self.assertEqual([item["account_id"] for item in found], [info["account_id"]])
        found = self.bank.search_accounts(
            self.token, account_type="premium", status=AccountStatus.FROZEN, currency=Currency.USD,
        )
        self.assertEqual(found[0]["account_id"], premium["account_id"])
        self.assertEqual(self.bank.search_accounts(self.token, currency=Currency.EUR), [])
        self.assertEqual(self.bank.search_accounts(self.token, account_id=foreign["account_id"]), [])
        self.assertEqual(self.bank.search_accounts(self.token, account_id="unknown"), [])
        self.assertEqual(len(self.bank.search_accounts(self.token, status=AccountStatus.CLOSED)), 1)
        self.assertEqual(self.bank.search_accounts(
            self.token, account_type="premium", status=AccountStatus.ACTIVE,
        ), [])

        # Чтение доступно ночью; изменение результата не меняет счёт.
        self.current = datetime(2026, 11, 13, 1, tzinfo=Bank.BANK_TIMEZONE)
        found = self.bank.search_accounts(self.token, account_id=investment["account_id"])
        found[0]["portfolio"]["stocks"] = Decimal("100")
        found[0]["balance"] = Decimal("100")
        fresh = self.bank.search_accounts(self.token, account_id=investment["account_id"])[0]
        self.assertEqual(fresh["portfolio"]["stocks"], 0)
        self.assertEqual(fresh["balance"], 0)

    def test_invalid_filters_and_session(self):
        with self.assertRaises(AuthenticationError):
            self.bank.search_accounts("invalid")
        for settings in ({"account_type": []}, {"account_type": "unknown"},
                         {"account_id": " "}, {"account_id": 123},
                         {"status": "active"}, {"currency": "RUB"}):
            with self.subTest(settings=settings), self.assertRaises(InvalidOperationError):
                self.bank.search_accounts(self.token, **settings)


if __name__ == "__main__":
    unittest.main()
