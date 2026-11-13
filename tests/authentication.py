"""Проверки входа: python -B -m unittest tests.authentication -v."""
import unittest
from datetime import date, datetime

from bank.bank import Bank
from bank.clients import Client, ClientStatus
from bank.exceptions import AuthenticationError, ClientBlockedError


class AuthenticationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bank = Bank(
            now=lambda: datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
        )
        self.client = Client(
            full_name="Иван Петров",
            birth_date=date(2000, 5, 15),
            contacts={"phone": "+79991234567"},
        )
        self.password = "ExamplePassword123"
        self.bank.add_client(self.client, self.password)

    def login(self) -> str:
        return self.bank.authenticate_client(self.client.client_id, self.password)

    def wrong_password(self) -> None:
        with self.assertRaises(AuthenticationError) as caught:
            self.bank.authenticate_client(self.client.client_id, "WrongPassword")
        self.assertNotIsInstance(caught.exception, ClientBlockedError)

    def test_success_resets_consecutive_failures(self) -> None:
        self.wrong_password()
        self.wrong_password()
        token = self.login()
        self.assertIs(self.bank._get_authenticated_client(token), self.client)
        self.wrong_password()
        self.wrong_password()
        self.assertIs(self.client.status, ClientStatus.ACTIVE)
        second_token = self.login()
        self.assertNotEqual(token, second_token)
        self.assertIs(self.bank._get_authenticated_client(second_token), self.client)

    def test_third_failure_blocks_and_revokes_only_client_sessions(self) -> None:
        tokens = [self.login(), self.login()]
        other = Client("Анна", date(1990, 1, 1), {"email": "anna@example.com"})
        self.bank.add_client(other, self.password)
        other_token = self.bank.authenticate_client(other.client_id, self.password)

        self.wrong_password()
        self.wrong_password()
        with self.assertRaises(ClientBlockedError):
            self.bank.authenticate_client(self.client.client_id, "")
        self.assertIs(self.client.status, ClientStatus.BLOCKED)
        with self.assertRaises(ClientBlockedError):
            self.login()
        for token in tokens:
            with self.assertRaises(AuthenticationError):
                self.bank._get_authenticated_client(token)
        self.assertIs(self.bank._get_authenticated_client(other_token), other)

    def test_invalid_credentials_and_sessions(self) -> None:
        for client_id, password in [("unknown", self.password), ([], self.password),
                                    ("", self.password), (self.client.client_id, None)]:
            with self.subTest(client_id=client_id), self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(client_id, password)
        for token in (None, [], "", "unknown"):
            with self.subTest(token=token), self.assertRaises(AuthenticationError):
                self.bank._get_authenticated_client(token)
        self.assertIs(self.client.status, ClientStatus.ACTIVE)
        self.assertIs(self.bank._get_authenticated_client(self.login()), self.client)

    def test_session_checks_current_client_status(self) -> None:
        token = self.login()
        self.client._status = ClientStatus.BLOCKED
        with self.assertRaises(ClientBlockedError):
            self.bank._get_authenticated_client(token)


if __name__ == "__main__":
    unittest.main()
