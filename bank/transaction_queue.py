"""Очередь транзакций с приоритетом и назначенным временем выполнения."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from .exceptions import InvalidOperationError
from .transactions import Transaction, TransactionStatus


@dataclass(frozen=True)
class _QueueEntry:
    transaction: Transaction
    priority: int
    scheduled_at: datetime
    sequence: int


class TransactionQueue:
    """Хранит ожидающие операции в памяти; деньги не перемещает.

    Больший priority обслуживается раньше. При равном приоритете
    сохраняется порядок добавления. Очередь рассчитана на один поток.
    """

    def __init__(self, *, now: Callable[[], datetime] | None = None):
        if now is not None and not callable(now):
            raise InvalidOperationError("Источник времени должен быть вызываемым объектом")
        self._now = now if now is not None else lambda: datetime.now(timezone.utc)
        self._entries: dict[str, _QueueEntry] = {}
        self._sequence = 0

    @staticmethod
    def _validate_time(value: datetime) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise InvalidOperationError("Время должно иметь тип datetime с часовым поясом")
        return value.astimezone(timezone.utc)

    def add(
        self,
        transaction: Transaction,
        *,
        priority: int = 0,
        scheduled_at: datetime | None = None,
    ) -> None:
        """Добавить новую операцию или разрешённую процессором повторную попытку."""
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("Ожидается объект Transaction")
        if type(priority) is not int:
            raise InvalidOperationError("Приоритет должен быть целым числом")
        if transaction.transaction_id in self._entries:
            raise InvalidOperationError("Транзакция уже находится в очереди")
        scheduled_at = self._validate_time(
            self._now() if scheduled_at is None else scheduled_at
        )

        # Сначала проверяем переход: при отказе очередь не изменяется.
        transaction.mark_queued()
        self._entries[transaction.transaction_id] = _QueueEntry(
            transaction, priority, scheduled_at, self._sequence,
        )
        self._sequence += 1

    def get_current_time(self) -> datetime:
        """Вернуть время из часов очереди для согласованного планирования повторов."""
        return self._validate_time(self._now())

    def get_next(self) -> Transaction | None:
        """Забрать готовую операцию и отметить начало обработки.

        None означает, что сейчас нет готовых операций. При этом
        в очереди могут оставаться отложенные транзакции.
        """
        current_time = self.get_current_time()
        # Если поручение отменили напрямую, оно больше не ожидает обработки.
        self._entries = {
            transaction_id: entry
            for transaction_id, entry in self._entries.items()
            if entry.transaction.status is TransactionStatus.QUEUED
        }
        ready = [
            entry for entry in self._entries.values()
            if entry.scheduled_at <= current_time
        ]
        if not ready:
            return None

        entry = min(ready, key=lambda item: (-item.priority, item.sequence))
        entry.transaction.mark_processing()
        del self._entries[entry.transaction.transaction_id]
        return entry.transaction

    def cancel(self, transaction_id: str) -> None:
        """Отменить ожидающую транзакцию и убрать её из очереди."""
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise InvalidOperationError("ID транзакции должен быть непустой строкой")
        entry = self._entries.get(transaction_id)
        if entry is None:
            raise InvalidOperationError("Транзакция не найдена в очереди")
        entry.transaction.cancel()
        del self._entries[transaction_id]

    def __len__(self) -> int:
        """Количество ожидающих операций, включая отложенные."""
        return sum(
            entry.transaction.status is TransactionStatus.QUEUED
            for entry in self._entries.values()
        )
