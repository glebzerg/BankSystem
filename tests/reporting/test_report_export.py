"""Чтение JSON/CSV обратно, точность, пустые разделы и ошибки записи."""

import csv
import json
import unittest
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import InvalidOperationError, ReportExportError
from bank.reporting.builder import ReportBuilder
from bank.models.transactions import ExternalRecipient, TransactionType


class ReportExportTests(unittest.TestCase):
    def setUp(self):
        hashing = patch.object(Bank, "PASSWORD_HASH_ITERATIONS", 1000)
        hashing.start()
        self.addCleanup(hashing.stop)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.now = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
        self.bank = Bank(now=lambda: self.now)
        self.name = 'Иван; "Петров"\nВторой'
        client = Client(self.name, date(1990, 1, 1), {"email": "ivan@example.com"})
        self.bank.add_client(client, "private-password")
        self.token = self.bank.authenticate_client(client.client_id, "private-password")
        self.builder = ReportBuilder(self.bank, now=lambda: self.now)
        self.account = self.bank.open_account(self.token, "standard", balance=Decimal("1000.123456789"))["account_id"]

    def read_csv(self, folder, name):
        with (folder / f"{name}.csv").open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream, delimiter=";"))

    def test_json_round_trip_preserves_complete_snapshot_money_dates_and_unicode(self):
        for report in (self.builder.build_client_report(self.token), self.builder.build_bank_report(),
                       self.builder.build_risk_report(self.token)):
            with self.subTest(kind=report["report_type"]):
                path = self.folder / report["report_type"] / "report.json"
                self.assertEqual(self.builder.export_to_json(report, str(path)), path)
                content = path.read_text(encoding="utf-8")
                saved = json.loads(content)
                self.assertEqual(set(saved), set(report))
                self.assertEqual(saved["report_type"], report["report_type"])
                self.assertEqual(saved["generated_at"], self.now.isoformat())
                self.assertIn("Иван", content)
                if report["report_type"] != "risk":
                    self.assertEqual(saved["accounts"][0]["owner"], self.name)
                    self.assertEqual(saved["summary"]["total_by_currency"]["RUB"], "1000.123456789")
                    self.assertEqual(saved["accounts"][0]["balance"], "1000.123456789")
                else:
                    self.assertIsNone(saved["summary"]["highest_risk"])

    def test_client_csv_precision_quotes_newlines_portfolio_and_history(self):
        investment = self.bank.open_account(self.token, "investment", balance=Decimal("500"))["account_id"]
        self.bank.invest(self.token, investment, "stocks", Decimal("123.456"))
        report = self.builder.build_client_report(self.token)
        folder = self.folder / "client"
        paths = self.builder.export_to_csv(report, folder)
        self.assertEqual({path.stem for path in paths},
                         {"metadata", "summary", "accounts", "portfolios", "transactions", "balance_history", "risk_reasons"})
        self.assertTrue((folder / "accounts.csv").read_bytes().startswith(b"\xef\xbb\xbf"))
        accounts = self.read_csv(folder, "accounts")
        self.assertEqual(accounts[0]["owner"], self.name)
        self.assertEqual(accounts[0]["balance"], "1000.123456789")
        portfolio = self.read_csv(folder, "portfolios")
        stocks = next(row for row in portfolio if row["asset_type"] == "stocks")
        self.assertEqual(stocks["amount"], "123.456")
        self.assertEqual(stocks["account_id"], investment)
        summary = {row["metric"]: row["value"] for row in self.read_csv(folder, "summary")}
        self.assertEqual(Decimal(summary["total_by_currency.RUB"]), Decimal("1500.123456789"))
        history = self.read_csv(folder, "balance_history")
        self.assertEqual(history[-1]["balance"], "376.544")
        self.assertEqual(history[-1]["occurred_at"], self.now.isoformat())

    def test_transaction_csv_flattens_external_recipient_and_risk_reasons(self):
        transaction = self.bank.create_transaction(
            self.token, TransactionType.EXTERNAL_TRANSFER, Decimal("100"), Currency.RUB,
            sender_account_id=self.account, external_recipient=ExternalRecipient("123", "банк"),
        )
        self.bank.process_transactions()
        folder = self.folder / "transactions"
        self.builder.export_to_csv(self.builder.build_client_report(self.token), folder)
        row = self.read_csv(folder, "transactions")[0]
        self.assertEqual(row["transaction_id"], transaction["transaction_id"])
        self.assertEqual(row["external_recipient.account_number"], "123")
        self.assertEqual(row["external_recipient.bank_id"], "банк")
        self.assertEqual(row["fee"], "1.00")
        self.assertEqual(row["risk.level"], "medium")
        self.assertEqual(self.read_csv(folder, "risk_reasons"),
                         [{"operation_id": transaction["transaction_id"], "reason": "new_recipient"}])

    def test_bank_csv_has_separate_client_rankings_and_top_three(self):
        report = self.builder.build_bank_report()
        folder = self.folder / "bank"
        self.builder.export_to_csv(report, folder)
        row = self.read_csv(folder, "top_three")[0]
        self.assertEqual(row["currency"], "RUB")
        self.assertEqual(row["position"], "1")
        self.assertEqual(row["total_balance"], "1000.123456789")
        self.assertEqual(len(self.read_csv(folder, "client_balances")), len(Currency))

    def test_risk_csv_has_period_error_counts_and_one_row_per_reason(self):
        recipient = self.bank.open_account(self.token, "standard")["account_id"]
        self.bank.create_transaction(self.token, TransactionType.INTERNAL_TRANSFER, Decimal("100000"),
                                    Currency.RUB, sender_account_id=self.account, recipient_account_id=recipient)
        self.bank.process_transactions()
        report = self.builder.build_risk_report(self.token, start_at=self.now, end_at=self.now)
        folder = self.folder / "risk"
        self.builder.export_to_csv(report, folder)
        row = self.read_csv(folder, "suspicious_operations")[0]
        self.assertEqual(row["blocked"], "true")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["first_assessed_at"], self.now.isoformat())
        self.assertEqual({row["reason"] for row in self.read_csv(folder, "risk_reasons")},
                         {"large_amount", "new_recipient"})
        errors = {row["metric"]: row["value"] for row in self.read_csv(folder, "errors")}
        self.assertEqual(errors["by_error_type.HighRiskTransactionError"], "1")
        metadata = {row["metric"]: row["value"] for row in self.read_csv(folder, "metadata")}
        self.assertEqual(metadata["period.start_at"], self.now.isoformat())

    def test_empty_csv_tables_keep_headers_and_repeat_export_does_not_append(self):
        report = self.builder.build_client_report(self.token)
        folder = self.folder / "empty"
        paths = self.builder.export_to_csv(report, folder)
        before = {path: path.read_bytes() for path in paths}
        self.assertEqual(self.read_csv(folder, "transactions"), [])
        with (folder / "transactions.csv").open(encoding="utf-8-sig", newline="") as stream:
            self.assertIn("transaction_id", next(csv.reader(stream, delimiter=";")))
        self.builder.export_to_csv(report, folder)
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_export_uses_snapshot_and_does_not_change_it(self):
        report = self.builder.build_client_report(self.token)
        before = deepcopy(report)
        with patch.object(self.bank, "get_client_info", side_effect=AssertionError("Чтение банка")):
            self.builder.export_to_json(report, self.folder / "report.json")
            self.builder.export_to_csv(report, self.folder / "csv")
        self.assertEqual(report, before)
        content = (self.folder / "report.json").read_text(encoding="utf-8")
        self.assertNotIn("private-password", content)
        self.assertNotIn(self.token, content)

    def test_invalid_data_is_rejected_before_replacing_json_or_creating_csv(self):
        path = self.folder / "report.json"
        path.write_text("original", encoding="utf-8")
        report = self.builder.build_client_report(self.token)
        report["accounts"][0]["balance"] = Decimal("NaN")
        with self.assertRaises(InvalidOperationError):
            self.builder.export_to_json(report, path)
        self.assertEqual(path.read_text(encoding="utf-8"), "original")
        folder = self.folder / "invalid-csv"
        with self.assertRaises(InvalidOperationError):
            self.builder.export_to_csv(report, folder)
        self.assertFalse(folder.exists())

    def test_file_errors_preserve_existing_file_and_remove_temporary_files(self):
        report = self.builder.build_client_report(self.token)
        path = self.folder / "report.json"
        path.write_text("original", encoding="utf-8")
        with patch.object(Path, "replace", side_effect=PermissionError("Нет доступа")):
            with self.assertRaises(ReportExportError) as caught:
                self.builder.export_to_json(report, path)
        self.assertIsInstance(caught.exception.__cause__, PermissionError)
        self.assertEqual(path.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.folder.glob(".report-*.tmp")), [])
        occupied = self.folder / "file"
        occupied.write_text("occupied", encoding="utf-8")
        with self.assertRaises(ReportExportError):
            self.builder.export_to_csv(report, occupied)

    def test_invalid_paths_report_types_and_naive_dates(self):
        valid = self.builder.build_client_report(self.token)
        for method in (self.builder.export_to_json, self.builder.export_to_csv):
            for path in ("", " ", 123, None):
                with self.subTest(path=path), self.assertRaises(InvalidOperationError):
                    method(valid, path)
            for report in (None, {}, {**valid, "report_type": "unknown"},
                           {**valid, "generated_at": datetime(2026, 11, 17)}):
                with self.subTest(report=report), self.assertRaises(InvalidOperationError):
                    method(report, self.folder / "invalid")


if __name__ == "__main__":
    unittest.main()
