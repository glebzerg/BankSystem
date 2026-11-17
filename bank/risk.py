"""Учебная оценка риска и история активности клиентов в памяти."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from .accounts import Currency
from .exceptions import InvalidOperationError
from .transactions import ExternalRecipient


def _validate_identifier(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise InvalidOperationError("ID должен быть непустой строкой")


def _validate_time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidOperationError("Время операции должно содержать часовой пояс")


def _validate_recipient(value: str | ExternalRecipient) -> None:
    if not isinstance(value, ExternalRecipient):
        _validate_identifier(value)


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class RiskAssessment:
    """Результат анализа с кодами обнаруженных признаков риска."""

    level: RiskLevel
    score: int
    reasons: tuple[str, ...]


class RiskAnalyzer:
    """Суммирует баллы признаков и определяет уровень риска.

    Крупная сумма и частые операции дают по 2 балла,
    ночная операция и новый получатель — по 1 баллу.
    По умолчанию 0 — низкий риск, 1–2 — средний, от 3 — высокий.
    Пороги сумм условные, задаются в валюте операции без конвертации.
    """

    BANK_TIMEZONE = timezone(timedelta(hours=3), name="MSK")
    LARGE_AMOUNT_SCORE = 2
    NIGHT_OPERATION_SCORE = 1
    FREQUENT_OPERATIONS_SCORE = 2
    NEW_RECIPIENT_SCORE = 1

    def __init__(
        self,
        *,
        large_amount_thresholds: Mapping[Currency, Decimal] | None = None,
        night_start_hour: int = 0,
        night_end_hour: int = 5,
        medium_score: int = 1,
        high_score: int = 3,
        frequency_window: timedelta = timedelta(minutes=1),
        frequency_limit: int = 5,
    ):
        if not isinstance(frequency_window, timedelta) or frequency_window <= timedelta(0):
            raise InvalidOperationError("Окно частоты должно быть положительным timedelta")
        if type(frequency_limit) is not int or frequency_limit <= 0:
            raise InvalidOperationError("Порог частоты должен быть целым числом > 0")
        for hour in (night_start_hour, night_end_hour):
            if type(hour) is not int or not 0 <= hour <= 23:
                raise InvalidOperationError("Границы ночи должны быть целыми часами от 0 до 23")
        if night_start_hour == night_end_hour:
            raise InvalidOperationError("Начало и конец ночного интервала должны различаться")
        if (
            type(medium_score) is not int or type(high_score) is not int
            or not 0 < medium_score < high_score
        ):
            raise InvalidOperationError("Границы риска должны быть целыми: 0 < medium < high")

        thresholds = {
            Currency.RUB: Decimal("100000"),
            Currency.USD: Decimal("1000"),
            Currency.EUR: Decimal("1000"),
            Currency.KZT: Decimal("500000"),
            Currency.CNY: Decimal("10000"),
        }
        if large_amount_thresholds is not None:
            if not isinstance(large_amount_thresholds, Mapping):
                raise InvalidOperationError("Пороги сумм должны быть отображением валюта → Decimal")
            for currency, threshold in large_amount_thresholds.items():
                if (
                    not isinstance(currency, Currency)
                    or not isinstance(threshold, Decimal)
                    or not threshold.is_finite() or threshold <= 0
                ):
                    raise InvalidOperationError("Порог должен быть конечным Decimal > 0 для Currency")
                thresholds[currency] = threshold
        self._large_amount_thresholds = thresholds
        self._night_start_hour = night_start_hour
        self._night_end_hour = night_end_hour
        self._medium_score = medium_score
        self._high_score = high_score
        self._frequency_window = frequency_window
        self._frequency_limit = frequency_limit
        # Храним время первой попытки каждой операции, а не каждого повтора.
        self._attempts: dict[str, dict[str, datetime]] = {}
        self._known_recipients: dict[str, set[str | ExternalRecipient]] = {}

    def record_attempt(self, client_id: str, operation_id: str, *, occurred_at: datetime) -> None:
        """Учесть первую попытку операции, включая отклонённую; повтор не меняет время."""
        _validate_identifier(client_id)
        _validate_identifier(operation_id)
        _validate_time(occurred_at)
        self._attempts.setdefault(client_id, {}).setdefault(operation_id, occurred_at)

    def record_successful_transfer(self, client_id: str, recipient: str | ExternalRecipient) -> None:
        """Запомнить получателя только после подтверждённого успешного перевода.

        Внутренний получатель — ID счёта, внешний — ExternalRecipient.
        У внешнего получателя сравниваются номер счёта и ID банка вместе.
        """
        _validate_identifier(client_id)
        _validate_recipient(recipient)
        self._known_recipients.setdefault(client_id, set()).add(recipient)

    def analyze(
        self, amount: Decimal, currency: Currency, *, occurred_at: datetime,
        client_id: str | None = None,
        operation_id: str | None = None,
        recipient: str | ExternalRecipient | None = None,
    ) -> RiskAssessment:
        """Оценить сумму операции и время её попытки, а не время создания.

        Порог крупной суммы включён. Начало ночного интервала включено,
        конец исключён. Часы всегда сравниваются по московскому времени.
        Для правил истории нужны client_id и operation_id вместе.
        recipient передаётся только для перевода. Анализ историю не меняет.
        Частота включает текущую операцию один раз и предыдущие операции
        в интервале (occurred_at - frequency_window, occurred_at].
        """
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise InvalidOperationError("Сумма должна быть конечным Decimal > 0")
        if not isinstance(currency, Currency):
            raise InvalidOperationError("Валюта должна быть элементом Currency")
        _validate_time(occurred_at)
        if client_id is not None or operation_id is not None or recipient is not None:
            _validate_identifier(client_id)
            _validate_identifier(operation_id)
        if recipient is not None:
            _validate_recipient(recipient)

        score = 0
        reasons = []
        if amount >= self._large_amount_thresholds[currency]:
            score += self.LARGE_AMOUNT_SCORE
            reasons.append("large_amount")

        hour = occurred_at.astimezone(self.BANK_TIMEZONE).hour
        if self._night_start_hour < self._night_end_hour:
            is_night = self._night_start_hour <= hour < self._night_end_hour
        else:
            # Интервал, например 22:00–06:00, проходит через полночь.
            is_night = hour >= self._night_start_hour or hour < self._night_end_hour
        if is_night:
            score += self.NIGHT_OPERATION_SCORE
            reasons.append("night_operation")

        if client_id is not None:
            previous_count = sum(
                1 for identifier, first_attempt in self._attempts.get(client_id, {}).items()
                if identifier != operation_id
                and timedelta(0) <= occurred_at - first_attempt < self._frequency_window
            )
            if previous_count + 1 >= self._frequency_limit:
                score += self.FREQUENT_OPERATIONS_SCORE
                reasons.append("frequent_operations")
            if recipient is not None and recipient not in self._known_recipients.get(client_id, set()):
                score += self.NEW_RECIPIENT_SCORE
                reasons.append("new_recipient")

        if score >= self._high_score:
            level = RiskLevel.HIGH
        elif score >= self._medium_score:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW
        return RiskAssessment(level, score, tuple(reasons))
