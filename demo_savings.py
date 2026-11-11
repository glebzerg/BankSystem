from decimal import Decimal

from bank.accounts import SavingsAccount
from bank.exceptions import InsufficientFundsError


def main() -> None:
    account = SavingsAccount(
        owner="Иван",
        balance=Decimal("10000"),
        min_balance=Decimal("1000"),
        monthly_interest_rate=Decimal("0.01"),
    )

    print("Начальный баланс:", account.balance)

    # 1. Обычное снятие.
    account.withdraw(Decimal("2000"))
    print("После снятия 2000:", account.balance)

    assert account.balance == Decimal("8000"), (
        f"После снятия 2000 ожидался баланс 8000, получен {account.balance}"
    )

    # 2. Снятие ровно до минимального остатка.
    account.withdraw(Decimal("7000"))
    print("После снятия 7000:", account.balance)

    assert account.balance == Decimal("1000"), (
        f"После снятия 7000 ожидался баланс 1000, получен {account.balance}"
    )

    # 3. Попытка нарушить минимальный остаток.
    try:
        account.withdraw(Decimal("1"))
    except InsufficientFundsError as error:
        print("Снятие отклонено:", error)
    else:
        raise AssertionError(
            "Снятие ниже минимального остатка разрешено"
        )

    assert account.balance == Decimal("1000"), (
        f"Отклонённое снятие изменило баланс: "
        f"ожидалось 1000, получено {account.balance}"
    )

    # 4. Пополнение: используем унаследованный метод.
    account.deposit(Decimal("9000"))
    print("После пополнения:", account.balance)

    assert account.balance == Decimal("10000"), (
        f"После пополнения ожидался баланс 10000, получен {account.balance}"
    )

    # 5. Доход за первый месяц.
    interest = account.apply_monthly_interest()
    print("Доход за первый месяц:", interest)
    print("Баланс:", account.balance)

    assert interest == Decimal("100"), (
        f"Доход за первый месяц должен быть 100, получен {interest}"
    )
    assert account.balance == Decimal("10100"), (
        f"После первого месяца ожидался баланс 10100, получен {account.balance}"
    )

    # 6. Доход за второй месяц с учётом прошлого начисления.
    interest = account.apply_monthly_interest()
    print("Доход за второй месяц:", interest)
    print("Баланс:", account.balance)

    assert interest == Decimal("101"), (
        f"Доход за второй месяц должен быть 101, получен {interest}"
    )
    assert account.balance == Decimal("10201"), (
        f"После второго месяца ожидался баланс 10201, получен {account.balance}"
    )

    print("Все проверки пройдены!")


if __name__ == "__main__":
    main()