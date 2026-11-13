"""Проверки журнала подозрительных действий."""
import unittest
from datetime import date, datetime
from decimal import Decimal

from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuthenticationError, ClientBlockedError, InvalidOperationError


class SecurityEventsTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.current)
        self.clients = [
            Client(name, date(2000, 1, 1), {"phone": phone})
            for name, phone in [("Иван", "+79991234567"), ("Анна", "+79991234568")]
        ]
        self.tokens = []
        for client in self.clients:
            self.bank.add_client(client, "password")
            self.tokens.append(self.bank.authenticate_client(client.client_id, "password"))

    def test_foreign_and_night_events_are_isolated_and_copied(self):
        account = self.bank.open_account(self.tokens[0], "standard")
        with self.assertRaises(InvalidOperationError):
            self.bank.freeze_account(self.tokens[1], account["account_id"])
        self.assertEqual(self.bank.get_suspicious_events(self.tokens[0]), [])
        events = self.bank.get_suspicious_events(self.tokens[1])
        self.assertEqual(events[0]["event_type"], "foreign_account_access")
        self.assertEqual(events[0]["client_id"], self.clients[1].client_id)
        events[0]["description"] = "changed"
        events.clear()
        self.assertNotEqual(self.bank.get_suspicious_events(self.tokens[1])[0]["description"], "changed")

        self.current = datetime(2026, 11, 13, 1, tzinfo=Bank.BANK_TIMEZONE)
        with self.assertRaises(InvalidOperationError):
            self.bank.deposit(self.tokens[0], account["account_id"], Decimal("1"))
        own_events = self.bank.get_suspicious_events(self.tokens[0])
        self.assertEqual(own_events[0]["event_type"], "night_operation")
        self.assertEqual(own_events[0]["time"], self.current)
        with self.assertRaises(AuthenticationError):
            self.bank.get_suspicious_events("invalid")
        newcomer = Client("Олег", date(2000, 1, 1), {"phone": "+79991234569"})
        with self.assertRaises(InvalidOperationError):
            self.bank.add_client(newcomer, "password")
        self.assertIsNone(self.bank._suspicious_events[-1]["client_id"])

    def test_blocking_is_logged_once_without_credentials(self):
        client = self.clients[0]
        for _ in range(2):
            with self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(client.client_id, "wrong")
        self.assertEqual(self.bank._suspicious_events, [])
        with self.assertRaises(ClientBlockedError):
            self.bank.authenticate_client(client.client_id, "wrong")
        with self.assertRaises(ClientBlockedError):
            self.bank.authenticate_client(client.client_id, "password")
        self.assertEqual(len(self.bank._suspicious_events), 1)
        event = self.bank._suspicious_events[0]
        self.assertEqual(event["event_type"], "login_blocked")
        self.assertEqual(event["client_id"], client.client_id)
        self.assertEqual(set(event), {"time", "event_type", "client_id", "description"})
        self.assertNotIn("password", str(event))
        self.assertNotIn(self.tokens[0], str(event))
        with self.assertRaises(AuthenticationError):
            self.bank.get_suspicious_events(self.tokens[0])


if __name__ == "__main__":
    unittest.main()
