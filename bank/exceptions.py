class BankError(Exception):
    """Базовая ошибка банковской системы."""


class AccountFrozenError(BankError):
    """Операция над замороженным счётом."""


class AccountClosedError(BankError):
    """Операция над закрытым счётом."""


class InvalidOperationError(BankError):
    """Некорректная операция: неверная сумма, тип данных и т. п."""


class InsufficientFundsError(BankError):
    """На счёте недостаточно средств."""