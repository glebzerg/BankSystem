from decimal import Decimal

from bank.accounts import AccountStatus, BankAccount, Currency
from bank.exceptions import AccountFrozenError, BankError


def main() -> None:
    active_account = BankAccount(
        owner="Иван Петров",
        currency=Currency.RUB,
        balance=Decimal("1000"),
    )

    print("=== Активный счёт ===")
    print(active_account)

    print("\nПополнение на 500 RUB:")

    try:
        active_account.deposit(Decimal("500"))
    except BankError as error:
        print(f"Пополнение отклонено: {error}")
    else:
        print("Пополнение выполнено")

    print(active_account)

    print("\nСнятие 200 RUB:")

    try:
        active_account.withdraw(Decimal("200"))
    except BankError as error:
        print(f"Снятие отклонено: {error}")
    else:
        print("Снятие выполнено")

    print(active_account)

    frozen_account = BankAccount(
        owner="Анна Смирнова",
        currency=Currency.RUB,
        balance=Decimal("2000"),
        status=AccountStatus.FROZEN,
    )

    print("\n=== Замороженный счёт ===")
    print(frozen_account)

    print("\nПопытка пополнения на 100 RUB:")

    try:
        frozen_account.deposit(Decimal("100"))
    except AccountFrozenError as error:
        print(f"Пополнение отклонено: {error}")
    else:
        print("Пополнение выполнено")

    print("\nПопытка снятия 100 RUB:")

    try:
        frozen_account.withdraw(Decimal("100"))
    except AccountFrozenError as error:
        print(f"Снятие отклонено: {error}")
    else:
        print("Снятие выполнено")

    print("\nИтоговое состояние замороженного счёта:")
    print(frozen_account)


if __name__ == "__main__":
    main()