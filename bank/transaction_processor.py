"""Однопоточная обработка очереди с учебными курсами валют."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, DecimalException

from .accounts import BankAccount, Currency, PremiumAccount
from .exceptions import BankError, InvalidOperationError, TemporaryTransactionError
from .transaction_queue import TransactionQueue
from .transactions import Transaction, TransactionType


@dataclass(frozen=True)
class TransactionErrorRecord:
    """Неизменяемая запись об одной неудачной попытке."""

    transaction_id: str
    attempt: int
    occurred_at: datetime
    error_type: str
    message: str
    retry_at: datetime | None


class TransactionProcessor:
    """Выполняет готовые операции; Bank проверяет доступ через before_execute.

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
        max_attempts: int = 3,
        retry_delay: timedelta = timedelta(seconds=60),
        external_preflight: Callable[[Transaction], None] | None = None,
        before_execute: Callable[[Transaction], None] | None = None,
        on_completed: Callable[[Transaction], None] | None = None,
    ):
        if not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("Ожидается очередь TransactionQueue")
        if not isinstance(accounts, Mapping):
            raise InvalidOperationError("Счета должны быть переданы как отображение ID → счёт")
        if type(max_attempts) is not int or max_attempts <= 0:
            raise InvalidOperationError("Число попыток должно быть целым числом > 0")
        if not isinstance(retry_delay, timedelta) or retry_delay <= timedelta(0):
            raise InvalidOperationError("Задержка повторов должна быть положительным timedelta")
        if external_preflight is not None and not callable(external_preflight):
            raise InvalidOperationError("Проверка внешнего банка должна быть вызываемым объектом")
        if before_execute is not None and not callable(before_execute):
            raise InvalidOperationError("Проверка операции должна быть вызываемым объектом")
        if on_completed is not None and not callable(on_completed):
            raise InvalidOperationError("Обработчик успеха должен быть вызываемым объектом")
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
        self._max_attempts = max_attempts
        self._retry_delay = retry_delay
        # Проверка только доступности: она не должна отправлять перевод
        # или менять балансы. TemporaryTransactionError гарантирует отсутствие списания.
        self._external_preflight = external_preflight
        self._before_execute = before_execute
        self._on_completed = on_completed
        self._errors: list[TransactionErrorRecord] = []

    @property
    def errors(self) -> tuple[TransactionErrorRecord, ...]:
        """Снимок журнала: вызывающий код не может изменить внутренний список."""
        return tuple(self._errors)

    def process_next(self) -> Transaction | None:
        """Обработать одну готовую операцию; банковский отказ сохранить в ней."""
        occurred_at = self._queue.get_current_time()
        transaction = self._queue.get_next()
        if transaction is None:
            return None
        try:
            if self._before_execute is not None:
                self._before_execute(transaction)
            self._execute(transaction)
        except TemporaryTransactionError as error:
            message = str(error).strip() or type(error).__name__
            retry_at = None
            if transaction.attempts_count < self._max_attempts:
                try:
                    retry_at = occurred_at + self._retry_delay
                except OverflowError:
                    # Если назначенное время не представимо, завершаем отказом.
                    pass
            if retry_at is None:
                transaction.mark_failed(message)
            else:
                transaction.mark_retry_pending(message)
                # Новая запись получает обычный приоритет 0 и новое место в очереди.
                self._queue.add(transaction, scheduled_at=retry_at)
            self._record_error(transaction, error, message, occurred_at, retry_at)
        except BankError as error:
            message = str(error).strip() or type(error).__name__
            transaction.mark_failed(message)
            self._record_error(transaction, error, message, occurred_at)
        except DecimalException as error:
            message = "Ошибка точности или диапазона денежного расчёта"
            transaction.mark_failed(message)
            self._record_error(transaction, error, message, occurred_at)
        else:
            transaction.mark_completed()
            # Уведомление после успеха не должно повторять уже выполненный перевод.
            if self._on_completed is not None:
                self._on_completed(transaction)
        return transaction

    def _record_error(
        self,
        transaction: Transaction,
        error: Exception,
        message: str,
        occurred_at: datetime,
        retry_at: datetime | None = None,
    ) -> None:
        self._errors.append(TransactionErrorRecord(
            transaction.transaction_id, transaction.attempts_count,
            occurred_at, type(error).__name__, message, retry_at,
        ))

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
            sender._ensure_active()
            sender._validate_transfer_funds(amount + fee)
            if self._external_preflight is not None:
                self._external_preflight(transaction)
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
