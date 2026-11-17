"""Данные диаграмм, пустые выборки и реальный рендер PNG без окон."""

import io
import unittest
import warnings
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from PIL import Image
from matplotlib.backends.backend_agg import FigureCanvasAgg

from bank.accounts import Currency
from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import InvalidOperationError
from bank.report_builder import ReportBuilder
from bank.transactions import TransactionType


class ReportChartsTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.now)
        client = Client("Иван Петров", date(1990, 1, 1), {"phone": "123"})
        self.bank.add_client(client, "password")
        self.token = self.bank.authenticate_client(client.client_id, "password")
        self.builder = ReportBuilder(self.bank, now=lambda: self.now)

    def charts(self, report):
        charts = self.builder.build_charts(report)
        for figure in charts.values():
            self.addCleanup(figure.clear)
        return charts

    def open(self, kind="standard", **kwargs):
        return self.bank.open_account(self.token, kind, **kwargs)["account_id"]

    def test_pie_proportions_match_transaction_statuses_and_omit_zero_counts(self):
        account = self.open(balance=Decimal("1000"))
        for amount in ("10", "20"):
            self.bank.create_transaction(self.token, TransactionType.DEPOSIT, Decimal(amount), Currency.RUB,
                                        recipient_account_id=account)
        self.bank.create_transaction(self.token, TransactionType.WITHDRAWAL, Decimal("2000"), Currency.RUB,
                                    sender_account_id=account)
        self.bank.process_transactions()
        figure = self.charts(self.builder.build_bank_report())["status_distribution"]
        axes = figure.axes[0]
        self.assertEqual(len(axes.patches), 2)
        angles = [wedge.theta2 - wedge.theta1 for wedge in axes.patches]
        self.assertAlmostEqual(angles[0] / sum(angles), 2 / 3, places=5)
        self.assertEqual([text.get_text() for text in axes.get_legend().get_texts()],
                         ["Выполнены: 2", "Отклонены: 1"])

    def test_bank_bars_include_portfolio_and_keep_currencies_separate(self):
        account = self.open("investment", balance=Decimal("1000"))
        self.bank.invest(self.token, account, "stocks", Decimal("250"))
        self.open(currency=Currency.USD, balance=Decimal("10"))
        charts = self.charts(self.builder.build_bank_report())
        self.assertEqual(set(charts), {"status_distribution", "client_balances_RUB", "client_balances_USD"})
        self.assertEqual(charts["client_balances_RUB"].axes[0].patches[0].get_width(), 1000)
        self.assertEqual(charts["client_balances_USD"].axes[0].patches[0].get_width(), 10)
        self.assertEqual(charts["client_balances_USD"].axes[0].get_xlabel(), "USD")

    def test_client_bars_use_free_balance_and_show_overdraft_below_zero(self):
        investment = self.open("investment", balance=Decimal("500"))
        premium = self.open("premium")
        self.bank.invest(self.token, investment, "stocks", Decimal("200"))
        self.bank.withdraw(self.token, premium, Decimal("100"))
        figure = self.charts(self.builder.build_client_report(self.token))["account_balances_RUB"]
        self.assertEqual([bar.get_width() for bar in figure.axes[0].patches], [300, -150])
        self.assertLess(figure.axes[0].get_xlim()[0], -150)

    def test_risk_bar_counts_unique_operations_by_level(self):
        sender = self.open(balance=Decimal("1000"))
        recipient = self.open()
        for amount in ("100000", "100"):
            self.bank.create_transaction(self.token, TransactionType.INTERNAL_TRANSFER, Decimal(amount),
                                        Currency.RUB, sender_account_id=sender, recipient_account_id=recipient)
        self.bank.process_transactions()
        figure = self.charts(self.builder.build_risk_report(self.token))["risk_distribution"]
        self.assertEqual([bar.get_height() for bar in figure.axes[0].patches], [0, 1, 1])
        self.assertEqual([label.get_text() for label in figure.axes[0].get_xticklabels()],
                         ["Низкий", "Средний", "Высокий"])

    def test_empty_reports_have_messages_instead_of_invalid_pie(self):
        for report in (self.builder.build_client_report(self.token), self.builder.build_bank_report(),
                       self.builder.build_risk_report(self.token)):
            for figure in self.charts(report).values():
                axes = figure.axes[0]
                self.assertEqual(len(axes.patches), 0)
                self.assertTrue(any("Нет" in text.get_text() for text in axes.texts))

    def test_zero_balance_is_real_bar_data_and_not_empty_message(self):
        self.open()
        figure = self.charts(self.builder.build_client_report(self.token))["account_balances_RUB"]
        self.assertEqual(len(figure.axes[0].patches), 1)
        self.assertEqual(figure.axes[0].patches[0].get_width(), 0)

    def test_figures_render_readable_png_and_do_not_need_gui(self):
        self.open(balance=Decimal("1000"))
        reports = [self.builder.build_bank_report(), self.builder.build_client_report(self.token),
                   self.builder.build_risk_report(self.token)]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for report in reports:
                for figure in self.charts(report).values():
                    self.assertIsInstance(figure.canvas, FigureCanvasAgg)
                    buffer = io.BytesIO()
                    figure.savefig(buffer, format="png")
                    buffer.seek(0)
                    with Image.open(buffer) as picture:
                        self.assertEqual(picture.format, "PNG")
                        self.assertGreater(picture.width, 500)
                        self.assertGreater(picture.height, 300)
                        self.assertIsNotNone(picture.convert("RGB").getbbox())
            self.assertFalse(any("Glyph" in str(warning.message) for warning in caught))

    def test_charts_do_not_read_bank_or_mutate_decimal_snapshot(self):
        self.open(balance=Decimal("0.123456789"))
        report = self.builder.build_bank_report()
        before = deepcopy(report)
        with patch.object(self.bank, "get_bank_snapshot", side_effect=AssertionError("Чтение банка")):
            self.charts(report)
        self.assertEqual(report, before)

    def test_invalid_reports_and_non_finite_values_are_rejected(self):
        self.open(balance=Decimal("1000"))
        valid = self.builder.build_bank_report()
        for report in (None, {}, {**valid, "report_type": "unknown"},
                       {**valid, "generated_at": datetime(2026, 11, 17)}):
            with self.subTest(report=report), self.assertRaises(InvalidOperationError):
                self.builder.build_charts(report)
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("1e1000")):
            report = deepcopy(valid)
            report["client_balances_by_currency"]["RUB"][0]["total_balance"] = value
            with self.subTest(value=value), self.assertRaises(InvalidOperationError):
                self.builder.build_charts(report)
        valid["summary"]["transactions_by_status"]["completed"] = -1
        with self.assertRaises(InvalidOperationError):
            self.builder.build_charts(valid)


if __name__ == "__main__":
    unittest.main()
