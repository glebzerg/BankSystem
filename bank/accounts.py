import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum

from .exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)


class AccountStatus(Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class Currency(Enum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"
    KZT = "KZT"
    CNY = "CNY"


class AbstractAccount(ABC):
    """Базовый контракт для всех банковских счетов."""

    def __init__(
        self,
        account_id: str,
        owner: str,
        balance: Decimal = Decimal("0"),
        status: AccountStatus = AccountStatus.ACTIVE,
    ):
        self._account_id = account_id
        self._owner = owner
        self._balance = balance
        self._status = status

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def balance(self) -> Decimal:
        return self._balance

    @property
    def status(self) -> AccountStatus:
        return self._status

    @abstractmethod
    def deposit(self, amount: Decimal) -> None:
        """Пополнить счёт на указанную сумму."""

    @abstractmethod
    def withdraw(self, amount: Decimal) -> None:
        """Снять со счёта указанную сумму."""

    @abstractmethod
    def get_account_info(self) -> dict:
        """Вернуть сведения о счёте."""


class BankAccount(AbstractAccount):
    """Обычный банковский счёт."""

    def __init__(
        self,
        owner: str,
        currency: Currency = Currency.RUB,
        balance: Decimal = Decimal("0"),
        status: AccountStatus = AccountStatus.ACTIVE,
        account_id: str | None = None,
    ):
        if not isinstance(owner, str):
            raise InvalidOperationError(
                "Имя владельца должно быть строкой"
            )

        owner = owner.strip()

        if not owner:
            raise InvalidOperationError(
                "Имя владельца не должно быть пустым"
            )

        if not isinstance(currency, Currency):
            raise InvalidOperationError(
                "Валюта должна быть элементом Currency"
            )

        if not isinstance(status, AccountStatus):
            raise InvalidOperationError(
                "Статус должен быть элементом AccountStatus"
            )

        if not isinstance(balance, Decimal):
            raise InvalidOperationError(
                "Начальный баланс должен иметь тип Decimal"
            )

        if not balance.is_finite():
            raise InvalidOperationError(
                "Начальный баланс должен быть конечным числом"
            )

        if balance < Decimal("0"):
            raise InvalidOperationError(
                "Начальный баланс не может быть отрицательным"
            )

        if account_id is None:
            account_id = self._generate_id()
        else:
            if not isinstance(account_id, str):
                raise InvalidOperationError(
                    "Номер счёта должен быть строкой"
                )

            account_id = account_id.strip()

            if not account_id:
                raise InvalidOperationError(
                    "Номер счёта не должен быть пустым"
                )

        super().__init__(
            account_id=account_id,
            owner=owner,
            balance=balance,
            status=status,
        )

        self._currency = currency

    @staticmethod
    def _generate_id() -> str:
        """Создать 12-значный номер на основе UUID."""
        number = uuid.uuid4().int % 10**12
        return f"{number:012d}"

    @property
    def currency(self) -> Currency:
        return self._currency

    @staticmethod
    def _validate_amount(amount: Decimal) -> None:
        """Проверить сумму пополнения или снятия."""
        if not isinstance(amount, Decimal):
            raise InvalidOperationError(
                "Сумма операции должна иметь тип Decimal"
            )

        if not amount.is_finite():
            raise InvalidOperationError(
                "Сумма операции должна быть конечным числом"
            )

        if amount <= Decimal("0"):
            raise InvalidOperationError(
                "Сумма операции должна быть больше нуля"
            )

    def _ensure_active(self) -> None:
        """Проверить, что операции по счёту разрешены."""
        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError("Счёт заморожен")

        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError("Счёт закрыт")

        if self._status is not AccountStatus.ACTIVE:
            raise InvalidOperationError(
                "Неизвестный статус счёта"
            )

    def deposit(self, amount: Decimal) -> None:
        """Пополнить активный счёт."""
        self._ensure_active()
        self._validate_amount(amount)

        self._balance += amount

    def withdraw(self, amount: Decimal) -> None:
        """Снять деньги с активного счёта."""
        self._ensure_active()
        self._validate_amount(amount)

        if amount > self._balance:
            raise InsufficientFundsError(
                "На счёте недостаточно средств"
            )

        self._balance -= amount

    def get_account_info(self) -> dict:
        """Вернуть сведения о счёте."""
        return {
            "account_type": type(self).__name__,
            "account_id": self._account_id,
            "owner": self._owner,
            "balance": self._balance,
            "currency": self._currency.value,
            "status": self._status.value,
        }

    def __str__(self) -> str:
        status_labels = {
            AccountStatus.ACTIVE: "активный",
            AccountStatus.FROZEN: "замороженный",
            AccountStatus.CLOSED: "закрытый",
        }

        return (
            f"{type(self).__name__} | "
            f"Клиент: {self._owner} | "
            f"Счёт: ****{self._account_id[-4:]} | "
            f"Статус: {status_labels[self._status]} | "
            f"Баланс: {self._balance:.2f} "
            f"{self._currency.value}"
        )


class SavingsAccount(BankAccount):
    """Накопительный счёт с минимальным остатком."""

    def __init__(
        self,
        owner: str,
        currency: Currency = Currency.RUB,
        balance: Decimal = Decimal("0"),
        status: AccountStatus = AccountStatus.ACTIVE,
        account_id: str | None = None,
        *,
        min_balance: Decimal = Decimal("0"),
        monthly_interest_rate: Decimal = Decimal("0.01"),
    ):
        super().__init__(
            owner=owner,
            currency=currency,
            balance=balance,
            status=status,
            account_id=account_id,
        )

        if (
            not isinstance(min_balance, Decimal)
            or not min_balance.is_finite()
            or min_balance < 0
        ):
            raise InvalidOperationError(
                "Минимальный остаток должен быть конечным Decimal >= 0"
            )

        if (
            not isinstance(monthly_interest_rate, Decimal)
            or not monthly_interest_rate.is_finite()
            or monthly_interest_rate < 0
        ):
            raise InvalidOperationError(
                "Месячная ставка должна быть конечным Decimal >= 0"
            )

        if balance < min_balance:
            raise InvalidOperationError(
                "Начальный баланс ниже минимального остатка"
            )

        self._min_balance = min_balance
        self._monthly_interest_rate = monthly_interest_rate

    @property
    def min_balance(self) -> Decimal:
        return self._min_balance

    @property
    def monthly_interest_rate(self) -> Decimal:
        return self._monthly_interest_rate

    def withdraw(self, amount: Decimal) -> None:
        self._ensure_active()
        self._validate_amount(amount)

        if self._balance - amount < self._min_balance:
            raise InsufficientFundsError(
                "После снятия останется меньше минимального остатка"
            )

        super().withdraw(amount)

    def apply_monthly_interest(self) -> Decimal:
        """Начислить доход за месяц и вернуть начисленную сумму."""
        self._ensure_active()

        interest = self._balance * self._monthly_interest_rate
        self._balance += interest

        return interest


class PremiumAccount(BankAccount):
    """Премиальный счёт с овердрафтом и комиссией за снятие."""

    def __init__(
        self,
        owner: str,
        currency: Currency = Currency.RUB,
        balance: Decimal = Decimal("0"),
        status: AccountStatus = AccountStatus.ACTIVE,
        account_id: str | None = None,
        *,
        withdrawal_limit: Decimal = Decimal("100000"),
        overdraft_limit: Decimal = Decimal("10000"),
        fixed_fee: Decimal = Decimal("50"),
    ):
        super().__init__(
            owner=owner,
            currency=currency,
            balance=balance,
            status=status,
            account_id=account_id,
        )

        if (
            not isinstance(withdrawal_limit, Decimal)
            or not withdrawal_limit.is_finite()
            or withdrawal_limit <= 0
        ):
            raise InvalidOperationError(
                "Лимит снятия должен быть конечным Decimal > 0"
            )

        if (
            not isinstance(overdraft_limit, Decimal)
            or not overdraft_limit.is_finite()
            or overdraft_limit < 0
        ):
            raise InvalidOperationError(
                "Лимит овердрафта должен быть конечным Decimal >= 0"
            )

        if (
            not isinstance(fixed_fee, Decimal)
            or not fixed_fee.is_finite()
            or fixed_fee < 0
        ):
            raise InvalidOperationError(
                "Комиссия должна быть конечным Decimal >= 0"
            )

        self._withdrawal_limit = withdrawal_limit
        self._overdraft_limit = overdraft_limit
        self._fixed_fee = fixed_fee

    @property
    def withdrawal_limit(self) -> Decimal:
        return self._withdrawal_limit

    @property
    def overdraft_limit(self) -> Decimal:
        return self._overdraft_limit

    @property
    def fixed_fee(self) -> Decimal:
        return self._fixed_fee

    def withdraw(self, amount: Decimal) -> None:
        self._ensure_active()
        self._validate_amount(amount)

        if amount > self._withdrawal_limit:
            raise InvalidOperationError(
                "Превышен лимит одного снятия"
            )

        total_amount = amount + self._fixed_fee
        remaining_balance = self._balance - total_amount

        if remaining_balance < -self._overdraft_limit:
            raise InsufficientFundsError(
                "Недостаточно средств с учётом комиссии и овердрафта"
            )

        self._balance = remaining_balance


class InvestmentAccount(BankAccount):
    """Счёт со свободным балансом и виртуальным портфелем."""

    ASSET_TYPES = frozenset({"stocks", "bonds", "etf"})

    def __init__(
        self,
        owner: str,
        currency: Currency = Currency.RUB,
        balance: Decimal = Decimal("0"),
        status: AccountStatus = AccountStatus.ACTIVE,
        account_id: str | None = None,
    ):
        super().__init__(
            owner=owner,
            currency=currency,
            balance=balance,
            status=status,
            account_id=account_id,
        )

        self._portfolio = {
            asset_type: Decimal("0")
            for asset_type in sorted(self.ASSET_TYPES)
        }

    @property
    def portfolio(self) -> dict[str, Decimal]:
        return self._portfolio.copy()

    def invest(self, asset_type: str, amount: Decimal) -> None:
        """Перевести свободные деньги в выбранный тип активов."""
        self._ensure_active()

        if (
            not isinstance(asset_type, str)
            or asset_type not in self.ASSET_TYPES
        ):
            raise InvalidOperationError(
                "Допустимые типы активов: stocks, bonds, etf"
            )

        self._validate_amount(amount)

        if amount > self._balance:
            raise InsufficientFundsError(
                "Недостаточно свободных средств для инвестиции"
            )

        self._balance -= amount
        self._portfolio[asset_type] += amount

    def project_yearly_growth(
        self,
        growth_rates: dict[str, Decimal],
    ) -> Decimal:
        """Рассчитать ожидаемый годовой прирост без изменения портфеля."""
        if not isinstance(growth_rates, dict):
            raise InvalidOperationError(
                "Ставки должны быть переданы словарём"
            )

        for asset_type, rate in growth_rates.items():
            if asset_type not in self.ASSET_TYPES:
                raise InvalidOperationError(
                    f"Неизвестный тип актива: {asset_type}"
                )

            if not isinstance(rate, Decimal) or not rate.is_finite():
                raise InvalidOperationError(
                    f"Ставка для {asset_type} должна быть конечным Decimal"
                )

        total_growth = Decimal("0")

        for asset_type, amount in self._portfolio.items():
            if amount == 0:
                continue

            if asset_type not in growth_rates:
                raise InvalidOperationError(
                    f"Не задана ставка для актива: {asset_type}"
                )

            total_growth += amount * growth_rates[asset_type]

        return total_growth