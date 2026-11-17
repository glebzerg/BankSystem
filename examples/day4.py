"""Десять поручений через Bank: python -B -m examples.day4."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.exceptions import TemporaryTransactionError
from bank.monitoring.risk import RiskAnalyzer
from bank.models.transactions import ExternalRecipient, Transaction, TransactionType


def main() -> None:
    current_time = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
    external_attempts = 0

    def check_external_bank(transaction: Transaction) -> None:
        nonlocal external_attempts
        if transaction.external_recipient.bank_id == "unstable-bank":
            external_attempts += 1
            if external_attempts == 1:
                raise TemporaryTransactionError("Учебный сбой: внешний банк недоступен")

    bank = Bank(
        now=lambda: current_time,
        # Day4 проверяет пакетную обработку: для этого сценария допускаем до 9 операций в минуту.
        risk_analyzer=RiskAnalyzer(frequency_limit=10),
        exchange_rates={(Currency.RUB, Currency.USD): Decimal("0.01")},
        external_preflight=check_external_bank,
    )
    ivan = Client("Иван Петров", date(2000, 1, 1), {"phone": "+79991234567"})
    anna = Client("Анна Смирнова", date(1995, 1, 1), {"email": "anna@example.com"})
    bank.add_client(ivan, "IvanPassword123")
    bank.add_client(anna, "AnnaPassword123")
    ivan_token = bank.authenticate_client(ivan.client_id, "IvanPassword123")
    anna_token = bank.authenticate_client(anna.client_id, "AnnaPassword123")
    sender = bank.open_account(ivan_token, "standard")["account_id"]
    recipient = bank.open_account(anna_token, "standard")["account_id"]
    usd = bank.open_account(anna_token, "standard", currency=Currency.USD)["account_id"]
    premium = bank.open_account(ivan_token, "premium", balance=Decimal("100"))["account_id"]
    frozen = bank.open_account(anna_token, "standard")["account_id"]
    bank.freeze_account(anna_token, frozen)

    operations: list[tuple[str, str]] = []

    def add(label: str, kind: TransactionType, amount: str, **participants) -> str:
        info = bank.create_transaction(
            ivan_token, kind, Decimal(amount), Currency.RUB, **participants,
        )
        operations.append((label, info["transaction_id"]))
        return info["transaction_id"]

    # Все десять поручений создаём до первого запуска обработки.
    add("Внутренний перевод", TransactionType.INTERNAL_TRANSFER, "1000",
        sender_account_id=sender, recipient_account_id=recipient)
    add("Внешний перевод с комиссией", TransactionType.EXTERNAL_TRANSFER, "500",
        sender_account_id=sender, external_recipient=ExternalRecipient("123", "other-bank"))
    add("Конвертация RUB → USD", TransactionType.INTERNAL_TRANSFER, "1000",
        sender_account_id=sender, recipient_account_id=usd)
    add("Недостаточно средств", TransactionType.INTERNAL_TRANSFER, "50000",
        sender_account_id=sender, recipient_account_id=recipient)
    add("Премиальный овердрафт", TransactionType.INTERNAL_TRANSFER, "1500",
        sender_account_id=premium, recipient_account_id=recipient)
    add("Получатель заморожен", TransactionType.INTERNAL_TRANSFER, "100",
        sender_account_id=sender, recipient_account_id=frozen)
    delayed_id = add("Отложенный перевод", TransactionType.INTERNAL_TRANSFER, "200",
        sender_account_id=sender, recipient_account_id=recipient,
        scheduled_at=current_time + timedelta(minutes=2))
    add("Приоритетное пополнение", TransactionType.DEPOSIT, "10000",
        recipient_account_id=sender, priority=10)
    cancelled_id = add("Отмена до выполнения", TransactionType.INTERNAL_TRANSFER, "400",
        sender_account_id=sender, recipient_account_id=recipient)
    retry_id = add("Повтор после временного сбоя", TransactionType.EXTERNAL_TRANSFER, "100",
        sender_account_id=sender, external_recipient=ExternalRecipient("456", "unstable-bank"))

    assert len(bank.get_transactions(ivan_token)) == 10
    bank.cancel_transaction(ivan_token, cancelled_id)

    def info(transaction_id: str) -> dict:
        return bank.get_transaction(ivan_token, transaction_id)

    def balance(token: str, account_id: str) -> Decimal:
        return bank.search_accounts(token, account_id=account_id)[0]["balance"]

    print("=== 12:00 — обработка готовых операций ===")
    assert balance(ivan_token, sender) == Decimal("0")
    attempts = bank.process_transactions()
    assert attempts == 8, f"Ожидалось 8 готовых попыток, получено {attempts}"
    assert info(operations[0][1])["status"] == "completed", "Пополнение должно пройти первым"
    assert info(delayed_id)["attempts_count"] == 0
    assert info(retry_id)["status"] == "queued"
    assert balance(ivan_token, sender) == Decimal("7495"), "Сбой не должен списать деньги"
    for label, transaction_id in operations:
        print(f"{label}: {info(transaction_id)['status']}")

    print("\n=== 12:01 — повтор внешнего перевода ===")
    current_time += timedelta(minutes=1)
    assert bank.process_transactions() == 1
    assert info(retry_id)["status"] == "completed"
    assert info(retry_id)["attempts_count"] == 2
    assert balance(ivan_token, sender) == Decimal("7394")
    assert info(delayed_id)["status"] == "queued"
    print("Повтор успешен: списано 100 RUB + 1 RUB комиссии, ровно один раз.")

    print("\n=== 12:02 — отложенный перевод ===")
    current_time += timedelta(minutes=1)
    assert bank.process_transactions() == 1
    assert info(delayed_id)["status"] == "completed"
    assert bank.process_transactions() == 0, "Повторный запуск не должен повторять списания"

    expected_statuses = [
        "completed", "completed", "completed", "failed", "completed",
        "failed", "completed", "completed", "cancelled", "completed",
    ]
    final = [info(transaction_id) for _, transaction_id in operations]
    assert [item["status"] for item in final] == expected_statuses
    assert final[1]["fee"] == Decimal("5")
    assert final[2]["exchange_rate"] == Decimal("0.01")
    assert final[2]["recipient_amount"] == Decimal("10")
    assert final[4]["fee"] == Decimal("0"), "Перевод не должен брать комиссию за снятие"
    assert final[8]["attempts_count"] == 0
    assert all(item["completed_at"] is not None for item in final if item["status"] == "completed")
    assert all(item["failure_reason"] for item in final if item["status"] == "failed")

    expected_balances = [
        (ivan_token, sender, "Отправитель", Decimal("7194")),
        (ivan_token, premium, "Премиальный счёт", Decimal("-1400")),
        (anna_token, recipient, "Получатель RUB", Decimal("2700")),
        (anna_token, usd, "Получатель USD", Decimal("10")),
        (anna_token, frozen, "Замороженный счёт", Decimal("0")),
    ]
    print("\n=== Итоговые балансы ===")
    for token, account_id, label, expected in expected_balances:
        account = bank.search_accounts(token, account_id=account_id)[0]
        assert account["balance"] == expected, f"Неверный баланс: {label}"
        print(f"{label}: {account['balance']:.2f} {account['currency']}")

    errors = bank.get_transaction_errors(ivan_token)
    assert len(errors) == 3
    assert [record["error_type"] for record in errors] == [
        "InsufficientFundsError", "AccountFrozenError", "TemporaryTransactionError",
    ]
    assert errors[-1]["retry_at"] is not None, "История сбоя сохраняется после успеха"
    print("\n=== Журнал ошибок ===")
    for record in errors:
        print(f"Попытка {record['attempt']}: {record['error_type']} — {record['message']}")
    print("\nДень 4: 10 транзакций, 7 успешных, 2 отказа, 1 отмена. Все проверки пройдены!")


if __name__ == "__main__":
    main()
