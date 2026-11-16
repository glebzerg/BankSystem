"""Записи аудита и журнал событий в памяти."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .exceptions import InvalidOperationError


class AuditLevel(Enum):
    """Важность события; это не уровень риска операции."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class AuditRecord:
    """Неизменяемая запись об одном событии банка."""

    occurred_at: datetime
    level: AuditLevel
    event_type: str
    description: str
    client_id: str | None = None
    transaction_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise InvalidOperationError("Время события должно содержать часовой пояс")
        if not isinstance(self.level, AuditLevel):
            raise InvalidOperationError("Важность должна быть элементом AuditLevel")
        for label, value in (
            ("Тип события", self.event_type),
            ("Описание", self.description),
        ):
            if not isinstance(value, str) or not value.strip():
                raise InvalidOperationError(f"{label} должен быть непустой строкой")
        for label, value in (
            ("ID клиента", self.client_id),
            ("ID транзакции", self.transaction_id),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise InvalidOperationError(f"{label} должен быть непустой строкой или None")


class AuditLog:
    """Хранит события в порядке добавления и отдаёт неизменяемый снимок."""

    def __init__(self, *, now: Callable[[], datetime] | None = None):
        if now is not None and not callable(now):
            raise InvalidOperationError("Источник времени должен быть вызываемым объектом")
        self._now = now if now is not None else lambda: datetime.now(timezone.utc)
        self._records: list[AuditRecord] = []

    def add(
        self,
        level: AuditLevel,
        event_type: str,
        description: str,
        *,
        client_id: str | None = None,
        transaction_id: str | None = None,
    ) -> AuditRecord:
        """Создать и сохранить запись; некорректные данные не меняют журнал."""
        record = AuditRecord(
            occurred_at=self._now(),
            level=level,
            event_type=event_type,
            description=description,
            client_id=client_id,
            transaction_id=transaction_id,
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        """Снимок журнала, который не изменяется при добавлении новых событий."""
        return tuple(self._records)
