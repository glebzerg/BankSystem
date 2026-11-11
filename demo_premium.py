from decimal import Decimal

from bank.accounts import PremiumAccount
from bank.exceptions import InsufficientFundsError, InvalidOperationError


def main() -> None:
    account = PremiumAccount(
        owner="Иван",
        balance=Decimal("10000"),
        withdrawal_limit=Decimal("20000"),
        overdraft_limit=Decimal("5000"),
        fixed_fee=Decimal("50"),
    )

    print("Начальный баланс:", account.balance)

    # 1. Обычное снятие с комиссией.
    account.withdraw(Decimal("2000"))
    print("После снятия 2000 и комиссии 50:", account.balance)

    assert account.balance == Decimal("7950"), (
        f"После снятия ожидался баланс 7950, получен {account.balance}"
    )

    # 2. Снятие с использованием овердрафта.
    account.withdraw(Decimal("9000"))
    print("После снятия 9000 и комиссии 50:", account.balance)

    assert account.balance == Decimal("-1100"), (
        f"После использования овердрафта ожидался баланс -1100, "
        f"получен {account.balance}"
    )

    # 3. Снятие ровно до границы овердрафта.
    account.withdraw(Decimal("3850"))
    print("На границе овердрафта:", account.balance)

    assert account.balance == Decimal("-5000"), (
        f"Ожидался баланс -5000, получен {account.balance}"
    )

    # 4. Попытка превысить овердрафт.
    try:
        account.withdraw(Decimal("1"))
    except InsufficientFundsError as error:
        print("Снятие отклонено:", error)
    else:
        raise AssertionError(
            "Снятие сверх лимита овердрафта разрешено"
        )

    assert account.balance == Decimal("-5000"), (
        f"Отклонённое снятие изменило баланс: "
        f"ожидалось -5000, получено {account.balance}"
    )

    # 5. Пополнение погашает задолженность.
    account.deposit(Decimal("15000"))
    print("После пополнения на 15000:", account.balance)

    assert account.balance == Decimal("10000"), (
        f"После пополнения ожидался баланс 10000, получен {account.balance}"
    )

    # 6. Попытка превысить лимит одного снятия.
    try:
        account.withdraw(Decimal("20001"))
    except InvalidOperationError as error:
        print("Снятие отклонено:", error)
    else:
        raise AssertionError(
            "Снятие сверх лимита одной операции разрешено"
        )

    assert account.balance == Decimal("10000"), (
        f"Отклонённое снятие изменило баланс: "
        f"ожидалось 10000, получено {account.balance}"
    )

    # 7. Комиссия тоже должна помещаться в овердрафт.
    # Без комиссии снятие 15000 оставило бы ровно -5000.
    # С комиссией получилось бы -5050 — операция запрещена.
    try:
        account.withdraw(Decimal("15000"))
    except InsufficientFundsError as error:
        print("Снятие с учётом комиссии отклонено:", error)
    else:
        raise AssertionError(
            "Снятие разрешено, хотя комиссия превышает доступный овердрафт"
        )

    assert account.balance == Decimal("10000"), (
        f"После отказа баланс должен оставаться 10000, "
        f"получен {account.balance}"
    )

    print("Все проверки пройдены!")


if __name__ == "__main__":
    main()