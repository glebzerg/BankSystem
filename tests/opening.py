"""Проверки открытия счетов: python -B -m unittest tests.opening -v."""
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from bank.accounts import BankAccount, Currency
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuthenticationError, InvalidOperationError


class OpeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bank = Bank()
        self.client = Client("Иван", date(2000, 1, 1), {"phone": "+79991234567"})
        self.bank.add_client(self.client, "password")
        self.token = self.bank.authenticate_client(self.client.client_id, "password")

    def test_all_types_and_owner_links(self) -> None:
        for kind, name, settings in [
            ("standard", "BankAccount", {}),
            ("savings", "SavingsAccount", {"min_balance": Decimal("100")}),
            ("premium", "PremiumAccount", {"fixed_fee": Decimal("25")}),
            ("investment", "InvestmentAccount", {}),
        ]:
            info = self.bank.open_account(
                self.token, kind, Currency.USD, Decimal("1000"), **settings,
            )
            self.assertEqual(info["account_type"], name)
            self.assertEqual(info["currency"], "USD")
            self.assertEqual(info["owner"], self.client.full_name)
            self.assertIn(info["account_id"], self.client.account_numbers)
            self.assertEqual(self.bank._account_owners[info["account_id"]], self.client.client_id)
        self.assertEqual(len(self.client.account_numbers), 4)
        info["portfolio"]["stocks"] = Decimal("999")
        self.assertEqual(self.bank._get_account(info["account_id"]).portfolio["stocks"], 0)

    def test_failures_leave_no_accounts(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.bank.open_account("invalid", "standard")
        for kind, settings in [
            ("unknown", {}), ([], {}), ("standard", {"owner": "Другой"}),
            ("standard", {"balance": Decimal("-1")}),
            ("savings", {"min_balance": Decimal("100")}),
        ]:
            with self.subTest(kind=kind), self.assertRaises(InvalidOperationError):
                self.bank.open_account(self.token, kind, **settings)
        self.assertEqual(self.client.account_numbers, [])
        self.assertEqual(self.bank._accounts, {})
        self.assertEqual(self.bank._account_owners, {})

    def test_duplicate_account_number_is_regenerated(self) -> None:
        with patch.object(BankAccount, "_generate_id", side_effect=[
            "000000000001", "000000000001", "000000000002",
        ]):
            first = self.bank.open_account(self.token, "standard")
            second = self.bank.open_account(self.token, "standard")
        self.assertNotEqual(first["account_id"], second["account_id"])
        self.assertEqual(len(self.bank._accounts), 2)


if __name__ == "__main__":
    unittest.main()
