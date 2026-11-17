"""Записи аудита, фильтрация и дописывание событий в JSONL-файл."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from .exceptions import AuditWriteError, InvalidOperationError
from .risk import RiskAssessment


def _validate_time(value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise InvalidOperationError("Время должно содержать часовой пояс")


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
    risk: RiskAssessment | None = None

    def __post_init__(self) -> None:
        _validate_time(self.occurred_at)
        if self.risk is not None and not isinstance(self.risk, RiskAssessment):
            raise InvalidOperationError("Оценка риска должна быть RiskAssessment или None")
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

    def __init__(
        self,
        *,
        now: Callable[[], datetime] | None = None,
        file_path: str | Path | None = None,
    ):
        if now is not None and not callable(now):
            raise InvalidOperationError("Источник времени должен быть вызываемым объектом")
        if file_path is not None and (
            not isinstance(file_path, (str, Path))
            or (isinstance(file_path, str) and not file_path.strip())
        ):
            raise InvalidOperationError("Путь журнала должен быть непустой строкой или Path")
        self._now = now if now is not None else lambda: datetime.now(timezone.utc)
        self._file_path = Path(file_path) if file_path is not None else None
        self._records: list[AuditRecord] = []

    def add(
        self,
        level: AuditLevel,
        event_type: str,
        description: str,
        *,
        client_id: str | None = None,
        transaction_id: str | None = None,
        occurred_at: datetime | None = None,
        risk: RiskAssessment | None = None,
    ) -> AuditRecord:
        """Создать и сохранить запись; некорректные данные не меняют журнал."""
        record = AuditRecord(
            occurred_at=occurred_at if occurred_at is not None else self._now(),
            level=level,
            event_type=event_type,
            description=description,
            client_id=client_id,
            transaction_id=transaction_id,
            risk=risk,
        )
        if self._file_path is not None:
            self._append_to_file(record)
        self._records.append(record)
        return record

    def _append_to_file(self, record: AuditRecord) -> None:
        """Дописывает одну запись; существующий файл не очищается.

        Родительская папка должна существовать. Старые записи из файла
        в память не загружаются. Журнал рассчитан на одного писателя.
        """
        data = {
            "occurred_at": record.occurred_at.isoformat(),
            "level": record.level.value,
            "event_type": record.event_type,
            "description": record.description,
            "client_id": record.client_id,
            "transaction_id": record.transaction_id,
        }
        if record.risk is not None:
            data["risk"] = {
                "level": record.risk.level.value,
                "score": record.risk.score,
                "reasons": record.risk.reasons,
            }
        try:
            line = json.dumps(data, ensure_ascii=False) + "\n"
            with self._file_path.open("a", encoding="utf-8") as stream:
                stream.write(line)
        except (OSError, UnicodeError, ValueError) as error:
            raise AuditWriteError("Не удалось записать событие в файл аудита") from error

    def filter(
        self,
        *,
        level: AuditLevel | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        client_id: str | None = None,
        transaction_id: str | None = None,
    ) -> tuple[AuditRecord, ...]:
        """Все заданные условия применяются вместе; границы периода включены.

        None означает отсутствие фильтра. Порядок добавления сохраняется.
        Поиск выполняется только по записям текущего объекта в памяти.
        """
        if level is not None and not isinstance(level, AuditLevel):
            raise InvalidOperationError("Важность должна быть элементом AuditLevel")
        for boundary in (start_at, end_at):
            if boundary is not None:
                _validate_time(boundary)
        if start_at is not None and end_at is not None and start_at > end_at:
            raise InvalidOperationError("Начало периода не должно быть позже конца")
        for identifier in (client_id, transaction_id):
            if identifier is not None and (
                not isinstance(identifier, str) or not identifier.strip()
            ):
                raise InvalidOperationError("ID должен быть непустой строкой или None")
        return tuple(
            record for record in self._records
            if (level is None or record.level is level)
            and (start_at is None or record.occurred_at >= start_at)
            and (end_at is None or record.occurred_at <= end_at)
            and (client_id is None or record.client_id == client_id)
            and (transaction_id is None or record.transaction_id == transaction_id)
        )

    @property
    def records(self) -> tuple[AuditRecord, ...]:
        """Снимок журнала, который не изменяется при добавлении новых событий."""
        return tuple(self._records)
