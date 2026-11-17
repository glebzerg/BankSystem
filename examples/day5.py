"""Демонстрация аудита и риска: python -X utf8 -B -m examples.day5."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bank.models.accounts import Currency
from bank.monitoring.audit import AuditLevel, AuditLog
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import InvalidOperationError, TemporaryTransactionError
from bank.monitoring.risk import RiskAnalyzer
from bank.models.transactions import ExternalRecipient, TransactionType


def main() -> None:
    current = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
    external_attempts = 0

    def preflight(transaction):
        nonlocal external_attempts
        external_attempts += 1
        if external_attempts == 1:
            raise TemporaryTransactionError("Учебный сбой внешнего банка")

    path = Path("audit_day5.jsonl")
    audit = AuditLog(file_path=path)
    bank = Bank(now=lambda: current, audit_log=audit, external_preflight=preflight)
    client = Client("Иван", date(2000, 1, 1), {"phone": "+79991234567"})
    bank.add_client(client, "ExamplePassword123")
    token = bank.authenticate_client(client.client_id, "ExamplePassword123")
    sender = bank.open_account(token, "standard", balance=Decimal("200000"))["account_id"]
    familiar = bank.open_account(token, "standard")["account_id"]
    new_recipient = bank.open_account(token, "standard")["account_id"]
    empty = bank.open_account(token, "standard")["account_id"]

    def add(kind, amount, **participants):
        return bank.create_transaction(token, kind, Decimal(amount), Currency.RUB,
                                       **participants)["transaction_id"]

    def transfer(amount, recipient):
        return add(TransactionType.INTERNAL_TRANSFER, amount,
                   sender_account_id=sender, recipient_account_id=recipient)

    def info(identifier):
        return bank.get_transaction(token, identifier)

    def balance(identifier):
        return bank.search_accounts(token, account_id=identifier)[0]["balance"]

    print("=== Обычные и подозрительные операции ===")
    deposit = add(TransactionType.DEPOSIT, "100", recipient_account_id=sender)
    first = transfer("100", familiar)
    repeated = transfer("100", familiar)
    large = transfer("100000", familiar)
    blocked = transfer("100", new_recipient)
    assert bank.process_transactions() == 5
    assert [info(identifier)["risk"]["level"] for identifier in
            (deposit, first, repeated, large, blocked)] == ["low", "medium", "low", "medium", "high"]
    assert info(large)["risk"]["reasons"] == ("large_amount",)
    assert info(blocked)["risk"]["reasons"] == ("frequent_operations", "new_recipient")
    assert info(blocked)["status"] == "failed"
    assert balance(new_recipient) == Decimal("0"), "Заблокированный перевод не должен зачисляться"
    assert balance(sender) == Decimal("99900")
    print("Крупный перевод знакомому счёту разрешён; пятый перевод новому счёту заблокирован.")

    print("\n=== Временная ошибка и успешный повтор ===")
    current += timedelta(minutes=2)
    retry = add(TransactionType.EXTERNAL_TRANSFER, "100", sender_account_id=sender,
                external_recipient=ExternalRecipient("123", "unstable-bank"))
    assert bank.process_transactions() == 1
    assert info(retry)["status"] == "queued"
    assert balance(sender) == Decimal("99900")
    current += timedelta(minutes=1)
    assert bank.process_transactions() == 1
    assert info(retry)["status"] == "completed"
    assert info(retry)["attempts_count"] == 2
    assert balance(sender) == Decimal("99799"), "Списывается только 100 RUB + 1 RUB комиссии"
    print("Повтор выполнен: комиссия и сумма списаны ровно один раз.")

    failed = add(TransactionType.WITHDRAWAL, "1", sender_account_id=empty)
    cancelled = transfer("100", new_recipient)
    bank.cancel_transaction(token, cancelled)
    assert bank.process_transactions() == 1
    assert info(failed)["status"] == "failed"
    assert info(cancelled)["status"] == "cancelled"
    bank.deposit(token, sender, Decimal("1"))

    print("\n=== Ночной признак и запрет банка ===")
    current = datetime(2026, 11, 18, 1, tzinfo=Bank.BANK_TIMEZONE)
    night = RiskAnalyzer().analyze(Decimal("1"), Currency.RUB, occurred_at=current)
    assert night.reasons == ("night_operation",)
    before = balance(sender)
    try:
        bank.withdraw(token, sender, Decimal("1"))
    except InvalidOperationError:
        print("Ночное снятие отклонено до изменения баланса.")
    else:
        raise AssertionError("Ночной запрет должен сработать")
    assert balance(sender) == before

    print("\n=== Подозрительные операции ===")
    suspicious = bank.get_suspicious_operations(token)
    assert len(suspicious) == 4
    for row in suspicious:
        print(f"{row['risk_level']}: {', '.join(row['reasons'])}; результат: {row['status']}")

    print("\n=== Риск-профиль клиента ===")
    profile = bank.get_client_risk_profile(token)
    assert profile["total_operations"] == 8
    assert profile["by_level"] == {"low": 4, "medium": 3, "high": 1}
    assert profile["blocked_operations"] == 1
    print(profile)

    print("\n=== Статистика ошибок ===")
    statistics = bank.get_error_statistics(token)
    assert statistics == {
        "failed_attempts": 4, "failed_operations": 3, "risk_blocked_operations": 1,
        "by_error_type": {"HighRiskTransactionError": 1, "InsufficientFundsError": 1,
                          "InvalidOperationError": 1, "TemporaryTransactionError": 1},
    }
    print(statistics)

    assert balance(sender) == Decimal("99800")
    assert balance(familiar) == Decimal("100200")
    assert len(bank.get_transactions(token)) == 8
    assert audit.filter(level=AuditLevel.WARNING, client_id=client.client_id)
    # Файл дописывается при каждом запуске: проверяем записи именно текущего запуска.
    saved = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    current_records = saved[-len(audit.records):]
    assert len(current_records) == len(audit.records)
    assert all(record["client_id"] == client.client_id for record in current_records)
    assert any(record.get("risk", {}).get("level") == "high" for record in current_records)
    assert "ExamplePassword123" not in json.dumps(current_records)
    print(f"\nАудит: {len(audit.records)} событий, файл: {path.resolve()}")
    print("День 5: все проверки пройдены!")


if __name__ == "__main__":
    main()
