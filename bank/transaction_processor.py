"""Однопоточная обработка очереди с учебными курсами валют."""

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, DecimalException

from .accounts import BankAccount, Currency, PremiumAccount
from .exceptions import BankError, InvalidOperationError
from .transaction_queue import TransactionQueue
from .transactions import Transaction, TransactionType


class TransactionProcessor:
    """Выполняет готовые операции. Доступ клиентов проверит Bank при интеграции.

    Внешний перевод моделируется списанием: реальных сетевых запросов нет.
    Курсы задаются направленными парами, автоматически обратный курс не ищется.
    """

    def __init__(
        self,
        queue: TransactionQueue,
        accounts: Mapping[str, BankAccount],
        *,
        exchange_rates: Mapping[tuple[Currency, Currency], Decimal] | None = None,
        external_fee_rate: Decimal = Decimal("0.01"),
    ):
        if not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("Ожидается очередь TransactionQueue")
        if not isinstance(accounts, Mapping):
            raise InvalidOperationError("Счета должны быть переданы как отображение ID → счёт")
        if (
            not isinstance(external_fee_rate, Decimal)
            or not external_fee_rate.is_finite()
            or external_fee_rate < 0
        ):
            raise InvalidOperationError("Ставка комиссии должна быть конечным Decimal >= 0")
        if exchange_rates is not None and not isinstance(exchange_rates, Mapping):
            raise InvalidOperationError("Курсы должны быть переданы как отображение")
        self._exchange_rates = dict(exchange_rates) if exchange_rates is not None else {}
        for pair, rate in self._exchange_rates.items():
            if (
                not isinstance(pair, tuple) or len(pair) != 2
                or not all(isinstance(currency, Currency) for currency in pair)
                or not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0
                or (pair[0] is pair[1] and rate != 1)
            ):
                raise InvalidOperationError("Неверная валютная пара или курс")
        self._queue = queue
        # Сохраняем доступ к актуальным счетам, без копирования их балансов.
        self._accounts = accounts
        self._external_fee_rate = external_fee_rate

    def process_next(self) -> Transaction | None:
        """Обработать одну готовую операцию; банковский отказ сохранить в ней."""
        transaction = self._queue.get_next()
        if transaction is None:
            return None
        try:
            self._execute(transaction)
        except BankError as error:
            transaction.mark_failed(str(error))
        except DecimalException:
            transaction.mark_failed("Ошибка точности или диапазона денежного расчёта")
        else:
            transaction.mark_completed()
        return transaction

    def process_all(self) -> list[Transaction]:
        """Обработать всё, что готово сейчас, не ожидая отложенных операций."""
        processed = []
        while True:
            transaction = self.process_next()
            if transaction is None:
                return processed
            processed.append(transaction)

    def _get_account(self, account_id: str | None) -> BankAccount:
        account = self._accounts.get(account_id)
        if not isinstance(account, BankAccount) or account.account_id != account_id:
            raise InvalidOperationError("Счёт транзакции не найден")
        return account

    @staticmethod
    def _ensure_currency(transaction: Transaction, account: BankAccount) -> None:
        if transaction.currency is not account.currency:
            raise InvalidOperationError("Валюта поручения не совпадает с валютой счёта")

    @staticmethod
    def _round(amount: Decimal) -> Decimal:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _execute(self, transaction: Transaction) -> None:
        amount = transaction.amount
        if amount != self._round(amount):
            raise InvalidOperationError("Сумма поручения должна содержать не более двух знаков после запятой")
        kind = transaction.transaction_type
        if kind is TransactionType.DEPOSIT:
            recipient = self._get_account(transaction.recipient_account_id)
            self._ensure_currency(transaction, recipient)
            transaction.set_calculation(
                Decimal("0"), exchange_rate=Decimal("1"),
                recipient_amount=amount, recipient_currency=recipient.currency,
            )
            recipient.deposit(amount)
            return

        sender = self._get_account(transaction.sender_account_id)
        self._ensure_currency(transaction, sender)
        if kind is TransactionType.WITHDRAWAL:
            fee = sender.fixed_fee if isinstance(sender, PremiumAccount) else Decimal("0")
            transaction.set_calculation(fee)
            sender.withdraw(amount)
        elif kind is TransactionType.EXTERNAL_TRANSFER:
            fee = self._round(amount * self._external_fee_rate)
            transaction.set_calculation(
                fee, exchange_rate=Decimal("1"),
                recipient_amount=amount, recipient_currency=sender.currency,
            )
            sender.debit_transfer(amount, fee=fee)
        elif kind is TransactionType.INTERNAL_TRANSFER:
            recipient = self._get_account(transaction.recipient_account_id)
            if sender.currency is recipient.currency:
                rate = Decimal("1")
                received = amount
            else:
                rate = self._exchange_rates.get((sender.currency, recipient.currency))
                if rate is None:
                    raise InvalidOperationError("Не задан курс для валютной пары")
                received = self._round(amount * rate)
            transaction.set_calculation(
                Decimal("0"), exchange_rate=rate,
                recipient_amount=received, recipient_currency=recipient.currency,
            )
            sender.transfer_to(recipient, amount, exchange_rate=rate)
        else:
            raise InvalidOperationError("Неизвестный тип транзакции")
