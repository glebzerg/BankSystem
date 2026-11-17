"""Три отчёта, JSON, CSV и PNG: python -X utf8 -B -m examples.day7."""

import csv
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image

from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.reporting.builder import ReportBuilder
from bank.models.transactions import ExternalRecipient, TransactionType


def main() -> None:
    current = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
    bank = Bank(now=lambda: current)
    tokens = []
    for name in ("Иван Петров", "Анна Смирнова", "Олег Иванов"):
        client = Client(name, date(1990, 1, 1), {"email": f"client{len(tokens)}@example.com"})
        bank.add_client(client, "DemoPassword123")
        tokens.append(bank.authenticate_client(client.client_id, "DemoPassword123"))
    ivan, anna, oleg = tokens

    def account(token, kind="standard", balance="0", currency=Currency.RUB):
        return bank.open_account(token, kind, balance=Decimal(balance), currency=currency)["account_id"]

    ivan_main = account(ivan, balance="10000")
    investment = account(ivan, "investment", "2000")
    anna_main = account(anna, balance="5000")
    savings = account(anna, "savings", "1000")
    account(oleg, balance="200", currency=Currency.USD)
    frozen = account(oleg)

    def transact(token, kind, amount, **participants):
        bank.create_transaction(token, kind, Decimal(amount), Currency.RUB, **participants)
        processed = bank.process_transactions()
        assert processed == 1

    # Учебные часы дают различимые точки линии баланса без реального ожидания.
    current += timedelta(minutes=5)
    bank.deposit(ivan, ivan_main, Decimal("1000"))
    current += timedelta(minutes=5)
    transact(ivan, TransactionType.INTERNAL_TRANSFER, "500",
             sender_account_id=ivan_main, recipient_account_id=anna_main)
    current += timedelta(minutes=5)
    transact(ivan, TransactionType.EXTERNAL_TRANSFER, "100", sender_account_id=ivan_main,
             external_recipient=ExternalRecipient("123", "demo-bank"))
    current += timedelta(minutes=5)
    transact(ivan, TransactionType.INTERNAL_TRANSFER, "100000",
             sender_account_id=ivan_main, recipient_account_id=frozen)
    transact(anna, TransactionType.WITHDRAWAL, "50000", sender_account_id=anna_main)
    current += timedelta(minutes=5)
    bank.invest(ivan, investment, "stocks", Decimal("500"))
    current += timedelta(minutes=5)
    bank.apply_monthly_interest(anna, savings)
    current += timedelta(minutes=5)
    bank.freeze_account(oleg, frozen)
    transact(ivan, TransactionType.INTERNAL_TRANSFER, "10",
             sender_account_id=ivan_main, recipient_account_id=frozen)

    builder = ReportBuilder(bank, now=lambda: current)
    reports = {
        "client": builder.build_client_report(ivan),
        "bank": builder.build_bank_report(),
        "risk": builder.build_risk_report(ivan),
    }
    client_summary = reports["client"]["summary"]
    assert client_summary["balances_by_currency"]["RUB"] == Decimal("11899")
    assert client_summary["total_by_currency"]["RUB"] == Decimal("12399")
    assert len(reports["client"]["balance_history"]) == 6
    bank_summary = reports["bank"]["summary"]
    assert bank_summary["client_count"] == 3 and bank_summary["account_count"] == 6
    assert bank_summary["transaction_count"] == 5
    assert bank_summary["transactions_by_status"]["completed"] == 2
    assert bank_summary["transactions_by_status"]["failed"] == 3
    assert bank_summary["fees_by_currency"]["RUB"] == Decimal("1")
    assert bank_summary["total_by_currency"] == bank.get_total_balance()
    assert bank_summary["total_by_currency"]["RUB"] == Decimal("18909")
    assert bank_summary["total_by_currency"]["USD"] == Decimal("200")
    assert reports["risk"]["summary"]["by_level"] == {"low": 2, "medium": 3, "high": 1}
    assert len(reports["risk"]["suspicious_operations"]) == 4
    assert reports["risk"]["error_statistics"]["failed_operations"] == 2

    # Каждый запуск получает свою папку: PNG с новыми номерами счетов не смешиваются со старыми.
    run_id = datetime.now(Bank.BANK_TIMEZONE).strftime("%Y%m%d_%H%M%S_%f")
    output = Path("reports") / "day7" / run_id
    png_count = 0
    csv_count = 0
    for name, report in reports.items():
        folder = output / name
        folder.mkdir(parents=True, exist_ok=True)
        text = builder.to_text(report)
        (folder / "report.txt").write_text(text, encoding="utf-8")
        json_path = builder.export_to_json(report, folder / "report.json")
        csv_paths = builder.export_to_csv(report, folder / "csv")
        chart_paths = builder.save_charts(report, folder / "charts")

        # Сверяем один и тот же снимок в текстовом, JSON и CSV представлении.
        assert (folder / "report.txt").read_text(encoding="utf-8") == text
        saved = json.loads(json_path.read_text(encoding="utf-8"))
        assert saved["report_type"] == name
        assert saved["generated_at"] == current.isoformat()
        with (folder / "csv" / "summary.csv").open(encoding="utf-8-sig", newline="") as stream:
            csv_summary = {row["metric"]: row["value"] for row in csv.DictReader(stream, delimiter=";")}
        if name == "risk":
            assert saved["summary"]["by_level"] == report["summary"]["by_level"]
            assert int(csv_summary["total_operations"]) == report["summary"]["total_operations"]
        else:
            for currency, amount in report["summary"]["total_by_currency"].items():
                assert Decimal(saved["summary"]["total_by_currency"][currency]) == amount
                assert Decimal(csv_summary[f"total_by_currency.{currency}"]) == amount
        for path in chart_paths:
            with Image.open(path) as picture:
                assert picture.format == "PNG"
                picture.verify()
        csv_count += len(csv_paths)
        png_count += len(chart_paths)
        print(text)
        print(f"Сохранено: {folder.resolve()}\n")

    assert png_count == 8
    assert csv_count == 20
    print(f"День 7: все проверки пройдены! Создано 3 TXT, 3 JSON, {csv_count} CSV и {png_count} PNG.")
    print(f"Результаты: {output.resolve()}")


if __name__ == "__main__":
    main()
