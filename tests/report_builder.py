"""Данные клиентского отчёта: суммы, снимки и доступ по сессии."""

import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from bank.accounts import Currency
from bank.audit import AuditLog
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import AuthenticationError, InvalidOperationError
from bank.report_builder import ReportBuilder
from bank.transactions import TransactionType


class ClientReportTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.audit = AuditLog()
        self.bank = Bank(now=lambda: self.now, audit_log=self.audit)
        self.builder = ReportBuilder(self.bank, now=lambda: self.now)
        self.clients = []
        self.tokens = []
        for name in ("Иван", "Анна"):
            client = Client(name, date(1990, 1, 1), {"email": f"{name}@example.com"})
            self.bank.add_client(client, "private-password")
            self.clients.append(client)
            self.tokens.append(self.bank.authenticate_client(client.client_id, "private-password"))

    def open(self, kind="standard", owner=0, **kwargs):
        return self.bank.open_account(self.tokens[owner], kind, **kwargs)["account_id"]

    def create(self, kind, amount, **participants):
        return self.bank.create_transaction(self.tokens[0], kind, Decimal(amount), Currency.RUB,
                                            **participants)["transaction_id"]

    def test_empty_report_has_identity_time_and_zero_summary(self):
        report = self.builder.build_client_report(self.tokens[0])
        self.assertEqual(report["report_type"], "client")
        self.assertEqual(report["generated_at"], self.now)
        self.assertEqual(report["client"], {"client_id": self.clients[0].client_id,
                                             "full_name": "Иван", "status": "active"})
        self.assertEqual(report["accounts"], [])
        self.assertEqual(report["transactions"], [])
        self.assertEqual(report["balance_history"], [])
        summary = report["summary"]
        self.assertEqual(summary["account_count"], 0)
        self.assertEqual(summary["transaction_count"], 0)
        self.assertEqual(sum(summary["transactions_by_status"].values()), 0)
        self.assertEqual(summary["total_by_currency"], {currency.value: Decimal("0") for currency in Currency})

    def test_totals_keep_currencies_precision_and_portfolio_separate(self):
        standard = self.open(balance=Decimal("1000"))
        investment = self.open("investment", balance=Decimal("500"))
        self.open(currency=Currency.USD, balance=Decimal("10"))
        self.open(owner=1, balance=Decimal("999999"))
        self.bank.invest(self.tokens[0], investment, "stocks", Decimal("200"))
        self.bank.deposit(self.tokens[0], standard, Decimal("0.123"))
        report = self.builder.build_client_report(self.tokens[0])
        summary = report["summary"]
        self.assertEqual(summary["account_count"], 3)
        self.assertEqual(summary["balances_by_currency"]["RUB"], Decimal("1300.123"))
        self.assertEqual(summary["total_by_currency"]["RUB"], Decimal("1500.123"))
        self.assertEqual(summary["total_by_currency"]["USD"], Decimal("10"))
        self.assertTrue(all(isinstance(value, Decimal) for value in summary["total_by_currency"].values()))
        points = [point for point in report["balance_history"] if point["account_id"] == investment]
        self.assertEqual(points[-1]["balance"], Decimal("300"))
        self.assertEqual(points[-1]["operation_type"], "investment")

    def test_summary_counts_completed_failed_cancelled_and_pending_transactions(self):
        account = self.open(balance=Decimal("1000"))
        self.create(TransactionType.DEPOSIT, "100", recipient_account_id=account)
        self.create(TransactionType.WITHDRAWAL, "2000", sender_account_id=account)
        cancelled = self.create(TransactionType.DEPOSIT, "1", recipient_account_id=account)
        self.bank.cancel_transaction(self.tokens[0], cancelled)
        self.bank.process_transactions()
        self.create(TransactionType.DEPOSIT, "1", recipient_account_id=account)
        report = self.builder.build_client_report(self.tokens[0])
        counts = report["summary"]["transactions_by_status"]
        self.assertEqual(report["summary"]["transaction_count"], 4)
        for status in ("completed", "failed", "cancelled", "queued"):
            self.assertEqual(counts[status], 1)
        self.assertEqual(sum(counts.values()), 4)
        self.assertEqual(report["summary"]["total_by_currency"]["RUB"], Decimal("1100"))

    def test_incoming_transfer_appears_in_balance_without_foreign_transaction_details(self):
        recipient = self.open()
        sender = self.open(owner=1, balance=Decimal("1000"))
        self.bank.create_transaction(self.tokens[1], TransactionType.INTERNAL_TRANSFER,
                                    Decimal("100"), Currency.RUB,
                                    sender_account_id=sender, recipient_account_id=recipient)
        self.bank.process_transactions()
        report = self.builder.build_client_report(self.tokens[0])
        self.assertEqual(report["transactions"], [])
        self.assertEqual(report["summary"]["total_by_currency"]["RUB"], Decimal("100"))
        self.assertEqual([point["balance"] for point in report["balance_history"]],
                         [Decimal("0"), Decimal("100")])
        self.assertNotIn(sender, repr(report))
        self.assertNotIn("Анна", repr(report))

    def test_snapshots_and_nested_data_do_not_change_bank_or_previous_reports(self):
        investment = self.open("investment", balance=Decimal("500"))
        self.bank.invest(self.tokens[0], investment, "stocks", Decimal("100"))
        old = self.builder.build_client_report(self.tokens[0])
        self.now += timedelta(minutes=1)
        self.bank.deposit(self.tokens[0], investment, Decimal("10"))
        self.assertEqual(old["summary"]["total_by_currency"]["RUB"], Decimal("500"))
        self.assertEqual(len(old["balance_history"]), 2)
        old["accounts"][0]["portfolio"]["stocks"] = Decimal("9999")
        old["balance_history"][0]["balance"] = Decimal("9999")
        old["client"]["full_name"] = "Изменено"
        new = self.builder.build_client_report(self.tokens[0])
        self.assertEqual(new["summary"]["total_by_currency"]["RUB"], Decimal("510"))
        self.assertEqual(new["accounts"][0]["portfolio"]["stocks"], Decimal("100"))
        self.assertEqual(new["balance_history"][0]["balance"], Decimal("500"))
        self.assertEqual(new["client"]["full_name"], "Иван")

    def test_closed_accounts_remain_visible_and_overdraft_stays_negative(self):
        closed = self.open()
        self.bank.close_account(self.tokens[0], closed)
        premium = self.open("premium")
        self.bank.withdraw(self.tokens[0], premium, Decimal("100"))
        report = self.builder.build_client_report(self.tokens[0])
        self.assertEqual(report["summary"]["account_count"], 2)
        self.assertEqual(report["summary"]["total_by_currency"]["RUB"], Decimal("-150"))
        self.assertEqual(report["accounts"][0]["status"], "closed")
        self.assertTrue(any(point["account_id"] == closed for point in report["balance_history"]))

    def test_report_does_not_process_queue_or_write_audit_or_expose_credentials(self):
        account = self.open(balance=Decimal("1000"))
        self.create(TransactionType.WITHDRAWAL, "100", sender_account_id=account)
        before = self.audit.records
        report = self.builder.build_client_report(self.tokens[0])
        self.assertEqual(self.audit.records, before)
        self.assertEqual(report["transactions"][0]["status"], "queued")
        self.assertEqual(report["summary"]["total_by_currency"]["RUB"], Decimal("1000"))
        for secret in ("private-password", *self.tokens):
            self.assertNotIn(secret, repr(report))

    def test_invalid_and_revoked_sessions_cannot_read_report(self):
        for token in (None, [], "", "unknown"):
            with self.subTest(token=token), self.assertRaises(AuthenticationError):
                self.builder.build_client_report(token)
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                self.bank.authenticate_client(self.clients[0].client_id, "wrong")
        with self.assertRaises(AuthenticationError):
            self.builder.build_client_report(self.tokens[0])

    def test_report_time_uses_configured_clock_and_normalizes_timezone(self):
        builder = ReportBuilder(self.bank, now=lambda: self.now.astimezone(timezone.utc))
        self.assertEqual(builder.build_client_report(self.tokens[0])["generated_at"].tzinfo,
                         Bank.BANK_TIMEZONE)
        default = ReportBuilder(self.bank).build_client_report(self.tokens[0])
        self.assertIsNotNone(default["generated_at"].utcoffset())

    def test_invalid_configuration_and_clock_result(self):
        for value in (None, {}, "bank"):
            with self.subTest(value=value), self.assertRaises(InvalidOperationError):
                ReportBuilder(value)
        with self.assertRaises(InvalidOperationError):
            ReportBuilder(self.bank, now=self.now)
        for value in (None, "today", datetime(2026, 11, 17)):
            builder = ReportBuilder(self.bank, now=lambda: value)
            with self.subTest(value=value), self.assertRaises(InvalidOperationError):
                builder.build_client_report(self.tokens[0])


if __name__ == "__main__":
    unittest.main()
