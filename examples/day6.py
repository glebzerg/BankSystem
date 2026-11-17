"""Комплексная демонстрация: python -X utf8 -B -m examples.day6."""

from datetime import date, datetime, timedelta
from decimal import Decimal

from bank.models.accounts import Currency
from bank.bank import Bank
from bank.models.clients import Client
from bank.models.transactions import ExternalRecipient, TransactionType


def main() -> None:
    current = datetime(2026, 11, 17, 12, tzinfo=Bank.BANK_TIMEZONE)
    bank = Bank(now=lambda: current)

    # Имя, баланс основного счёта, тип и баланс дополнительного счёта.
    client_data = [
        ("Иван Петров", "200000", "savings", "5000"),
        ("Анна Смирнова", "10000", "premium", "1000"),
        ("Олег Иванов", "15000", "investment", "8000"),
        ("Мария Соколова", "20000", "savings", "3000"),
        ("Павел Орлов", "25000", "premium", "2000"),
    ]
    clients = []
    tokens = []
    account_ids = []

    print("=== Создание банка, клиентов и счетов ===")
    for index, (name, main_balance, extra_type, extra_balance) in enumerate(client_data, start=1):
        client = Client(name, date(1990, 1, 1), {"email": f"client{index}@example.com"})
        bank.add_client(client, "DemoPassword123")
        token = bank.authenticate_client(client.client_id, "DemoPassword123")
        main_account = bank.open_account(
            token, "standard", currency=Currency.RUB, balance=Decimal(main_balance),
        )
        extra_account = bank.open_account(
            token, extra_type, currency=Currency.RUB, balance=Decimal(extra_balance),
        )
        clients.append(client)
        tokens.append(token)
        account_ids.append((main_account["account_id"], extra_account["account_id"]))

        print(f"\nКлиент: {client.full_name}")
        for account in bank.search_accounts(token):
            print(f"  {account['account_type']} | {account['account_id']} | "
                  f"{account['balance']:.2f} {account['currency']}")

    assert len(clients) == 5
    assert sum(len(client.account_numbers) for client in clients) == 10
    assert all(len(bank.search_accounts(token)) == 2 for token in tokens)
    assert bank.get_total_balance()["RUB"] == Decimal("289000")
    print("\nПодготовлено: 5 клиентов, 10 счетов. Начальный баланс: 289000 RUB.")

    operations = []

    def add(client_index, label, kind, amount, **participants):
        transaction = bank.create_transaction(
            tokens[client_index], kind, Decimal(amount), Currency.RUB, **participants,
        )
        operations.append((client_index, label, transaction["transaction_id"]))
        print(f"[{len(operations):02}] В очереди: {clients[client_index].full_name} | "
              f"{label} | {amount} RUB | статус: {transaction['status']}")

    def process_batch(batch):
        print("\n--- Исполнение очереди ---")
        processed = bank.process_transactions()
        assert processed == len(batch)
        for client_index, label, identifier in batch:
            transaction = bank.get_transaction(tokens[client_index], identifier)
            result = "Выполнено" if transaction["status"] == "completed" else "Отклонено"
            print(f"{result}: {clients[client_index].full_name} | {label} | "
                  f"риск: {transaction['risk']['level']}")
            if transaction["failure_reason"]:
                print(f"  Причина: {transaction['failure_reason']}")

    print("\n=== Первые 20 транзакций: обычные операции ===")
    for index in range(len(clients)):
        main_id, extra_id = account_ids[index]
        next_client_main = account_ids[(index + 1) % len(clients)][0]
        add(index, "Пополнение", TransactionType.DEPOSIT, "1000", recipient_account_id=main_id)
        add(index, "Снятие", TransactionType.WITHDRAWAL, "100", sender_account_id=main_id)
        add(index, "Перевод на свой второй счёт", TransactionType.INTERNAL_TRANSFER, "500",
            sender_account_id=main_id, recipient_account_id=extra_id)
        add(index, "Перевод другому клиенту", TransactionType.INTERNAL_TRANSFER, "200",
            sender_account_id=main_id, recipient_account_id=next_client_main)
    process_batch(operations)

    # Новая группа начинается через две учебные минуты, без реального ожидания.
    current += timedelta(minutes=2)
    batch_start = len(operations)
    frozen_account = account_ids[4][1]
    bank.freeze_account(tokens[4], frozen_account)

    print("\n=== Ещё 10 транзакций: ошибки и подозрительные операции ===")
    add(0, "Крупный перевод новому получателю", TransactionType.INTERNAL_TRANSFER, "100000",
        sender_account_id=account_ids[0][0], recipient_account_id=account_ids[2][0])
    add(1, "Снятие без достаточных средств", TransactionType.WITHDRAWAL, "40000",
        sender_account_id=account_ids[1][0])
    add(2, "Перевод на замороженный счёт", TransactionType.INTERNAL_TRANSFER, "100",
        sender_account_id=account_ids[2][0], recipient_account_id=frozen_account)
    add(4, "Превышение лимита снятия", TransactionType.WITHDRAWAL, "60000",
        sender_account_id=account_ids[4][0])
    for _ in range(3):
        add(0, "Пополнение в серии частых операций", TransactionType.DEPOSIT, "100",
            recipient_account_id=account_ids[0][0])
    add(0, "Частый перевод новому получателю", TransactionType.INTERNAL_TRANSFER, "10",
        sender_account_id=account_ids[0][0], recipient_account_id=account_ids[3][0])
    add(1, "Перевод знакомому получателю", TransactionType.INTERNAL_TRANSFER, "100",
        sender_account_id=account_ids[1][0], recipient_account_id=account_ids[1][1])
    add(2, "Внешний перевод с комиссией", TransactionType.EXTERNAL_TRANSFER, "50",
        sender_account_id=account_ids[2][0], external_recipient=ExternalRecipient("123", "demo-bank"))
    process_batch(operations[batch_start:])
    bank.unfreeze_account(tokens[4], frozen_account)

    transactions = [bank.get_transaction(tokens[index], identifier)
                    for index, label, identifier in operations]
    assert len(transactions) == 30
    assert sum(transaction["status"] == "completed" for transaction in transactions) == 25
    assert sum(transaction["status"] == "failed" for transaction in transactions) == 5
    print("\nСимуляция завершена: 30 транзакций, 25 выполнено, 5 отклонено.")

    # Показываем пользовательский сценарий на примере первого клиента — Ивана.
    client_token = tokens[0]
    print(f"\n=== Счета клиента: {clients[0].full_name} ===")
    for account in bank.search_accounts(client_token):
        print(f"{account['account_type']} | {account['account_id']} | "
              f"{account['balance']:.2f} {account['currency']} | {account['status']}")

    print("\n=== История операций клиента ===")
    for transaction in bank.get_transactions(client_token):
        print(f"{transaction['transaction_id']} | {transaction['transaction_type']} | "
              f"{transaction['amount']:.2f} {transaction['currency']} | {transaction['status']}")
        if transaction["failure_reason"]:
            print(f"  Причина: {transaction['failure_reason']}")

    print("\n=== Подозрительные операции клиента ===")
    for operation in bank.get_suspicious_operations(client_token):
        print(f"{operation['operation_id']} | риск: {operation['risk_level']} | "
              f"{', '.join(operation['reasons'])} | {operation['status']}")

    print("\n=== Топ-3 клиентов по балансу ===")
    top_three = bank.get_clients_ranking(Currency.RUB)[:3]
    for place, client in enumerate(top_three, start=1):
        print(f"{place}. {client['full_name']}: {client['total_balance']:.2f} {client['currency']}")

    print("\n=== Статистика транзакций всего банка ===")
    print(f"Всего: {len(transactions)}")
    for status in ("completed", "failed"):
        count = sum(transaction["status"] == status for transaction in transactions)
        print(f"{status}: {count}")

    print("\n=== Общий баланс банка ===")
    totals = bank.get_total_balance()
    print(f"{totals['RUB']:.2f} RUB")

    # Проверяем все счета: ошибочные и заблокированные операции не должны менять деньги.
    expected_balances = [
        ("200700", "5500"),
        ("10300", "1600"),
        ("15349.50", "8500"),
        ("20400", "3500"),
        ("25400", "2500"),
    ]
    for index, balances in enumerate(expected_balances):
        accounts = bank.search_accounts(tokens[index])
        actual = {account["account_id"]: account["balance"] for account in accounts}
        for identifier, expected in zip(account_ids[index], balances):
            assert actual[identifier] == Decimal(expected), f"Неверный баланс: {clients[index].full_name}"

    # Проверяем причины отказов, чтобы случайная ошибка не заменила ожидаемый сценарий.
    error_types = {
        error["transaction_id"]: error["error_type"]
        for token in tokens for error in bank.get_transaction_errors(token)
    }
    assert error_types == {
        operations[20][2]: "HighRiskTransactionError",
        operations[21][2]: "InsufficientFundsError",
        operations[22][2]: "AccountFrozenError",
        operations[23][2]: "InvalidOperationError",
        operations[27][2]: "HighRiskTransactionError",
    }
    assert transactions[20]["risk"]["reasons"] == ("large_amount", "new_recipient")
    assert transactions[27]["risk"]["reasons"] == ("frequent_operations", "new_recipient")
    assert transactions[-1]["fee"] == Decimal("0.50")
    assert totals["RUB"] == Decimal("293749.50")
    assert [client["full_name"] for client in top_three] == [
        "Иван Петров", "Павел Орлов", "Мария Соколова",
    ]
    processed_again = bank.process_transactions()
    assert processed_again == 0, "Очередь должна быть пустой после симуляции"
    print("\nДень 6: все проверки пройдены!")


if __name__ == "__main__":
    main()
