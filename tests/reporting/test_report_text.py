"""Читаемый текст из снимков трёх типов отчётов."""

import unittest
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import InvalidOperationError
from bank.reporting.builder import ReportBuilder
from bank.models.transactions import ExternalRecipient, TransactionType


class ReportTextTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.now)
        client = Client("Иван Петров", date(1990, 1, 1), {"email": "ivan@example.com"})
        self.bank.add_client(client, "private-password")
        self.token = self.bank.authenticate_client(client.client_id, "private-password")
        self.builder = ReportBuilder(self.bank, now=lambda: self.now)

    def open(self, balance="0"):
        return self.bank.open_account(self.token, "standard", balance=Decimal(balance))["account_id"]

    def test_client_text_has_identity_timezone_tables_and_money(self):
        account = self.open("1234.567")
        self.bank.deposit(self.token, account, Decimal("10"))
        report = self.builder.build_client_report(self.token)
        text = self.builder.to_text(report)
        for expected in ("Отчёт по клиенту", "Иван Петров", "2026-11-17 12:00:00+03:00",
                         "Счета", "Обычный", "История свободного баланса", "Открытие",
                         "Пополнение", "1244.57", "RUB"):
            self.assertIn(expected, text)
        self.assertEqual(report["accounts"][0]["balance"], Decimal("1244.567"))

    def test_bank_text_has_counts_statuses_and_top_three_by_currency(self):
        account = self.open("1000")
        self.bank.create_transaction(self.token, TransactionType.DEPOSIT, Decimal("10"), Currency.RUB,
                                    recipient_account_id=account)
        self.bank.process_transactions()
        text = self.builder.to_text(self.builder.build_bank_report())
        for expected in ("Отчёт по банку", "Клиентов: 1", "Счетов: 1", "Поручений: 1",
                         "Выполнена", "Топ-3 по каждой валюте", "Иван Петров", "1010.00"):
            self.assertIn(expected, text)

    def test_risk_text_has_translated_reasons_period_and_failure_statistics(self):
        sender = self.open("1000")
        recipient = self.open()
        self.bank.create_transaction(self.token, TransactionType.INTERNAL_TRANSFER, Decimal("100000"),
                                    Currency.RUB, sender_account_id=sender, recipient_account_id=recipient)
        self.bank.process_transactions()
        report = self.builder.build_risk_report(self.token, start_at=self.now, end_at=self.now)
        text = self.builder.to_text(report)
        for expected in ("Отчёт по рискам", "границы включены", "Крупная сумма", "Новый получатель",
                         "Высокий", "Отклонена", "HighRiskTransactionError", "Неудачных попыток: 1",
                         "Окончательных отказов: 1", "Первая оценка", "Блокировка"):
            self.assertIn(expected, text)

    def test_empty_reports_have_explicit_messages(self):
        reports = [self.builder.build_client_report(self.token), self.builder.build_risk_report(self.token),
                   ReportBuilder(Bank(), now=lambda: self.now).build_bank_report()]
        for report in reports:
            with self.subTest(report_type=report["report_type"]):
                self.assertIn("Нет данных.", self.builder.to_text(report))
        self.assertIn("Максимальный риск: Нет оценок", self.builder.to_text(reports[1]))
        self.assertIn("Период: без начала — без конца", self.builder.to_text(reports[1]))

    def test_formatting_is_repeatable_and_does_not_read_bank_or_mutate_snapshot(self):
        self.open("100")
        reports = [self.builder.build_client_report(self.token), self.builder.build_bank_report(),
                   self.builder.build_risk_report(self.token)]
        before = deepcopy(reports)
        with patch.object(self.bank, "get_client_info", side_effect=AssertionError("Повторное чтение")), \
             patch.object(self.bank, "get_bank_snapshot", side_effect=AssertionError("Повторное чтение")), \
             patch.object(self.builder, "_now", side_effect=AssertionError("Новая дата")):
            for report in reports:
                self.assertEqual(self.builder.to_text(report), self.builder.to_text(report))
        self.assertEqual(reports, before)

    def test_failed_fee_is_labelled_as_calculation_and_not_added_to_summary(self):
        account = self.open("1000")
        self.bank.create_transaction(self.token, TransactionType.EXTERNAL_TRANSFER, Decimal("5000"),
                                    Currency.RUB, sender_account_id=account,
                                    external_recipient=ExternalRecipient("123", "bank"))
        self.bank.process_transactions()
        report = self.builder.build_client_report(self.token)
        text = self.builder.to_text(report)
        self.assertEqual(report["summary"]["fees_by_currency"]["RUB"], Decimal("0"))
        self.assertIn("комиссия — расчёт, списание только при успехе", text)
        self.assertIn("50.00", text)
        self.assertIn("Причина отказа", text)

    def test_negative_balance_and_separate_currency_rows(self):
        account = self.bank.open_account(self.token, "premium")["account_id"]
        self.bank.withdraw(self.token, account, Decimal("100"))
        self.bank.open_account(self.token, "standard", currency=Currency.USD, balance=Decimal("10"))
        text = self.builder.to_text(self.builder.build_client_report(self.token))
        self.assertIn("-150.00", text)
        self.assertIn("10.00", text)
        self.assertIn("RUB", text)
        self.assertIn("USD", text)

    def test_text_does_not_expose_credentials(self):
        text = self.builder.to_text(self.builder.build_client_report(self.token))
        self.assertNotIn("private-password", text)
        self.assertNotIn(self.token, text)

    def test_invalid_report_has_clear_bank_error(self):
        valid = self.builder.build_client_report(self.token)
        for report in (None, [], {}, {"report_type": []}, {"report_type": "unknown"},
                       {**valid, "generated_at": datetime(2026, 11, 17)},
                       {**valid, "summary": {}}, {**valid, "accounts": [None]}):
            with self.subTest(report=report), self.assertRaises(InvalidOperationError):
                self.builder.to_text(report)


if __name__ == "__main__":
    unittest.main()
