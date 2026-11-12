from decimal import Decimal

from bank.accounts import (
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from bank.exceptions import InsufficientFundsError, InvalidOperationError


def main() -> None:
    savings_accounts = [
        SavingsAccount(
            owner="Иван",
            balance=Decimal("10000"),
            min_balance=Decimal("1000"),
        ),
        SavingsAccount(
            owner="Анна",
            balance=Decimal("5000"),
            min_balance=Decimal("500"),
            monthly_interest_rate=Decimal("0.02"),
        ),
    ]

    premium_accounts = [
        PremiumAccount(
            owner="Мария",
            balance=Decimal("1000"),
        ),
        PremiumAccount(
            owner="Пётр",
            balance=Decimal("2000"),
            fixed_fee=Decimal("25"),
        ),
    ]

    investment_accounts = [
        InvestmentAccount(
            owner="Олег",
            balance=Decimal("10000"),
        ),
        InvestmentAccount(
            owner="Елена",
            balance=Decimal("6000"),
        ),
    ]

    print("=== Начисление месячного дохода ===")

    for account in savings_accounts:
        interest = account.apply_monthly_interest()

        assert interest == Decimal("100"), (
            f"Для {account.owner} ожидался доход 100, получен {interest}"
        )

        print(f"{account.owner}: начислено {interest:.2f}")

    print("\n=== Использование овердрафта ===")

    premium_accounts[0].withdraw(Decimal("1500"))

    assert premium_accounts[0].balance == Decimal("-550"), (
        f"После снятия 1500 с комиссией 50 ожидался баланс -550, "
        f"получен {premium_accounts[0].balance}"
    )

    print(premium_accounts[0])

    print("\n=== Инвестиции и прогноз ===")

    growth_rates = {
        "stocks": Decimal("0.10"),
        "bonds": Decimal("0.04"),
        "etf": Decimal("0.07"),
    }

    for account in investment_accounts:
        balance_before = account.balance

        account.invest("stocks", Decimal("1000"))
        account.invest("bonds", Decimal("2000"))
        account.invest("etf", Decimal("1000"))

        assert account.balance == balance_before - Decimal("4000"), (
            f"Для {account.owner} неверно списана сумма инвестиций: "
            f"получен баланс {account.balance}"
        )

        assert account.portfolio == {
            "stocks": Decimal("1000"),
            "bonds": Decimal("2000"),
            "etf": Decimal("1000"),
        }, f"Для {account.owner} неверно сформирован портфель"

        balance_before_projection = account.balance
        portfolio_before_projection = account.portfolio

        growth = account.project_yearly_growth(growth_rates)

        assert growth == Decimal("250"), (
            f"Ожидался прирост 250, получен {growth}"
        )
        assert account.balance == balance_before_projection, (
            "Расчёт прогноза изменил баланс"
        )
        assert account.portfolio == portfolio_before_projection, (
            "Расчёт прогноза изменил портфель"
        )

        print(f"{account.owner}: ожидаемый прирост {growth:.2f}")

    print("\n=== Отклонённые инвестиции ===")

    investment = investment_accounts[0]
    balance_before = investment.balance
    portfolio_before = investment.portfolio

    try:
        investment.invest("gold", Decimal("100"))
    except InvalidOperationError as error:
        print("Неизвестный актив:", error)
    else:
        raise AssertionError("Инвестиция в неизвестный актив разрешена")

    try:
        investment.invest("stocks", Decimal("-100"))
    except InvalidOperationError as error:
        print("Отрицательная сумма:", error)
    else:
        raise AssertionError("Инвестиция отрицательной суммы разрешена")

    try:
        investment.invest("etf", investment.balance + Decimal("1"))
    except InsufficientFundsError as error:
        print("Недостаточно средств:", error)
    else:
        raise AssertionError("Инвестиция сверх свободного баланса разрешена")

    assert investment.balance == balance_before, (
        "Отклонённые инвестиции изменили баланс"
    )
    assert investment.portfolio == portfolio_before, (
        "Отклонённые инвестиции изменили портфель"
    )

    print("\n=== Полиморфизм ===")

    accounts: list[BankAccount] = (
        savings_accounts + premium_accounts + investment_accounts
    )

    for account in accounts:
        balance_before = account.balance

        account.deposit(Decimal("100"))
        account.withdraw(Decimal("100"))

        expected_balance = balance_before

        # У премиального счёта дополнительно списывается комиссия.
        if isinstance(account, PremiumAccount):
            expected_balance -= account.fixed_fee

        assert account.balance == expected_balance, (
            f"{account.owner}: ожидался баланс {expected_balance}, "
            f"получен {account.balance}"
        )

        print(account)
        print(account.get_account_info())
        print()

    print("Все проверки пройдены!")


if __name__ == "__main__":
    main()