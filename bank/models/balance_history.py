"""Неизменяемая точка истории свободного баланса счёта."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from bank.models.accounts import Currency
from bank.exceptions import InvalidOperationError


@dataclass(frozen=True)
class BalancePoint:
    account_id: str
    occurred_at: datetime
    balance: Decimal
    currency: Currency
    operation_type: str
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None
        ):
            raise InvalidOperationError("Время баланса должно содержать часовой пояс")
        # Отрицательный баланс допустим для премиального счёта с овердрафтом.
        if not isinstance(self.balance, Decimal) or not self.balance.is_finite():
            raise InvalidOperationError("Баланс должен быть конечным Decimal")
        if not isinstance(self.currency, Currency):
            raise InvalidOperationError("Валюта должна быть элементом Currency")
        for value in (self.account_id, self.operation_type):
            if not isinstance(value, str) or not value.strip():
                raise InvalidOperationError("ID счёта и тип операции должны быть непустыми строками")
        if self.operation_id is not None and (
            not isinstance(self.operation_id, str) or not self.operation_id.strip()
        ):
            raise InvalidOperationError("ID операции должен быть непустой строкой или None")
