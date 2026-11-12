from decimal import Decimal

from bank.accounts import BankAccount, InvestmentAccount, PremiumAccount, SavingsAccount
from bank.exceptions import InsufficientFundsError, InvalidOperationError


def main() -> None:
    print("=== Сравнение лимитов счетов ===")

    for account_type in (BankAccount, SavingsAccount, InvestmentAccount):
        standard = account_type(owner="Иван", balance=Decimal("200000"))
        assert standard.withdrawal_limit == Decimal("50000"), (
            f"У {account_type.__name__} ожидался лимит 50000"
        )

        try:
            standard.withdraw(Decimal("50000.01"))
        except InvalidOperationError as error:
            print(f"{account_type.__name__}: снятие отклонено: {error}")
        else:
            raise AssertionError("Превышение обычного лимита разрешено")

        assert standard.balance == Decimal("200000"), (
            "Отклонённое снятие изменило баланс"
        )
        standard.withdraw(Decimal("50000"))
        assert standard.balance == Decimal("150000"), (
            "Снятие ровно до лимита выполнено неверно"
        )

    premium = PremiumAccount(owner="Анна", balance=Decimal("200000"))
    assert premium.withdrawal_limit == Decimal("100000"), (
        "У премиального счёта ожидался лимит 100000"
    )
    premium.withdraw(Decimal("60000"))
    assert premium.balance == Decimal("139950"), (
        "Премиальный счёт должен разрешать снятие 60000 с комиссией 50"
    )
    premium.withdraw(Decimal("100000"))
    assert premium.balance == Decimal("39900"), (
        "Премиальный счёт должен разрешать снятие ровно 100000 с комиссией"
    )
    try:
        premium.withdraw(Decimal("100000.01"))
    except InvalidOperationError:
        pass
    else:
        raise AssertionError("Превышение премиального лимита разрешено")
    assert premium.balance == Decimal("39900"), (
        "Отклонённое снятие изменило баланс премиального счёта"
    )

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
