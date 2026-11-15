"""Модель поручения на банковскую операцию."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from .accounts import Currency
from .exceptions import InvalidOperationError


class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    INTERNAL_TRANSFER = "internal_transfer"
    EXTERNAL_TRANSFER = "external_transfer"


class TransactionStatus(Enum):
    """Этапы обработки поручения."""

    CREATED = "created"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_PENDING = "retry_pending"


def _clean_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationError(f"{label} должен быть непустой строкой")
    return value.strip()


@dataclass(frozen=True)
class ExternalRecipient:
    """Неизменяемые реквизиты получателя в другом банке."""

    account_number: str
    bank_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "account_number",
            _clean_identifier(self.account_number, "Номер внешнего счёта"),
        )
        object.__setattr__(
            self, "bank_id", _clean_identifier(self.bank_id, "ID банка"),
        )


class Transaction:
    """Хранит поручение и его состояние; самостоятельно деньги не переводит.

    Сумма перевода задаётся в валюте отправителя, пополнения — получателя.
    Существование счетов и возможность операции проверит процессор.
    """

    def __init__(
        self,
        transaction_type: TransactionType,
        amount: Decimal,
        currency: Currency,
        *,
        sender_account_id: str | None = None,
        recipient_account_id: str | None = None,
        external_recipient: ExternalRecipient | None = None,
    ):
        if not isinstance(transaction_type, TransactionType):
            raise InvalidOperationError("Тип должен быть элементом TransactionType")
        if not isinstance(currency, Currency):
            raise InvalidOperationError("Валюта должна быть элементом Currency")
        if (
            not isinstance(amount, Decimal)
            or not amount.is_finite()
            or amount <= 0
        ):
            raise InvalidOperationError("Сумма должна быть конечным Decimal > 0")

        if sender_account_id is not None:
            sender_account_id = _clean_identifier(sender_account_id, "ID отправителя")
        if recipient_account_id is not None:
            recipient_account_id = _clean_identifier(recipient_account_id, "ID получателя")
        if external_recipient is not None and not isinstance(
            external_recipient, ExternalRecipient,
        ):
            raise InvalidOperationError("Внешние реквизиты должны иметь тип ExternalRecipient")

        # Для каждого типа задан точный набор участников: отправитель,
        # внутренний получатель и внешний получатель соответственно.
        required_participants = {
            TransactionType.DEPOSIT: (False, True, False),
            TransactionType.WITHDRAWAL: (True, False, False),
            TransactionType.INTERNAL_TRANSFER: (True, True, False),
            TransactionType.EXTERNAL_TRANSFER: (True, False, True),
        }
        participants = (
            sender_account_id is not None,
            recipient_account_id is not None,
            external_recipient is not None,
        )
        if participants != required_participants[transaction_type]:
            raise InvalidOperationError("Набор участников не соответствует типу операции")
        if (
            transaction_type is TransactionType.INTERNAL_TRANSFER
            and sender_account_id == recipient_account_id
        ):
            raise InvalidOperationError("Нельзя переводить на тот же самый счёт")

        self._transaction_id = str(uuid.uuid4())
        self._transaction_type = transaction_type
        self._amount = amount
        self._currency = currency
        self._sender_account_id = sender_account_id
        self._recipient_account_id = recipient_account_id
        self._external_recipient = external_recipient
        self._fee: Decimal | None = None
        self._status = TransactionStatus.CREATED
        self._failure_reason: str | None = None
        self._created_at = datetime.now(timezone.utc)
        self._updated_at = self._created_at
        self._completed_at: datetime | None = None

    def mark_queued(self) -> None:
        """Поставить новое поручение или повторную попытку в очередь."""
        self._change_status(
            TransactionStatus.QUEUED,
            (TransactionStatus.CREATED, TransactionStatus.RETRY_PENDING),
        )

    def mark_processing(self) -> None:
        """Начать выполнение операции из очереди."""
        self._change_status(
            TransactionStatus.PROCESSING, (TransactionStatus.QUEUED,),
        )
        self._failure_reason = None

    def mark_completed(self) -> None:
        """Зафиксировать успешное выполнение операции."""
        self._change_status(
            TransactionStatus.COMPLETED, (TransactionStatus.PROCESSING,),
        )
        self._completed_at = self._updated_at

    def mark_failed(self, reason: str) -> None:
        """Зафиксировать окончательный отказ с обязательной причиной."""
        reason = _clean_identifier(reason, "Описание ошибки")
        self._change_status(
            TransactionStatus.FAILED, (TransactionStatus.PROCESSING,),
        )
        self._failure_reason = reason

    def mark_retry_pending(self, reason: str) -> None:
        """Зафиксировать временную ошибку и ожидание повторной попытки.

        Время следующей попытки и её допустимость определит процессор.
        """
        reason = _clean_identifier(reason, "Описание ошибки")
        self._change_status(
            TransactionStatus.RETRY_PENDING, (TransactionStatus.PROCESSING,),
        )
        self._failure_reason = reason

    def cancel(self) -> None:
        """Отменить поручение, которое сейчас не выполняется."""
        self._change_status(
            TransactionStatus.CANCELLED,
            (
                TransactionStatus.CREATED,
                TransactionStatus.QUEUED,
                TransactionStatus.RETRY_PENDING,
            ),
        )
        self._failure_reason = None

    def _change_status(
        self,
        new_status: TransactionStatus,
        allowed_from: tuple[TransactionStatus, ...],
    ) -> None:
        """Проверить переход до изменения состояния и обновить время."""
        if self._status not in allowed_from:
            raise InvalidOperationError(
                f"Недопустимый переход: {self._status.value} → {new_status.value}"
            )

        changed_at = datetime.now(timezone.utc)
        self._status = new_status
        self._updated_at = changed_at

    @property
    def transaction_id(self) -> str:
        return self._transaction_id

    @property
    def transaction_type(self) -> TransactionType:
        return self._transaction_type

    @property
    def amount(self) -> Decimal:
        return self._amount

    @property
    def currency(self) -> Currency:
        return self._currency

    @property
    def sender_account_id(self) -> str | None:
        return self._sender_account_id

    @property
    def recipient_account_id(self) -> str | None:
        return self._recipient_account_id

    @property
    def external_recipient(self) -> ExternalRecipient | None:
        return self._external_recipient

    @property
    def fee(self) -> Decimal | None:
        return self._fee

    @property
    def status(self) -> TransactionStatus:
        return self._status

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def completed_at(self) -> datetime | None:
        return self._completed_at
