"""Проверки одной подсистемы, объединённые без изменения сценариев."""

import io
import unittest
import warnings
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from PIL import Image
from matplotlib.backends.backend_agg import FigureCanvasAgg
from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import InvalidOperationError, ReportExportError
from bank.reporting.builder import ReportBuilder
from bank.models.transactions import TransactionType
from pathlib import Path
from tempfile import TemporaryDirectory


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


class BalanceChartsTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.now)
        client = Client("Иван", date(1990, 1, 1), {"phone": "123"})
        self.bank.add_client(client, "password")
        self.token = self.bank.authenticate_client(client.client_id, "password")
        self.builder = ReportBuilder(self.bank, now=lambda: self.now)
        self.account = self.bank.open_account(self.token, "premium", balance=Decimal("100"))["account_id"]

    def charts(self, report, **kwargs):
        charts = self.builder.build_charts(report, **kwargs)
        for figure in charts.values():
            self.addCleanup(figure.clear)
        return charts

    def test_step_line_uses_real_balances_with_fee_and_overdraft(self):
        self.now += timedelta(minutes=1)
        self.bank.deposit(self.token, self.account, Decimal("50"))
        self.now += timedelta(minutes=1)
        self.bank.withdraw(self.token, self.account, Decimal("200"))
        report = self.builder.build_client_report(self.token)
        figure = self.charts(report)[f"balance_history_{self.account}"]
        axes = figure.axes[0]
        line = axes.lines[0]
        self.assertEqual(list(line.get_ydata()), [100, 150, -100])
        self.assertEqual(line.get_drawstyle(), "steps-post")
        self.assertEqual(list(line.get_xdata()), [point["occurred_at"] for point in report["balance_history"]])
        self.assertIn("RUB", axes.get_ylabel())
        self.assertIn("MSK", axes.get_xlabel())

    def test_points_are_sorted_stably_and_timezone_is_preserved(self):
        self.bank.deposit(self.token, self.account, Decimal("1"))
        self.bank.deposit(self.token, self.account, Decimal("2"))
        report = self.builder.build_client_report(self.token)
        for point in report["balance_history"]:
            point["occurred_at"] = point["occurred_at"].astimezone(timezone.utc)
        # При одинаковом времени сохраняется исходный порядок операций.
        line = self.charts(report)[f"balance_history_{self.account}"].axes[0].lines[0]
        self.assertEqual(list(line.get_ydata()), [100, 101, 103])
        self.assertTrue(all(time.tzinfo == Bank.BANK_TIMEZONE for time in line.get_xdata()))
        report["balance_history"][0]["occurred_at"] += timedelta(minutes=2)
        report["balance_history"][1]["occurred_at"] += timedelta(minutes=1)
        line = self.charts(report)[f"balance_history_{self.account}"].axes[0].lines[0]
        self.assertEqual(list(line.get_ydata()), [103, 101, 100])

    def test_single_point_and_missing_history_render_without_invented_points(self):
        report = self.builder.build_client_report(self.token)
        figure = self.charts(report)[f"balance_history_{self.account}"]
        self.assertEqual(len(figure.axes[0].lines[0].get_ydata()), 1)
        figure.canvas.draw()
        report["balance_history"] = []
        axes = self.charts(report)[f"balance_history_{self.account}"].axes[0]
        self.assertEqual(len(axes.lines), 0)
        self.assertTrue(any("Нет истории" in text.get_text() for text in axes.texts))

    def test_account_selection_restricts_only_timelines_to_snapshot_accounts(self):
        other = self.bank.open_account(self.token, "standard")["account_id"]
        report = self.builder.build_client_report(self.token)
        charts = self.charts(report, account_id=other)
        self.assertIn(f"balance_history_{other}", charts)
        self.assertNotIn(f"balance_history_{self.account}", charts)
        self.assertIn("status_distribution", charts)
        for identifier in ("missing", "", 123):
            with self.subTest(identifier=identifier), self.assertRaises(InvalidOperationError):
                self.builder.build_charts(report, account_id=identifier)
        with self.assertRaises(InvalidOperationError):
            self.builder.build_charts(self.builder.build_bank_report(), account_id=self.account)
        with self.assertRaises(InvalidOperationError):
            self.builder.build_charts({**report, "accounts": [None]}, account_id=self.account)

    def test_saved_pngs_are_readable_for_all_report_types(self):
        reports = [self.builder.build_client_report(self.token), self.builder.build_bank_report(),
                   self.builder.build_risk_report(self.token)]
        before = deepcopy(reports)
        for report in reports:
            folder = self.folder / report["report_type"]
            paths = self.builder.save_charts(report, folder)
            self.assertTrue(paths)
            self.assertEqual(set(paths), set(folder.glob("*.png")))
            for path in paths:
                with Image.open(path) as picture:
                    self.assertEqual(picture.format, "PNG")
                    self.assertGreater(picture.width, 500)
                    self.assertAlmostEqual(picture.info["dpi"][0], 150, delta=1)
                    picture.load()
        self.assertEqual(reports, before)

    def test_figures_are_cleared_after_success_and_repeat_export_replaces_files(self):
        report = self.builder.build_client_report(self.token)
        figures = self.charts(report)
        with patch("bank.reporting.charts.build_charts", return_value=figures):
            paths = self.builder.save_charts(report, self.folder / "charts")
        self.assertTrue(all(not figure.axes for figure in figures.values()))
        self.assertEqual(self.builder.save_charts(report, self.folder / "charts"), paths)
        self.assertEqual(len(list((self.folder / "charts").glob("*.png"))), len(paths))

    def test_write_failure_preserves_existing_png_and_clears_all_figures(self):
        report = self.builder.build_client_report(self.token)
        figures = self.charts(report)
        existing = self.folder / "status_distribution.png"
        existing.write_bytes(b"original")
        with patch("bank.reporting.charts.build_charts", return_value=figures), \
             patch.object(Path, "replace", side_effect=PermissionError("Нет доступа")):
            with self.assertRaises(ReportExportError) as caught:
                self.builder.save_charts(report, self.folder)
        self.assertIsInstance(caught.exception.__cause__, PermissionError)
        self.assertEqual(existing.read_bytes(), b"original")
        self.assertEqual(list(self.folder.glob(".chart-*.tmp")), [])
        self.assertTrue(all(not figure.axes for figure in figures.values()))

    def test_invalid_paths_dpi_and_history_are_rejected(self):
        report = self.builder.build_client_report(self.token)
        for folder in (None, "", " ", 123):
            with self.subTest(folder=folder), self.assertRaises(InvalidOperationError):
                self.builder.save_charts(report, folder)
        for dpi in (0, -1, True, 1.5):
            with self.subTest(dpi=dpi), self.assertRaises(InvalidOperationError):
                self.builder.save_charts(report, self.folder, dpi=dpi)
        for field, value in (("occurred_at", datetime(2026, 11, 17)), ("balance", Decimal("NaN")),
                             ("currency", "USD")):
            changed = deepcopy(report)
            changed["balance_history"][0][field] = value
            with self.subTest(field=field), self.assertRaises(InvalidOperationError):
                self.builder.build_charts(changed)

    def test_chart_names_cannot_escape_export_folder(self):
        report = self.builder.build_client_report(self.token)
        report["accounts"][0]["account_id"] = "../unexpected"
        folder = self.folder / "charts"
        with self.assertRaises(InvalidOperationError):
            self.builder.save_charts(report, folder)
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
