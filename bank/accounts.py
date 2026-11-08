from abc import ABC, abstractmethod
from decimal import Decimal
from enum import Enum


class AccountStatus(Enum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


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

    # --- Доступ только на чтение ---

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

    # --- Контракт: наследники обязаны реализовать ---

    @abstractmethod
    def deposit(self, amount: Decimal) -> None:
        """Пополнить счёт на amount."""

    @abstractmethod
    def withdraw(self, amount: Decimal) -> None:
        """Снять со счёта amount."""

    @abstractmethod
    def get_account_info(self) -> dict:
        """Вернуть сведения о счёте."""