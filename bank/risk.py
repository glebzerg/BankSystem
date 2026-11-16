"""Учебная оценка риска по сумме и времени; банковские операции не выполняет."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from .accounts import Currency
from .exceptions import InvalidOperationError


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

    Крупная сумма даёт 2 балла, ночная операция — 1 балл.
    По умолчанию 0 — низкий риск, 1–2 — средний, от 3 — высокий.
    Пороги сумм условные, задаются в валюте операции без конвертации.
    """

    BANK_TIMEZONE = timezone(timedelta(hours=3), name="MSK")
    LARGE_AMOUNT_SCORE = 2
    NIGHT_OPERATION_SCORE = 1

    def __init__(
        self,
        *,
        large_amount_thresholds: Mapping[Currency, Decimal] | None = None,
        night_start_hour: int = 0,
        night_end_hour: int = 5,
        medium_score: int = 1,
        high_score: int = 3,
    ):
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

    def analyze(
        self, amount: Decimal, currency: Currency, *, occurred_at: datetime,
    ) -> RiskAssessment:
        """Оценить сумму операции и время её попытки, а не время создания.

        Порог крупной суммы включён. Начало ночного интервала включено,
        конец исключён. Часы всегда сравниваются по московскому времени.
        """
        if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
            raise InvalidOperationError("Сумма должна быть конечным Decimal > 0")
        if not isinstance(currency, Currency):
            raise InvalidOperationError("Валюта должна быть элементом Currency")
        if (
            not isinstance(occurred_at, datetime)
            or occurred_at.tzinfo is None or occurred_at.utcoffset() is None
        ):
            raise InvalidOperationError("Время операции должно содержать часовой пояс")

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

        if score >= self._high_score:
            level = RiskLevel.HIGH
        elif score >= self._medium_score:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW
        return RiskAssessment(level, score, tuple(reasons))
