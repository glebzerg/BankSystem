"""Отчёты по оценённым операциям из журнала аудита в памяти."""

from collections import Counter
from datetime import datetime

from bank.monitoring.audit import AuditLog, AuditRecord
from bank.exceptions import InvalidOperationError
from bank.monitoring.risk import RiskLevel


class AuditReports:
    """Служебный построитель отчётов; Bank проверяет клиентскую сессию.

    Период относится ко времени оценок риска и событий результата, обе
    границы включены. Одна операция учитывается один раз с максимальным
    уровнем риска за период. При равенстве уровня выбираем больший балл,
    затем более позднюю оценку. Результаты за пределами периода не учитываются.
    В отчёты о риске операции без оценки (например, начисление процентов) не включаются.
    """

    _LEVEL_RANK = {"low": 0, "medium": 1, "high": 2}
    _OUTCOMES = {
        "transaction_completed": "completed",
        "operation_completed": "completed",
        "transaction_failed": "failed",
        "operation_failed": "failed",
        "transaction_retry_scheduled": "retry_pending",
        "transaction_cancelled": "cancelled",
    }

    def __init__(self, audit_log: AuditLog):
        if not isinstance(audit_log, AuditLog):
            raise InvalidOperationError("Ожидается журнал AuditLog")
        self._audit_log = audit_log

    def _client_records(
        self, client_id: str, *, start_at: datetime | None, end_at: datetime | None,
    ) -> tuple[AuditRecord, ...]:
        if not isinstance(client_id, str) or not client_id.strip():
            raise InvalidOperationError("ID клиента должен быть непустой строкой")
        return self._audit_log.filter(client_id=client_id, start_at=start_at, end_at=end_at)

    def _operation_rows(
        self, client_id: str, *, start_at: datetime | None, end_at: datetime | None,
    ) -> list[dict]:
        records = self._client_records(client_id, start_at=start_at, end_at=end_at)
        rows = {}
        for record in sorted(records, key=lambda item: item.occurred_at):
            identifier = record.transaction_id
            if identifier is None:
                continue
            if record.event_type in ("risk_assessed", "risk_blocked") and record.risk is not None:
                risk = record.risk
                if identifier not in rows:
                    rows[identifier] = {
                        "operation_id": identifier,
                        "first_assessed_at": record.occurred_at,
                        "last_assessed_at": record.occurred_at,
                        "risk_level": risk.level.value,
                        "score": risk.score,
                        "reasons": risk.reasons,
                        "attempts": 0,
                        "blocked": False,
                        "status": "assessed",
                    }
                row = rows[identifier]
                if (self._LEVEL_RANK[risk.level.value], risk.score) >= (
                    self._LEVEL_RANK[row["risk_level"]], row["score"],
                ):
                    row.update(risk_level=risk.level.value, score=risk.score, reasons=risk.reasons)
                row["last_assessed_at"] = record.occurred_at
                row["attempts"] += 1
                row["blocked"] = row["blocked"] or record.event_type == "risk_blocked"
                row["status"] = "blocked" if record.event_type == "risk_blocked" else "assessed"
            elif identifier in rows and record.event_type in self._OUTCOMES:
                rows[identifier]["status"] = self._OUTCOMES[record.event_type]
        return list(rows.values())

    def suspicious_operations(
        self, client_id: str, *, start_at: datetime | None = None, end_at: datetime | None = None,
    ) -> list[dict]:
        """Вернуть операции среднего и высокого риска с причинами и результатом."""
        return [
            row for row in self._operation_rows(client_id, start_at=start_at, end_at=end_at)
            if row["risk_level"] in (RiskLevel.MEDIUM.value, RiskLevel.HIGH.value)
        ]

    def client_risk_profile(
        self, client_id: str, *, start_at: datetime | None = None, end_at: datetime | None = None,
    ) -> dict:
        """Распределение максимальных уровней и причин; повторы не удваивают операции."""
        rows = self._operation_rows(client_id, start_at=start_at, end_at=end_at)
        levels = {level.value: 0 for level in RiskLevel}
        reasons = Counter()
        for row in rows:
            levels[row["risk_level"]] += 1
            reasons.update(set(row["reasons"]))
        return {
            "client_id": client_id,
            "total_operations": len(rows),
            "suspicious_operations": levels["medium"] + levels["high"],
            "blocked_operations": sum(row["blocked"] for row in rows),
            "by_level": levels,
            "highest_risk": max((row["risk_level"] for row in rows), key=self._LEVEL_RANK.get,
                                default=None),
            "reason_counts": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        }

    def error_statistics(
        self, client_id: str, *, start_at: datetime | None = None, end_at: datetime | None = None,
    ) -> dict:
        """Ошибки денежных операций по времени события, границы периода включены.

        Повтор с последующим успехом — неудачная попытка, но не окончательный
        отказ. risk_blocked не считается второй ошибкой той же попытки.
        Ошибки входа и незаписанные события в эту статистику не входят.
        """
        records = self._client_records(client_id, start_at=start_at, end_at=end_at)
        errors = [record for record in records if record.error_type is not None
                  and record.event_type in (
                      "transaction_failed", "transaction_retry_scheduled", "operation_failed",
                  )]
        failed = {record.transaction_id for record in errors
                  if record.transaction_id is not None
                  and record.event_type in ("transaction_failed", "operation_failed")}
        blocked = {record.transaction_id for record in records
                   if record.transaction_id is not None and record.event_type == "risk_blocked"}
        return {
            "failed_attempts": len(errors),
            "failed_operations": len(failed),
            "risk_blocked_operations": len(blocked),
            "by_error_type": dict(sorted(Counter(record.error_type for record in errors).items())),
        }
