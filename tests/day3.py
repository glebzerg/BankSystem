"""Общая демонстрация системы Bank: python -B -m tests.day3."""
from datetime import date, datetime
from decimal import Decimal

from bank.accounts import AccountStatus, Currency
from bank.bank import Bank
from bank.clients import Client, ClientStatus
from bank.exceptions import (
    AccountFrozenError, AuthenticationError, ClientBlockedError, InvalidOperationError,
)


def expect_error(expected_error, operation) -> None:
    """Проверить, что операция отклоняется с нужной ошибкой."""
    try:
        operation()
    except expected_error as error:
        print(f"Ожидаемый отказ: {error}")
    else:
        raise AssertionError(f"Ожидалась ошибка {expected_error.__name__}")


def main() -> None:
    current_time = datetime(2026, 11, 13, 12, tzinfo=Bank.BANK_TIMEZONE)
    bank = Bank(now=lambda: current_time)

    print("=== Регистрация клиентов ===")
    ivan = Client("Иван Петров", date(2000, 5, 15), {"phone": "+79991234567"})
    anna = Client("Анна Смирнова", date(1995, 8, 20), {"email": "anna@example.com"})
    bank.add_client(ivan, "IvanPassword123")
    bank.add_client(anna, "AnnaPassword123")
    expect_error(InvalidOperationError, lambda: bank.add_client(ivan, "AnotherPassword"))
    expect_error(
        InvalidOperationError,
        lambda: Client("Несовершеннолетний", date.today(), {"phone": "+79991234568"}),
    )
    ivan_token = bank.authenticate_client(ivan.client_id, "IvanPassword123")
    anna_token = bank.authenticate_client(anna.client_id, "AnnaPassword123")

    print("\n=== Открытие счетов и операции ===")
    standard = bank.open_account(ivan_token, "standard", balance=Decimal("1000"))["account_id"]
    savings = bank.open_account(
        ivan_token, "savings", balance=Decimal("2000"), min_balance=Decimal("500"),
    )["account_id"]
    premium = bank.open_account(anna_token, "premium", balance=Decimal("1000"))["account_id"]
    investment = bank.open_account(anna_token, "investment", balance=Decimal("5000"))["account_id"]
    bank.withdraw(ivan_token, standard, Decimal("100"))
    bank.apply_monthly_interest(ivan_token, savings)
    bank.withdraw(anna_token, premium, Decimal("1500"))
    bank.invest(anna_token, investment, "stocks", Decimal("1000"))
    assert len(ivan.account_numbers) == 2, "У Ивана должно быть два счёта"
    assert len(anna.account_numbers) == 2, "У Анны должно быть два счёта"

    print("\n=== Заморозка и разморозка ===")
    bank.freeze_account(ivan_token, standard)
    expect_error(AccountFrozenError, lambda: bank.withdraw(ivan_token, standard, Decimal("1")))
    bank.unfreeze_account(ivan_token, standard)

    print("\n=== Закрытие пустого счёта ===")
    empty = bank.open_account(ivan_token, "standard")["account_id"]
    bank.close_account(ivan_token, empty)
    closed = bank.search_accounts(ivan_token, account_id=empty, status=AccountStatus.CLOSED)
    assert len(closed) == 1, "Закрытый счёт должен сохраняться в истории"

    print("\n=== Защита чужого счёта ===")
    expect_error(InvalidOperationError, lambda: bank.freeze_account(anna_token, standard))
    assert bank.get_suspicious_events(anna_token)[0]["event_type"] == "foreign_account_access", (
        "Обращение к чужому счёту должно попасть в журнал"
    )

    print("\n=== Ночной запрет ===")
    current_time = datetime(2026, 11, 14, 1, tzinfo=Bank.BANK_TIMEZONE)
    expect_error(InvalidOperationError, lambda: bank.deposit(ivan_token, standard, Decimal("100")))
    print(bank.search_accounts(ivan_token))
    print(bank.get_suspicious_events(ivan_token))

    print("\n=== Общие суммы и рейтинг ===")
    totals = bank.get_total_balance()
    assert totals["RUB"] == Decimal("7370"), (
        f"Ожидалось 7370 RUB, получено {totals['RUB']}"
    )
    print(totals)
    ranking = bank.get_clients_ranking(Currency.RUB)
    assert [row["total_balance"] for row in ranking] == [Decimal("4450"), Decimal("2920")], (
        "Неверные суммы или порядок клиентов в рейтинге"
    )
    for position, row in enumerate(ranking, start=1):
        print(f"{position}. {row['full_name']}: {row['total_balance']:.2f} {row['currency']}")

    print("\n=== Блокировка после трёх ошибок ===")
    for _ in range(2):
        expect_error(
            AuthenticationError,
            lambda: bank.authenticate_client(anna.client_id, "WrongPassword"),
        )
    expect_error(
        ClientBlockedError,
        lambda: bank.authenticate_client(anna.client_id, "WrongPassword"),
    )
    assert anna.status is ClientStatus.BLOCKED, (
        "После трёх ошибок клиент должен быть заблокирован"
    )
    expect_error(AuthenticationError, lambda: bank.search_accounts(anna_token))
    print("\nВсе проверки дня 3 пройдены!")


if __name__ == "__main__":
    main()
