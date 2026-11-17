"""Линия фактического баланса и сохранение PNG с очисткой фигур."""

import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from bank.bank import Bank
from bank.clients import Client
from bank.exceptions import InvalidOperationError, ReportExportError
from bank.report_builder import ReportBuilder


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
        with patch("bank.report_charts.build_charts", return_value=figures):
            paths = self.builder.save_charts(report, self.folder / "charts")
        self.assertTrue(all(not figure.axes for figure in figures.values()))
        self.assertEqual(self.builder.save_charts(report, self.folder / "charts"), paths)
        self.assertEqual(len(list((self.folder / "charts").glob("*.png"))), len(paths))

    def test_write_failure_preserves_existing_png_and_clears_all_figures(self):
        report = self.builder.build_client_report(self.token)
        figures = self.charts(report)
        existing = self.folder / "status_distribution.png"
        existing.write_bytes(b"original")
        with patch("bank.report_charts.build_charts", return_value=figures), \
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
